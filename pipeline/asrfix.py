#!/usr/bin/env python
"""转写后处理：繁简 → 术语校正 → 静音一致性 → 体检。

三条原则：
1. **只报告，不静默改**：改了几处、哪几段可疑，都要能被下一个人看见。
2. **唯一输入是 `evidence/asr/asr.json`**（+ 词表）。这样重跑幂等；反过来拿 `transcript.md`
   当输入就不幂等——比如 `{"寒暑":"setter","setter":"setter()"}` 第二次会把 `setter` 再改成
   `setter()`。所以这里永远从 asr.json 重新算一遍。
3. **输入先繁转简**：asr.json 是 whisper 的原始输出，中文常是繁体（实测 `BV1EAKH6GE7C`
   304 段里 282 段是繁体）。漏了这一步，`--write` 会把一篇已经转简体的成品稿退回繁体。

不做"音乐幻觉丢段"（参考仓库 `filterSuspectSegments`）：我们的 ASR 是 faster-whisper +
`vad_filter=True`，非语音段根本不会产出 segment（60s 纯音 / 粉噪实测 0 段）。8 篇 1764 段里
标点段 0、低字/秒长段 0，且中文三篇**没有任何 >8s 的段**——那条阈值的半边结构性够不到。
所以只保留"检出数"字段，如实为 0。

用法（离线，不重跑 ASR）：
    py -3.14 pipeline/asrfix.py BV1EAKH6GE7C          # 只看报告
    py -3.14 pipeline/asrfix.py BV1EAKH6GE7C --write  # 写 evidence/transcript.fixed.md + research.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import store  # 同目录：JSON 原子写 / 容错读

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pydeps"))
NOTES = ROOT / "notes"

SILENCE_RATIO = 0.85     # ≥85% 时长落在静音段里才报（0.6 时 13 条全是真话，纯噪声）
SILENCE_MIN_DUR = 5.0    # 太短的段不值得提示
SUSPECT_MIN_DUR = 8.0
SUSPECT_CPS = 0.8
MAX_GAP_WARN = 25.0
CPS_FLOOR = 1.0
CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))


# ---------------------------------------------------------------- 繁简

_T2S = None


def to_simplified(text: str) -> str:
    """whisper 的中文输出常是繁体；有 opencc 就转，没有就原样返回（并告警一次）。"""
    global _T2S
    if _T2S is None:
        try:
            from opencc import OpenCC  # type: ignore
            _T2S = OpenCC("t2s")
        except Exception as exc:
            print(f"[asr] 繁转简不可用（{type(exc).__name__}: {exc}），转写保留原文",
                  file=sys.stderr, flush=True)
            _T2S = False
    return _T2S.convert(text) if _T2S else text


def t2s_available() -> bool:
    to_simplified("測試")
    return bool(_T2S)


# ---------------------------------------------------------------- 基础

def meaningful_text(text: str) -> str:
    """去掉空白、标点、符号后的文本（中英文都适用）。"""
    return "".join(ch for ch in (text or "")
                   if not ch.isspace() and unicodedata.category(ch)[0] not in "PSZ")


def meaningful_len(text: str) -> int:
    return len(meaningful_text(text))


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(a <= o <= b for a, b in CJK_RANGES)


def script_mix(segs: list[dict]) -> dict:
    """汉字 vs 拉丁字母的占比。用来判断"转出来的文字跟请求的语种对不上"。"""
    cjk = latin = 0
    for s in segs:
        for ch in meaningful_text(s.get("text") or ""):
            if _is_cjk(ch):
                cjk += 1
            elif ch.isascii() and ch.isalpha():
                latin += 1
    total = cjk + latin
    return {"cjk_chars": cjk, "latin_chars": latin,
            "cjk_ratio": round(cjk / total, 3) if total else 0.0,
            "latin_ratio": round(latin / total, 3) if total else 0.0}


def silence_spans(events: list[dict]) -> list[tuple[float, float]]:
    """timeline.silences 是事件流 `[{"kind":"start","t":..},{"kind":"end","t":..}]`，
    这里折成区间；未闭合的 start 丢掉。"""
    spans: list[tuple[float, float]] = []
    start: float | None = None
    for e in events or []:
        kind, t = e.get("kind"), e.get("t")
        if kind == "start" and t is not None:
            start = float(t)
        elif kind == "end" and start is not None and t is not None:
            if float(t) > start:
                spans.append((start, float(t)))
            start = None
    return merge_spans(spans)


def merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """合并重叠/相邻区间——不合并的话覆盖率会重复累加，超过 1。"""
    out: list[tuple[float, float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def coverage(spans: list[tuple[float, float]], a: float, b: float) -> float:
    """[a,b] 被静音区间覆盖的比例，0–1。"""
    if b <= a:
        return 0.0
    covered = sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans)
    return min(1.0, covered / (b - a))


# ---------------------------------------------------------------- 术语表

def _clean_terms(raw: object) -> dict[str, str]:
    """只收「非空、无换行/制表符」的字符串对；键以 _ 开头视为注释。

    不做"键==值"过滤在这里，交给 apply_terms（那样能报告"这条是空操作"）。
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not (isinstance(k, str) and isinstance(v, str)):
            continue
        k, v = k.strip(), v.strip()
        if not k or not v or k.startswith("_"):
            continue
        if any(c in k + v for c in "\r\n\t"):
            continue            # 换行会破坏 transcript.md 的单行结构
        out[k] = v
    return out


def load_terms(root: Path = ROOT, note_dir: Path | None = None) -> dict:
    """全局 `asr-terms.json` 打底，`notes/<id>/terms.json` 覆盖同名键。

    BOM / 坏 JSON 不抛异常（转写校正不该挡住主链路），但**要把问题带回去**，
    否则"词表没生效"和"词表是空的"看起来一模一样。
    """
    terms: dict[str, str] = {}
    files: list[str] = []
    errors: list[str] = []
    for path in (root / "asr-terms.json", (note_dir / "terms.json") if note_dir else None):
        if not path or not path.exists():
            continue
        try:
            # utf-8-sig：记事本存的 UTF-8 with BOM 会让 json.loads 直接抛
            terms.update(_clean_terms(json.loads(path.read_text(encoding="utf-8-sig"))))
            files.append(path.name if path.parent == note_dir else str(path.relative_to(root)))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
            print(f"[asrfix] 术语表读不动，跳过 {path.name}：{exc}", file=sys.stderr)
    return {"terms": terms, "files": files, "errors": errors}


def _term_pattern(terms: dict[str, str]) -> re.Pattern:
    """CJK 键照原样；含拉丁字母的键加边界，避免 `set` 命中 `settings`。"""
    cjk_keys = [k for k in terms if not re.search(r"[A-Za-z]", k)]
    latin_keys = [k for k in terms if re.search(r"[A-Za-z]", k)]
    parts = [re.escape(k) for k in sorted(cjk_keys, key=len, reverse=True)]
    parts += [rf"(?<![A-Za-z]){re.escape(k)}(?![A-Za-z])"
              for k in sorted(latin_keys, key=len, reverse=True)]
    return re.compile("|".join(parts))


def apply_terms(segs: list[dict], terms: dict[str, str]) -> dict:
    """单遍替换：一条交替正则走完。

    为什么单遍：循环 `str.replace` 会让新文本再被后面的键命中
    （`{"像散":"X","像":"Y"}` 处理 "像散" 会变成 "XY"）。

    注意（已知边界）：如果词表**自己成链**（`{"寒暑":"酷暑","酷暑":"炎夏"}`），
    单遍只保证"这一次稳定"；再跑一遍会变成"炎夏"。以 asr.json 为唯一输入时不会累积，
    `load_terms` 会把这种链报出来。
    """
    if not terms or not segs:
        return {"segments": segs, "replaced": 0, "touched": 0, "hits": {}, "noop": 0}
    terms = _clean_terms(terms)      # 直接调用本函数的人可能没过滤空键/换行，这里再挡一次
    skip = {k for k, v in terms.items() if k == v}
    effective = {k: v for k, v in terms.items() if k != v}
    if not effective:
        return {"segments": segs, "replaced": 0, "touched": 0, "hits": {}, "noop": len(skip)}
    pattern = _term_pattern(effective)
    hits: dict[str, int] = {}
    replaced = touched = 0
    out: list[dict] = []
    for seg in segs:
        text = seg.get("text") or ""
        local = 0

        def sub(m: re.Match) -> str:
            nonlocal local
            word = m.group(0)
            hits[word] = hits.get(word, 0) + 1
            local += 1
            return effective[word]

        new_text = pattern.sub(sub, text)
        out.append({**seg, "text": new_text} if local else seg)
        replaced += local
        touched += 1 if local else 0
    return {"segments": out, "replaced": replaced, "touched": touched, "hits": hits, "noop": len(skip)}


# ---------------------------------------------------------------- 检查

def suspect(segs: list[dict], min_dur: float = SUSPECT_MIN_DUR,
            min_cps: float = SUSPECT_CPS) -> dict:
    """参考实现那两条判据的**检出数**（只报告，不丢）。本仓库语料上恒为 0。"""
    punct = music = 0
    for s in segs:
        d = max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
        n = meaningful_len(s.get("text") or "")
        if n == 0:
            punct += 1
        elif d > min_dur and n / d < min_cps:
            music += 1
    return {"punctuation_only": punct, "music_like": music}


def text_in_silence(segs: list[dict], spans: list[tuple[float, float]],
                    ratio: float = SILENCE_RATIO, min_dur: float = SILENCE_MIN_DUR,
                    samples: int = 20) -> dict:
    """≥ratio 时长落在静音段里的句子 → 音量低到几乎听不见。不丢段，只报告。

    阈值定在 0.85 而不是 0.6：0.6 时 13 条样本全是真话（`Here we go` / `trim`），
    它们是"音量低"，不是"该删"。0.85 在现有语料上命中 0，作为守卫留着。
    """
    hits = []
    for s in segs:
        a, b = float(s.get("start", 0)), float(s.get("end", 0))
        if b - a < min_dur:
            continue
        r = coverage(spans, a, b)
        if r >= ratio:
            hits.append({"start": round(a, 2), "end": round(b, 2),
                         "ratio": round(r, 2), "text": core_text(s)})
    return {"count": len(hits), "samples": hits[:samples]}


def core_text(seg: dict, limit: int = 40) -> str:
    text = (seg.get("text") or "").strip()
    return text[:limit] if meaningful_len(text) else "（无实义字符）"


def health(segs: list[dict], *, expect_lang: str | None = None,
           media_duration: float | None = None) -> dict:
    """转写体检：有没有人说话、覆盖够不够、大空洞、文字跟请求语种对不对得上。

    **注意**：`asr.json.language` 是"你请求的语种"（vw.py 把 `--lang` 直接传给 whisper），
    不是"whisper 自己判出来的"，所以它不能当检测结果用。这里改用**汉字/拉丁字母占比**判断
    "转出来的文字跟语种对不上"，这一条不依赖那个字段。
    """
    chars = sum(meaningful_len(s.get("text") or "") for s in segs)
    seconds = sum(max(0.0, float(s.get("end", 0)) - float(s.get("start", 0))) for s in segs)
    first = min((float(s.get("start", 0)) for s in segs), default=0.0)
    last = max((float(s.get("end", 0)) for s in segs), default=0.0)
    gaps = [float(b.get("start", 0)) - float(a.get("end", 0)) for a, b in zip(segs, segs[1:])]
    max_gap = max([*gaps, first], default=0.0)
    tail_gap = (media_duration - last) if media_duration else 0.0
    cps = chars / seconds if seconds else 0.0
    mix = script_mix(segs)

    flags: list[str] = []
    if not segs:
        flags.append("没有转写：音轨无人声，或转写失败")
    else:
        if (expect_lang == "en" and mix["cjk_ratio"] > 0.5) or \
           (expect_lang == "zh" and mix["latin_ratio"] > 0.8):
            flags.append(f"文字与请求语种对不上：要 {expect_lang}，实际汉字占 "
                         f"{mix['cjk_ratio']:.0%} / 拉丁字母占 {mix['latin_ratio']:.0%}"
                         "（换 --asr-lang 重跑）")
        if cps < CPS_FLOOR:
            flags.append(f"全片只有 {cps:.2f} 字/秒：基本等于没人说话，别按口播素材写")
        if max_gap > MAX_GAP_WARN:
            flags.append(f"最大空洞 {max_gap:.0f}s（片头算在内）：可能语言判错或音轨缺失")
        if tail_gap > MAX_GAP_WARN:
            flags.append(f"片尾有 {tail_gap:.0f}s 没有转写")
        if media_duration and last < media_duration * 0.5:
            flags.append(f"转写只覆盖到 {last:.0f}s / 素材 {media_duration:.0f}s")
    return {
        "segments": len(segs),
        "chars": chars,
        "speech_seconds": round(seconds, 1),
        "chars_per_sec": round(cps, 2),
        "max_gap": round(max_gap, 1),
        "tail_gap": round(tail_gap, 1),
        "script": mix,
        "expect_lang": expect_lang,
        "flags": flags,
    }


# ---------------------------------------------------------------- CLI（离线）

def _note_dir(slug: str) -> Path:
    note = NOTES / slug
    if not note.exists():
        raise SystemExit(f"没有这个项目：{note}")
    return note


def load_segments(note: Path) -> tuple[list[dict], dict]:
    """读 asr.json 并**先做繁转简**——漏了这一步，写回会把繁体灌回成品稿。"""
    asr_path = note / "evidence" / "asr" / "asr.json"
    if not asr_path.exists():
        raise SystemExit(f"{note.name} 没有转写：{asr_path}")
    data = store.load_json(asr_path, default={})
    segs = [{"start": round(float(s["start"]), 2), "end": round(float(s["end"]), 2),
             "text": to_simplified((s.get("text") or "").strip())}
            for s in data.get("segments") or []]
    return segs, {"model": data.get("model"), "language": data.get("language")}


def run_one(slug: str, write: bool = False) -> dict:
    note = _note_dir(slug)
    segs, info = load_segments(note)

    research_path = note / "research.json"
    research = store.load_json(research_path, default={})
    timeline = research.get("timeline") or {}
    spans = silence_spans(timeline.get("silences") or [])
    if not spans:                       # 旧笔记的 research.json 可能没同步静音段
        probe = note / "evidence" / "probe" / "timeline.json"
        if probe.exists():
            spans = silence_spans(store.load_json(probe, default={}).get("silences") or [])

    loaded = load_terms(ROOT, note)
    terms = loaded["terms"]
    chained = sorted({k for k in terms if terms[k] in terms})
    fixed = apply_terms(segs, terms)
    meta = {
        "terms": {"files": loaded["files"], "errors": loaded["errors"],
                  "count": len(terms), "replaced": fixed["replaced"],
                  "touched": fixed["touched"], "hits": fixed["hits"],
                  "noop": fixed["noop"], "chained": chained},
        "in_silence": text_in_silence(fixed["segments"], spans),
        "health": health(fixed["segments"], expect_lang=None,
                         media_duration=timeline.get("duration")),
        "suspect": suspect(fixed["segments"]),
        "asr_backend": info,
        "t2s": t2s_available(),
    }

    if write:
        research["asr"] = fixed["segments"]
        research["asr_meta"] = meta
        store.save_json(research_path, research)   # 原子写：别让半个文件落盘
        write_fixed_transcript(note, fixed["segments"], meta)
    return meta


def write_fixed_transcript(note: Path, segs: list[dict], meta: dict) -> Path:
    """写**旁路**文件，不覆盖 `evidence/transcript.md`。

    两篇成品笔记里写着"原稿 `evidence/transcript.md`"——覆盖它等于把"原稿"改成"校正稿"，
    而 NOTE_CONTRACT 要求"附录并列机器原文"，所以原稿必须留着。
    """
    path = note / "evidence" / "transcript.fixed.md"
    t, s, h = meta["terms"], meta["in_silence"], meta["health"]
    lines = ["# 机器转写（术语已按词表校正，未人工校对）", "",
             "> 原稿见 `transcript.md`（未校正）。这里只改了词表命中的字，其余与原文一致。"]
    if t["files"]:
        lines.append(f"> 词表：{', '.join(t['files'])}；命中 {t['replaced']} 处 / {t['touched']} 段"
                     + (f"；{t['hits']}" if t["hits"] else ""))
    if t.get("errors"):
        lines.append(f"> ⚠️ 词表有问题：{'；'.join(t['errors'])}")
    if t.get("chained"):
        lines.append(f"> ⚠️ 词表成链（值又是键），重复跑会继续变：{', '.join(t['chained'])}")
    if s["count"]:
        lines.append(f"> 音量：{s['count']} 段 ≥85% 时长落在静音段里，这几句要回原帧核对")
    lines.append(f"> 体检：{h['segments']} 段 / {h['chars_per_sec']} 字/秒 / 最大空洞 {h['max_gap']}s"
                 + (f"；⚠️ {'；'.join(h['flags'])}" if h["flags"] else ""))
    lines.append("")
    for seg in segs:
        lines.append(f"- `{_stamp(float(seg['start']))}` → `{_stamp(float(seg['end']))}`　"
                     f"{seg.get('text', '')}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _stamp(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description="转写后处理（离线，不重跑 ASR）")
    ap.add_argument("slug")
    ap.add_argument("--write", action="store_true",
                    help="写回 research.json 的 asr/asr_meta 与 evidence/transcript.fixed.md")
    args = ap.parse_args()
    meta = run_one(args.slug, write=args.write)
    t, s, h = meta["terms"], meta["in_silence"], meta["health"]
    print(f"{args.slug}")
    print(f"  词表：{t['count']} 条 {t['files'] or '（无）'}；命中 {t['replaced']} 处 / {t['touched']} 段"
          + (f"；{t['hits']}" if t["hits"] else ""))
    for e in t["errors"]:
        print(f"    ⚠️ 词表错误：{e}")
    if t["chained"]:
        print(f"    ⚠️ 词表成链：{', '.join(t['chained'])}")
    print(f"  音量：{s['count']} 段 ≥85% 落在静音段里")
    for x in s["samples"][:3]:
        print(f"    {x['start']:.1f}-{x['end']:.1f} ({x['ratio']:.0%}) {x['text']}")
    print(f"  体检：{h['segments']} 段 / {h['chars_per_sec']} 字/秒 / 最大空洞 {h['max_gap']}s"
          f" / 汉字 {h['script']['cjk_ratio']:.0%}")
    for f in h["flags"]:
        print(f"    ⚠️ {f}")
    print("  已写回 research.json + transcript.fixed.md" if args.write else "  （未写回，加 --write）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
