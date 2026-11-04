#!/usr/bin/env python
"""时间戳的唯一一份实现：`h:mm:ss` / `mm:ss` ↔ 秒数。

为什么要单独一个模块：`note_audit` 与 `quote_check` 曾各写一套正则，同一行 `1:02:03`
两边分别算出 **3723** 和 **62**（后者只吃掉 `1:02`）——引文的时刻一旦算错，
"附近有没有原帧"就会误判。现在两个脚本都只调这里。

    纯函数，不依赖任何第三方库，也不碰文件。
"""
from __future__ import annotations

import re

# h:mm:ss 或 mm:ss；小时的位数放宽到 1–2 位，秒必须两位。
# 前后加 \b 是有意的：`12:345` 不能被切成 `12:34`，`1:02:03:04` 只取前三段。
STAMP_RE = re.compile(r"\b(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)\b")
# 只认"分:秒"的窄形态（引文标注里常见的写法），给需要严格匹配的调用方
MIN_SEC_RE = re.compile(r"\b(\d{1,2}):([0-5]\d)\b")


def parse_stamps(text: str) -> list[int]:
    """抓出一行里所有时间戳，返回秒数（可能为空列表）。

    语义：`h:mm:ss` 取三段；`mm:ss` 取两段。分与秒都限定 0–59，
    所以 `12:60`、`12:345` 这类都不会被当成时间戳。
    """
    out: list[int] = []
    for h, mnt, sec in STAMP_RE.findall(text or ""):
        out.append(int(h or 0) * 3600 + int(mnt) * 60 + int(sec))
    return out


def stamp(sec: float) -> str:
    """秒数 → `mm:ss`；满一小时写成 `h:mm:ss`。"""
    total = int(sec)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def stamp_span(t0: float, t1: float) -> str:
    """`mm:ss–mm:ss`，附录/简报里引用时间段用。"""
    return f"{stamp(t0)}–{stamp(t1)}"
