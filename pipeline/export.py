#!/usr/bin/env python
"""把一篇笔记导出成「一个文件夹，三种形态」。

输出结构（文件夹名 = 笔记标题）：

    exports/
      Lexispell-UI动画/
        Lexispell-UI动画.md     单文件 md，图片内嵌，发一个文件就能看
        Lexispell-UI动画.html   单文件 html，浏览器打开，可打印成 PDF
        Lexispell-UI动画.zip    md + 图片文件夹，解压后相对路径可用

默认就把三种都出，不要选。默认把图压成 webp（体积小一个数量级），
要无损原图加 --no-compact。

一般不用直接调它——走 `pipeline/finish.py <id>`，会先验收再导出。
"""
from __future__ import annotations

import argparse
import base64
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pydeps"))

IMG = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
ILLEGAL = re.compile(r'[\\/:*?"<>|\n\r\t]')


def find_note(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        p = p / "note.md"
    elif not p.exists():
        p = ROOT / "notes" / arg / "note.md"
    if not p.exists():
        raise SystemExit(f"找不到笔记：{arg}")
    return p


def title_of(note: Path, limit: int = 48) -> str:
    """取一级标题做文件夹名；去掉文件系统非法字符，过长截断。"""
    for ln in note.read_text(encoding="utf-8").splitlines():
        if ln.startswith("# "):
            t = ILLEGAL.sub(" ", ln[2:].strip())
            t = re.sub(r"\s+", " ", t).strip().strip(".")
            return t[:limit].rstrip() or note.parent.name
    return note.parent.name


def referenced_images(text: str, note_dir: Path) -> list[tuple[str, Path]]:
    out, seen = [], set()
    for ref in IMG.findall(text):
        if ref in seen or re.match(r"^(?:[a-z]+:|/)", ref):
            continue
        seen.add(ref)
        f = note_dir / ref
        if f.exists():
            out.append((ref, f))
    return out


def compact_bytes(f: Path, max_width: int = 1600, quality: int = 88) -> tuple[bytes, str]:
    """截图压成 webp：q88 + 限宽，体积通常小一个数量级，字仍清晰。"""
    import io

    from PIL import Image  # type: ignore

    with Image.open(f) as im:
        im = im.convert("RGBA") if im.mode in ("P", "LA") else im.convert("RGB")
        if im.width > max_width:
            im = im.resize((max_width, round(im.height * max_width / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=quality, method=4)
    return buf.getvalue(), "image/webp"


def image_bytes(f: Path, compact: bool) -> tuple[bytes, str]:
    if compact:
        return compact_bytes(f)
    mime = "image/png" if f.suffix.lower() == ".png" else f"image/{f.suffix.lstrip('.').lower()}"
    return f.read_bytes(), mime


def inline(text: str, imgs, compact: bool) -> str:
    for ref, f in imgs:
        data, mime = image_bytes(f, compact)
        text = text.replace(f"]({ref})", f"](data:{mime};base64,{base64.b64encode(data).decode()})")
    return text


def write_md(note: Path, imgs, dest: Path, compact: bool) -> Path:
    dest.write_text(inline(note.read_text(encoding="utf-8"), imgs, compact), encoding="utf-8")
    return dest


def write_html(note: Path, imgs, dest: Path, compact: bool) -> Path:
    try:
        import markdown  # type: ignore
    except ImportError:
        raise SystemExit("html 需要 python-markdown：py -3.14 scripts/install_pydeps.py markdown")
    body = markdown.markdown(inline(note.read_text(encoding="utf-8"), imgs, compact),
                             extensions=["tables", "fenced_code", "toc"])
    lightbox = (
        "<div id='lb'><img id='lbimg' alt=''></div>"
        "<script>(function(){"
        "var lb=document.getElementById('lb'),im=document.getElementById('lbimg');"
        "var s=1,tx=0,ty=0,drag=false,moved=false,px=0,py=0,ox=0,oy=0;"
        "function paint(){im.style.transform='translate('+tx+'px,'+ty+'px) scale('+s+')';}"
        "function reset(){s=1;tx=0;ty=0;paint();}"
        "function open(src){im.src=src;reset();lb.classList.add('on');"
        "document.documentElement.style.overflow='hidden';}"
        "function close(){lb.classList.remove('on');document.documentElement.style.overflow='';}"
        "document.querySelectorAll('body img').forEach(function(el){"
        "if(el.id==='lbimg')return;el.style.cursor='zoom-in';"
        "el.addEventListener('click',function(){open(el.src)})});"
        "lb.addEventListener('click',function(){if(!moved)close();moved=false;});"
        "lb.addEventListener('wheel',function(e){e.preventDefault();"
        "var k=e.deltaY<0?1.15:1/1.15,n=Math.min(8,Math.max(1,s*k));"
        "if(n!==s){s=n;paint();}},{passive:false});"
        "lb.addEventListener('pointerdown',function(e){drag=true;moved=false;"
        "px=e.clientX;py=e.clientY;ox=tx;oy=ty;});"
        "lb.addEventListener('pointermove',function(e){if(!drag)return;"
        "var dx=e.clientX-px,dy=e.clientY-py;if(Math.abs(dx)+Math.abs(dy)>4)moved=true;"
        "tx=ox+dx;ty=oy+dy;paint();});"
        "lb.addEventListener('pointerup',function(){drag=false;});"
        "document.addEventListener('keydown',function(e){if(e.key==='Escape')close()});"
        "})()</script>")
    dest.write_text(
        "<!doctype html><meta charset='utf-8'><title>" + title_of(note) + "</title><style>"
        "body{max-width:860px;margin:40px auto;padding:0 20px;"
        "font:16px/1.75 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:#222}"
        "img{max-width:100%;border:1px solid #e3e3e3;border-radius:6px;margin:10px 0;display:block}"
        "#lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.86);z-index:99;"
        "align-items:center;justify-content:center;padding:24px;cursor:zoom-out;"
        "overflow:hidden;touch-action:none}"
        "#lb.on{display:flex}"
        "#lb img{max-width:96vw;max-height:92vh;width:auto;border:none;border-radius:4px;"
        "background:#fff;box-shadow:0 10px 40px rgba(0,0,0,.6);"
        "transform-origin:center center;will-change:transform;user-select:none}"
        "p>em:only-child{display:block;font-size:.86em;color:#666;"
        "text-align:center;margin:-4px 0 16px}"
        "code{background:#f5f5f5;padding:2px 5px;border-radius:3px;font-size:.9em}"
        "pre{background:#f7f7f7;padding:12px;border-radius:6px;overflow:auto}"
        "pre code{background:none;padding:0}"
        "table{border-collapse:collapse;width:100%;margin:12px 0}"
        "th,td{border:1px solid #ddd;padding:6px 10px;text-align:left}"
        "blockquote{border-left:3px solid #d0d0d0;margin:12px 0;padding:2px 14px;color:#555}"
        "h1{border-bottom:1px solid #eee;padding-bottom:8px}"
        "</style>" + body + lightbox, encoding="utf-8")
    return dest


def write_zip(note: Path, note_dir: Path, imgs, dest: Path) -> Path:
    """md + 图片按原相对路径打包，解压后相对路径直接可用（不压图，保留原始质量）。"""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("note.md", note.read_text(encoding="utf-8"))
        for ref, f in imgs:
            z.write(f, ref)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="导出笔记：一个文件夹，三种形态")
    ap.add_argument("note", help="笔记 id 或 note.md 路径")
    ap.add_argument("--out", default=None, help="导出根目录（默认 <项目>/exports）")
    ap.add_argument("--no-compact", action="store_true",
                    help="保留原始 PNG（默认把图压成 webp，体积小一个数量级）")
    args = ap.parse_args()

    note = find_note(args.note)
    note_dir = note.parent
    text = note.read_text(encoding="utf-8")
    imgs = referenced_images(text, note_dir)
    if not imgs:
        print(f"提醒：{note_dir.name} 的笔记没有引用任何图片。", file=sys.stderr)

    title = title_of(note)
    outdir = (Path(args.out) if args.out else ROOT / "exports") / title
    outdir.mkdir(parents=True, exist_ok=True)
    compact = not args.no_compact

    md = write_md(note, imgs, outdir / f"{title}.md", compact)
    html = write_html(note, imgs, outdir / f"{title}.html", compact)
    zp = write_zip(note, note_dir, imgs, outdir / f"{title}.zip")

    rel = lambda p: p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p)
    print(f"导出：{rel(outdir)}/   （{len(imgs)} 张图，{'webp 压缩' if compact else '原始 PNG'}）")
    for p, why in ((md, "单文件 md，图片内嵌"), (html, "单文件 html，可打印 PDF"),
                   (zp, "md + 图片文件夹，解压即用")):
        print(f"  {p.stat().st_size / 1e6:>6.2f} MB  {p.name:<24} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
