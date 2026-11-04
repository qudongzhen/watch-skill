#!/usr/bin/env python
"""`pipeline/hardsub.py` 的自检：字幕带定位、LCS 判据、引擎缺失时的行为。

    py -3.14 pipeline/test_hardsub.py

不依赖 ffmpeg / OCR 引擎：定位用合成图，判据用字符串。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pydeps"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw  # noqa: E402

import hardsub  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILED.append(name)


def tmpdir() -> Path:
    d = ROOT / "work" / "tmp" / "test_hardsub"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def band_image(path: Path, band: tuple[int, int], w: int = 480, h: int = 270,
               busy: bool = False) -> Path:
    """灰底 + 指定 y 区间画竖条（模拟文字笔画）。busy=True 时整幅都画条纹。"""
    img = Image.new("L", (w, h), 64)
    d = ImageDraw.Draw(img)
    y0, y1 = band if not busy else (0, h)
    for x in range(0, w, 5):
        d.line([(x, y0), (x, y1)], fill=230, width=1)
    img.save(path)
    return path


def test_band() -> None:
    d = tmpdir()
    bottom = band_image(d / "bottom.png", (228, 250))
    b = hardsub.band_from_densities([hardsub.row_density(bottom)])
    check("底部字幕带被发现", b is not None and 220 <= b["y0"] <= 232 and 245 <= b["y1"] <= 255,
          str(b))

    top = band_image(d / "top.png", (16, 38))
    b2 = hardsub.band_from_densities([hardsub.row_density(top)])
    check("顶部字幕带也能发现（没写死底部）",
          b2 is not None and 10 <= b2["y0"] <= 22 and 32 <= b2["y1"] <= 44, str(b2))

    plain = d / "plain.png"
    Image.new("L", (480, 270), 90).save(plain)
    check("纯色图不误报", hardsub.band_from_densities([hardsub.row_density(plain)]) is None)

    busy = band_image(d / "busy.png", (0, 0), busy=True)
    b3 = hardsub.band_from_densities([hardsub.row_density(busy)])
    check("整幅都忙（无字幕带）不给出带", b3 is None, str(b3))

    # 多帧：内容在动、字幕不动 → 仍能定位；纯动态内容则定位不到
    moving = []
    for i in range(4):
        p = d / f"m{i}.png"
        img = Image.new("L", (480, 270), 40)
        dd = ImageDraw.Draw(img)
        for k in range(0, 480, 7):
            dd.line([(k, (i * 30) % 200), (k + 3, ((i * 30) % 200) + 40)], fill=200, width=1)
        for k in range(0, 480, 5):
            dd.line([(k, 230), (k, 250)], fill=235, width=1)
        img.save(p)
        moving.append(hardsub.row_density(p))
    b4 = hardsub.band_from_densities(moving)
    check("多帧下仍锁定不动的字幕带", b4 is not None and 225 <= b4["y0"] <= 235, str(b4))


def test_judge() -> None:
    asr = "先看一下这段代码"
    same = hardsub.judge(asr, asr)
    check("完全相同 → 接受且覆盖 1.0", same["accept"] and same["cover"] == 1.0, str(same))

    partial = hardsub.judge("先看这段代码", "先看一下这段代码")
    check("缺几个字仍接受（LCS 保序）", partial["accept"], str(partial))

    unrelated = hardsub.judge("完全不同的内容", asr)
    check("无关文本被拒", not unrelated["accept"], str(unrelated))

    shuffled = hardsub.judge("代码这段一下先看", asr)
    check("乱序不算覆盖", not shuffled["accept"], str(shuffled))

    empty = hardsub.judge("", asr)
    check("空 OCR 被拒", not empty["accept"] and empty["reason"] == "空文本", str(empty))

    long_ocr = hardsub.judge(asr * 4, asr)
    check("同一句重复：剪成一句（字幕带里叠了两行也认）",
          long_ocr["accept"] and long_ocr["text"] == asr, str(long_ocr))
    long_raw = hardsub.judge(asr * 4, asr, trim=False)
    check("不剪时长度比异常被拒", not long_raw["accept"], str(long_raw))
    stuffed = hardsub.judge("先看" + "胡言乱语" * 4 + "这段代码", asr)
    check("中间夹垃圾仍是长度比异常", not stuffed["accept"], str(stuffed))

    punct = hardsub.judge("先看一下，这段代码！", asr)
    check("标点差异不影响判定", punct["accept"], str(punct))

    # 字幕带裁宽了会连带 OCR 到相邻 UI 文字；trim 只留与 ASR 对齐的那一段
    noisy = hardsub.judge("LOGI 的 CREDITS 先看一下这段代码 关注 点赞", asr)
    check("带外文字被剪掉", noisy["accept"] and noisy["text"] == "先看一下这段代码", str(noisy))
    check("raw 里保留原样", noisy["raw"] == "LOGI 的 CREDITS 先看一下这段代码 关注 点赞",
          noisy["raw"])
    keep = hardsub.judge("LOGI 先看一下这段代码", asr, trim=False)
    check("trim=False 不剪", keep["text"] == "LOGI 先看一下这段代码", str(keep))

    # 中英混排 + 内部标点：剪完不能把句子中间的标点也削掉
    en = "and then the code, right here"
    mixed = hardsub.judge("UH and then the code, right here", en)
    check("英文句首语气词剪掉、句中标点保留",
          mixed["text"] == "and then the code, right here", str(mixed))

    # 与 ASR 一模一样的候选是噪音（实测占一半），不该进列表
    check("一字不差 → 不需要改", not hardsub.needs_fix("先看一下这段代码", asr))
    check("只差标点 → 不需要改", not hardsub.needs_fix("先看一下，这段代码！", asr))
    check("真改了字 → 需要改", hardsub.needs_fix("先看一下这段代玛", asr))


def test_normalize_ocr() -> None:
    check("CJK 之间的空格并回去",
          hardsub.normalize_ocr("需 要 注 意 的 是") == "需要注意的是")
    check("英文单词之间的空格保留",
          hardsub.normalize_ocr("the visible layer") == "the visible layer")
    check("中英混排只并 CJK 侧", hardsub.normalize_ocr("打开 Layer 面板") == "打开 Layer 面板",
          hardsub.normalize_ocr("打开 Layer 面板"))
    check("CJK 前方标点不留空格",
          hardsub.normalize_ocr("你好 ， 世界 。") == "你好，世界。",
          hardsub.normalize_ocr("你好 ， 世界 。"))
    check("空串安全", hardsub.normalize_ocr("") == "")


def _hide_engines():
    """临时伪装成"两个引擎都没有"：藏掉回退脚本，并让 rapidocr 的 import 失败。

    `sys.modules[name] = None` 是标准做法——之后 `import name` 直接抛 ImportError。
    """
    saved = (hardsub.OCR_PS1, sys.modules.get("rapidocr_onnxruntime", KeyError))
    hardsub.OCR_PS1 = ROOT / "work" / "tmp" / "no_such_ocr.ps1"
    hardsub._ENGINE = None
    sys.modules["rapidocr_onnxruntime"] = None
    return saved


def _restore_engines(saved) -> None:
    ps1, mod = saved
    hardsub.OCR_PS1 = ps1
    hardsub._ENGINE = None
    if mod is KeyError:
        sys.modules.pop("rapidocr_onnxruntime", None)
    else:
        sys.modules["rapidocr_onnxruntime"] = mod


def test_engine() -> None:
    """引擎选择：有 rapidocr 用它，否则回退 Windows 自带；都没有才报缺。"""
    hardsub._ENGINE = None
    try:
        kind, eng = hardsub.engine()
    except hardsub.OcrUnavailable as exc:
        check("无引擎时消息里有安装命令", "install_pydeps" in str(exc), str(exc)[:80])
    else:
        check("引擎类型是已知的", kind in {"rapidocr", "windows"}, kind)
        check("windows 引擎指向 ocr_win.ps1",
              kind != "windows" or Path(eng).name == "ocr_win.ps1", str(eng))
    finally:
        hardsub._ENGINE = None

    # 两个引擎都藏起来：必须抛 OcrUnavailable（不是崩栈）
    saved = _hide_engines()
    try:
        hardsub.engine()
    except hardsub.OcrUnavailable as exc:
        check("两个引擎都没有时抛 OcrUnavailable", "install_pydeps" in str(exc), str(exc)[:80])
    else:
        check("两个引擎都没有时抛 OcrUnavailable", False, "居然拿到了引擎")
    finally:
        _restore_engines(saved)

    # OCR 读不到图：引擎自己的异常也要包成 OcrUnavailable，不能抛别的
    try:
        hardsub.ocr_band(tmpdir() / "no_such.png")
    except hardsub.OcrUnavailable as exc:
        check("OCR 失败也走 OcrUnavailable", "OCR 失败" in str(exc) or "Windows OCR" in str(exc),
              str(exc)[:100])
    except Exception as exc:  # noqa: BLE001
        check("OCR 失败也走 OcrUnavailable", False, f"{type(exc).__name__}: {exc}")
    else:
        print("  SKIP  无引擎，跳过\"OCR 失败\"检查")


def main() -> int:
    print("pipeline/hardsub.py 自检\n")
    test_band()
    test_judge()
    test_normalize_ocr()
    test_engine()
    print(f"\n{len(FAILED)} 项失败" if FAILED else "\n全绿")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
