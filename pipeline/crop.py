#!/usr/bin/env python
"""候选裁切框 + 按需生成放大图。

    py -3.14 pipeline/crop.py <id> --at 326              # 只给候选框（Top 3）与区域描述
    py -3.14 pipeline/crop.py <id> --at 326 --write      # 按 Top1 生成 t00326_zoom1.png
    py -3.14 pipeline/crop.py <id> --at 326 --box 1420,60,500,360 --write   # 手动框
    py -3.14 pipeline/crop.py <id> --image assets/stills/s02_00326.png --write  # 对已有截图裁

为什么只给"哪里字多"而不判断内容：
    边缘密度分不清"代码帧/游戏帧"（D5），但"这一块文字密集"是可测的像素事实。
    框选由脚本做，裁不裁、裁哪个由写作者决定——按需生成，不默认铺满。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pydeps"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sharp  # noqa: E402
import store  # noqa: E402

NOTES = ROOT / "notes"
MIN_W, MIN_H = 60, 20.0


def _smooth(vals: list[float], win: int = 5) -> list[float]:
    if not vals:
        return vals
    half = win // 2
    out = []
    for i in range(len(vals)):
        lo, hi = max(0, i - half), min(len(vals), i + half + 1)
        out.append(sum(vals[lo:hi]) / (hi - lo))
    return out


def _runs(profile: list[float], thresh: float, min_len: int, gap: int = 3) -> list[tuple[int, int]]:
    runs, start = [], None
    for i, v in enumerate(profile):
        if v >= thresh:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(profile)))
    merged: list[tuple[int, int]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a, b) for a, b in merged if b - a >= min_len]


def profiles(img) -> tuple[list[float], list[float], int, int]:
    """行/列边缘能量（缩到 480 宽后算，纯像素，不看内容）。"""
    w, h = img.size
    tw = 480
    th = max(1, round(h * tw / w))
    small = img.convert("L").resize((tw, th))
    px = small.load()
    rows = [0.0] * th
    cols = [0.0] * tw
    for y in range(1, th):
        for x in range(1, tw):
            g = abs(px[x, y] - px[x - 1, y]) + abs(px[x, y] - px[x, y - 1])
            rows[y] += g
            cols[x] += g
    return _smooth(rows), _smooth(cols), tw, th


def region(x0: int, y0: int, x1: int, y1: int, w: int, h: int) -> str:
    cx, cy = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
    if cy < 0.18:
        return "顶部（参数条/菜单）"
    if cy > 0.78:
        return "底部（字幕带/时间轴）"
    if cx > 0.72:
        return "右侧（属性栏/大纲）"
    if cx < 0.28:
        return "左侧（工具栏/大纲）"
    return "视口中部"


def _pct(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def candidates(img, top: int = 3) -> list[dict]:
    """块状候选：在"文字密集的行带"里再找"密集的列带"，框成一块可读区域。

    只给接近阅读比例（宽高比 0.5–3.0）的块——整条 1920 宽的横条在正文里就是一根细线，
    放大也读不了；宁可选一小块（一段参数/一个面板），也不给整条。
    """
    rows, cols, tw, th = profiles(img)
    w, h = img.size
    sx, sy = w / tw, h / th

    def mk(x0: int, y0: int, x1: int, y1: int, score: float) -> dict | None:
        bw, bh = x1 - x0, y1 - y0
        if bw < 160 or bh < 40:
            return None
        # 关键不是"比例"，而是**绝对宽度**：块宽 ≤1000px，放进 860px 正文才会被放大
        if bw > 1000 or bh > 700:
            return None
        area = bw * bh / max(1, w * h)
        if area > 0.25:
            return None
        return {"box": [int(x0), int(y0), int(bw), int(bh)], "score": round(score, 1),
                "region": region(x0, y0, x1, y1, w, h), "area": round(area * 100, 1)}

    out: list[dict] = []
    all_rows = _runs(rows, _pct(rows, 0.78), 4)
    tops = [b for b in all_rows if b[0] < 0.25 * th]      # 顶部工具条/参数条
    best = sorted(all_rows, key=lambda ab: -sum(rows[ab[0]:ab[1]]))[:1]
    row_bands = []
    for b in (tops[:1] + best):
        if b not in row_bands:
            row_bands.append(b)
    win = max(8, int(950 / sx))           # 约 950px 宽的滑动窗（小图坐标）
    for ra, rb in row_bands:
        # 细横条（工具条/参数条）：整宽切三段给候选，写作者挑"哪一段"
        if (rb - ra) * sy < h * 0.15:
            starts = [0, (tw - win) // 2, tw - win]
            for x0 in starts:
                x1 = min(tw, x0 + win)
                c = mk(int(x0 * sx), int(ra * sy), int(x1 * sx), int(rb * sy),
                       sum(cols[x0:x1]) / max(1, x1 - x0))
                if c:
                    out.append(c)
            continue
        # 其余行带：在该带内找列带，再切窗
        sub = [0.0] * tw
        for x in range(tw):
            sub[x] = cols[x] * (rb - ra) / max(1, th)
        col_bands = sorted(_runs(sub, max(_pct(sub, 0.65), 1e-6), 6),
                           key=lambda ab: -sum(sub[ab[0]:ab[1]]))[:2]
        for ca, cb in col_bands:
            starts = [ca, (ca + cb - win) // 2, cb - win] if cb - ca > win else [ca]
            for x0 in starts:
                x1 = x0 + win if cb - ca > win else cb
                c = mk(int(x0 * sx), int(ra * sy), int(x1 * sx), int(rb * sy),
                       sum(sub[x0:x1]) / max(1, x1 - x0))
                if c:
                    out.append(c)

    out.sort(key=lambda c: -c["score"])
    seen, uniq = set(), []
    for c in out:
        key = (c["box"][0] // 40, c["box"][1] // 40, c["box"][2] // 40, c["box"][3] // 40)
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    # 区域多样性优先：先每个区域各给一个（底部/顶部/右侧…），再按分数补
    diverse, seen_region = [], set()
    for c in uniq:
        r = c["region"].split("（")[0]
        if r not in seen_region:
            seen_region.add(r)
            diverse.append(c)
    for c in uniq:
        if len(diverse) >= top:
            break
        if c not in diverse:
            diverse.append(c)
    return diverse[:top]


def write_zoom(src, dest: Path, box: list[int], scale: float = 2.0) -> Path:
    from PIL import Image
    img = Image.open(src)
    x, y, w, h = box
    x, y = max(0, x), max(0, y)
    w, h = min(w, img.width - x), min(h, img.height - y)
    crop = img.crop((x, y, x + w, y + h))
    # 目标：输出宽度 ≈1400（正文 860px 下仍约 1.6 倍原尺寸），高度不超过 1000
    factor = max(2.0, min(4.0, 1400 / max(1, w))) if scale == 2.0 else scale
    if crop.height * factor > 1000:
        factor = max(1.2, 1000 / crop.height)
    crop = crop.resize((int(crop.width * factor), int(crop.height * factor)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    crop.save(dest)
    return dest


def write_zoom(src, dest: Path, box: list[int], scale: float = 2.0) -> Path:
    from PIL import Image
    img = Image.open(src)
    x, y, w, h = box
    x, y = max(0, x), max(0, y)
    w, h = min(w, img.width - x), min(h, img.height - y)
    crop = img.crop((x, y, x + w, y + h))
    # 目标：放大后宽约 1600，但不小于 2 倍、不超过 4 倍
    factor = max(2.0, min(4.0, 1600 / max(1, w))) if scale == 2.0 else scale
    crop = crop.resize((int(crop.width * factor), int(crop.height * factor)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    crop.save(dest)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="候选裁切框 / 按需放大图")
    ap.add_argument("slug")
    ap.add_argument("--at", type=float, default=None, help="取该秒的全屏帧再找框")
    ap.add_argument("--image", default=None, help="直接对已有截图找框（相对笔记目录）")
    ap.add_argument("--top", type=int, default=3, help="给几个候选框")
    ap.add_argument("--box", default=None, help="手动指定 x,y,w,h（原分辨率像素）")
    ap.add_argument("--write", action="store_true", help="生成放大图到 assets/stills/")
    ap.add_argument("--scale", type=float, default=2.0)
    args = ap.parse_args()

    note = NOTES / args.slug
    if not note.exists():
        raise SystemExit(f"没有这个项目：{note}")
    research = store.load_json(note / "research.json", default={})
    dur = float((research.get("timeline") or {}).get("duration") or 0)

    if args.image:
        src = note / args.image
        if not src.exists():
            raise SystemExit(f"找不到图：{src}")
        stem = src.stem
    elif args.at is not None:
        media = next((p for p in sorted(note.glob("source.*"))
                      if p.suffix.lower() not in {".json", ".part"}), None)
        if not media:
            raise SystemExit("没有素材")
        if dur and args.at > dur:
            raise SystemExit(f"--at 超出片长 {dur:.0f}s")
        # 优先复用笔记里已有的那张全屏图（±1s），保证裁切图与正文里的图是同一帧
        reuse = None
        for p in sorted((note / "assets" / "stills").glob("*.png")):
            m = re.search(r"_(\d{5})$", p.stem)
            if m and abs(int(m.group(1)) - args.at) <= 1.0:
                reuse = p
                break
        if reuse:
            src, stem = reuse, reuse.stem
            print(f"复用已有截图 {reuse.name}（与 --at {args.at:g}s 相差 ≤1s）")
        else:
            media = next((p for p in sorted(note.glob("source.*"))
                          if p.suffix.lower() not in {".json", ".part"}), None)
            if not media:
                raise SystemExit("没有素材")
            tmp = ROOT / "work" / "tmp" / "crop"
            tmp.mkdir(parents=True, exist_ok=True)
            src = tmp / f"f{int(args.at):05d}.png"
            sharp.grab(media, args.at, src)
            stem = f"t{int(args.at):05d}"
    else:
        raise SystemExit("要给 --at <秒> 或 --image <相对路径>")

    from PIL import Image
    img = Image.open(src)
    print(f"源图 {src.relative_to(ROOT)}　{img.width}×{img.height}")

    if args.box:
        try:
            box = [int(v) for v in args.box.split(",")]
            assert len(box) == 4
        except Exception:
            raise SystemExit("--box 要写成 x,y,w,h 四个整数")
        chosen = {"box": box, "region": region(box[0], box[1], box[0] + box[2], box[1] + box[3],
                                               img.width, img.height), "score": 0.0, "area": 0.0}
        cands = [chosen]
    else:
        cands = candidates(img, args.top)
        if not cands:
            print("没找到明显的高密度块（可能是纯画面/纯色）；用 --box 手动指定")
            return 1
        for i, c in enumerate(cands, 1):
            x, y, w, h = c["box"]
            print(f"  候选 {i}　x={x} y={y} w={w} h={h}　{c['region']}　"
                  f"占画面 {c['area']}%　密度 {c['score']}")
        chosen = cands[0]

    if not args.write:
        print("\n（未生成，加 --write 按候选 1 出图；或用 --box 指定别的区域）")
        return 0

    dest = note / "assets" / "stills" / f"{stem}_zoom1.png"
    write_zoom(src, dest, chosen["box"], args.scale)
    rel = dest.relative_to(note).as_posix()
    print(f"\n已生成 {rel}　（{chosen['region']}，框 {chosen['box']}）")
    print(f"引用写法：![{chosen['region']}]({rel})")
    print("提示：裁切图要和它的全屏图放在同一步骤里，图注写清是哪一块。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
