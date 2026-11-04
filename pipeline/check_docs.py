#!/usr/bin/env python
"""文档体检：Markdown 里提到的脚本与开关，必须真的存在。

为什么需要：`--windows` 这种开关被方案否决后从代码里删掉了，文档里却还留着——
下一个人照着敲会得到 `unrecognized arguments`。这类漂移肉眼很难发现，
所以做成可执行检查，挂进 `selfcheck.py`。

    py -3.14 pipeline/check_docs.py

规则：
  1. `pipeline/<name>.py` 提到的脚本必须存在；
  2. 同一行里跟着的 `--flag` 必须是该脚本 `add_argument` 声明过的（或 argparse 自带的 --help）；
  3. `docs/` 里的历史方案文档允许提到已否决的开关，**但要带"已否决/已删除"字样**
     ——历史记录不该被改写，但必须能被认出来。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "pipeline"
MD_FILES = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "DECISIONS.md",
            ROOT / "pipeline" / "NOTE_CONTRACT.md", *sorted((ROOT / "docs").glob("*.md"))]

# argparse 自带、写在任何一行都合法
BUILTIN = {"--help", "--version"}
# 历史文档里出现已删除开关时，同一行/同段得有这些字样之一
HISTORY_MARKERS = ("否决", "删掉", "已删除", "不再", "撤回", "废弃", "原来", "以前")

FLAG_RE = re.compile(r"--[a-z0-9][a-z0-9-]*")
# `pipeline/run.py` 与裸 `run.py` 都要认；`vw.py`、`sitecustomize.py` 这类不在管线里的不查
SCRIPT_RE = re.compile(r"(?:pipeline/|scripts/)?([A-Za-z0-9_]+)\.py")
# 一条命令行到哪结束：分号/管道/注释/反引号都是边界（同一行可能写了多条命令）
CMD_SPLIT_RE = re.compile(r"[;|#`\n]")


def declared_flags(script: Path) -> set[str]:
    """从源码里抠出这个脚本声明过哪些长开关（不执行它）。"""
    text = script.read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r'add_argument\(\s*"(--[A-Za-z0-9-]+)"', text)) | BUILTIN


def commands(line: str) -> list[tuple[str, set[str]]]:
    """把一行拆成"命令段"，返回每段里的 (脚本名, 该段出现的开关集合)。

    为什么要拆：早期版本对**整行**做笛卡尔配对，于是文档里一句
    `yt-dlp --write-auto-subs` 会被算成 `run.py` 的开关（误报，曾让 selfcheck 变红）。
    现在只认"同一段命令里、脚本名之后"出现的开关。
    """
    out: list[tuple[str, set[str]]] = []
    for seg in CMD_SPLIT_RE.split(line):
        names = SCRIPT_RE.findall(seg)
        if not names:
            continue
        flags = set(FLAG_RE.findall(seg))
        for name in names:
            out.append((f"{name}.py", flags))
    return out


def audit() -> list[str]:
    known: dict[str, set[str]] = {}
    for folder in (PIPELINE, ROOT / "scripts"):
        for script in folder.glob("*.py"):
            known[script.name] = declared_flags(script)

    issues: list[str] = []
    for md in MD_FILES:
        if not md.exists():
            continue
        # DECISIONS 与 docs/ 是历史记录：允许提到已删掉的开关，但那一行要有"否决/删掉"字样
        historical = md.parent.name == "docs" or md.name == "DECISIONS.md"
        for lineno, line in enumerate(md.read_text(encoding="utf-8",
                                                   errors="replace").splitlines(), 1):
            for name, flags in commands(line):
                if name not in known:          # vw.py / sitecustomize.py 之类，不归这里管
                    continue
                for flag in flags - known[name]:
                    if historical and any(m in line for m in HISTORY_MARKERS):
                        continue
                    issues.append(f"{md.name}:{lineno} {name} 没有开关 {flag}")
    return issues


def main() -> int:
    issues = audit()
    for i in issues:
        print(f"  FAIL  {i}")
    print(f"\n{len(MD_FILES)} 个文档，{len(issues)} 处不一致" if issues else "\n文档与代码一致")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
