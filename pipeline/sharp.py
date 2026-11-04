#!/usr/bin/env python
"""选一帧「最清晰」的：在同一时刻附近取 N 个候选，按 Laplacian 方差取最大。

为什么需要：`-ss t` 取到的只是"这一秒有一帧"，不是"这一秒最清楚的一帧"。
t 来自冻结帧或转写关键词，可能正好压在运动模糊上。

判据来自自动对焦的老办法（Pech-Pacheco 2000）：清晰度 = 高频能量 = Laplacian 方差。
`ffmpeg thumbnail=n=` 按直方图挑帧，而**模糊不改变直方图**，所以它会挑到糊的。

两条硬约束（都踩过）：
1. **不跨镜头**：候选时刻必须被调用方夹在 [lo, hi] 里（通常是切点/段边界）。差 0.25s
   跨过切点，挑出来的就是另一个镜头——那是"换内容"，不是"换清晰度"。
2. **不用 PIL 的 ImageFilter.Kernel 算 Laplacian**：8bit 输出会被截断（值域 ±1020，不是
   ±128），而且 Kernel 不滤波最外 1px，纯黑图会得到 186.9 的假分数。这里用精确 float 实现，
   只算内部像素，480×270 一帧约 40–60ms。

用法（一般由 run.py / still.py 调用）：
    from sharp import pick_to
    info = pick_to(media, t, dest, lo=400.0, hi=460.0)
    print(info["t"], info["score"])
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pydeps"))  # PIL 在 pydeps/，不是 site-packages

from PIL import Image, ImageStat  # noqa: E402

FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
TMP = ROOT / "work" / "tmp" / "pick"

PICK_PAD = 0.25        # 候选窗口半宽（秒）
PICK_COUNT = 3         # 候选帧数
SCORE_WIDTH = 480      # 评分前统一缩到这个宽度（只用于同一组内排序）
SCORE_RESAMPLE = "LANCZOS"
LUMA_TOL = 10.0        # 候选与原点帧的平均亮度差超过它 → 判为淡入/闪白，弃用
METHOD = "float-laplacian-4n-var"


def laplacian_var(img: Image.Image) -> float:
    """灰度图的 4 邻域 Laplacian 方差（精确 float，只算内部像素）。

    v(x,y) = 4c - 上 - 下 - 左 - 右；方差用 E[v²] − E[v]²，避免存整个数组。
    """
    gray = img.convert("L")
    if gray.width > SCORE_WIDTH:
        gray = gray.resize((SCORE_WIDTH, max(1, round(gray.height * SCORE_WIDTH / gray.width))),
                           Image.LANCZOS)
    w, h = gray.size
    if w < 3 or h < 3:      # 没有内部像素，无从谈清晰度
        return 0.0
    px = gray.tobytes()
    s = s2 = 0.0
    n = 0
    for y in range(1, h - 1):
        base = y * w
        for x in range(1, w - 1):
            i = base + x
            v = 4 * px[i] - px[i - 1] - px[i + 1] - px[i - w] - px[i + w]
            s += v
            s2 += v * v
            n += 1
    mean = s / n
    return s2 / n - mean * mean


def score_file(path: Path | str) -> float:
    with Image.open(path) as img:
        img.load()
        return laplacian_var(img)


def mean_luma(path: Path | str) -> float:
    with Image.open(path) as img:
        return float(ImageStat.Stat(img.convert("L")).mean[0])


def candidates(t: float, lo: float | None = None, hi: float | None = None,
               count: int = PICK_COUNT, pad: float = PICK_PAD) -> list[float]:
    """以 t 为中心的 count 个候选，均匀铺在 [t-pad, t+pad]，再夹进 [lo, hi]。

    顺序：**原时刻排第一**，其余按离原时刻由近到远——这样"挑不出来"时天然回落到原帧。
    夹取后去重（贴边/短素材时可能少于 count 个），至少一个。
    """
    t = float(t)
    pad = max(0.0, float(pad))
    count = max(1, int(count))
    raw: list[float] = []
    for i in range(count):
        off = 0.0 if count == 1 else -pad + 2 * pad * i / (count - 1)
        raw.append(t + off)
    raw.sort(key=lambda v: (abs(v - t), v))     # 原时刻优先，其次是最近的邻居
    out: list[float] = []
    for v in raw:
        if lo is not None:
            v = max(float(lo), v)
        if hi is not None:
            v = min(float(hi), v)
        v = round(max(0.0, v), 3)
        if v not in out:
            out.append(v)
    return out or [round(max(0.0, t), 3)]


def window_between(t: float, dur: float | None, cuts=(), fps: float = 25.0,
                   t0: float | None = None, t1: float | None = None) -> tuple[float, float]:
    """可以挪动的范围：**不许跨切点、不许跨段**。

    跨切点那一帧是另一个镜头——那是"换内容"，不是"换清晰度"。夹到边界后没地方挑，
    调用方会自己退回 exact（见 run.py）。
    """
    eps = 1.0 / max(float(fps or 25.0), 1.0)
    lo = 0.0
    hi = None if dur is None else max(0.0, float(dur) - eps)
    prev = max((c for c in cuts if c <= t), default=None) if cuts else None
    nxt = min((c for c in cuts if c > t), default=None) if cuts else None
    if prev is not None:
        lo = max(lo, float(prev) + eps)
    if nxt is not None:
        cut_hi = float(nxt) - eps
        hi = cut_hi if hi is None else min(hi, cut_hi)
    if t0 is not None:
        lo = max(lo, float(t0))
    if t1 is not None:
        hi = float(t1) if hi is None else min(hi, float(t1))
    out_hi = t if hi is None else hi
    return min(lo, t), max(out_hi, t)


def grab(media: Path | str, t: float, dest: Path, vf: str | None = None) -> None:
    """取一帧到 dest。输出落文件、不走管道（ffmpeg 的 stderr 同时充当错误信息）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    log = TMP / "ffmpeg.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error",
           "-ss", f"{max(0.0, float(t)):.3f}", "-i", str(media), "-frames:v", "1"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-y", str(dest)]
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
    if rc != 0 or not dest.exists() or dest.stat().st_size == 0:
        tail = log.read_text(encoding="utf-8", errors="replace").strip()[-400:]
        raise SystemExit(f"截帧失败 t={t}: {tail}")


def pick_to(media: Path | str, t: float, dest: Path, *, lo: float | None = None,
            hi: float | None = None, vf: str | None = None,
            mode: str = "sharp", count: int = PICK_COUNT, pad: float = PICK_PAD,
            luma_tol: float = LUMA_TOL) -> dict:
    """取帧并挑最清晰的一张，落在 dest。

    mode="exact"：只取 t 这一帧，与旧行为完全一致。
    mode="sharp"：取候选、评分、选最高分。原帧永远是候选之一，所以分数不会低于原帧。
    亮度守卫：与原点帧平均亮度差 > luma_tol 的候选视为淡入/闪白，直接排除（见评审 R1）。
    vf（裁切/放大）只作用于**最终落盘那一帧**，不参与评分——否则评分会被重采样锐化带偏。

    返回（写进 research.json 的 stills[].pick）：
        {"mode", "asked_t", "t", "score", "candidates": [{"t","score","luma","rejected"}]}
    """
    t = float(t)
    times = [round(t, 3)] if mode == "exact" else candidates(t, lo, hi, count, pad)
    tmpdir = TMP / dest.stem
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)
    tmpdir.mkdir(parents=True, exist_ok=True)

    scored: list[dict] = []
    try:
        for i, ct in enumerate(times):
            tmp = tmpdir / f"c{i:02d}_{ct:.3f}.png"
            grab(media, ct, tmp)
            scored.append({"t": ct, "score": round(score_file(tmp), 1),
                           "luma": round(mean_luma(tmp), 2), "path": tmp})

        base = next((c for c in scored if c["t"] == round(t, 3)), scored[0])
        # 亮度守卫用**候选组中位亮度**做基准，而不是"与原帧比"：闪白帧往往就是原帧本身，
        # 拿原帧当基准会把干净的邻居全判成异常（实测反了）。中位数对单个异常值是稳的。
        med = sorted(c["luma"] for c in scored)[len(scored) // 2]
        usable = []
        for c in scored:
            c["rejected"] = None
            if len(scored) > 2 and abs(c["luma"] - med) > luma_tol:
                c["rejected"] = "luma"
            else:
                usable.append(c)
        if not usable:                      # 理论上不会发生（中位数本身就来自某个候选）
            usable = [base]
        winner = max(usable, key=lambda c: c["score"])

        dest.parent.mkdir(parents=True, exist_ok=True)
        if vf:
            grab(media, winner["t"], dest, vf)          # 裁切/放大补一次，只针对赢家
        else:
            shutil.copyfile(winner["path"], dest)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "mode": mode,
        "asked_t": round(t, 3),
        "t": winner["t"],
        "score": winner["score"],
        "method": METHOD,
        "score_width": SCORE_WIDTH,
        "resample": SCORE_RESAMPLE,
        "pil": Image.__version__,
        "candidates": [{k: c[k] for k in ("t", "score", "luma", "rejected")} for c in scored],
    }
