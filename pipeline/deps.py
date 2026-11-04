#!/usr/bin/env python
"""依赖探测的唯一一份实现：谁必需、谁可选、缺了怎么办。

为什么要单独一个模块：`doctor.py` 与 `selfcheck.py` 曾各写一套探测，**口径还相反**——
缺 `opencc` 时 selfcheck 判 FAIL（退出码 1）、doctor 只提示（退出码 0）；PyAV ≥19 只有
selfcheck 拦。同一台机器两种结论，接手的人不知道该信谁。现在两边都只调这里。

分类口径（想改先改这里，测试会拦）：
- **必需**：缺了取证主链路直接不可用（python / ffmpeg / ImageMagick / yt-dlp / Pillow）；
- **可选**：只在某条链路要用时缺（faster-whisper+PyAV+opencc = 转写；OCR = 硬字幕对照）。
  可选项缺失只提示，不影响退出码——`--no-asr` 本来就能绕过转写。

    用法：`probe()` 拿一轮结果，`worst()` 决定退出码，`fix_hint()` 给人看的修复命令。
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
MAGICK = ROOT / "tools" / "imagemagick" / "magick.exe"
OCR_PS1 = ROOT / "pipeline" / "ocr_win.ps1"

# 必需项：名字必须与下方 probe() 里产出的 name 一致（测试会核对集合相等）
REQUIRED = ("python", "ffmpeg 滤镜", "ImageMagick", "yt-dlp", "Pillow（选帧）")
# 可选项：缺了只是某条链路不可用
OPTIONAL = ("faster-whisper", "PyAV", "opencc 繁转简", "OCR（硬字幕）")

REQUIRED_FILTERS = ("scdet", "freezedetect", "silencedetect", "tblend", "signalstats",
                    "tile", "drawtext")

FIX = {
    "ffmpeg": "py -3.14 scripts/bootstrap.py --only ffmpeg",
    "ImageMagick": "py -3.14 scripts/bootstrap.py --only imagemagick",
    "yt-dlp": "py -3.14 scripts/install_pydeps.py yt-dlp",
    "faster-whisper": "py -3.14 scripts/install_pydeps.py faster-whisper",
    "PyAV": 'py -3.14 scripts/install_pydeps.py "av==18.1.0"',
    "opencc 繁转简": "py -3.14 scripts/install_pydeps.py opencc-python-reimplemented",
    "Pillow（选帧）": "py -3.14 scripts/install_pydeps.py Pillow",
    "OCR（硬字幕）": "py -3.14 scripts/install_pydeps.py rapidocr-onnxruntime",
}


def _item(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail,
            "required": name in REQUIRED,
            "fix": "" if ok else FIX.get(name, "")}


def _python() -> dict:
    v = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return _item("python", sys.version_info >= (3, 10), v)


def _ffmpeg() -> dict:
    if not FFMPEG.exists():
        return _item("ffmpeg 滤镜", False, "缺 tools/ffmpeg")
    out = subprocess.run([str(FFMPEG), "-hide_banner", "-filters"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    missing = [f for f in REQUIRED_FILTERS if f not in out]
    ver = subprocess.run([str(FFMPEG), "-version"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout.split()[2]
    return _item("ffmpeg 滤镜", not missing,
                 f"ffmpeg {ver}" if not missing else f"缺 {', '.join(missing)}（需完整构建）")


def _magick() -> dict:
    if not MAGICK.exists():
        return _item("ImageMagick", False, "缺 tools/imagemagick")
    v = subprocess.run([str(MAGICK), "-version"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace").stdout.splitlines()[0]
    return _item("ImageMagick", True, v.strip()[:60])


def _module(name: str, label: str, version_attr: str = "__version__") -> dict:
    """按模块名探测；`version_attr` 支持点号（yt-dlp 的版本在 `version.__version__`）。"""
    try:
        mod = importlib.import_module(name)
    except Exception:
        return _item(label, False, "缺")
    ver = mod
    for part in version_attr.split("."):
        ver = getattr(ver, part, "")
    return _item(label, True, str(ver or "")[:40])


def _av() -> dict:
    """faster-whisper 只兼容 PyAV < 19；太新要明确说，否则转写会神秘失败。"""
    try:
        import av
    except Exception:
        return _item("PyAV", False, "缺")
    ver = getattr(av, "__version__", "")
    try:
        major = int(str(ver).split(".")[0])
    except ValueError:
        return _item("PyAV", True, str(ver))
    if major >= 19:
        return _item("PyAV", False, f"{ver} 太新，faster-whisper 需要 <19")
    return _item("PyAV", True, str(ver))


def _ocr() -> dict:
    try:
        importlib.import_module("rapidocr_onnxruntime")
        return _item("OCR（硬字幕）", True, "rapidocr")
    except Exception:
        pass
    if sys.platform == "win32" and OCR_PS1.exists():
        return _item("OCR（硬字幕）", True, "Windows 自带（WinRT）")
    return _item("OCR（硬字幕）", False, "缺（只影响 hardsub --apply）")


def probe() -> list[dict]:
    """探一轮，返回每项 {name, ok, detail, required, fix}。顺序即展示顺序。"""
    return [
        _python(),
        _ffmpeg(),
        _magick(),
        _module("yt_dlp", "yt-dlp", "version.__version__"),
        _module("PIL", "Pillow（选帧）"),
        _module("faster_whisper", "faster-whisper"),
        _av(),
        _module("opencc", "opencc 繁转简"),
        _ocr(),
    ]


def missing_required(items: list[dict]) -> list[dict]:
    return [i for i in items if i["required"] and not i["ok"]]


def missing_optional(items: list[dict]) -> list[dict]:
    return [i for i in items if not i["required"] and not i["ok"]]


def worst(items: list[dict]) -> int:
    """退出码：只有**必需项**缺失才算失败（可选项缺失只提示）。"""
    return 1 if missing_required(items) else 0


def summary(items: list[dict]) -> str:
    req = missing_required(items)
    opt = missing_optional(items)
    if req:
        return "必需项没齐：" + "、".join(i["name"] for i in req)
    if opt:
        return "取证链路可用；可选链路缺：" + "、".join(i["name"] for i in opt)
    return "全部就绪"
