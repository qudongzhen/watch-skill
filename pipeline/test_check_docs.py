#!/usr/bin/env python
"""`pipeline/check_docs.py` 的自检：命令段切分与开关归属。

    py -3.14 pipeline/test_check_docs.py

为什么单独测它：它是 selfcheck 的一道闸门，宁可自己错报也不能漏报。
曾经的 bug 是对**整行**做"脚本 × 开关"笛卡尔配对，于是文档里一句
`yt-dlp --write-auto-subs` 被算成 `run.py` 的开关，selfcheck 直接变红。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_docs  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILED.append(name)


def flags_on(line: str, script: str) -> set[str]:
    """某条命令段里属于某个脚本的开关。"""
    return {f for name, flags in check_docs.commands(line) if name == script for f in flags}


def test_segments() -> None:
    # 外部工具的开关不能算到管线脚本头上（这就是当初误报的那一行）
    line = "run.py --apply，必要时 `yt-dlp --write-auto-subs` 也行"
    check("管线脚本只认自己那段", flags_on(line, "run.py") == {"--apply"},
          str(check_docs.commands(line)))
    check("没有管线脚本的行不产出命令段",
          check_docs.commands("yt-dlp --write-auto-subs") == [],
          str(check_docs.commands("yt-dlp --write-auto-subs")))

    # 同一行两条命令：各算各的
    two = "py -3.14 pipeline/still.py <id> --at 12; py -3.14 pipeline/zoom.py <id> --at 12 --window 8"
    check("分号切成两段", len(check_docs.commands(two)) == 2, str(check_docs.commands(two)))
    check("still.py 只拿到 --at", flags_on(two, "still.py") == {"--at"}, str(flags_on(two, "still.py")))
    check("zoom.py 拿到 --at 与 --window",
          flags_on(two, "zoom.py") == {"--at", "--window"}, str(flags_on(two, "zoom.py")))

    # 管道/注释也要断开
    piped = "py -3.14 pipeline/doctor.py | Select-Object -Last 5  # 不用 --verbose"
    check("管道与注释之后不再算", flags_on(piped, "doctor.py") == set(),
          str(flags_on(piped, "doctor.py")))


def test_audit() -> None:
    """真跑一遍全库；同时确认已知开关不会被误判。"""
    issues = check_docs.audit()
    check("全库文档与代码一致", issues == [], "；".join(issues[:3]))
    known = check_docs.declared_flags(ROOT / "pipeline" / "run.py")
    check("run.py 声明了 --auto-subs", "--auto-subs" in known)
    check("run.py 声明了 --refresh-meta", "--refresh-meta" in known)
    check("argparse 自带 --help 不报错", "--help" in check_docs.BUILTIN)


def main() -> int:
    print("pipeline/check_docs.py 自检\n")
    test_segments()
    test_audit()
    print(f"\n{len(FAILED)} 项失败" if FAILED else "\n全绿")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
