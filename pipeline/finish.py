#!/usr/bin/env python
"""一条命令收尾：先验收，过了再导出。

这是「写完笔记之后」唯一要跑的命令，等价于：

    py -3.14 pipeline/note_audit.py <id>   # 不过就直接停，不产生半成品导出
    py -3.14 pipeline/export.py <id>       # 过了才出 exports/<标题>/ 三件套

用法：
    py -3.14 pipeline/finish.py BV1EAKH6GE7C
    py -3.14 pipeline/finish.py BV1EAKH6GE7C --no-compact   # 要无损原图

验收失败时退出码为 1，且**不会**导出——避免把有 FAIL 的笔记发出去。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(script: str, args: list[str]) -> int:
    cmd = [sys.executable, str(ROOT / "pipeline" / script), *args]
    return subprocess.run(cmd, cwd=ROOT).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="验收 + 导出，一步到位")
    ap.add_argument("note", help="笔记 id 或 note.md 路径")
    ap.add_argument("--no-compact", action="store_true", help="导出时保留原始 PNG")
    args = ap.parse_args()

    print("=" * 56)
    if run("note_audit.py", [args.note]) != 0:
        print("\n验收没通过，未导出。先把 FAIL 修掉再跑一次。")
        return 1
    print("=" * 56)
    export_args = [args.note] + (["--no-compact"] if args.no_compact else [])
    return run("export.py", export_args)


if __name__ == "__main__":
    sys.exit(main())
