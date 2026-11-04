#!/usr/bin/env python
"""笔记验收：查"这篇笔记经不经得起核对"，不查"写得好不好"。

它只验**形式与可回溯性**——时间戳、图片、置信度、引用的秒数是否落在图版覆盖范围内。
内容对不对、判断准不准，脚本管不了，那是人（或下一轮看图）的事。

用法：
    py -3.14 pipeline/note_audit.py BV1Xdnc6jEbL
    py -3.14 pipeline/note_audit.py notes/BV1Xdnc6jEbL/note.md

退出码：0 = 没有 FAIL；1 = 有 FAIL。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import timefmt          # 同目录：时间戳解析只此一份（与 quote_check 共用）
import store            # 同目录：JSON 原子写 / 容错读

ROOT = Path(__file__).resolve().parents[1]

FAIL, WARN, PASS = "FAIL", "WARN", "OK  "
rows: list[tuple[str, str, str]] = []


def add(level: str, name: str, msg: str) -> None:
    rows.append((level, name, msg))


def parse_times(line: str) -> list[float]:
    """抓一行里的 mm:ss 或 h:mm:ss（解析在 `timefmt`，与 quote_check 共用一份）。"""
    return [float(x) for x in timefmt.parse_stamps(line)]


def china_len(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arg = sys.argv[1]
    note = Path(arg)
    if note.is_dir():
        note = note / "note.md"
    elif not note.exists():
        note = ROOT / "notes" / arg / "note.md"
    if not note.exists():
        raise SystemExit(f"找不到笔记：{note}")

    note_dir = note.parent
    text = note.read_text(encoding="utf-8")
    lines = text.splitlines()
    research_path = note_dir / "research.json"
    research = store.load_json(research_path, default={})
    dur = float(research.get("timeline", {}).get("duration") or 0)
    grids = research.get("images") or []

    if not research:
        add(WARN, "research.json", "缺失，跳过时长与覆盖检查（先跑 pipeline/run.py）")

    # ---- 1. 图片：存在、相对路径、非空 ----
    imgs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)
    missing = [p for p in imgs if not (note_dir / p).exists()]
    absolute = [p for p in imgs if re.match(r"^(?:[a-zA-Z]:|/|https?:)", p)]
    empty = [p for p in imgs if (note_dir / p).exists() and (note_dir / p).stat().st_size < 1024]
    if not imgs:
        add(FAIL, "图片", "没有内嵌任何图版/关键帧")
    elif missing:
        add(FAIL, "图片", f"{len(missing)} 张不存在：{', '.join(missing[:3])}")
    else:
        add(PASS, "图片", f"{len(imgs)} 张均可解析")
    if absolute:
        add(WARN, "图片路径", f"{len(absolute)} 张是绝对路径/外链，换个目录就断：{absolute[0][:60]}")
    if empty:
        add(WARN, "图片内容", f"{len(empty)} 张小于 1KB，可能是空帧")

    # ---- 2. 小节：两种体例分开查 ----
    tutorial = bool(re.search(r"^###\s*步骤\s*\d+", text, re.M))
    in_body, subs, steps = False, [], []
    cur = None
    target = None
    for ln in lines:
        if ln.startswith("## "):
            in_body = ("分章" in ln or "章节" in ln) or ln.strip().startswith("## 步骤")
            cur = None
            continue
        if ln.startswith("### "):
            if re.match(r"^###\s*步骤\s*\d+", ln):
                cur = {"title": ln[4:].strip()[:30], "times": parse_times(ln),
                       "bullets": 0, "imgs": 0}
                steps.append(cur)
            elif in_body:
                cur = {"title": ln[4:].strip(), "times": parse_times(ln), "bullets": 0}
                subs.append(cur)
            else:
                cur = None
            continue
        if cur is not None and re.match(r"^\s*[-*]\s+", ln):
            cur["bullets"] += 1
        elif cur is not None and re.match(r"^\s*!\[", ln):
            cur["imgs"] = cur.get("imgs", 0) + 1

    if tutorial:
        no_img = [s["title"] for s in steps if s["imgs"] == 0]
        no_time = [s["title"] for s in steps if not s["times"]]
        thin = [s["title"] for s in steps if s["bullets"] < 2]
        if no_img:
            add(FAIL, "步骤配图", f"{len(no_img)}/{len(steps)} 个步骤没有图：{no_img[0]}")
        else:
            add(PASS, "步骤配图", f"{len(steps)} 个步骤都有图")
        if no_time:
            add(FAIL, "步骤时间", f"{len(no_time)} 个步骤缺起始秒数：{no_time[0]}")
        else:
            add(PASS, "步骤时间", "每个步骤都标了起始时刻")
        if thin:
            add(WARN, "步骤要点", f"{len(thin)} 个步骤不足两条要点：{thin[0]}")
        else:
            add(PASS, "步骤要点", f"{len(steps)} 个步骤均有 ≥2 条要点")
        units = len(steps)
    else:
        if not subs:
            add(FAIL, "分章", "找不到 `### 时间｜标题` 形式的分章小节，也不像图文教程")
        else:
            no_time = [s["title"][:24] for s in subs if not s["times"]]
            thin = [s["title"][:24] for s in subs if s["bullets"] < 2]
            if no_time:
                add(FAIL, "分章时间", f"{len(no_time)} 节没有时间范围：{no_time[0]}")
            else:
                add(PASS, "分章时间", f"{len(subs)} 节都有时间范围")
            if thin:
                add(WARN, "分章要点", f"{len(thin)} 节不足两条要点：{thin[0]}")
            else:
                add(PASS, "分章要点", f"{len(subs)} 节均有 ≥2 条要点")
        units = len(subs)

    # ---- 3. 大纲：条目数与时间递增 ----
    # ---- 3. 大纲（教程体没有大纲，步骤自带时间即可） ----
    if not tutorial:
        outline = []
        for ln in lines:
            if re.match(r"^\s*\d+\.\s+", ln):
                ts = parse_times(ln)
                if ts:
                    outline.append(ts[0])
        if len(outline) < 3:
            add(FAIL, "大纲", f"带时间戳的条目只有 {len(outline)} 条（至少 3 条）")
        elif outline != sorted(outline):
            add(FAIL, "大纲", "时间戳不是递增的，读者跳转会乱")
        else:
            add(PASS, "大纲", f"{len(outline)} 条，时间递增")

    # ---- 4. 可回溯：正文引用的秒数要落在时长内、且被某张图版覆盖 ----
    if dur and grids:
        spans = [(g["t0"], g["t1"]) for g in grids]
        cited, bad, uncovered = 0, [], []
        for ln in lines:
            if ln.startswith("![") or ln.startswith(">"):
                continue
            for t in parse_times(ln):
                cited += 1
                if t > dur + 1:
                    bad.append(t)
                elif not any(a - 0.5 <= t <= b + 0.5 for a, b in spans):
                    uncovered.append(t)
        if bad:
            add(FAIL, "时间越界", f"{len(bad)} 处超出片长 {dur:.0f}s：{[int(x) for x in bad[:4]]}")
        else:
            add(PASS, "时间越界", f"{cited} 处引用均在片长内")
        if uncovered:
            add(WARN, "可回溯性", f"{len(uncovered)} 处秒数不在任何图版覆盖内：{[int(x) for x in uncovered[:4]]}")
        else:
            add(PASS, "可回溯性", "每处引用都能对到一张图版的覆盖区间")

    # ---- 5. 结论纪律 ----
    if re.search(r"^##+\s*.*置信度", text, re.M):
        seg = text.split("置信度", 1)[1]
        add(PASS, "置信度", f"有置信度小节（{china_len(seg[:600])} 字内）")
    else:
        add(FAIL, "置信度", "缺「置信度与缺口」小节——没有它，推断会被当成事实读")
    if re.search(r"^##+\s*.*(附录|取证说明)", text, re.M):
        add(PASS, "附录", "有取证说明")
    else:
        add(WARN, "附录", "缺取证说明（采样间隔/转写模型/校正记录）")

    # ---- 6. 篇幅 ----
    body = re.split(r"^##+\s*.*(?:附录|取证说明)", text, maxsplit=1, flags=re.M)[0]
    n = china_len(body)
    # 上限随小节数放宽：每节至少要写两句，章节多就必然更长
    cap = max(1800, 600 + 160 * max(units, 1))
    label = "步骤" if tutorial else "节"
    if n < 700:
        add(WARN, "篇幅", f"正文 {n} 汉字（不含附录），偏短（下限 700）")
    elif n > cap:
        add(WARN, "篇幅", f"正文 {n} 汉字，超过 {units} {label}对应的上限 {cap}")
    else:
        add(PASS, "篇幅", f"正文 {n} 汉字（{units} {label}，上限 {cap}）")

    # ---- 7. 残留 ----
    ph = re.findall(r"(TODO|待填|待补|XXX|\?\?\?)", text)
    if ph:
        add(FAIL, "占位符", f"残留 {len(ph)} 处：{ph[0]}")

    # ---- 输出 ----
    print(f"\n验收：{note.relative_to(ROOT) if note.is_relative_to(ROOT) else note}\n")
    for level, name, msg in rows:
        print(f"  {level}  {name:<10} {msg}")
    fails = sum(1 for r in rows if r[0] == FAIL)
    warns = sum(1 for r in rows if r[0] == WARN)
    print(f"\n{len(rows)} 项检查：{fails} FAIL / {warns} WARN / {len(rows) - fails - warns} OK")
    print("注意：这里只验形式与可回溯性，不验内容真假。")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
