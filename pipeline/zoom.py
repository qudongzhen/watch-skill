#!/usr/bin/env python
"""按需密集图版：把任意一段"看不清"的时间扩成一张高频图版。

为什么需要：预生成的图版是"每章 9 帧"或"每窗 16 帧"，段内间隔十几秒到几十秒，
一个 5 秒的操作步骤会整个漏掉。写笔记时发现某一步不清楚，用这个要一张 1–2 秒一帧的图。

    py -3.14 pipeline/zoom.py BV1uf8e6dEyA --at 62 --window 8          # 62s 前后各 4s，25 帧
    py -3.14 pipeline/zoom.py BV1EAKH6GE7C --t0 300 --t1 330           # 30s × 25 帧 = 1.2s/帧
    py -3.14 pipeline/zoom.py <id> --t0 300 --t1 330 --frames 25 --cols 5

产物：notes/<id>/assets/grids/zoom_<t0>_<t1>/grid.png + manifest.json（与既有图版同契约）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import store

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "notes"
VW = ROOT / "vendor" / "video-watch" / "vw.py"
PDEPS = ROOT / "pydeps"
COMPAT = ROOT / "pipeline" / "compat"


def env() -> dict:
    import os
    e = os.environ.copy()
    e["PYTHONPATH"] = os.pathsep.join(str(p) for p in (COMPAT, PDEPS))
    e["PYTHONIOENCODING"] = "utf-8"
    e["PATH"] = str(ROOT / "tools" / "ffmpeg" / "bin") + os.pathsep + e.get("PATH", "")
    e["TMP"] = e["TEMP"] = str(ROOT / "work" / "tmp")
    return e


def context(note: Path) -> tuple[Path, float, float]:
    media = next((p for p in sorted(note.glob("source.*"))
                  if p.suffix.lower() not in {".json", ".part"}), None)
    if not media:
        raise SystemExit(f"{note} 里没有素材文件（source.*）")
    rp = note / "research.json"
    dur = fps = None
    if rp.exists():
        tl = store.load_json(rp, default={}).get("timeline") or {}
        dur = float(tl.get("duration") or 0) or None
        fps = float(tl.get("fps") or 0) or None
    if not dur:
        raise SystemExit(f"{note} 没有 research.json（先跑 run.py）")
    return media, dur, fps or 25.0


def main() -> int:
    ap = argparse.ArgumentParser(description="按需密集图版")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--at", type=float, help="中心时刻（秒）")
    g.add_argument("--t0", type=float, help="区间起点（秒）")
    ap.add_argument("--t1", type=float, help="区间终点（秒）；配 --t0 用")
    ap.add_argument("--window", type=float, default=8.0,
                    help="--at 模式的总时长（秒），默认 8")
    ap.add_argument("--frames", type=int, default=25, help="帧数，默认 25")
    ap.add_argument("--cols", type=int, default=5, help="列数，默认 5")
    ap.add_argument("slug")
    args = ap.parse_args()

    note = NOTES / args.slug
    if not note.exists():
        raise SystemExit(f"没有这个项目：{note}")
    media, dur, fps = context(note)

    if args.at is not None:
        half = max(0.5, args.window / 2)
        t0, t1 = args.at - half, args.at + half
    else:
        if args.t1 is None:
            raise SystemExit("用 --t0 时必须给 --t1")
        t0, t1 = args.t0, args.t1

    if t0 < 0:
        t0 = 0.0
    if t0 >= dur:
        raise SystemExit(f"起点 {t0:.2f}s 超出素材时长 {dur:.2f}s")
    if t1 <= t0:
        raise SystemExit(f"终点 {t1:.2f}s 必须大于起点 {t0:.2f}s")
    if t1 > dur:
        print(f"[zoom] 终点 {t1:.2f}s 超出素材，收到 {dur:.2f}s", file=sys.stderr)
        t1 = dur
    if t1 - t0 < 0.3:
        raise SystemExit(f"区间太短：{t0:.2f}–{t1:.2f}s（要 ≥0.3s）")

    total_frames = int((t1 - t0) * fps) + 1
    frames = max(2, min(args.frames, total_frames))
    if frames != args.frames:
        print(f"[zoom] 区间只有约 {total_frames} 帧，帧数从 {args.frames} 降到 {frames}",
              file=sys.stderr)

    out = note / "assets" / "grids" / f"zoom_{int(round(t0))}_{int(round(t1))}"
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(VW), "grid", "--media", str(media),
           "--t0", f"{t0:.2f}", "--t1", f"{t1:.2f}",
           "--frames", str(frames), "--cols", str(args.cols), "--out", str(out)]
    r = subprocess.run(cmd, cwd=ROOT, env=env(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print((r.stderr or r.stdout or "").strip()[-800:], file=sys.stderr)
        raise SystemExit("生成图版失败")

    mf = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    step = mf.get("step_s") or ((t1 - t0) / max(1, frames - 1))
    print(f"{out / 'grid.png'}　{mf['cols']}×{mf['rows']}　{frames} 帧　"
          f"{t0:.1f}–{t1:.1f}s　间隔 {step:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
