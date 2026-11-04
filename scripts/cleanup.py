#!/usr/bin/env python
"""工作区清理：只删"能再生成"的中间物（白名单，默认 dry-run）。

删什么（详见 docs/plan-cleanup.md §0）：
  work/jobs/*           staging 目录，含与 notes/*/source.* 重复的视频副本
  work/tmp/*            一次性脚本与日志
  work/refs/*-20*/      参考仓库快照（fetch*.py 可重取；原型 .py 保留）
  notes/*/evidence/probe/analysis_v.txt   probe 的逐帧量测表（pipeline/ 没代码读它）
可选：--include-sources 再删 notes/*/source.*（默认关：删了要重下，且无法离线复验）

不删：pydeps/、tools/、work/hf/（whisper 模型）、notes/*/assets/、exports/。

用法：
    py -3.14 scripts/cleanup.py                      # 只报体积与将删的路径
    py -3.14 scripts/cleanup.py --apply              # 真删
    py -3.14 scripts/cleanup.py --apply --include-sources
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
NOTES = ROOT / "notes"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def size_of(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return total


def plan(include_sources: bool) -> list[tuple[str, list[Path], str]]:
    """返回 [(类别, 目标路径, 处置说明)]；路径都在 ROOT 之内。"""
    out: list[tuple[str, list[Path], str]] = []

    jobs = [p for p in (WORK / "jobs").glob("*") if p.is_dir()]
    if jobs:
        out.append(("work/jobs", jobs, "staging 副本，与 notes/*/source.* 重复"))

    tmp = [p for p in (WORK / "tmp").glob("*")]
    if tmp:
        out.append(("work/tmp", tmp, "一次性脚本与日志"))

    snapshots = [p for p in (WORK / "refs").glob("*-20*") if p.is_dir()]
    if snapshots:
        out.append(("work/refs 快照", snapshots, "参考仓库快照，fetch*.py 可重取"))

    analysis = sorted(NOTES.glob("*/evidence/probe/analysis_v.txt"))
    if analysis:
        out.append(("analysis_v.txt", analysis, "probe 的逐帧量测表，重跑 probe 会再生成"))

    if include_sources:
        sources = sorted(p for p in NOTES.glob("*/source.*") if p.is_file())
        if sources:
            out.append(("notes/*/source.*", sources, "视频本体（--include-sources 才删）"))

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="清理可再生成的中间物（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真的删除（默认只报告）")
    ap.add_argument("--include-sources", action="store_true",
                    help="连 notes/*/source.* 一起删（删后重跑要重新下载）")
    args = ap.parse_args()

    if not WORK.exists():
        print(f"没有 work/ 目录，什么都不用清：{WORK}")
        return 0

    groups = plan(args.include_sources)
    if not groups:
        print("没有可清理的中间物，工作区已经是干净的。")
        return 0

    grand = 0
    for name, paths, why in groups:
        n = size_of(paths)
        grand += n
        print(f"\n[{name}] {len(paths)} 项，{human(n)} —— {why}")
        for p in paths[:8]:
            print(f"  {p.relative_to(ROOT).as_posix()}")
        if len(paths) > 8:
            print(f"  …还有 {len(paths) - 8} 项")

    print(f"\n合计可回收 {human(grand)}")
    if not args.apply:
        print("这是 dry-run；加 --apply 才真删。")
        return 0

    for name, paths, _ in groups:
        for p in paths:
            # 双保险：只删 ROOT 之下的路径
            try:
                p.resolve().relative_to(ROOT)
            except ValueError:
                print(f"跳过（不在工作区内）：{p}", file=sys.stderr)
                continue
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        print(f"已清理 {name}")

    left = size_of([WORK / "jobs", WORK / "tmp"])
    print(f"\n完成，回收 {human(grand)}；work/jobs+tmp 现在 {human(left)}")
    print("提示：下次 run.py 会重新下载视频（staging 已清空）；whisper 模型仍在 work/hf/。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
