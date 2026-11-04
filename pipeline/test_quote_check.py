#!/usr/bin/env python
"""`pipeline/quote_check.py` 的自检：抽取、分类、CLI 退出码。

    py -3.14 pipeline/test_quote_check.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import quote_check as qc  # noqa: E402

FAILED: list[str] = []
MD = """# 测试笔记

- 作者说「贵是贵点，但吃了不烧心」（00:12，原话）。
- 例证：「呵呵」。十几年前是温和的微笑。
- 参数用行内代码 `hover_animate` 不算引文。
- 这里引一句长的「画面上的字写着请勿触摸」（读屏）。
- 没有任何出处的引文「这一句有八个字呢」。
- 带秒数但哪都够不着的「这句转写里根本没有啊」（03:30）。
"""

SEGS = [
    {"start": 10.0, "end": 14.0, "text": "贵是贵点 但吃了不烧心"},
    {"start": 200.0, "end": 210.0, "text": "完全无关的一句话"},
]
STILLS = [12, 96]


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILED.append(name)


def tmp_note(md: str, stills: list[int]) -> Path:
    d = ROOT / "work" / "tmp" / "test_quote"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "note.md").write_text(md, encoding="utf-8")
    (d / "research.json").write_text(
        '{"asr": %s, "stills": [%s]}' % (
            __import__("json").dumps(SEGS, ensure_ascii=False),
            ",".join(f'{{"image": "notes/x/assets/stills/s01_{s:05d}.png"}}' for s in stills)),
        encoding="utf-8")
    return d


def test_extract() -> None:
    qs = qc.extract(MD)
    texts = [q["text"] for q in qs]
    check("抽出 4 条（「呵呵」2 字被滤掉）", len(qs) == 4, str(len(qs)))
    check("行内代码不算引文", not any("hover_animate" in t for t in texts), str(texts))
    check("标了 mm:ss 的记下来", any(q["stamps"] == [12] for q in qs))
    check("括注式出处标记被识别", sum(1 for q in qs if q["marker"]) == 2,
          str([(q["text"][:6], q["marker"]) for q in qs]))
    check("正文里顺嘴提到「转写」不算标注",
          not next(q for q in qs if "根本没有" in q["text"])["marker"])
    check("短引文被过滤（「呵呵」只有 2 字）", "呵呵" not in texts, str(texts))
    check("代码块里的引号不算", qc.extract("```\n「代码里的引号」\n```") == [])


def test_classify() -> None:
    segs, stills = SEGS, STILLS
    q1 = {"text": "贵是贵点，但吃了不烧心", "stamps": [12], "marker": True}
    c1 = qc.classify(q1, segs, stills)
    check("转写命中的判「有出处」", c1["verdict"] == "有出处" and c1["transcript"] >= 0.9, str(c1))

    q2 = {"text": "画面上的字写着请勿触摸", "stamps": [], "marker": True}
    check("有出处标记即可", qc.classify(q2, segs, stills)["verdict"] == "有出处")

    q3 = {"text": "这句转写里根本没有啊", "stamps": [210], "marker": False}
    c3 = qc.classify(q3, segs, stills)
    check("只有时刻、够不着 → 仅时刻", c3["verdict"] == "仅时刻", str(c3))

    q4 = {"text": "没有任何出处的引文在这里", "stamps": [], "marker": False}
    check("既无时刻也无标记 → 无出处", qc.classify(q4, segs, stills)["verdict"] == "无出处")

    q6 = {"text": "贵是贵点 但吃了不烧心", "stamps": [], "marker": False}
    check("转写里确实有这句（无括注无时刻）也算有出处",
          qc.classify(q6, segs, stills)["verdict"] == "有出处",
          str(qc.classify(q6, segs, stills)))

    q5 = {"text": "某句只靠原帧的话", "stamps": [97], "marker": False}
    c5 = qc.classify(q5, segs, stills)
    check("时刻附近有原帧 → 有出处", c5["verdict"] == "有出处" and c5["image"], str(c5))

    check("空转写不崩", qc.classify({"text": "随便一句引文", "stamps": [], "marker": False},
                                   [], [])["verdict"] == "无出处")


def test_report_and_cli() -> None:
    note = tmp_note(MD, STILLS)
    r = qc.report(note)
    check("报告总数与分类之和一致",
          r["total"] == 4 and sum(r["counts"].values()) == 4, str(r["counts"]))
    check("有无出处和有仅时刻各 1 条",
          r["counts"]["无出处"] == 1 and r["counts"]["仅时刻"] == 1, str(r["counts"]))

    sys.argv = ["quote_check.py", str(note.name), "--strict"]
    qc.NOTES = note.parent
    code = qc.main()
    check("--strict 有无出处时退出 1", code == 1, str(code))

    (note / "note.md").write_text("- 「贵是贵点，但吃了不烧心」（00:12，原话）。\n",
                                  encoding="utf-8")
    sys.argv = ["quote_check.py", str(note.name), "--strict"]
    check("全都有出处时 --strict 退出 0", qc.main() == 0)
    shutil.rmtree(note, ignore_errors=True)


def main() -> int:
    print("pipeline/quote_check.py 自检\n")
    test_extract()
    test_classify()
    test_report_and_cli()
    print(f"\n{len(FAILED)} 项失败" if FAILED else "\n全绿")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
