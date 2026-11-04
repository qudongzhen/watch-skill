#!/usr/bin/env python
"""从素材里取一张「可读」的静帧：整帧，或按区域裁切放大。

为什么单独一个脚本：图版里的字**不能采信**——分辨率不够时视觉会渲染一个合理内容，
而不是报告"读不到"。写笔记要用到画面上的原文时，必须走这里放大看。

默认 `--pick exact`：你点名的是某一秒，就要那一秒（菜单/高亮/光标状态都在那一秒）。
要"这一秒附近最清晰的一张"再加 `--pick sharp`（不会跨切点，会打印挪了多少）。

用法：
    py -3.14 pipeline/still.py <slug> --at 123.4
    py -3.14 pipeline/still.py <slug> --at 123.4 --crop 120,700,1040,180 --zoom 2
    py -3.14 pipeline/still.py <slug> --at 60,300 --crop 0,0,1280,720     # 多个时刻
    py -3.14 pipeline/still.py <slug> --at 424 --crop 0,0,960,540 --pick sharp

产物：notes/<slug>/assets/stills/<name>.png，并打印绝对路径。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pydeps"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sharp  # noqa: E402
import store  # noqa: E402  （JSON 原子写 / 容错读）

NOTES = ROOT / "notes"


def load_context(note: Path) -> tuple[float | None, list[float], float]:
    """从 research.json 拿时长、切点、帧率——用来夹住"可以挪动多少"。没有就退化为未知。"""
    rp = note / "research.json"
    if not rp.exists():
        return None, [], 25.0
    try:
        data = store.load_json(rp, default={})
    except (OSError, ValueError):
        return None, [], 25.0
    tl = data.get("timeline") or {}
    return (float(tl.get("duration") or 0) or None,
            [float(c) for c in tl.get("cuts") or []],
            float(tl.get("fps") or 25.0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--at", required=True, help="时刻秒，可逗号分隔多个")
    ap.add_argument("--crop", default=None, help="x,y,w,h；不填则整帧")
    ap.add_argument("--zoom", type=float, default=1.0, help="放大倍数（裁切时常用 2~3）")
    ap.add_argument("--name", default=None, help="输出文件名前缀")
    ap.add_argument("--pick", choices=["exact", "sharp"], default="exact",
                    help="exact=就取这一秒（默认）；sharp=在 ±window 内挑最清晰的一张")
    ap.add_argument("--pick-window", type=float, default=sharp.PICK_PAD,
                    help=f"sharp 模式的候选半窗秒数（默认 {sharp.PICK_PAD}，不会跨切点）")
    ap.add_argument("--pick-count", type=int, default=sharp.PICK_COUNT,
                    help=f"sharp 模式的候选帧数（默认 {sharp.PICK_COUNT}）")
    args = ap.parse_args()

    note = NOTES / args.slug
    if not note.exists():
        raise SystemExit(f"没有这个项目：{note}")
    media = next(p for p in sorted(note.glob("source.*"))
                 if p.suffix.lower() not in {".json", ".part"})
    stills = note / "assets" / "stills"
    stills.mkdir(parents=True, exist_ok=True)

    dur, cuts, fps = load_context(note)

    vf = []
    if args.crop:
        x, y, w, h = (int(v) for v in args.crop.split(","))
        vf.append(f"crop={w}:{h}:{x}:{y}")
    if args.zoom and args.zoom != 1.0:
        vf.append(f"scale=iw*{args.zoom}:ih*{args.zoom}:flags=lanczos")
    filter_arg = ",".join(vf) or None

    times = [float(t) for t in args.at.split(",")]
    for t in times:
        stem = args.name or f"t{int(t):05d}"
        if len(times) > 1:
            stem = f"{stem}_{int(t):05d}"
        out = stills / f"{stem}.png"
        lo, hi = sharp.window_between(t, dur, cuts, fps) if args.pick == "sharp" else (t, t)
        mode = args.pick
        if mode == "sharp" and min(t - lo, hi - t) < 0.05:
            mode = "exact"       # 贴着切点/片头片尾，没地方挑
        info = sharp.pick_to(media, t, out, lo=lo, hi=hi, vf=filter_arg, mode=mode,
                             count=args.pick_count, pad=args.pick_window)
        if mode == "sharp":
            k = next((j for j, c in enumerate(info["candidates"]) if c["t"] == info["t"]), 0) + 1
            print(f"{out}  [{len(info['candidates'])} 帧取第 {k} 帧，"
                  f"{info['t'] - info['asked_t']:+.2f}s]")
        else:
            print(f"{out}  [t={info['t']:.2f}s]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
