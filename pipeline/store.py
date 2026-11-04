#!/usr/bin/env python
"""JSON 读写的唯一约定：**原子写** + 读坏文件不崩。

为什么需要：`research.json` 是"时间映射 + 分段 + 转写 + 截图挑选"的唯一机器摘要，
写它有几十毫秒窗口。裸 `write_text` 在这个窗口里被杀，会留下**半个 JSON**；下游 9 处
`json.loads` 会直接抛 `JSONDecodeError`，看起来像脚本有 bug。更糟的是重跑守卫
（D22 第四件：成品笔记存在时复用既有转写）读到半个文件会判定"没有既有转写"，
于是重跑 ASR、把引文证据换掉——守卫被文件损坏绕过。

- `save_json()`：写同目录临时文件 → `os.replace`，同盘原子；格式统一 `indent=1` + 中文原样。
- `load_json()`：文件不存在 → 返回 default；存在但损坏 → **stderr 一行警告** + 返回 default
  （不静默、也不让整条链路崩）。`strict=True` 时抛异常，给需要强校验的调用方。

    纯标准库，不依赖项目内其它模块。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def save_json(path: Path | str, obj: Any) -> Path:
    """原子写 JSON：要么是旧文件，要么是完整的新文件，不存在"半个文件"的中间态。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)              # 同目录 → 同盘，replace 是原子的
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)  # 失败时别把 .tmp 留在产物目录
    return p


def load_json(path: Path | str, default: Any = None, strict: bool = False) -> Any:
    """读 JSON。不存在 → default；损坏 → 警告 + default（strict=True 则抛）。"""
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        if strict:
            raise
        print(f"[warn] {p} 读不动（{type(exc).__name__}: {exc}），按空处理；"
              f"重跑一次取证可修复", file=sys.stderr, flush=True)
        return default
