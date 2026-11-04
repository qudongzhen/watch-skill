#!/usr/bin/env python
"""`pipeline/sharp.py` 的自检：评分的语义、退化规则、守卫、e2e 行为。

    py -3.14 pipeline/test_sharp.py

判据都是**可执行的断言**，不是"看起来对"：
- 手算 oracle：一张 5×5 阶跃图的 Laplacian 方差必须等于手算值（防止度量被"改良"）；
- 纯黑/纯白/纯 128 必须严格为 0（PIL 的 Kernel 实现会给出 186.9，这条就是为了挡住它）；
- 模糊必须单调，且清晰版至少 6 倍于 blur(2)；
- 亮度守卫必须把"闪白帧"排除掉。

不依赖测试框架（本仓库没有），失败即非零退出。全部用 work/tmp，不动 notes/。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "pydeps"))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

import sharp  # noqa: E402

FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK    {name} {detail}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILED.append(name)


def text_image(w: int = 480, h: int = 270) -> Image.Image:
    """有细笔画的图：清晰度差异要靠高频细节，纯色/大色块区分不出来。"""
    img = Image.new("L", (w, h), 30)
    d = ImageDraw.Draw(img)
    for i in range(0, w, 6):
        d.line([(i, 0), (i, h)], fill=220, width=1)
    for i in range(0, h, 9):
        d.line([(0, i), (w, i)], fill=180, width=1)
    return img


def test_oracle() -> None:
    """手算对照：5×5 图，左两列 0、右三列 200。

    内部像素 3×3，逐列 v = -200 / 200 / 0（上下邻也是 200，所以不是 600/400）：
      x=1: 4·0    - 0 - 200 - 200 - 200 = -200
      x=2: 4·200  - 0 - 200 - 200 - 200 =  200
      x=3: 4·200  - 200 - 200 - 200 - 200 = 0
    均值 0；方差 = E[v²] = (40000+40000+0)·3/9 = 26666.67
    """
    img = Image.new("L", (5, 5), 0)
    for y in range(5):
        for x in range(2, 5):
            img.putpixel((x, y), 200)
    got = sharp.laplacian_var(img)
    want = 26666.6667
    check("手算 oracle（阶跃图）", abs(got - want) < 0.01, f"({got:.2f} vs {want:.2f})")

    flat = Image.new("L", (7, 7), 128)
    check("纯 128 = 0", sharp.laplacian_var(flat) == 0.0)
    check("纯黑 = 0（PIL Kernel 会给 186.9）", sharp.laplacian_var(Image.new("L", (480, 270), 0)) == 0.0)
    check("纯白 = 0", sharp.laplacian_var(Image.new("L", (480, 270), 255)) == 0.0)
    check("1×1 不崩且为 0", sharp.laplacian_var(Image.new("L", (1, 1), 128)) == 0.0)
    check("单像素宽不崩", sharp.laplacian_var(Image.new("L", (1, 100), 128)) == 0.0)


def test_monotone() -> None:
    base = text_image()
    scores = [sharp.laplacian_var(base.filter(ImageFilter.GaussianBlur(r)))
              for r in (0, 0.5, 1, 2, 3)]
    check("模糊单调下降", all(a > b for a, b in zip(scores, scores[1:])),
          " > ".join(f"{s:.0f}" for s in scores))
    check("清晰版 ≥ 6× blur(2)", scores[0] > scores[3] * 6, f"({scores[0] / scores[3]:.1f}×)")
    check("blur(2) 与 blur(3) 已接近 0", scores[4] < 50, f"({scores[4]:.1f})")

    # 压暗 10%：方差按对比度平方下降，这是"亮度带偏"的根源，记下来别当成 bug
    dark = base.point(lambda p: int(p * 0.9))
    check("压暗会掉分（已知偏差，靠 luma 守卫兜）", sharp.laplacian_var(dark) < scores[0] * 0.95)


def test_candidates() -> None:
    c = sharp.candidates(10.0, 0.0, 100.0, 3, 0.25)
    check("3 候选 = 原点在首位、对称 ±pad", c == [10.0, 9.75, 10.25], str(c))
    check("候选都在界内", all(9.7 <= v <= 10.3 for v in c))
    c5 = sharp.candidates(10.0, 0.0, 100.0, 5, 0.5)
    check("5 候选半宽=pad", c5[0] == 10.0 and min(c5) == 9.5 and max(c5) == 10.5, str(c5))
    edge = sharp.candidates(0.05, 0.0, 100.0, 3, 0.25)
    check("贴开头无负值、无重复", all(v >= 0 for v in edge) and len(set(edge)) == len(edge),
          str(edge))
    tail = sharp.candidates(99.9, 0.0, 100.0, 3, 0.25)
    check("贴结尾不越界", max(tail) <= 100.0, str(tail))
    win = sharp.candidates(10.0, 9.9, 10.1, 3, 0.5)
    check("窗口比 pad 窄时被夹住", all(9.9 <= v <= 10.1 for v in win), str(win))
    check("count=1 只给原时刻", sharp.candidates(3.0, 0.0, 10.0, 1, 0.25) == [3.0])
    check("无重复时刻", len(set(sharp.candidates(5.0, 0.0, 10.0, 5, 0.5))) == 5)


def build_clip(path: Path, vf: str, secs: float = 1.6) -> bool:
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", f"testsrc2=size=320x180:rate=25:duration={secs}",
           "-vf", vf, "-pix_fmt", "yuv420p", "-y", str(path)]
    log = path.parent / "ffmpeg.log"
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode == 0


def test_e2e() -> None:
    """两个场景：前半段糊（挑到清晰段）、中间闪白（亮度守卫必须拦掉）。"""
    tmp = ROOT / "work" / "tmp" / "test_sharp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        blurred = tmp / "blur.mp4"
        if not build_clip(blurred, "boxblur=8:enable='lt(t,0.7)'"):
            check("e2e 造片（模糊）", False)
            return
        info = sharp.pick_to(blurred, 0.6, tmp / "picked.png", lo=0.0, hi=1.5,
                             count=3, pad=0.25)
        asked = next(c for c in info["candidates"] if c["t"] == 0.6)
        check("e2e 挑到清晰段", info["t"] == 0.85, f't={info["t"]}')
        check("e2e 比原帧明显更清晰", info["score"] > asked["score"] * 2,
              f'({info["score"]:.0f} vs {asked["score"]:.0f})')
        check("e2e 落盘", (tmp / "picked.png").stat().st_size > 1024)

        flash = tmp / "flash.mp4"
        if not build_clip(flash, "eq=brightness=0.45:enable='between(t,0.5,0.7)'"):
            check("e2e 造片（闪白）", False)
            return
        finfo = sharp.pick_to(flash, 0.6, tmp / "flash.png", lo=0.0, hi=1.5,
                              count=3, pad=0.25)
        rejected = [c for c in finfo["candidates"] if c["rejected"]]
        check("e2e 闪白帧被亮度守卫拦下", bool(rejected), str(rejected))
        check("e2e 闪白帧没被选中", finfo["t"] != 0.6 or not rejected,
              f't={finfo["t"]}')

        exact = sharp.pick_to(blurred, 0.6, tmp / "exact.png", lo=0.0, hi=1.5, mode="exact")
        check("exact 只取一帧", len(exact["candidates"]) == 1 and exact["t"] == 0.6)
        check("exact 不记 mode 以外的东西", exact["asked_t"] == exact["t"] == 0.6)

        win = sharp.pick_to(blurred, 0.6, tmp / "win.png", lo=0.5, hi=0.62, count=3, pad=0.25)
        check("窗口夹紧后不越界", all(0.5 <= c["t"] <= 0.62 for c in win["candidates"]),
              str([c["t"] for c in win["candidates"]]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("pipeline/sharp.py 自检\n")
    test_oracle()
    test_monotone()
    test_candidates()
    test_e2e()
    print(f"\n{len(FAILED)} 项失败" if FAILED else "\n全绿")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
