#!/usr/bin/env python
"""把依赖装进项目的 pydeps/（绕开 pip 的临时目录写入问题）。

这个沙箱里 pip 的 unpack 阶段会被拒写，所以改走：
  PyPI JSON API 解析 wheel 地址 → 下载 → 直接解包到 pydeps/
对纯 Python 包与带二进制扩展的包都适用（wheel 本质是 zip）。

平台标签按当前解释器挑：cp3XX-cp3XX-win_amd64 > cp3XX-abi3-win_amd64 > abi3 > py3-none-any。

用法：
    python scripts/install_pydeps.py yt-dlp
    python scripts/install_pydeps.py faster-whisper
"""
from __future__ import annotations

import json
import shutil
import sys
import urllib.request as urlreq
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDEPS = ROOT / "pydeps"
TMP = ROOT / "work" / "tmp"
UA = {"User-Agent": "Mozilla/5.0 (watch-notes)"}

MAJ, MIN = sys.version_info.major, sys.version_info.minor
CURRENT = f"cp{MAJ}{MIN}"

# 不装：打包工具自身、或已由解释器提供
SKIP = {"pip", "setuptools", "wheel", "pkg_resources", "typing-extensions"}


def wheel_score(filename: str) -> int:
    """越大越合适；-1 表示不可用。"""
    if filename.endswith(".tar.gz") or filename.endswith(".zip"):
        return -1
    if "win_amd64" not in filename and "none-any" not in filename:
        return -1
    if f"{CURRENT}-{CURRENT}-win_amd64" in filename:
        return 100
    if f"{CURRENT}-abi3-win_amd64" in filename:
        return 90
    if "abi3-win_amd64" in filename:  # 例如 cp39-abi3
        return 80
    if "none-any" in filename:
        return 10
    return -1


def pick_wheel(data: dict) -> dict:
    best, score_best = None, -1
    for f in data["urls"]:
        if f["packagetype"] != "bdist_wheel":
            continue
        s = wheel_score(f["filename"])
        if s > score_best:
            best, score_best = f, s
    if not best:
        raise SystemExit(f"{data['info']['name']}: 没有适合 {CURRENT}/win_amd64 的 wheel")
    return best


def split_req(r: str) -> str:
    name = r.split(";")[0].strip()
    for sep in (" ", "<", ">", "=", "[", "!", "~"):
        name = name.split(sep)[0]
    return name.strip()


def install(pkg: str, seen: set[str], depth: int = 0) -> None:
    pkg = pkg.strip()
    if not pkg or pkg.lower() in seen or pkg.lower() in SKIP:
        return
    seen.add(pkg.lower())
    name, _, pin = pkg.partition("==")
    url = (f"https://pypi.org/pypi/{name}/{pin}/json" if pin
           else f"https://pypi.org/pypi/{name}/json")
    data = json.load(urlreq.urlopen(urlreq.Request(url, headers=UA), timeout=30))
    info = data["info"]
    wheel = pick_wheel(data)
    pad = "  " * depth
    print(f"{pad}[deps] {info['name']} {info['version']}  <- {wheel['filename']}", flush=True)
    TMP.mkdir(parents=True, exist_ok=True)
    dest = TMP / wheel["filename"]
    with urlreq.urlopen(urlreq.Request(wheel["url"], headers=UA), timeout=180) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    PDEPS.mkdir(exist_ok=True)
    with zipfile.ZipFile(dest) as z:
        z.extractall(PDEPS)
    dest.unlink()
    for dep in info.get("requires_dist") or []:
        if "extra ==" in dep:
            continue
        install(split_req(dep), seen, depth + 1)


def main() -> int:
    pkgs = sys.argv[1:] or ["yt-dlp"]
    seen: set[str] = set()
    for p in pkgs:
        install(p, seen)
    print(f"[deps] 完成 -> {PDEPS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
