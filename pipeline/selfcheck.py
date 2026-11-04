#!/usr/bin/env python
"""一条命令判断"这个工作区还是好的吗"。

查四类，全绿才算可交接：
  1. 环境   python / ffmpeg / ImageMagick / 依赖包（口径见 `deps.py`：必需 FAIL、可选 WARN）
  2. 代码   pipeline 的脚本都能编译（改坏了当场暴露）
  3. 产物   既有笔记全部过 note_audit
  4. 结构   vendor 仓库在位、契约文件在位、文档与代码一致

用法：py -3.14 pipeline/selfcheck.py
退出码：0 全绿（或只有 WARN）；1 有 FAIL。
"""
from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "pydeps"))

import deps  # noqa: E402

OK, BAD, WARN = "OK  ", "FAIL", "WARN"
rows: list[tuple[str, str, str]] = []


def add(level: str, name: str, msg: str = "") -> None:
    rows.append((level, name, msg))


def check_env() -> None:
    """依赖探测只有 `deps.py` 一处口径：必需项缺失 = FAIL，可选项缺失 = WARN。

    早先这里自己写一套、doctor 另写一套，缺 opencc 时本文件红、doctor 绿。
    """
    for item in deps.probe():
        if item["ok"]:
            add(OK, item["name"], item["detail"])
        elif item["required"]:
            add(BAD, item["name"], f"{item['detail']}；{item['fix']}")
        else:
            add(WARN, item["name"], f"{item['detail']}（只影响对应链路）；{item['fix']}")


def check_code() -> None:
    bad = []
    for f in sorted((ROOT / "pipeline").glob("*.py")) + sorted((ROOT / "scripts").glob("*.py")):
        try:
            py_compile.compile(str(f), doraise=True, cfile=str(ROOT / "work" / "tmp" / "chk.pyc"))
        except py_compile.PyCompileError as exc:
            bad.append(f"{f.name}: {str(exc).splitlines()[-1][:80]}")
    add(OK if not bad else BAD, "脚本编译", "全部通过" if not bad else bad[0])


def check_structure() -> None:
    need = ["vendor/video-watch/vw.py", "pipeline/NOTE_CONTRACT.md", "pipeline/run.py",
            "vw.config.json", "AGENTS.md", "README.md", "asr-terms.json", "docs/README.md"]
    missing = [p for p in need if not (ROOT / p).exists()]
    add(OK if not missing else BAD, "关键文件", "齐全" if not missing else f"缺 {missing}")

    # 文档里提到的脚本/开关必须真的存在（--windows 那类幽灵开关就是这么混进来的）
    try:
        from check_docs import audit
        issues = audit()
        add(OK if not issues else BAD, "文档与代码",
            "一致" if not issues else f"{len(issues)} 处不一致：{issues[0]}")
    except Exception as exc:      # 检查器自己坏了不该把整份自检拖下水
        add(BAD, "文档与代码", f"检查器出错 {type(exc).__name__}: {exc}")


def check_notes() -> None:
    notes = [p for p in (ROOT / "notes").glob("*/note.md")]
    if not notes:
        add("WARN", "既有笔记", "还没有成品笔记")
        return
    for note in notes:
        r = subprocess.run([sys.executable, str(ROOT / "pipeline" / "note_audit.py"), str(note)],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        tail = [ln for ln in (r.stdout or "").splitlines() if "项检查" in ln]
        add(OK if r.returncode == 0 else BAD, f"笔记 {note.parent.name}",
            tail[0].strip() if tail else "验收失败")


def main() -> int:
    (ROOT / "work" / "tmp").mkdir(parents=True, exist_ok=True)
    check_env()
    check_code()
    check_structure()
    check_notes()

    print("\n工作区自检\n")
    for level, name, msg in rows:
        print(f"  {level}  {name:<18} {msg}")
    bad = sum(1 for r in rows if r[0] == BAD)
    warn = sum(1 for r in rows if r[0] == "WARN")
    print(f"\n{len(rows)} 项：{bad} FAIL / {warn} WARN")
    print("全绿 = 可以交接。" if not bad else "红项先修，别在这个状态下改代码。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
