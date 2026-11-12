#!/usr/bin/env python
"""笔记验收：查"这篇笔记经不经得起核对"，不查"写得好不好"。

它只验**形式与可回溯性**——时间戳、图片、置信度、引用的秒数是否落在图版/截图的覆盖范围内。

规则分两档，按 `research.json` 的 `note_schema` 区分：

- **旧包**（没有 `note_schema` 字段）：沿用 2026-11 之前的规则，旧笔记不补不改；
- **新包**（`note_schema >= 2`）：字数不设限，改用结构底线（每步 ≥3 条要点、每步有判据）、
  图必须落在步骤内并带跳转链接，另有重复句/转述词/参数集中度三条可读性检查。

用法：
    py -3.14 pipeline/note_audit.py BV1Xdnc6jEbL
    py -3.14 pipeline/note_audit.py notes/BV1Xdnc6jEbL/note.md

退出码：0 = 没有 FAIL；1 = 有 FAIL。
"""
from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import timefmt          # 同目录：时间戳解析只此一份（与 quote_check 共用）
import store            # 同目录：JSON 原子写 / 容错读

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_V2 = 2

FAIL, WARN, PASS = "FAIL", "WARN", "OK  "
rows: list[tuple[str, str, str]] = []

JUMP = re.compile(r"\?t=\d+|&t=\d+|#t=\d+")
JUDGE = re.compile(r"怎么判断|判据|自检|做对了")
NARRATE = re.compile(r"讲了|说明了|介绍了|提到了|讲述了")
PARAM = re.compile(r"\d+(?:\.\d+)?\s?(?:px/s|px|m|°|s|秒|帧)")


def add(level: str, name: str, msg: str) -> None:
    rows.append((level, name, msg))


def parse_times(line: str) -> list[float]:
    """抓一行里的 mm:ss 或 h:mm:ss（解析在 `timefmt`，与 quote_check 共用一份）。"""
    return [float(x) for x in timefmt.parse_stamps(line)]


def china_len(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def sentences(text: str) -> list[str]:
    """切句供重复检测用：跳过列表条目与表格行——模板化条目本来就长得像。"""
    keep = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("- ") or s.startswith("* ") or s.startswith("|"):
            continue
        keep.append(s)
    parts = [p.strip() for p in re.split(r"[。！？]", " ".join(keep))]
    return [p for p in parts if len(p) >= 24]


def duplicate_pairs(text: str, threshold: float = 0.9) -> int:
    """近似重复的长句对数（列表/表格行不参与，模板化条目本来就长得像）。"""
    sents = sentences(text)
    if len(sents) > 400:
        return 0
    n = 0
    for i in range(len(sents)):
        for j in range(i + 1, len(sents)):
            if abs(len(sents[i]) - len(sents[j])) > 12:
                continue
            if SequenceMatcher(None, sents[i], sents[j]).ratio() > threshold:
                n += 1
    return n


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
    stills = research.get("stills") or []
    schema = int(research.get("note_schema") or 0)
    modern = schema >= SCHEMA_V2

    if not research:
        add(WARN, "research.json", "缺失，跳过时长与覆盖检查（先跑 pipeline/run.py）")
    add(PASS, "规则版本", f"note_schema={schema or '旧包'}（{'v2 结构底线' if modern else '沿用旧规则'}）")

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
    outside_imgs = 0
    for ln in lines:
        if ln.startswith("## "):
            in_body = ("分章" in ln or "章节" in ln) or ln.strip().startswith("## 步骤")
            cur = None
            continue
        if ln.startswith("### "):
            if re.match(r"^###\s*步骤\s*\d+", ln):
                cur = {"title": ln[4:].strip()[:30], "times": parse_times(ln),
                       "bullets": 0, "imgs": 0,
                       "jump": 1 if JUMP.search(ln) else 0,   # 标题行自带跳转也算
                       "judge": 1 if JUDGE.search(ln) else 0, "body": []}
                steps.append(cur)
            elif in_body:
                cur = {"title": ln[4:].strip(), "times": parse_times(ln), "bullets": 0}
                subs.append(cur)
            else:
                cur = None
            continue
        if ln.startswith("!["):
            if cur is not None and "imgs" in cur:
                cur["imgs"] += 1
                cur.setdefault("img_lines", []).append(ln)
            elif tutorial:
                outside_imgs += 1
            continue
        if cur is not None and re.match(r"^\s*[-*]\s+", ln):
            cur["bullets"] += 1
        if cur is not None and "body" in cur:
            cur["body"].append(ln)
            if JUMP.search(ln):
                cur["jump"] += 1
            if JUDGE.search(ln):
                cur["judge"] += 1

    if tutorial:
        no_img = [s["title"] for s in steps if s["imgs"] == 0]
        no_time = [s["title"] for s in steps if not s["times"]]
        if no_img:
            add(WARN, "步骤配图", f"{len(no_img)}/{len(steps)} 个步骤没有图：{no_img[0]}")
        else:
            add(PASS, "步骤配图", f"{len(steps)} 个步骤都有图")
        if no_time:
            add(FAIL, "步骤时间", f"{len(no_time)} 个步骤缺起始秒数：{no_time[0]}")
        else:
            add(PASS, "步骤时间", "每个步骤都标了起始时刻")
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

    # ---- 2b. v2 新检查：图的位置、图配跳转、裁切图成对、图注 ----
    if modern:
        caps = [ln.strip() for ln in lines if re.match(r"^\s*\*图[ 0-9]", ln) or "<figcaption" in ln]
        if imgs and len(caps) < len(imgs):
            add(WARN, "图注", f"{len(imgs)} 张图只有 {len(caps)} 条图注（写 `*图 N：…*`）")
        elif imgs:
            add(PASS, "图注", f"{len(caps)} 条图注对应 {len(imgs)} 张图")
        # 机械图注：正文与 Alt 完全一样，或只有"画面/截图/图片（时间）"这类占位
        alts = [a.strip() for a in re.findall(r"!\[([^\]]*)\]", text)]
        body_of = [re.sub(r"^\*图\s*\d+：", "", c).rsplit("（", 1)[0].strip() for c in caps]
        lazy = [b for b in body_of if b in alts or re.fullmatch(r"(画面|截图|图片|示意|上图|下图)", b or "")]
        if lazy:
            add(WARN, "图注质量", f"{len(lazy)} 条图注照抄 Alt 或是占位词（按内容手写）：{lazy[0][:18]}")

    if modern and tutorial:
        if outside_imgs:
            add(WARN, "图片位置", f"{outside_imgs} 张图落在步骤之外（应嵌进对应步骤）")
        else:
            add(PASS, "图片位置", "所有图都在步骤内")
        no_jump = [s["title"] for s in steps if s["imgs"] and not s["jump"]]
        if no_jump:
            add(WARN, "图配跳转", f"{len(no_jump)} 个步骤有图但没有该秒跳转链接：{no_jump[0]}")
        else:
            add(PASS, "图配跳转", "每张图所在步骤都有跳转链接")
        # 裁切图（*_zoom*.png）按需生成：用了就得和全屏图同处一步
        zoom_steps = [s for s in steps if any("_zoom" in ln for ln in s.get("img_lines", []))]
        lone_zoom = [s["title"] for s in zoom_steps
                     if not any("_zoom" not in ln for ln in s["img_lines"])]
        if lone_zoom:
            add(WARN, "裁切图成对", f"{len(lone_zoom)} 个步骤只有裁切图、没有对应全屏图：{lone_zoom[0]}")
        elif zoom_steps:
            add(PASS, "裁切图成对", f"{len(zoom_steps)} 个步骤的裁切图都有全屏图打底")
        else:
            add(PASS, "裁切图", "未使用裁切图（按需生成，不强制）")

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
        if modern:
            jump_heads = [ln for ln in lines if ln.startswith("### ") and parse_times(ln)]
            no_link = [ln for ln in jump_heads if not JUMP.search(ln)]
            outline_lines = [ln for ln in lines if re.match(r"^\s*\d+\.\s+", ln) and parse_times(ln)]
            no_link += [ln for ln in outline_lines if not JUMP.search(ln)]
            if no_link:
                add(WARN, "章节跳转", f"{len(no_link)} 处大纲/分章缺跳转链接：{no_link[0].strip()[:24]}")
            elif jump_heads or outline_lines:
                add(PASS, "章节跳转", "大纲与分章都带跳转链接")

    # ---- 4. 可回溯：引用的秒数落在时长内、且被某张图版或截图覆盖 ----
    if dur and (grids or stills):
        spans = [(g["t0"], g["t1"]) for g in grids if "t0" in g and "t1" in g]
        spans += [(s["t"] - 1.0, s["t"] + 1.0) for s in stills if "t" in s]
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
            add(WARN, "可回溯性", f"{len(uncovered)} 处秒数不在任何图版/截图覆盖内：{[int(x) for x in uncovered[:4]]}")
        else:
            add(PASS, "可回溯性", "每处引用都能对到一张图版或截图")

    # ---- 5. 首尾结构：正文内部不设模板，只要求开头讲清、结尾留底 ----
    if modern:
        missing = []
        if not re.search(r"(?m)^#\s+\S", text):
            missing.append("标题")
        if "一句话结论" not in text:
            missing.append("一句话结论")
        if not re.search(r"\*\*来源\*\*", text):
            missing.append("来源")
        if missing:
            add(WARN, "开头结构", "缺：" + "、".join(missing))
        else:
            add(PASS, "开头结构", "标题 / 一句话结论 / 来源 齐备")

    if re.search(r"^##+\s*.*置信度", text, re.M):
        seg = text.split("置信度", 1)[1]
        add(PASS, "置信度", f"有置信度小节（{china_len(seg[:600])} 字内）")
    else:
        add(FAIL, "置信度", "缺「置信度与缺口」小节——没有它，推断会被当成事实读")
    if re.search(r"^##+\s*.*(附录|取证说明)", text, re.M):
        add(PASS, "附录", "有取证说明")
    else:
        add(WARN, "附录", "缺取证说明（采样间隔/转写模型/校正记录）")

    # ---- 6. 篇幅：新包不设限，旧包沿用旧口径 ----
    body = re.split(r"^##+\s*.*(?:附录|取证说明)", text, maxsplit=1, flags=re.M)[0]
    n = china_len(body)
    label = "步骤" if tutorial else "节"
    if modern:
        dens = (len(imgs) * 1000 / n) if n else 0
        add(PASS, "篇幅", f"正文 {n} 汉字（不设上下限）；图密度 {dens:.1f} 张/千字，{units} {label}")
    else:
        cap = max(1800, 600 + 160 * max(units, 1))
        if n < 700:
            add(WARN, "篇幅", f"正文 {n} 汉字（不含附录），偏短（下限 700）")
        elif n > cap:
            add(WARN, "篇幅", f"正文 {n} 汉字，超过 {units} {label}对应的上限 {cap}")
        else:
            add(PASS, "篇幅", f"正文 {n} 汉字（{units} {label}，上限 {cap}）")

    # ---- 6b. v2 可读性：重复句 / 转述词 / 参数集中度 ----
    if modern:
        dup = duplicate_pairs(body)
        if dup:
            add(WARN, "重复句", f"{dup} 对句子相似度 >0.85（合并或删一条）")
        else:
            add(PASS, "重复句", "没有近似重复的长句")

        narr = len(NARRATE.findall(body))
        rate = narr * 1000 / max(n, 1)
        if rate > 2:
            add(WARN, "转述词", f"「讲了/说明了」类 {narr} 次，{rate:.1f} 次/千字（改写为动作）")
        else:
            add(PASS, "转述词", f"转述词 {narr} 次（{rate:.1f} 次/千字）")

        counts: dict[str, int] = {}
        for tok in PARAM.findall(body):
            key = tok.replace(" ", "")
            counts[key] = counts.get(key, 0) + 1
        hot = sorted(((c, k) for k, c in counts.items() if c > 3), reverse=True)
        if hot:
            add(WARN, "参数集中度", f"{len(hot)} 个参数在正文出现 >3 次（移到总表）：{hot[0][1]} ×{hot[0][0]}")
        else:
            add(PASS, "参数集中度", "参数没有在正文里反复堆叠")

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
