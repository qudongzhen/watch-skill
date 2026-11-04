#!/usr/bin/env python
"""引文出处体检：这条引文**凭什么**？（不判真假，只判有没有出处）

为什么不做"真假判定"：测过四种自动核验（见 `docs/plan-quote-check.md` §1），
在没有 OCR 的情况下全部有大量误报——引号在中文笔记里既用于引语也用于强调，
而"是不是原话"只有看画面才知道。

所以这里只回答三个可判定的事实：
1. 这句话在**转写**里能找到吗（近似匹配，允许同音错字）；
2. 它附近有没有**可核的原帧**（`assets/stills/`）；
3. 作者有没有标**出处**（mm:ss 或"读屏/原文/字幕"这类字样）。

判定：有出处 / 仅时刻（够不着转写也没原帧，多半是读屏）/ 无出处。

用法：
    py -3.14 pipeline/quote_check.py <id>
    py -3.14 pipeline/quote_check.py <id> --strict     # 有无出处时退出码 1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import timefmt          # 同目录：时间戳解析只此一份（见 timefmt.py 顶部说明）
import store            # 同目录：JSON 原子写 / 容错读

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "notes"

QUOTE_RE = re.compile(r"[「『“\"]([^「」『』“”\"\n]{2,120})[」』”\"]")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
FENCE_RE = re.compile(r"```.*?```", re.S)
# 出处标记必须是**括注**，不能是正文里顺嘴提到的词（"这句转写里根本没有"不算标注）
MARKER_RE = re.compile(r"[（(][^）)\n]{0,24}(原文|原话|字幕|转写|读屏|照录|画面上的字|引文)[^）)\n]{0,24}[）)]")
STILL_SEC_RE = re.compile(r"_(\d{4,6})")
MIN_LEN = 6          # 少于 6 个实义字符的引文不查（多是术语、标签）
NEAR_SEC = 15        # "附近有原帧"的容差
TR_HIT = 0.6         # 转写命中阈值


def meaningful(text: str) -> str:
    """去空白/标点/符号后的实义字符。"""
    return "".join(ch for ch in (text or "")
                   if not ch.isspace() and unicodedata.category(ch)[0] not in "PSZ")


def lcs_ratio(a: str, b: str) -> float:
    """a 被 b 覆盖的比例（保序）。"""
    if not a or not b:
        return 0.0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1] / len(a)


def stamps_of(line: str) -> list[int]:
    """一行里的时间戳（秒）。解析只走 `timefmt`，别在这里另写正则。

    早先这里是 `(\\d{1,2}):([0-5]\\d)`，会把 `1:02:03` 切成 `1:02`（62 秒），
    而 `note_audit` 同一行算出 3723——引文跟原帧的匹配因此会错。
    """
    return timefmt.parse_stamps(line)


def extract(md_text: str) -> list[dict]:
    """抽出所有引文，并记下它那一行的秒数标注与出处标记。"""
    body = FENCE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), md_text)   # 保住行号
    lines = body.splitlines()
    out: list[dict] = []
    for i, line in enumerate(lines):
        clean = INLINE_CODE_RE.sub(" ", line)      # 行内代码是参数，不是引文
        prev = INLINE_CODE_RE.sub(" ", lines[i - 1]) if i else ""
        # 上一行的出处标记只在"它是引子的那一行"时继承（如「作者在 00:12 说：」），
        # 否则上一行自己的括注会被下一行蹭走
        prev_ok = bool(MARKER_RE.search(prev)) and not QUOTE_RE.search(prev)
        marker = bool(MARKER_RE.search(line)) or prev_ok
        for raw in QUOTE_RE.findall(clean):
            text = raw.strip()
            if len(meaningful(text)) < MIN_LEN:
                continue
            out.append({"text": text, "line": i + 1, "stamps": stamps_of(line),
                        "marker": marker})
    return out


def classify(q: dict, segs: list[dict], still_secs: list[int]) -> dict:
    """一条引文的事实：转写里最像的那句、附近有没有原帧、够不够得着。"""
    mq = meaningful(q["text"])
    best, best_t = 0.0, None
    for s in segs:
        r = lcs_ratio(mq, meaningful(s.get("text") or ""))
        if r > best:
            best, best_t = r, float(s.get("start", 0))
    near_image = bool(q["stamps"] and still_secs and
                      min((abs(st - sec) for st in still_secs for sec in q["stamps"]),
                          default=999) <= NEAR_SEC)
    got_transcript = best >= TR_HIT
    # 出处判定：只要有一条来源成立就算有出处——
    # ① 作者标了括注（原话/读屏）；② 转写里确实有这句；③ 附近有可核的原帧
    if q["marker"] or got_transcript or (q["stamps"] and near_image):
        verdict = "有出处"
    elif q["stamps"]:
        verdict = "仅时刻"
    else:
        verdict = "无出处"
    return {"verdict": verdict, "transcript": round(best, 2), "transcript_t": best_t,
            "image": near_image, "marker": q["marker"], "stamps": q["stamps"]}


def report(note_dir: Path) -> dict:
    md = note_dir / "note.md"
    if not md.exists():
        return {"error": f"没有 {md}"}
    research = {}
    rp = note_dir / "research.json"
    if rp.exists():
        research = store.load_json(rp, default={})
    segs = research.get("asr") or []
    still_secs = []
    for s in research.get("stills") or []:
        m = STILL_SEC_RE.search(s.get("image") or "")
        if m:
            still_secs.append(int(m.group(1)))

    quotes = extract(md.read_text(encoding="utf-8"))
    rows = [{**q, **classify(q, segs, still_secs)} for q in quotes]
    counts = {"有出处": 0, "仅时刻": 0, "无出处": 0}
    for r in rows:
        counts[r["verdict"]] += 1
    return {"total": len(rows), "counts": counts, "rows": rows,
            "segs": len(segs), "stills": len(still_secs)}


def main() -> int:
    ap = argparse.ArgumentParser(description="引文出处体检")
    ap.add_argument("slug")
    ap.add_argument("--strict", action="store_true", help="有无出处时退出码 1")
    ap.add_argument("--all", action="store_true", help="打印全部引文，不只是有问题的")
    args = ap.parse_args()

    note = NOTES / args.slug
    if not note.exists():
        raise SystemExit(f"没有这个项目：{note}")
    r = report(note)
    if "error" in r:
        raise SystemExit(r["error"])
    c = r["counts"]
    print(f"{args.slug}　引文 {r['total']} 条：有出处 {c['有出处']}、"
          f"仅时刻 {c['仅时刻']}、无出处 {c['无出处']}"
          f"　（转写 {r['segs']} 段 / 原帧 {r['stills']} 张）")
    bad = [x for x in r["rows"] if x["verdict"] != "有出处"]
    for x in (r["rows"] if args.all else bad)[:20]:
        t = f"{x['transcript_t']:.0f}s" if x["transcript_t"] is not None else "—"
        print(f"  [{x['verdict']}] L{x['line']} 转写 {x['transcript']:.2f}@{t}"
              f"{' 原帧' if x['image'] else ''}{' 标记' if x['marker'] else ''}"
              f"  {x['text'][:34]}")
    if c["无出处"]:
        print("\n  无出处 = 既没标 mm:ss，也没有「原文/读屏」这类说明。"
              "补一下时间戳，或标「（读屏）」，或删掉。")
    return 1 if (args.strict and c["无出处"]) else 0


if __name__ == "__main__":
    sys.exit(main())
