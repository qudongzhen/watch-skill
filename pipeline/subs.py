#!/usr/bin/env python
"""字幕轨解析：SRT / WebVTT → `[{start, end, text}]`，与 whisper 的段同形。

为什么需要：B 站等站点的字幕是**独立轨道**，下载的视频文件里没有；它由作者自己写，
比 ASR 准且带准确时间码。拿不到再退回 whisper。

只做解析，不做联网（联网在 `run.py::try_subtitles`）。纯函数，便于单测。
"""
from __future__ import annotations

import html
import re
from pathlib import Path

# 00:00:01,000 / 00:01:02.500 / 1:02:03.400；SRT 用逗号，VTT 用句点，两种都吃
TIME_RE = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")
ARROW = "-->"
TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")          # HTML / ASS 残留（<i> <c.color> {\an8}）
LANG_PRIORITY = ("zh-hans", "zh-cn", "zh", "ai-zh", "en", "en-us")


def _to_seconds(h: str | None, m: str, s: str, frac: str) -> float:
    ms = int(frac.ljust(3, "0")[:3])
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + ms / 1000


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def cjk_ratio(text: str) -> float:
    """汉字在"字母"里的占比（CJK 双语字幕的判据用）。"""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if CJK_RE.match(c)) / len(letters)


def pick_bilingual(lines: list[str]) -> list[str]:
    """B 站双语 CC 一行一语言（原文 + 机翻）：只留**第一行**，也就是原文。

    判据必须严，否则会误伤"同一句被折成两行"的普通字幕：
      - 恰好两行，且两行都够长；
      - 一行的汉字占比 ≥0.5，另一行 ≤0.05（真的是一种中文一种外文）。
    中文视频的双语 CC 是中文在前，英文视频是英文在前——都取第一行，规则同一条。
    """
    body = [ln.strip() for ln in lines if ln.strip()]
    if len(body) != 2 or min(len(body[0]), len(body[1])) < 8:
        return body
    a, b = cjk_ratio(body[0]), cjk_ratio(body[1])
    if (a >= 0.5 and b <= 0.05) or (b >= 0.5 and a <= 0.05):
        return [body[0]]
    return body


def _clean(lines: list[str]) -> str:
    text = " ".join(pick_bilingual(lines))
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _cue_times(line: str) -> tuple[float, float] | None:
    parts = line.split(ARROW)
    if len(parts) != 2:
        return None
    left = TIME_RE.search(parts[0])
    # 右侧时间戳后面可能跟着 cue 设置（align:start position:0%），正则只取时间部分
    right = TIME_RE.search(parts[1])
    if not left or not right:
        return None
    a = _to_seconds(left.group(1), left.group(2), left.group(3), left.group(4))
    b = _to_seconds(right.group(1), right.group(2), right.group(3), right.group(4))
    return (a, b) if b > a else None


def parse_cues(text: str) -> list[dict]:
    """SRT 与 VTT 的公共部分：按空行切块，找 `-->` 行，后面几行是文本。

    两种格式的差异（序号行 / WEBVTT 头 / NOTE 块 / cue id）都靠"块里有没有箭头"化解。
    """
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    segs: list[dict] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].strip().upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        idx = next((i for i, ln in enumerate(lines) if ARROW in ln), None)
        if idx is None:
            continue
        times = _cue_times(lines[idx])
        if not times:
            continue
        body = _clean(lines[idx + 1:])
        if not body:                    # 空 cue：有时间没文本，丢掉
            continue
        segs.append({"start": round(times[0], 2), "end": round(times[1], 2), "text": body})
    # merge 先去完全重复的行；deroll 处理"逐句变长/变短"的滚动行（不满足条件会原样返回，幂等）
    return merge(deroll(merge(segs)))


def parse_srt(text: str) -> list[dict]:
    return parse_cues(text)


def parse_vtt(text: str) -> list[dict]:
    return parse_cues(text)


def parse(path: Path | str) -> list[dict]:
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    if p.suffix.lower() == ".vtt":
        return parse_vtt(text)
    return parse_srt(text)


def merge(segs: list[dict], gap: float = 0.5) -> list[dict]:
    """相邻且同文本的 cue 合并（VTT 的滚动字幕会重复同一行），并按开始时间排序。

    只合并"接得上 + 文本完全一样"的，避免把两句不同的话粘成一句。
    """
    out: list[dict] = []
    for s in sorted(segs, key=lambda x: (x["start"], x["end"])):
        last = out[-1] if out else None
        # 允许重叠：滚动字幕的重复 cue 常常是 start 相同、end 不同
        if (last and last["text"] == s["text"]
                and -gap <= s["start"] - last["end"] <= gap):
            last["end"] = max(last["end"], s["end"])
            continue
        out.append(dict(s))
    return out


# ---------------------------------------------------------------- 去滚动

ROLL_MIN_OVERLAP = 4     # 少于 4 个字符的重叠不当滚动（`… the code` / `code again` 会误剪）
ROLL_RATIO = 0.25        # 相邻 cue 里有 ≥4 字重叠的比例达到这个数才算"滚动字幕"


def rollover_len(prev: str, cur: str, min_overlap: int = ROLL_MIN_OVERLAP) -> int:
    """`prev` 的**最长后缀**同时是 `cur` 的前缀时，返回这个长度；否则 0。

    必须取最长：`A B` → `B` 这种，"短匹配"会把 B 留成重复内容。
    """
    if not prev or not cur:
        return 0
    top = min(len(prev), len(cur))
    for k in range(top, min_overlap - 1, -1):
        if prev[-k:] == cur[:k]:
            return k
    return 0


def strip_rollover(prev: str, cur: str, min_overlap: int = ROLL_MIN_OVERLAP) -> str:
    """剪掉 `cur` 与上一条尾部重复的前缀，返回新增文本（可能为空串）。"""
    k = rollover_len(prev, cur, min_overlap)
    if not k:
        return cur
    out = cur[k:]
    # 滚动行换行处常留一个空格或逗号；剪完只去掉句首的空白与标点，句中标点不动
    return out.lstrip(" ，,。.、；;：:·…—-")


def looks_rolling(segs: list[dict], min_overlap: int = ROLL_MIN_OVERLAP,
                  ratio: float = ROLL_RATIO) -> bool:
    """相邻 cue 里"有 ≥min_overlap 字重叠"的比例够高，才认定是滚动字幕。

    这是**安全闸门**：人工字幕偶尔也会出现句尾撞下句开头，不做判定就剪会静默吃字。
    宁可对真滚动字幕漏剪（退化成本来的重复），也不要对正常字幕误剪（丢内容）。
    """
    pairs = list(zip(segs, segs[1:]))
    if len(pairs) < 3:
        return False
    hits = sum(1 for a, b in pairs if rollover_len(a["text"], b["text"], min_overlap))
    return hits / len(pairs) >= ratio


def deroll(segs: list[dict], min_overlap: int = ROLL_MIN_OVERLAP,
           ratio: float = ROLL_RATIO) -> list[dict]:
    """把滚动字幕压成"每句只说一次"，并保留完整时间覆盖。

    整条都是重复的 cue 直接丢掉，时间并入上一条（`end` 取最大），所以覆盖区间不变。
    `looks_rolling` 不通过时**原样返回**——正常字幕逐字不变。
    """
    if not looks_rolling(segs, min_overlap, ratio):
        return [dict(s) for s in segs]
    out: list[dict] = []
    prev_text = ""
    for s in segs:
        text = strip_rollover(prev_text, s["text"], min_overlap)
        if not text.strip():
            if out:                     # 整条重复：把时间并给上一条
                out[-1]["end"] = max(out[-1]["end"], s["end"])
            prev_text = s["text"]
            continue
        out.append({"start": s["start"], "end": s["end"], "text": text.strip()})
        prev_text = s["text"]
    return out


def lang_of(path: Path) -> str:
    """`source.zh-Hans.srt` → `zh-hans`；`source.srt` → 空串。"""
    parts = path.name.split(".")
    return parts[-2].lower() if len(parts) >= 3 else ""


def pick_file(folder: Path) -> Path | None:
    """在一堆字幕文件里按语言优先级挑一个：中文优先，其次英文。

    同语言同时有 .srt 与 .vtt 时选 .srt（更好解析，也已经是 yt-dlp 转换过的）。
    """
    files = [p for p in folder.glob("*.srt")] + [p for p in folder.glob("*.vtt")]
    if not files:
        return None

    def rank(p: Path) -> tuple[int, int, str]:
        lang = lang_of(p)
        pri = LANG_PRIORITY.index(lang) if lang in LANG_PRIORITY else len(LANG_PRIORITY)
        suffix = 0 if p.suffix.lower() == ".srt" else 1
        return (pri, suffix, p.name)

    return sorted(files, key=rank)[0]
