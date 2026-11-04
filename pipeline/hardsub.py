#!/usr/bin/env python
"""硬字幕：定位字幕带 + 用画面原文校正 ASR 错字。

为什么要两半分开：
- **定位**很便宜（7 帧，约 1 秒），纯 PIL，离线可验——它让写作者一条命令就能裁到字幕；
- **OCR 校正**要 rapidocr 引擎，装不上就不能跑：这里**明确报缺**，不静默返回空串
  （否则会让人以为"这段没字幕"，比报错更糟）。

定位的原理：单帧的"文字密度"在实拍画面里分不出来（背景比字幕还花）。但字幕带是**唯一在每一帧
都有笔画、且 y 固定**的区域。所以对抽样帧逐行算横向梯度，取 **per-row 的 min**（每帧都忙才算数），
再在中位数之上找 3%–25% 画面高的连续区间。

用法：
    py -3.14 pipeline/hardsub.py <id> --detect            # 只看有没有字幕带、在哪
    py -3.14 pipeline/hardsub.py <id> --apply             # 需要 rapidocr；先报告不写
    py -3.14 pipeline/hardsub.py <id> --apply --write     # 写 research.asr_meta.hardsub
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

import store  # 同目录：JSON 原子写 / 容错读

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pydeps"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sharp  # noqa: E402

NOTES = ROOT / "notes"
GRAB_W = 480          # 算密度前统一宽度
BAND_MIN = 0.015      # 字幕带最矮 1.5% 画面高（竖屏 852px 上一行字约 2–3%）
BAND_MAX = 0.25       # 最高 25%
BAND_THRESH = 2.0     # 密度阈值：行均值密度的倍数
DEFAULT_FRAMES = 7
CLUSTER_TOL = 0.03    # 多帧候选聚类的 y 容差（× 画面高）
MIN_TEMPORAL_DIFF = 1.2   # 带内相邻帧的平均灰度差；低于它判为"静态 UI 栏"而非字幕
CJK_RANGE = r"\u3400-\u4dbf\u4e00-\u9fff"
OCR_PS1 = Path(__file__).resolve().parent / "ocr_win.ps1"


class OcrUnavailable(RuntimeError):
    """引擎没装：要能明确区分"没字幕"和"读不了"。"""


# ---------------------------------------------------------------- 定位

def row_density(path: Path) -> list[float]:
    """逐行横向梯度能量（0–255 尺度），已按宽度归一化。"""
    from PIL import Image
    img = Image.open(path).convert("L")
    if img.width > GRAB_W:
        img = img.resize((GRAB_W, max(8, round(img.height * GRAB_W / img.width))),
                         Image.LANCZOS)
    w, h = img.size
    px = img.tobytes()
    out = []
    for y in range(h):
        base = y * w
        acc = 0
        for x in range(w - 1):
            acc += abs(px[base + x + 1] - px[base + x])
        out.append(acc / max(1, w - 1))
    return out


def band_from_densities(dens: list[list[float]], min_ratio: float = BAND_MIN,
                        max_ratio: float = BAND_MAX,
                        thresh: float = BAND_THRESH) -> dict | None:
    """多帧的行密度 → 字幕带（每帧都得忙的那条）。"""
    if not dens or not dens[0]:
        return None
    h = len(dens[0])
    mins = [min(col) for col in zip(*dens)]
    # 参考值不能用中位数：字幕带往往只占 5–10% 的行，中位数会是 0（纯背景）
    ref = sum(mins) / len(mins)
    if ref <= 0.5:
        return None
    mask = [v > thresh * ref for v in mins]

    runs, start = [], None
    for y, hot in enumerate(mask + [False]):
        if hot and start is None:
            start = y
        elif not hot and start is not None:
            runs.append((start, y))
            start = None
    lo, hi = min_ratio * h, max_ratio * h
    runs = [(a, b) for a, b in runs if lo <= b - a <= hi]
    if not runs:
        return None
    a, b = max(runs, key=lambda r: sum(mins[r[0]:r[1]]) / (r[1] - r[0]))
    return {"y0": a, "y1": b, "height": b - a,
            "score": round(sum(mins[a:b]) / (b - a), 1),
            "ratio": round((b - a) / h, 3)}


def sample_times(dur: float, n: int = DEFAULT_FRAMES) -> list[float]:
    """均匀铺开，但避开首尾 5%（片头片尾常有台标/黑场）。"""
    if dur <= 0:
        return []
    pad = min(2.0, dur * 0.05)
    lo, hi = pad, max(pad + 0.1, dur - pad)
    if n <= 1:
        return [round((lo + hi) / 2, 2)]
    return [round(lo + (hi - lo) * i / (n - 1), 2) for i in range(n)]


def band_temporal_diff(paths: list[Path], y0: int, y1: int) -> float:
    """字幕带在相邻帧之间的平均灰度差。

    为什么要这一步：编辑器状态栏、台标、进度条**也**满足"每帧都忙 + 位置固定"，
    单看密度分不出来。真正的分水岭是**内容在变**——字幕每句都不一样，UI 栏逐像素相同。
    实测：Godot 教程底部状态栏 0.2，真正的中文字幕 4–12（0–255 尺度）。
    """
    from PIL import Image, ImageChops, ImageStat
    prev = None
    diffs: list[float] = []
    for p in paths:
        img = Image.open(p).convert("L")
        if img.width > GRAB_W:
            img = img.resize((GRAB_W, max(8, round(img.height * GRAB_W / img.width))),
                             Image.LANCZOS)
        band = img.crop((0, max(0, y0), img.width, min(img.height, y1)))
        if prev is not None:
            diffs.append(ImageStat.Stat(ImageChops.difference(band, prev)).mean[0])
        prev = band
    return round(sum(diffs) / len(diffs), 2) if diffs else 0.0


def detect(media: Path, dur: float, frames: int = DEFAULT_FRAMES) -> dict:
    """抽样 N 帧 → 每帧各自找带 → 按 y 聚类 → **再用"内容在变"排除静态 UI 栏**。

    为什么不是"多帧取逐行 min 再找带"：字幕不是每帧都有（安静段、空镜），
    min 会把整段压没。逐帧判 + 跨帧聚类更稳。
    """
    tmp = ROOT / "work" / "tmp" / "hardsub"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    cands, shots, rows = [], [], 0
    try:
        for i, t in enumerate(sample_times(dur, frames)):
            p = tmp / f"f{i:02d}_{t:.1f}.png"
            sharp.grab(media, t, p)
            shots.append(p)
            dens = row_density(p)
            rows = len(dens)
            b = band_from_densities([dens])
            if b:
                cands.append({**b, "t": t})
        if not cands or not rows:
            return {"found": False, "samples": frames, "hits": 0}

        tol = rows * CLUSTER_TOL
        clusters: list[list[dict]] = []
        for c in sorted(cands, key=lambda x: x["y0"]):
            if clusters and abs(c["y0"] - clusters[-1][0]["y0"]) <= tol:
                clusters[-1].append(c)
            else:
                clusters.append([c])
        best = max(clusters, key=len)
        y0 = int(statistics.median(c["y0"] for c in best))
        y1 = int(statistics.median(c["y1"] for c in best))
        diff = band_temporal_diff(shots, y0, y1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if diff < MIN_TEMPORAL_DIFF:
        return {"found": False, "samples": frames, "hits": len(best),
                "reason": "static", "temporal_diff": diff,
                "y0": y0, "y1": y1}
    return {"found": True, "y0": y0, "y1": y1, "height": y1 - y0,
            "rows": rows, "samples": frames, "hits": len(best),
            "temporal_diff": diff,
            "stable": len(best) >= max(2, frames // 2)}


# ---------------------------------------------------------------- 文本比对

def _meaningful_map(text: str) -> tuple[str, list[int]]:
    """实义字符（去空白与标点）以及每个实义字符在原串里的下标。"""
    import unicodedata
    kept: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text or ""):
        if ch.isspace() or unicodedata.category(ch)[0] in "PSZ":
            continue
        kept.append(ch)
        idx.append(i)
    return "".join(kept), idx


def meaningful(text: str) -> str:
    """去掉空白与标点后的实义字符（中英都适用）。"""
    return _meaningful_map(text)[0]


def lcs_pairs(a: str, b: str) -> list[tuple[int, int]]:
    """最长公共子序列的配对下标（a 的第 i 字 ↔ b 的第 j 字）。文本都很短，
    直接用整张表换回溯能力；LCS 天然保序，所以乱序不会被算作覆盖。"""
    n, m = len(a), len(b)
    if not n or not m:
        return []
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        ai, row, prev = a[i - 1], dp[i], dp[i - 1]
        for j in range(1, m + 1):
            row[j] = prev[j - 1] + 1 if ai == b[j - 1] else max(prev[j], row[j - 1])
    pairs: list[tuple[int, int]] = []
    i, j = n, m
    while i and j:
        if a[i - 1] == b[j - 1]:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def needs_fix(ocr_text: str, asr_text: str) -> bool:
    """OCR 与 ASR 去过标点后一字不差 → 没有可改的东西，不该进候选列表。
    实测 12 段里有一半候选是"原/改"完全相同的噪音。"""
    return meaningful(ocr_text) != meaningful(asr_text)


def judge(ocr_text: str, asr_text: str, min_cover: float = 0.6,
          min_ratio: float = 0.5, max_ratio: float = 2.0,
          trim: bool = True) -> dict:
    """OCR 文本能不能替换 ASR 文本：覆盖 ≥min_cover 且同序（LCS 天然保序）。

    字幕带裁宽了会把相邻 UI 文字一并 OCR 进来（"LOGI 的 CREDITS 乎所有内容…"）。
    trim=True 时按配对下标把首尾多出来的部分剪掉，只留与 ASR 对齐的那一段；
    raw 里保留原样，方便人核对剪了什么。
    """
    o, oidx = _meaningful_map(ocr_text)
    a, _aidx = _meaningful_map(asr_text)
    if not o or not a:
        return {"accept": False, "cover": 0.0, "ratio": 0.0, "reason": "空文本"}
    pairs = lcs_pairs(o, a)
    core = o[pairs[0][0]:pairs[-1][0] + 1] if (trim and pairs) else o
    cover = len(pairs) / len(a)
    # 长度比按"实际会写进去的那段"算：裁宽进来的相邻 UI 文字由 trim 剪掉，
    # 不该因此判成"OCR 抓了一整段"。剪完仍然过长 = 中间夹了垃圾，照样拒。
    ratio = len(core) / len(a)
    if cover < min_cover:
        return {"accept": False, "cover": round(cover, 3), "ratio": round(ratio, 3),
                "reason": "覆盖不足"}
    if not min_ratio <= ratio <= max_ratio:
        return {"accept": False, "cover": round(cover, 3), "ratio": round(ratio, 3),
                "reason": "长度比异常"}
    raw = ocr_text.strip()
    text = raw
    if trim and pairs:
        text = ocr_text[oidx[pairs[0][0]]:oidx[pairs[-1][0]] + 1].strip()
        text = text.strip(" ，。、；：,.;:") or raw
    return {"accept": True, "cover": round(cover, 3), "ratio": round(ratio, 3),
            "text": text, "raw": raw}


# ---------------------------------------------------------------- OCR（需引擎）

_ENGINE = None


def engine():
    """优先 rapidocr（若有），否则用 Windows 自带 OCR；两个都没有才报缺。"""
    global _ENGINE
    if _ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            _ENGINE = ("rapidocr", RapidOCR())
        except Exception:  # noqa: BLE001
            if OCR_PS1.exists() and sys.platform == "win32":
                _ENGINE = ("windows", OCR_PS1)
            else:
                raise OcrUnavailable(
                    "没有 OCR 引擎：装 rapidocr（py -3.14 scripts/install_pydeps.py "
                    "rapidocr-onnxruntime），或使用 Windows 自带的 "
                    f"{OCR_PS1.name}（本机{'有' if OCR_PS1.exists() else '缺'}）")
    return _ENGINE


def normalize_ocr(text: str) -> str:
    """Windows OCR 把中文按字加空格（"需 要 注 意"），这里把 CJK 之间以及
    CJK 与中文标点之间的空格并回去；英文单词之间的空格保留。"""
    t = re.sub(rf"(?<=[{CJK_RANGE}])\s+(?=[{CJK_RANGE}])", "", text or "")
    t = re.sub(rf"\s+(?=[，。！？；：、）》」』])", "", t)
    t = re.sub(rf"(?<=[（《「『，。！？；：、])\s+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def ocr_band(img_path: Path, lang: str = "zh-Hans-CN") -> str:
    """整张图交给引擎（调用方已裁成字幕带）。

    引擎自己的失败（图片读不到、模型跑挂）也包成 OcrUnavailable：调用方只需要面对
    一种"OCR 没成"的异常，不必分别处理 rapidocr 的 LoadImageError / PowerShell 的退出码。
    """
    kind, eng = engine()
    try:
        if kind == "rapidocr":
            result, _ = eng(str(img_path))
            return normalize_ocr(" ".join(str(item[1]) for item in result or []))
        exe = "powershell" if sys.platform == "win32" else "pwsh"
        proc = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
                               "-File", str(eng),
                               "-Path", str(img_path), "-Lang", lang],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        if proc.returncode != 0:
            raise OcrUnavailable(f"Windows OCR 失败（exit {proc.returncode}）："
                                 f"{(proc.stderr or '').strip()[:200]}")
        return normalize_ocr(proc.stdout)
    except OcrUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OcrUnavailable(f"OCR 失败（{kind}）：{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------- CLI

def load_note(slug: str) -> tuple[Path, Path, float, dict]:
    note = NOTES / slug
    if not note.exists():
        raise SystemExit(f"没有这个项目：{note}")
    media = next((p for p in sorted(note.glob("source.*"))
                  if p.suffix.lower() not in {".json", ".part"}), None)
    if not media:
        raise SystemExit(f"{note} 里没有素材")
    rp = note / "research.json"
    research = store.load_json(rp, default={})
    dur = float((research.get("timeline") or {}).get("duration") or 0)
    if not dur:
        raise SystemExit("research.json 里没有时长（先跑 run.py）")
    return note, media, dur, research


def band_pixels(info: dict, research: dict) -> tuple[int, int, int, int]:
    """把检测得到的行号换算回原分辨率像素。密度是在缩到 480 宽的图上算的，
    行号与原始 y 不是一回事——直接拿行号裁全分辨率图会裁错位置。"""
    tl = research.get("timeline") or {}
    h, w = int(tl.get("height") or 0), int(tl.get("width") or 0)
    rows = int(info.get("rows") or 0) or 1
    s = (h / rows) if h else 1.0
    y0 = max(0, min(h or 10 ** 9, round(info["y0"] * s)))
    y1 = max(y0 + 1, min(h or 10 ** 9, round(info["y1"] * s)))
    return y0, y1, w, h


def cmd_detect(args) -> int:
    note, media, dur, research = load_note(args.slug)
    info = detect(media, dur, args.frames)
    print(f"{args.slug}　抽样 {info['samples']} 帧")
    if not info["found"]:
        why = "（带内像素几乎不动，更像静态 UI 栏）" if info.get("reason") == "static" else ""
        print(f"  没找到稳定的字幕带{why}")
        return 0
    y0, y1, w, h = band_pixels(info, research)
    print(f"  候选带 y≈{y0}–{y1}（{info['hits']}/{info['samples']} 帧命中，"
          f"帧间差异 {info.get('temporal_diff')}）")
    print("  ⚠️ 实验性：密度分不出「字幕」和「永远存在的 UI 栏」（实测 BV1EAKH6GE7C 的"
          "编辑器状态栏会被选中）。裁出来看一眼再决定。")
    if w and h:
        print(f"  裁出来看：py -3.14 pipeline/still.py {args.slug} "
              f"--at <秒> --crop 0,{y0},{w},{max(1, y1 - y0)} --zoom 2")
    return 0


def cmd_apply(args) -> int:
    note, media, dur, research = load_note(args.slug)
    try:
        kind, _ = engine()
    except OcrUnavailable as exc:
        print(f"  {exc}", file=sys.stderr)
        print("  （定位仍可用：加 --detect）", file=sys.stderr)
        return 2
    segs = research.get("asr") or []
    if not segs:
        print("  research.json 里没有转写（先跑 run.py）")
        return 1

    if (args.y0 is None) != (args.y1 is None):
        print("  --y0/--y1 要一起给", file=sys.stderr)
        return 2
    if args.y0 is not None and args.y1 is not None:
        y0, y1 = args.y0, args.y1
        if not (0 <= y0 < y1):
            print("  --y0/--y1 不合法", file=sys.stderr)
            return 2
        print(f"  使用指定字幕带 y={y0}–{y1}（原分辨率像素）")
    else:
        info = detect(media, dur, args.frames)
        if not info["found"]:
            print("  没找到字幕带，无从校正；可以用 --y0/--y1 手动指定")
            return 1
        y0, y1, _, _ = band_pixels(info, research)
        print(f"  自动定位字幕带 y={y0}–{y1}"
              "（实验性，用 --y0/--y1 可覆盖）")

    print(f"  OCR 引擎：{'rapidocr' if kind == 'rapidocr' else 'Windows 自带（WinRT）'}")
    tmp = ROOT / "work" / "tmp" / "hardsub"
    tmp.mkdir(parents=True, exist_ok=True)
    rows = []
    same = 0
    try:
        for i, seg in enumerate(segs[: args.limit]):
            t = (float(seg["start"]) + float(seg["end"])) / 2
            full = tmp / f"s{i:04d}.png"
            sharp.grab(media, t, full)
            crop = tmp / f"c{i:04d}.png"
            crop.parent.mkdir(parents=True, exist_ok=True)
            _crop_band(full, crop, y0, y1)
            text = ocr_band(crop)
            verdict = judge(text, seg.get("text") or "", args.min_cover)
            if verdict["accept"]:
                if not needs_fix(verdict["text"], seg.get("text") or ""):
                    same += 1
                    continue
                rows.append({"start": seg["start"], "end": seg["end"],
                             "asr": seg.get("text"), "ocr": verdict["text"],
                             "ocr_raw": verdict["raw"], "cover": verdict["cover"],
                             "ratio": verdict["ratio"]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"{args.slug}　校正候选 {len(rows)} / 检查 {min(len(segs), args.limit)} 段"
          f"（覆盖阈值 {args.min_cover:.0%}）")
    if same:
        print(f"  另有 {same} 段 OCR 与 ASR 完全一致，没有可改的，已略过")
    for r in rows[:10]:
        print(f"  {r['start']:.1f}s  cover={r['cover']:.0%} ratio={r['ratio']:.2f}")
        print(f"    原：{r['asr']}")
        print(f"    改：{r['ocr']}")
        if r["ocr_raw"] != r["ocr"]:
            print(f"    带外文字已剪掉：{r['ocr_raw']}")
    if not args.write:
        print("  （未写回，加 --write）")
        return 0
    research.setdefault("asr_meta", {})["hardsub"] = {
        "band": [y0, y1], "checked": min(len(segs), args.limit),
        "replaced": len(rows), "samples": rows[:20]}
    store.save_json(note / "research.json", research)
    print(f"  已写 research.asr_meta.hardsub（{len(rows)} 条候选）")
    return 0


def _crop_band(src: Path, dest: Path, y0: int, y1: int) -> None:
    from PIL import Image
    img = Image.open(src)
    img.crop((0, max(0, y0), img.width, min(img.height, y1))).save(dest)


def main() -> int:
    ap = argparse.ArgumentParser(description="硬字幕定位 / 校正")
    ap.add_argument("slug")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--detect", action="store_true", help="只看字幕带位置（无需 OCR 引擎）")
    g.add_argument("--apply", action="store_true", help="用 OCR 校正转写（rapidocr 或 Windows 自带 OCR）")
    ap.add_argument("--frames", type=int, default=DEFAULT_FRAMES, help="抽样帧数")
    ap.add_argument("--y0", type=int, default=None, help="手动指定字幕带上沿（原分辨率像素）")
    ap.add_argument("--y1", type=int, default=None, help="手动指定字幕带下沿（原分辨率像素）")
    ap.add_argument("--limit", type=int, default=30, help="--apply 最多检查多少段")
    ap.add_argument("--min-cover", type=float, default=0.6, help="覆盖阈值，默认 0.6")
    ap.add_argument("--write", action="store_true", help="写回 research.asr_meta.hardsub")
    args = ap.parse_args()
    if args.apply:
        return cmd_apply(args)
    return cmd_detect(args)


if __name__ == "__main__":
    sys.exit(main())
