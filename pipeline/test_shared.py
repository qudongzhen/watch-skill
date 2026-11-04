#!/usr/bin/env python
"""跨脚本共享工具的自检：时间戳解析（`timefmt`）与依赖探测（`deps`）。

    py -3.14 pipeline/test_shared.py

为什么要单独测：这两个模块的存在意义就是"只此一份、两边一致"。
`1:02:03` 曾经在 `note_audit` 里算 3723、在 `quote_check` 里算 62（差 60 倍），
依赖口径也曾让 selfcheck 红、doctor 绿——回归一次就会重新分叉。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "pydeps"))

import deps  # noqa: E402
import note_audit  # noqa: E402
import quote_check  # noqa: E402
import store  # noqa: E402
import timefmt  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILED.append(name)


def test_timefmt() -> None:
    check("h:mm:ss 取三段", timefmt.parse_stamps("（1:02:03，原话）") == [3723],
          str(timefmt.parse_stamps("（1:02:03，原话）")))
    check("mm:ss 取两段", timefmt.parse_stamps("00:20 与 01:05") == [20, 65],
          str(timefmt.parse_stamps("00:20 与 01:05")))
    check("时间段两个都抓", timefmt.parse_stamps("0:05–1:02:05") == [5, 3725],
          str(timefmt.parse_stamps("0:05–1:02:05")))
    check("12:345 不切出时间戳", timefmt.parse_stamps("12:345 不是时间") == [],
          str(timefmt.parse_stamps("12:345 不是时间")))
    check("12:60 不算时间戳", timefmt.parse_stamps("12:60") == [],
          str(timefmt.parse_stamps("12:60")))
    check("空串安全", timefmt.parse_stamps("") == [])
    check("stamp 满一小时带小时", timefmt.stamp(3723) == "1:02:03", timefmt.stamp(3723))
    check("stamp 不足一小时两位分", timefmt.stamp(62) == "01:02", timefmt.stamp(62))
    check("stamp_span 拼时间段", timefmt.stamp_span(5, 65) == "00:05–01:05",
          timefmt.stamp_span(5, 65))


def test_both_parsers_agree() -> None:
    """回归护栏：两个脚本对同一行的秒数必须一模一样。"""
    samples = ["（1:02:03，原话）", "00:20 和 01:05", "12:345", "12:60", "", "没有时刻"]
    for s in samples:
        a = [float(x) for x in note_audit.parse_times(s)]
        b = [float(x) for x in quote_check.stamps_of(s)]
        check(f"解析一致：{s or '空串'}", a == b, f"{a} vs {b}")


def test_deps() -> None:
    check("必需与可选不重叠", not (set(deps.REQUIRED) & set(deps.OPTIONAL)),
          str(set(deps.REQUIRED) & set(deps.OPTIONAL)))
    items = deps.probe()
    names = {i["name"] for i in items}
    check("探测覆盖面 = 必需 ∪ 可选", names == set(deps.REQUIRED) | set(deps.OPTIONAL),
          str(names ^ (set(deps.REQUIRED) | set(deps.OPTIONAL))))
    check("每项都带 required 与 fix",
          all(isinstance(i["required"], bool) and "fix" in i for i in items))
    check("每项都带 ok/detail", all({"ok", "detail"} <= set(i) for i in items))

    required_missing = [{"name": "x", "ok": False, "detail": "", "required": True, "fix": ""}]
    optional_missing = [{"name": "y", "ok": False, "detail": "", "required": False, "fix": ""}]
    check("缺必需 → 退出码 1", deps.worst(required_missing) == 1)
    check("只缺可选 → 退出码 0", deps.worst(optional_missing) == 0)
    check("全齐 → 退出码 0", deps.worst(items) == 0, str([i["name"] for i in deps.missing_required(items)]))
    check("summary 报必需缺失", "必需项没齐" in deps.summary(required_missing))
    check("summary 报可选缺失", "可选链路缺" in deps.summary(optional_missing))
    check("全齐时 summary 说就绪", deps.summary(items) == "全部就绪", deps.summary(items))


def test_store() -> None:
    """JSON 读写：原子写不留半个文件，读坏文件不崩。"""
    import json
    import shutil

    d = ROOT / "work" / "tmp" / "test_store"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    p = d / "r.json"

    store.save_json(p, {"名字": "测试", "n": 1})
    raw = p.read_text(encoding="utf-8")
    check("中文不转义", '"名字"' in raw and "\\u" not in raw, raw[:40])
    check("缩进与旧文件一致", raw.startswith('{\n "名字"'), raw[:12])
    check("读回来相等", store.load_json(p) == {"名字": "测试", "n": 1})
    check("不留 .tmp", not (d / "r.json.tmp").exists())

    # 原子性：写入中途失败，旧文件必须完好
    p.write_text('{"old": true}', encoding="utf-8")

    class Boom:
        def __repr__(self):
            raise RuntimeError("序列化炸了")

    try:
        store.save_json(p, {"x": Boom()})
    except Exception:
        pass
    check("写失败不动旧文件", json.loads(p.read_text(encoding="utf-8")) == {"old": True},
          p.read_text(encoding="utf-8"))
    check("失败也不留 .tmp", not (d / "r.json.tmp").exists())

    check("读不存在给 default", store.load_json(d / "nope.json", default={"d": 1}) == {"d": 1})
    p.write_text("{ 坏 json", encoding="utf-8")
    check("读坏文件不抛、给 default", store.load_json(p, default={}) == {})
    try:
        store.load_json(p, default={}, strict=True)
    except Exception as exc:  # noqa: BLE001
        check("strict=True 时才抛", "JSONDecodeError" in type(exc).__name__)
    else:
        check("strict=True 时才抛", False, "居然没抛")
    check("自动建目录", store.save_json(d / "sub" / "x.json", [1]) and (d / "sub" / "x.json").exists())


def main() -> int:
    print("pipeline/timefmt.py / deps.py / store.py 自检\n")
    test_timefmt()
    test_both_parsers_agree()
    test_deps()
    test_store()
    print(f"\n{len(FAILED)} 项失败" if FAILED else "\n全绿")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
