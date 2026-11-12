#!/usr/bin/env python
"""事件驱动取图 + v2 验收规则的自检。

    py -3.14 pipeline/test_notes.py

覆盖两件事：
1. `run.pick_stills` 的三道闸（5s 去重 / 每分钟 ≤3 / 预算封顶）与 `run.jump_url` 的分平台规则；
2. `note_audit` 对 `note_schema >= 2` 的新检查（要点 ≥3 升 FAIL、图配跳转、可读性），
   以及旧包不受新规则影响。

纯函数级：不跑 ffmpeg，不联网；审计部分用临时目录 + 子进程。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "pydeps"))

import run  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILED.append(name)


def tmpdir(tag: str) -> Path:
    d = ROOT / "work" / "tmp" / f"test_notes_{tag}"
    shutil.rmtree(d, ignore_errors=True)
    (d / "assets" / "stills").mkdir(parents=True)
    return d


# ---------------------------------------------------------------- 取图


def timeline(dur: float, cuts: list[float], freezes: list[float] | None = None) -> dict:
    return {"duration": dur, "cuts": cuts, "freezes": freezes or [], "silences": []}


def sections(dur: float, n: int) -> list[dict]:
    step = dur / n
    return [{"idx": i + 1, "t0": i * step, "t1": (i + 1) * step} for i in range(n)]


def test_dedupe() -> None:
    # 切点每 1 秒一个：去重后相邻间隔必须 ≥5 秒
    tl = timeline(60, [float(i) for i in range(0, 60)])
    picks = run.pick_stills(tl, sections(60, 3), None, 0, "tutorial")
    ts = sorted(p["t"] for p in picks)
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    check("去重：相邻 ≥5s", all(g >= 4.99 for g in gaps), f"min={min(gaps) if gaps else 'NA'}")
    check("去重：没有选到 0 秒以外", all(0 <= t <= 60 for t in ts))


def test_per_minute() -> None:
    tl = timeline(180, [float(i) for i in range(0, 180)])
    picks = run.pick_stills(tl, sections(180, 6), None, 0, "tutorial")
    ts = [p["t"] for p in picks]
    worst = max(sum(1 for t in ts if a <= t < a + 60) for a in ts) if ts else 0
    check("每分钟 ≤3 张", worst <= 3, f"worst={worst}")


def test_budget() -> None:
    tl = timeline(600, [float(i) for i in range(0, 600, 8)])
    secs = sections(600, 6)
    capped = run.pick_stills(tl, secs, None, 10, "tutorial")
    free = run.pick_stills(tl, secs, None, 0, "tutorial")
    check("预算封顶生效", len(capped) == 10, f"len={len(capped)}")
    check("预算 0 = 不截断", len(free) >= len(capped), f"{len(free)} vs {len(capped)}")


def test_priority() -> None:
    # 同一时刻既有冻结（prio 4）又有切点（prio 1），应保留冻结那条理由
    tl = timeline(100, [10.0], freezes=[9.7])
    picks = run.pick_stills(tl, sections(100, 2), None, 0, "tutorial")
    reasons = [p["reason"] for p in picks]
    check("优先级：冻结优先于切点", any("冻结" in r for r in reasons), str(reasons))


def test_focus() -> None:
    tl = timeline(100, [], [])
    picks = run.pick_stills(tl, sections(100, 2), None, 0, "tutorial", focus=[42.0])
    check("--focus 必定入选", any(abs(p["t"] - 42.0) < 0.01 for p in picks),
          str([p["t"] for p in picks]))


def test_jump_url() -> None:
    bili = run.jump_url("https://www.bilibili.com/video/BV1xx/?p=2", 95)
    check("B 站：保留 p 并追加 t", bili.endswith("&t=95"), bili)
    yt = run.jump_url("https://www.youtube.com/watch?v=abc", 95)
    check("YouTube：&t=秒", yt.endswith("&t=95"), yt)
    other = run.jump_url("https://example.com/v/1", 95)
    check("其他站点：#t=秒", other.endswith("#t=95"), other)
    check("空 URL 不出链接", run.jump_url("", 95) == "")


# ---------------------------------------------------------------- 验收


NOTE_V2 = """# 测试笔记

## 步骤

### 步骤 1｜00:10 做一件事 [00:10](https://example.com/v?t=10)

![图](assets/stills/a.png)

- 怎么做：把参数设成 0.05。
- 怎么判断做对了：面板里显示 0.05。
- 坑：别设成 0.5。

### 步骤 2｜00:20 再一件事 [00:20](https://example.com/v?t=20)

![图](assets/stills/a.png)

- 只有两条要点，应触发 FAIL。
- 再说一次 0.05。

## 置信度与缺口

- 高：测试。

## 附录：取证说明

- 采样：测试。
"""

NOTE_OLD = NOTE_V2.replace(" [00:10](https://example.com/v?t=10)", "") \
                 .replace(" [00:20](https://example.com/v?t=20)", "")


def audit(dirpath: Path, note: str, schema: int | None) -> tuple[int, str]:
    (dirpath / "note.md").write_text(note, encoding="utf-8")
    (dirpath / "assets" / "stills" / "a.png").write_bytes(b"\x89PNG" + b"0" * 2048)
    payload = {"timeline": {"duration": 60}, "images": [], "stills": [{"t": 10.0}, {"t": 20.0}]}
    if schema is not None:
        payload["note_schema"] = schema
    (dirpath / "research.json").write_text(json.dumps(payload), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "pipeline" / "note_audit.py"), str(dirpath)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_audit_v2() -> None:
    d = tmpdir("v2")
    code, out = audit(d, NOTE_V2, 2)
    check("v2：两条要点不再判 FAIL（模板已取消）", code == 0, f"exit={code}")
    check("v2：图配跳转不报（标题行有链接）", "图配跳转" in out and "每张图所在步骤都有跳转" in out)
    shutil.rmtree(d, ignore_errors=True)


def test_audit_structure() -> None:
    d = tmpdir("struct")
    thin = NOTE_V2.replace("> 一句话结论", "> 结论")
    code, out = audit(d, thin, 2)
    check("开头结构：缺一句话结论 → WARN", "开头结构" in out and "一句话结论" in out, f"exit={code}")
    shutil.rmtree(d, ignore_errors=True)


def test_audit_zoom() -> None:
    d = tmpdir("zoom")
    note = NOTE_V2.replace(
        "![图](assets/stills/a.png)\n\n- 怎么做：把参数设成 0.05。",
        "![图](assets/stills/a.png)\n![放大](assets/stills/a_zoom1.png)\n\n- 怎么做：把参数设成 0.05。")
    (d / "assets" / "stills" / "a_zoom1.png").write_bytes(b"\x89PNG" + b"1" * 2048)
    code, out = audit(d, note, 2)
    check("裁切图成对：全屏 + 放大 → PASS", "裁切图成对" in out and "全屏图打底" in out)
    lone = note.replace("![图](assets/stills/a.png)\n", "")
    code2, out2 = audit(d, lone, 2)
    check("裁切图成对：只剩放大 → WARN", "只有裁切图" in out2)
    shutil.rmtree(d, ignore_errors=True)


def test_audit_old() -> None:
    d = tmpdir("old")
    code, out = audit(d, NOTE_OLD, None)
    check("旧包：两条要点只 WARN 不 FAIL", code == 0, f"exit={code}")
    check("旧包：不跑图配跳转检查", "图配跳转" not in out)
    shutil.rmtree(d, ignore_errors=True)


def test_audit_duplicate() -> None:
    import note_audit as na
    one = "这一句足够长用来测试重复检测是否生效，长度超过阈值。"
    check("重复检测：同一句出现两次 → 1 对", na.duplicate_pairs(f"{one}\n\n{one}") == 1)
    check("重复检测：不同的长句 → 0 对",
          na.duplicate_pairs("第一句完全不同的话，用来做对照测试，长度也够长。\n\n"
                             "第二句讲的完全是另一件事，内容没有任何重叠。") == 0)
    check("重复检测：列表条目不算重复",
          na.duplicate_pairs("- 这一句足够长用来测试重复检测是否生效，长度超过阈值。\n"
                             "- 这一句足够长用来测试重复检测是否生效，长度超过阈值。") == 0)


def main() -> int:
    print("取图（事件驱动 + 三道闸）")
    for fn in (test_dedupe, test_per_minute, test_budget, test_priority, test_focus, test_jump_url):
        fn()
    print("\n验收（note_schema 分档）")
    for fn in (test_audit_v2, test_audit_structure, test_audit_zoom, test_audit_old, test_audit_duplicate):
        fn()
    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
