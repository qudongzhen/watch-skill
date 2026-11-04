#!/usr/bin/env python
"""`pipeline/asrfix.py` 的自检：繁简、词表语义、静音区间、体检。

    py -3.14 pipeline/test_asrfix.py

失败即非零退出。所有断言都写明"测的是哪一条行为"，不做"跑通了算过"。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asrfix  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILED.append(name)


def seg(a: float, b: float, text: str) -> dict:
    return {"start": a, "end": b, "text": text}


def tmpdir() -> Path:
    """不用 tempfile.mkdtemp：本机沙箱里它建的 0700 目录当场不可访问（AGENTS.md 三坑）。"""
    d = ROOT / "work" / "tmp" / "test_asrfix"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 繁简

def test_t2s() -> None:
    got = asrfix.to_simplified("在我的遊戲中我有很多用戶")
    # 只挑 t2s 一定会改的字；「介面/界面」这种词组级差异不归这里管
    leftover = [ch for ch in got if ch in "遊戲戶"]
    check("繁转简生效（或明确报告不可用）", not leftover or not asrfix.t2s_available(), f"→ {got}")
    check("t2s_available 不抛异常", isinstance(asrfix.t2s_available(), bool))


# ---------------------------------------------------------------- 词表

def test_terms() -> None:
    out = asrfix.apply_terms([seg(0, 1, "用寒暑和寒暑"), seg(1, 2, "无关")], {"寒暑": "setter"})
    check("中文键替换", out["segments"][0]["text"] == "用setter和setter", out["segments"][0]["text"])
    check("计数：2 处 / 1 段", out["replaced"] == 2 and out["touched"] == 1)

    one = asrfix.apply_terms([seg(0, 1, "A")], {"A": "B", "B": "C"})
    check("单遍不串联", one["segments"][0]["text"] == "B", one["segments"][0]["text"])

    two = asrfix.apply_terms([seg(0, 1, "像散")], {"像散": "X", "像": "Y"})
    check("长键优先", two["segments"][0]["text"] == "X", two["segments"][0]["text"])

    three = asrfix.apply_terms([seg(0, 1, "a.b*c")], {".": "。"})
    check("元字符按字面", three["segments"][0]["text"] == "a。b*c", three["segments"][0]["text"])

    # 拉丁键必须有边界：else `set` 会命中 `settings`
    latin = asrfix.apply_terms([seg(0, 1, "settings and set")], {"set": "SET"})
    check("拉丁键不误伤子串", latin["segments"][0]["text"] == "settings and SET",
          latin["segments"][0]["text"])
    latin2 = asrfix.apply_terms([seg(0, 1, "javascript")], {"av": "AV"})
    check("两字母拉丁键不误伤", latin2["segments"][0]["text"] == "javascript",
          latin2["segments"][0]["text"])

    noop = asrfix.apply_terms([seg(0, 1, "寒暑")], {"寒暑": "寒暑"})
    check("键==值不算替换", noop["replaced"] == 0 and noop["noop"] == 1, str(noop["replaced"]))

    empty = asrfix.apply_terms([seg(0, 1, "文字")], {"": "X"})
    check("空键不会插值", empty["segments"][0]["text"] == "文字", empty["segments"][0]["text"])

    check("空词表原样返回", asrfix.apply_terms([seg(0, 1, "x")], {})["replaced"] == 0)


def test_term_files() -> None:
    d = tmpdir()
    (d / "asr-terms.json").write_text(json.dumps({"甲": "A"}), encoding="utf-8")
    note = d / "note"
    note.mkdir(exist_ok=True)
    (note / "terms.json").write_text(json.dumps({"甲": "B", "乙": "C"}), encoding="utf-8")
    loaded = asrfix.load_terms(d, note)
    check("每篇覆盖全局同名键", loaded["terms"]["甲"] == "B" and loaded["terms"]["乙"] == "C",
          str(loaded["terms"]))
    check("记下来源文件", len(loaded["files"]) == 2, str(loaded["files"]))

    (note / "terms.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"丙": "D"}).encode("utf-8"))
    bom = asrfix.load_terms(d, note)
    check("UTF-8 BOM 能读", bom["terms"].get("丙") == "D", str(bom["terms"]))

    (note / "terms.json").write_text("{坏 json", encoding="utf-8")
    bad = asrfix.load_terms(d, note)
    check("坏 JSON 不抛、但被报告",
          bad["terms"].get("甲") == "A" and any("terms.json" in e for e in bad["errors"]),
          str(bad["errors"]))

    note2 = d / "note2"
    note2.mkdir(exist_ok=True)
    (note2 / "terms.json").write_text(json.dumps({"丁": "x\ny"}), encoding="utf-8")
    cleaned = asrfix.load_terms(d, note2)
    check("含换行的词条被拒", "丁" not in cleaned["terms"], str(cleaned["terms"]))


# ---------------------------------------------------------------- 静音

def test_silence() -> None:
    events = [{"kind": "start", "t": 364.1}, {"kind": "end", "t": 365.2},
              {"kind": "start", "t": 400.0}]          # 最后一个未闭合
    spans = asrfix.silence_spans(events)
    check("事件流折区间、丢未闭合", spans == [(364.1, 365.2)], str(spans))
    check("乱序也合并", asrfix.silence_spans(
        [{"kind": "start", "t": 10}, {"kind": "end", "t": 12},
         {"kind": "start", "t": 11}, {"kind": "end", "t": 13}]) == [(10.0, 13.0)])
    check("部分覆盖 ≈0.11", abs(asrfix.coverage(spans, 360, 370) - 0.11) < 0.01)
    check("全覆盖 = 1.0", asrfix.coverage(spans, 364.2, 365.0) == 1.0)
    check("重叠区间不会 >1", asrfix.coverage([(0, 10), (2, 8)], 0, 10) == 1.0)

    # 阈值 0.85：0.6 那批"真话被静音淹没"的样本不该再报
    hit = asrfix.text_in_silence([seg(0, 10, "整段都在静音里")], [(0, 9.5)])
    miss = asrfix.text_in_silence([seg(0, 3, "太短不算")], [(0, 3)])
    check("85% 命中", hit["count"] == 1, str(hit["count"]))
    check("短于 5s 不报", miss["count"] == 0, str(miss["count"]))


# ---------------------------------------------------------------- 体检

def test_health() -> None:
    # 造数据要贴近真实节奏：中文口播约 4 字/秒，英文约 12 字母/秒
    zh_speech = [seg(0, 4, "这是一句正常的中文口播内容"), seg(4, 8, "第二句也是正常语速的内容")]
    hp = asrfix.health(zh_speech, expect_lang="en")
    check("要英文却出汉字 → 报警", any("语种" in f for f in hp["flags"]), str(hp["flags"]))

    zh = asrfix.health(zh_speech, expect_lang="zh")
    check("中文素材正常 → 不报", not zh["flags"], str(zh["flags"]))

    gap = asrfix.health([seg(0, 4, "开头说了几句正常的话"), seg(100, 104, "中间空了九十秒再说")])
    check("大空洞被点名", any("空洞" in f for f in gap["flags"]), str(gap["flags"]))

    check("无转写被点名", any("没有转写" in f for f in asrfix.health([])["flags"]))

    cover = asrfix.health([seg(0, 60, "只覆盖了一小段但字数是够的，这里再补一些内容凑够语速")],
                          media_duration=600)
    check("覆盖不足被点名", any("只覆盖到" in f for f in cover["flags"]), str(cover["flags"]))

    tail = asrfix.health([seg(0, 60, "句尾之后就没了内容，这里再补一些字凑够语速吧")],
                         media_duration=120)
    check("片尾缺失被点名", any("片尾" in f for f in tail["flags"]), str(tail["flags"]))

    low = asrfix.health([seg(0, 100, "字")])
    check("几乎没说话被点名", any("没人说话" in f for f in low["flags"]), str(low["flags"]))


# ---------------------------------------------------------------- 复现性

def test_reproducible() -> None:
    """同一份 asr.json + 同一份词表，跑两次结果必须一样（输入是 asr.json，不是 md）。"""
    segs = [seg(0, 1, "寒暑"), seg(1, 2, "寒暑")]
    terms = {"寒暑": "setter"}
    a = asrfix.apply_terms(segs, terms)
    b = asrfix.apply_terms(segs, terms)          # 注意：输入仍是原始 segs
    check("重复跑结果一致", a["segments"] == b["segments"] and a["replaced"] == b["replaced"])

    chained = asrfix.apply_terms([seg(0, 1, "寒暑")], {"寒暑": "酷暑", "酷暑": "炎夏"})
    again = asrfix.apply_terms(chained["segments"], {"寒暑": "酷暑", "酷暑": "炎夏"})
    check("词表成链时一次稳定、二次会变（已知边界，load_terms 会报）",
          chained["segments"][0]["text"] == "酷暑"
          and again["segments"][0]["text"] == "炎夏",
          f'{chained["segments"][0]["text"]} → {again["segments"][0]["text"]}')


def main() -> int:
    print("pipeline/asrfix.py 自检\n")
    test_t2s()
    test_terms()
    test_term_files()
    test_silence()
    test_health()
    test_reproducible()
    print(f"\n{len(FAILED)} 项失败" if FAILED else "\n全绿")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
