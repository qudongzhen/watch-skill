#!/usr/bin/env python
"""一次性环境引导：把 video-watch 仓库、ffmpeg、ImageMagick 落到本项目 vendor/ 与 tools/。

特点：
- 只用标准库（zip）；.7z 优先用系统 tar(bsdtar)，否则退回 py7zr。
- 全程走 Python 自己的 OpenSSL，绕开 Windows schannel 在受限沙箱里拿不到证书的问题。
- 幂等：目标已存在则跳过，--force 可强制重下。

用法：
    python scripts/bootstrap.py            # 全量
    python scripts/bootstrap.py --only ffmpeg
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request as urlreq
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "Mozilla/5.0 (watch-notes bootstrap)"}

REPO_ZIP = "https://codeload.github.com/CFITCorporation/video-watch-skill/zip/refs/heads/main"
FFMPEG_ZIP = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
# ImageMagick 便携版只有 .7z；版本号由 GitHub API 动态取，避免写死
IM_API = "https://api.github.com/repos/ImageMagick/ImageMagick/releases/latest"


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urlreq.Request(url, headers=UA)
    with urlreq.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, t0, last = 0, time.time(), 0.0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if time.time() - last > 2:
                last = time.time()
                pct = f"{done * 100 / total:.0f}%" if total else "?"
                log(f"  {dest.name}: {human(done)} / {human(total)} ({pct}) {human(done / max(time.time() - t0, 1e-6))}/s")
    tmp.replace(dest)
    return dest


def extract_7z(archive: Path, dest: Path) -> None:
    """优先系统 tar(bsdtar 支持 7z)，退回 py7zr。"""
    dest.mkdir(parents=True, exist_ok=True)
    tar = shutil.which("tar")
    if tar:
        rc = subprocess.run([tar, "-xf", str(archive), "-C", str(dest)],
                            capture_output=True, text=True)
        if rc.returncode == 0:
            return
        log(f"  tar 解压失败，改用 py7zr: {rc.stderr.strip()[:120]}")
    try:
        import py7zr  # type: ignore
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "py7zr"], check=True)
        import py7zr  # type: ignore
    with py7zr.SevenZipFile(archive) as z:
        z.extractall(dest)


def flatten_single_root(dest: Path) -> Path:
    """压缩包常有一个顶层目录；把它里面的东西提上来，方便固定路径。"""
    entries = [p for p in dest.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for child in inner.iterdir():
            shutil.move(str(child), str(dest / child.name))
        inner.rmdir()
        return dest
    return dest


def setup_repo(force: bool) -> None:
    target = ROOT / "vendor" / "video-watch"
    if (target / "vw.py").exists() and not force:
        log(f"仓库已存在，跳过：{target}")
        return
    log("下载 video-watch 仓库…")
    zpath = download(REPO_ZIP, ROOT / "vendor" / "_video-watch.zip")
    if target.exists():
        shutil.rmtree(target)
    tmp = ROOT / "vendor" / "_extract"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(tmp)
    flatten_single_root(tmp)
    shutil.move(str(tmp), str(target))
    zpath.unlink()
    log(f"完成：{target}")


def setup_ffmpeg(force: bool) -> None:
    exe = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if exe.exists() and not force:
        log(f"ffmpeg 已存在，跳过：{exe}")
        return
    log("下载 ffmpeg（约 115MB）…")
    zpath = download(FFMPEG_ZIP, ROOT / "tools" / "_ffmpeg.zip")
    dest = ROOT / "tools" / "ffmpeg"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(dest)
    flatten_single_root(dest)
    zpath.unlink()
    log(f"完成：{exe}")


def setup_imagemagick(force: bool) -> None:
    magick = ROOT / "tools" / "imagemagick" / "magick.exe"
    if magick.exists() and not force:
        log(f"ImageMagick 已存在，跳过：{magick}")
        return
    log("查询 ImageMagick 最新便携版…")
    rel = json.load(urlreq.urlopen(urlreq.Request(IM_API, headers=UA), timeout=30))
    asset = next(a for a in rel["assets"]
                 if "portable-Q16-x64.7z" in a["name"] and "HDRI" not in a["name"])
    log(f"  {asset['name']} ({human(asset['size'])})")
    apath = download(asset["browser_download_url"], ROOT / "tools" / "_imagemagick.7z")
    dest = ROOT / "tools" / "imagemagick"
    if dest.exists():
        shutil.rmtree(dest)
    extract_7z(apath, dest)
    flatten_single_root(dest)
    apath.unlink()
    log(f"完成：{magick}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["repo", "ffmpeg", "imagemagick"], action="append")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    targets = args.only or ["repo", "ffmpeg", "imagemagick"]
    if "repo" in targets:
        setup_repo(args.force)
    if "ffmpeg" in targets:
        setup_ffmpeg(args.force)
    if "imagemagick" in targets:
        setup_imagemagick(args.force)
    log("全部完成。下一步：py -3.14 pipeline/doctor.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
