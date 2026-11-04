#!/usr/bin/env python
"""`run.sampling_note` / `run.meta_diff` 的自检：取证说明与重跑差异摘要。

    py -3.14 pipeline/test_meta.py

两个函数都是纯函数，不碰 ffmpeg / 网络。
"""
from __future__ import annotations

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


def tmpdir() -> Path:
    import shutil
    d = ROOT / "work" / "tmp" / "test_meta"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def img(kind: str, cols: int, rows: int, t0: float, t1: float, n: int) -> dict:
    return {"kind": kind, "cols": cols, "rows": rows, "t0": t0, "t1": t1,
            "tiles": [{"index": i + 1, "t": t0 + i} for i in range(n)]}


def test_sampling_note() -> None:
    images = [img("全片粗看 5×5", 5, 5, 0, 600, 25),
              img("第1段 4×4", 4, 4, 0, 192, 16),
              img("第2段 4×4", 4, 4, 192, 384, 16)]
    sections = [{"idx": 1, "t0": 0, "t1": 192, "cells": 16},
                {"idx": 2, "t0": 192, "t1": 384, "cells": 16}]
    stills = [{"pick": {"mode": "sharp"}}, {"pick": {"mode": "exact"}}]
    lines = run.sampling_note(images, sections, [{"start": 0, "end": 1}], stills)
    text = "\n".join(lines)
    check("有可直接抄的标题", "直接抄进附录" in text, lines[0][:30])
    check("全片图版写清张数与帧数", "5×5 全片图版 1 张（25 帧" in text)
    check("分段图版写了形状", "2 张（4×4）" in text, text.splitlines()[2][:40])
    check("段内间隔按 cells-1 算（192/15=12.8）", "12.8–12.8 秒" in text,
          [x for x in text.splitlines() if "间隔" in x])
    check("截图写了数量与选帧方式", "定点截图：2 张（其中 1 张是多候选取最清晰）" in text)
    check("转写写了段数", "转写：faster-whisper，1 段" in text)

    empty = "\n".join(run.sampling_note([], [], None, None))
    check("全空不崩且不写空条目", "全片图版" not in empty and "定点截图" not in empty)

    old_style = "\n".join(run.sampling_note(images, [{"idx": 1, "t0": 0, "t1": 192}], None, None))
    check("旧 sections 没有 cells 时按 9 格算（192/8=24.0）", "24.0–24.0 秒" in old_style,
          [x for x in old_style.splitlines() if "间隔" in x])


def test_meta_diff() -> None:
    base = {"sections": [{"t0": 0, "t1": 192}] * 8,
            "images": [img("a", 4, 4, 0, 10, 15), img("b", 4, 4, 10, 20, 16)],
            "stills": [1, 2, 3], "asr": [1, 2]}
    check("完全相同 → 无结构性变化", run.meta_diff(base, base) == "无结构性变化",
          run.meta_diff(base, base))

    fewer = {**base, "sections": base["sections"][:7]}
    check("段数变化被点出", "分段 8→7" in run.meta_diff(base, fewer), run.meta_diff(base, fewer))

    wider = {**base, "sections": [{"t0": 0, "t1": 300}] * 8}
    d = run.meta_diff(base, wider)
    check("段长变化被点出", "192–192s→300–300s" in d, d)
    check("段数没变时不重复报段数", "分段 8→8（" in d, d)

    more_frames = {**base, "images": base["images"] + [img("c", 4, 4, 20, 30, 16)]}
    d = run.meta_diff(base, more_frames)
    check("图版张数与帧数变化被点出", "图版 2→3 张" in d and "帧 31→47" in d, d)

    no_asr = {**base, "asr": [1, 2, 3]}
    check("转写段数变化被点出", "转写 2→3 段" in run.meta_diff(base, no_asr))

    check("旧文件为空时不崩", run.meta_diff({}, base) != "")
    check("新文件为空时不崩", run.meta_diff(base, {}) != "")


def test_guards() -> None:
    """两条"重跑安全"守卫：静音段计数、截图不被静默覆盖。"""
    event_stream = [{"kind": "start", "t": 1.0}, {"kind": "end", "t": 2.0},
                    {"kind": "start", "t": 3.0}, {"kind": "end", "t": 4.0}]
    check("静音段按区间数而不是事件数",
          run.silence_count({"silences": event_stream}) == 2,
          str(run.silence_count({"silences": event_stream})))
    check("没有静音时是 0", run.silence_count({}) == 0)
    check("重叠静音会被合并",
          run.silence_count({"silences": [{"kind": "start", "t": 1.0}, {"kind": "end", "t": 3.0},
                                          {"kind": "start", "t": 2.0}, {"kind": "end", "t": 4.0}]}) == 1)

    note = tmpdir()
    still = note / "assets" / "stills" / "pick_00012.png"
    still.parent.mkdir(parents=True)
    still.write_bytes(b"\x89PNG\r\n\x1a\n")
    (note / "note.md").write_text("![引文](assets/stills/pick_00012.png)\n", encoding="utf-8")
    refs = run.note_image_refs(note)
    check("引用路径按笔记目录取", refs == {"assets/stills/pick_00012.png"}, str(refs))
    check("被引用的图默认不覆盖", run.should_keep_image(still, note, refs, False))
    check("加 --refresh-stills 就覆盖", not run.should_keep_image(still, note, refs, True))
    other = still.parent / "pick_00099.png"
    other.write_bytes(b"\x89PNG\r\n\x1a\n")
    check("没被引用的图照常覆盖", not run.should_keep_image(other, note, refs, False))
    check("文件不存在不算保留",
          not run.should_keep_image(still.parent / "nope.png", note, refs, False))

    # 图版：与截图同一套判据，但"保留"时新图要落到 next/
    grid_dir = note / "assets" / "grids" / "s04"
    grid_dir.mkdir(parents=True)
    old_grid = grid_dir / "grid.png"
    old_grid.write_bytes(b"\x89PNG\r\n\x1a\nold")
    (grid_dir / "manifest.json").write_text(
        '{"cols": 3, "rows": 3, "t0": 10.0, "t1": 40.0, "tiles": [{"index": 1, "row": 1, '
        '"col": 1, "t": 10.0, "frame": 0}]}', encoding="utf-8")
    refs2 = run.note_image_refs(note) | {"assets/grids/s04/grid.png"}
    target, kept = run.decide_grid_target(grid_dir, note, refs2, False)
    check("被引用的图版：落 next/ 且标记保留", kept and target == grid_dir / "next", str(target))
    target2, kept2 = run.decide_grid_target(grid_dir, note, refs2, True)
    check("加 --refresh-stills：图版照常覆盖", not kept2 and target2 == grid_dir, str(target2))
    target3, kept3 = run.decide_grid_target(grid_dir, note, set(), False)
    check("没被引用的图版：照常覆盖", not kept3 and target3 == grid_dir, str(target3))

    new_manifest = {"cols": 4, "rows": 4, "t0": 10.0, "t1": 40.0,
                    "tiles": [{"index": i, "row": 1, "col": 1, "t": 10.0 + i, "frame": i}
                              for i in range(16)]}
    rec = run.record_grid(new_manifest, grid_dir, True, note)
    check("保留时记旧 manifest（3×3 而不是新的 4×4）", rec["cols"] == 3 and rec.get("kept") is True,
          str({k: rec[k] for k in ("cols", "kept", "regenerated") if k in rec}))
    check("保留时记下新图位置", rec.get("regenerated", "").endswith("next/grid.png"),
          rec.get("regenerated", ""))
    fresh = run.record_grid(new_manifest, grid_dir, False, note)
    check("不保留时原样记新的", fresh["cols"] == 4 and "kept" not in fresh)

    # slim() 是 research.json / BRIEF 的唯一出口，不能把 kept 丢掉
    thin = run.slim(rec, "第4段 3×3", grid_dir / "grid.png")
    check("slim 保留 kept/regenerated",
          thin.get("kept") is True and thin.get("regenerated", "").endswith("next/grid.png"),
          str({k: thin[k] for k in ("kept", "regenerated") if k in thin}))
    check("没保留时 slim 不带 kept", "kept" not in run.slim(new_manifest, "x", grid_dir / "grid.png"))

    # 成本参照：只比相对开销，别把绝对值当阈值
    grid_tk = run.image_tokens(765, 440)
    still_tk = run.image_tokens(1920, 1080)
    check("图版折算 ≈222 token", grid_tk == 222, str(grid_tk))
    check("全屏截图折算 ≈1224 token", still_tk == 1224, str(still_tk))
    check("全屏 ≈ 5.5 张图版", abs(still_tk / grid_tk - 5.5) < 0.1, f"{still_tk / grid_tk:.2f}")
    check("没有图版时不出成本行", run.token_hint([]) == "")
    hint = run.token_hint([{"image": "grid.png"}])
    check("成本行同时给出两边数字", "222" in hint and "1224" in hint, hint)

    # 转写复用：成品笔记存在时，重跑不该换掉它的引文证据
    check("没有 note.md 时不复用", run.previous_research(note, False) == {})
    (note / "research.json").write_text('{"asr": [{"start": 0, "end": 1, "text": "hi"}]}',
                                        encoding="utf-8")
    check("有 note.md + research.json 时读回来",
          len(run.previous_research(note, False).get("asr") or []) == 1)
    check("--refresh-meta 时照常重跑", run.previous_research(note, True) == {})
    (note / "research.json").write_text("{ 坏 json", encoding="utf-8")
    check("research.json 坏了也不崩", run.previous_research(note, False) == {})


def main() -> int:
    print("run.sampling_note / run.meta_diff 自检\n")
    test_sampling_note()
    test_meta_diff()
    test_guards()
    print(f"\n{len(FAILED)} 项失败" if FAILED else "\n全绿")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
