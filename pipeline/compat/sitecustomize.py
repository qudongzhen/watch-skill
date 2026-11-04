"""沙箱兼容层：让 tempfile.mkdtemp 建目录时不收紧权限，否则目录当场不可访问。

背景：在 DSH 的 Windows 沙箱下，`os.mkdir(path, 0o700)` 建出的目录会立刻变成
`PermissionError [WinError 5]`——连 `os.listdir` 都拒——因为 0700 会落成只授权
owner 的 DACL，而沙箱进程的有效身份匹配不上。默认权限的目录则完全正常。

`tempfile.mkdtemp` 内部固定用 0o700，于是 `tempfile.TemporaryDirectory()` 在沙箱里
必炸。上游 vw.py 的字体生效检测就用了它，`doctor` 会因此崩在最后一步。

这里把 mkdtemp 换成同语义、但用默认权限建目录的实现。只要本目录在 PYTHONPATH 上，
sitecustomize 就会在解释器启动时自动加载，不需要改上游代码。
"""
from __future__ import annotations

import os
import tempfile

_name_seq = tempfile._RandomNameSequence()


def mkdtemp(suffix: str | None = None, prefix: str | None = None, dir: str | None = None) -> str:
    prefix, suffix, dir = tempfile._sanitize_params(prefix or "tmp", suffix or "", dir)[:3]
    for _ in range(10000):
        name = os.path.join(dir, f"{prefix}{next(_name_seq)}{suffix}")
        try:
            os.mkdir(name)  # 关键：不复用默认的 0o700
            return name
        except FileExistsError:
            continue
    raise FileExistsError("无法创建临时目录：候选名已用尽")


tempfile.mkdtemp = mkdtemp
os.makedirs(os.path.join(tempfile.gettempdir()), exist_ok=True)
