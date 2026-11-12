#!/usr/bin/env python
"""视频链接 → 学习笔记「取证包」。

分工（这是整套方法的前提）：
  ffmpeg 零成本看完全部帧  →  采样成几十帧图版  →  模型/人只看这几十帧
  report/brief 只产骨架，正文由写作者看图后填写。

本脚本只做取证，不写笔记。跑完会打印下一步要看哪些图。

用法：
    py -3.14 pipeline/run.py "https://www.bilibili.com/video/BV1xx" 
    py -3.14 pipeline/run.py <url> --no-asr
    py -3.14 pipeline/run.py "<本地视频路径>" --force

产物（notes/<id>/）：
    note.md             ← 写作者填（本脚本只在缺失时放一份骨架）
    research.json       ← 机器摘要：元信息 + 图版索引 + 转写
    BRIEF.md            ← 给写作者的任务书
    assets/grids/*      ← 图版（含 manifest.json，行列→时间的权威表）
    evidence/           ← timeline / shotlist / asr 原始产物
"""
from __future__ import annotations

import argparse
import atexit
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pydeps"))  # 本进程也要能 import opencc 等（子进程另有 env()）
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录的 sharp.py
import sharp  # noqa: E402  （必须在 pydeps 之后，sharp 自己要 import PIL）
import hardsub  # noqa: E402  （硬字幕 OCR：教程体默认先定位再整片读）
import asrfix  # noqa: E402
import subs  # noqa: E402
import timefmt  # noqa: E402  （时间戳只有一份实现）
import store  # noqa: E402  （JSON 原子写 / 容错读）
FFMPEG_BIN = ROOT / "tools" / "ffmpeg" / "bin"
VW = ROOT / "vendor" / "video-watch" / "vw.py"
COMPAT = ROOT / "pipeline" / "compat"
PDEPS = ROOT / "pydeps"
WORK = ROOT / "work"
NOTES = ROOT / "notes"
TMP = WORK / "tmp"


# ---------------------------------------------------------------- 基础设施

def env() -> dict:
    """所有子进程统一环境：临时目录、模块路径、编码、ffmpeg 的 PATH。"""
    e = os.environ.copy()
    e["PYTHONPATH"] = os.pathsep.join(str(p) for p in (COMPAT, PDEPS))
    e["TMP"] = e["TEMP"] = str(TMP)
    e["PYTHONIOENCODING"] = "utf-8"
    e["PATH"] = str(FFMPEG_BIN) + os.pathsep + e.get("PATH", "")
    # 模型缓存放工作区（默认 ~/.cache 在沙箱里不可写）；Xet 传输在国内常不通
    e["HF_HOME"] = str(WORK / "hf")
    e["HF_HUB_DISABLE_XET"] = "1"
    return e


def run(cmd: list[str], desc: str) -> str:
    print(f"\n$ {desc}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT, env=env(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    if out:
        tail = out.splitlines()
        for line in tail[-6:]:
            print("  " + line, flush=True)
    if r.returncode != 0:
        print((r.stderr or "").strip()[-1500:], file=sys.stderr, flush=True)
        raise SystemExit(f"失败（exit {r.returncode}）：{desc}")
    return out


def vw(*args: str) -> str:
    return run([sys.executable, str(VW), *args], "vw.py " + " ".join(args[:2]))


# ---------------------------------------------------------------- 登录态（cookies）
# 为什么要临时副本：yt-dlp 退出时会**回写** cookie jar（服务器 refresh 过就写），
# 而用户的 cookies 一般在仓库外的私有目录——那里在 workspace-write 沙箱里只读，
# 回写直接 PermissionError，整次运行以失败收场。
# 所以复制到系统临时目录再用、进程退出即删：凭据不进工作区，也不改用户原文件。
COOKIE_SRC: Path | None = None
COOKIE_TMP: Path | None = None


def stage_cookies(src: str) -> Path:
    """把 --cookies 给的文件复制成临时副本，返回副本路径。"""
    global COOKIE_SRC, COOKIE_TMP
    p = Path(src).expanduser()
    if not p.is_file():
        raise SystemExit(f"--cookies 给的路径不是文件：{p}")
    d = Path(tempfile.gettempdir()) / "watch-skill-cookies"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"cookies-{os.getpid()}.txt"
    shutil.copy2(p, tmp)
    COOKIE_SRC, COOKIE_TMP = p, tmp
    print(f"[cookies] 已载入 {p}（用临时副本，退出即删，不回写原文件）", flush=True)
    return tmp


def cleanup_cookies() -> None:
    """删临时副本。挂在 atexit 上，SystemExit / 异常也会走到。"""
    global COOKIE_TMP
    tmp, COOKIE_TMP = COOKIE_TMP, None
    if tmp and tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass


def cookie_args() -> list[str]:
    """喂给 yt-dlp 的 cookie 参数（没设 --cookies 时为空）。"""
    return ["--cookies", str(COOKIE_TMP)] if COOKIE_TMP else []


atexit.register(cleanup_cookies)


def ytdlp(*args: str) -> str:
    return run([sys.executable, "-m", "yt_dlp", *cookie_args(), *args], "yt-dlp")


def link_at(url: str, sec: float) -> str:
    """带时间跳转的原始链接（B 站/YouTube 都认 ?t=）。"""
    if not url:
        return ""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}t={int(sec)}"


def jump_url(url: str, sec: float) -> str:
    """按平台生成"跳到第 N 秒"的链接。

    - B 站：沿用原 query（分 P 的 `?p=N` 已经在 url 里），追加 `&t=秒`；
      url 里没有 `p` 时不擅自补，避免把多 P 视频跳到 P1（调用方在 BRIEF 里说明）。
    - YouTube：`&t=秒`（两家都认 t=，写法一致）。
    - 其他站点：`#t=秒`（HTML5 媒体片段的通用写法）。
    """
    if not url:
        return ""
    sec = int(sec)
    host = url.split("/")[2].lower() if url.count("/") >= 2 else ""
    if "bilibili.com" in host or "youtu" in host:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}t={sec}"
    return f"{url.split('#')[0]}#t={sec}"


# ---------------------------------------------------------------- 1. 抓取

# 竖屏（1080×1920）的 height 是**长边**，纯 `[height<=1080]` 会把 720×1280、1080×1920
# 全过滤掉，只剩 480×854 —— 实测 YouTube Shorts（1080×1920）上就是这样，而且因为
# 第一段就匹配成功，末尾的兜底根本轮不到。`-S res:1080` 的 `res` 是**短边**（竖屏=1080、
# 横屏=1080），所以不写尺寸过滤、只靠排序就能两边都对：
#   竖屏 1080×1920 → 312+234（1080×1920）；横屏 4K 3840×2160 → 399+251（1920×1080）。
FORMAT_SELECTOR = "bv*+ba/b"
SUB_LANGS = "zh-Hans,zh-CN,zh,ai-zh,en,en-US"


def ytdlp_soft(*args: str) -> tuple[int, str, str]:
    """不抛异常的 yt-dlp 调用：字幕这类"拿不到也照跑"的步骤用它。"""
    r = subprocess.run([sys.executable, "-m", "yt_dlp", *cookie_args(), *args],
                       cwd=ROOT, env=env(),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


_LANG_LINE = re.compile(r"^([A-Za-z][\w.-]*)\s{2,}(\S.*)$")


def parse_subs_listing(*texts: str) -> dict[str, list[str]]:
    """解析 `--list-subs` 的输出，把**人工字幕**和**自动字幕**分开。

    yt-dlp 打印两张表：
        [info] Available subtitles for X:          ← 人工/作者上传
        [info] Available automatic captions for X: ← 平台自动生成
    两张表的表头都是 "Language Formats"，行的形状都是 "zh-Hans  srt, vtt"。
    混在一起会把"只有自动字幕"误判成"有字幕"，所以按小节归属。
    """
    out: dict[str, list[str]] = {"manual": [], "auto": []}
    section = "manual"
    inside = False
    for text in texts:
        for raw in (text or "").splitlines():
            line = raw.strip()
            low = line.lower()
            if "automatic captions" in low:      # yt-dlp 行首带 "[info] "，用包含判断
                section, inside = "auto", False
                continue
            if "available subtitles" in low:
                section, inside = "manual", False
                continue
            if low.startswith("language"):
                inside = True
                continue
            if not line:
                inside = False
                continue
            if not inside:
                continue
            m = _LANG_LINE.match(line)
            if m:
                out[section].append(m.group(1))
    return out


def listed_langs(*texts: str) -> list[str]:
    """人工字幕的语言代码（"danmaku  xml" / "zh-Hans  srt, vtt"）。"""
    return parse_subs_listing(*texts)["manual"]


def auto_caption_langs(*texts: str) -> list[str]:
    """平台自动字幕（YouTube 的"自动生成"）的语言代码。"""
    return parse_subs_listing(*texts)["auto"]


# 匿名能看到的只有弹幕，字幕接口直接返回空。实测（2026-10-31）4/4 个 B 站视频
# `player/v2` 的 subtitle.subtitles 都是 0 条，其中包括标题里写明"中英文字幕"的那条。
ANON_SUBS_HINT = ("B 站把字幕接口关给了匿名用户：实测 4/4 个视频 player/v2 返回 0 条字幕"
                  "（含标题写「中英文字幕」的），所以这里只能看到弹幕。要站点字幕得提供 cookies"
                  "（`--cookies <cookies.txt>`，浏览器导出）；本轮改回转写。")

# 带了登录态还是只有弹幕：那就不再是"接口没给"，而是这条素材真没有文本轨（D27 实测）
LOGGED_IN_SUBS_HINT = ("已带登录态，`--list-subs` 仍然只列出弹幕：说明这条视频没传 CC，也没开平台"
                       "智能字幕。这是素材的问题，不是登录的问题——换素材或退回转写。")

# 自动字幕是平台机的，不是作者原文；而且 YouTube 的 VTT 是"滚动行"（每句带上一句），
# 实测同一视频 506 段 / 5357 词，去重后才是 1828 词——直接用会把转写撑大三倍。
AUTO_ONLY_HINT = ("这条只有平台**自动字幕**（机器生成，不是作者原文）：实测 YouTube 自动字幕是滚动行，"
                  "同一视频 506 段 / 5357 词，去重后才 1828 词，而 whisper 本来就是 245 段 / 1828 词。"
                  "质量不比 whisper 高、还得先做去滚动，所以本轮仍用 whisper。")


def subtitle_note(info: dict) -> str:
    """把字幕尝试的结果翻成一句人话（写进 BRIEF）。"""
    if not info or info.get("ok"):
        return ""
    reason = info.get("reason") or ""
    if reason == "disabled":
        return "站点字幕：本次关掉了（--no-subs）"
    if reason == "local-file":
        return ""
    if reason == "danmaku-only":
        if info.get("logged_in"):
            return "站点字幕：带登录态仍只有弹幕（这条视频没传 CC）"
        return "站点字幕：匿名只拿到弹幕，没有 CC/AI 字幕"
    if reason == "auto-only":
        return "站点字幕：只有平台自动字幕（机器生成、非作者原文），已跳过"
    if reason == "needs-login":
        return "站点字幕：存在但要登录（提供 cookies.txt 才能用）"
    if reason == "no-subs":
        return "站点字幕：该视频没有字幕轨"
    return f"站点字幕：尝试失败（{reason}）"


def _try_subtitles_once(job: Path, url: str, auto: bool = False) -> dict:
    """一次 yt-dlp 字幕尝试。auto=True 时额外允许平台自动字幕（`--write-auto-subs`）。

    注意 `--list-subs` 是"只列出、不下载"，所以**不能**和下载放在同一条命令里
    （实测同一条命令加它，文件一个都不会落，返回码还是 0）。分类失败原因时才单独列一次。
    """
    # --ignore-errors 很关键：多语言一起要时，只要某一条（常见是机器翻译轨）返回 429，
    # yt-dlp 默认会把整批字幕判成失败（"has no subtitles"），实际把已有的也丢掉。
    cmd = ["--no-warnings", "--ignore-errors", "--skip-download", "--write-subs",
           "--convert-subs", "srt", "--ffmpeg-location", str(FFMPEG_BIN),
           "--sub-langs", SUB_LANGS,
           "--sub-format", "srt/vtt/best", "-o", str(job / "source.%(ext)s")]
    if auto:
        cmd.insert(4, "--write-auto-subs")
    rc, out, err = ytdlp_soft(*cmd, url)
    found = subs.pick_file(job)
    if found:
        return {"ok": True, "file": found.name, "lang": subs.lang_of(found),
                "source": "auto-subs" if auto else "subtitles"}
    blob = (out + err).lower()
    if "log in" in blob or "logged in" in blob or "登录" in blob:
        return {"ok": False, "reason": "needs-login"}      # 有字幕但要登录，不是"没有"
    if rc != 0:
        return {"ok": False, "reason": f"failed: {(err or out).strip().splitlines()[-1][:120]}"}
    # 没下到：单独列一次语言表，把"只有弹幕/只有自动字幕/真没有"分开
    _, lout, lerr = ytdlp_soft("--no-warnings", "--skip-download", "--list-subs",
                               "-o", str(job / "source.%(ext)s"), url)
    langs = listed_langs(lout, lerr)
    autos = auto_caption_langs(lout, lerr)
    if langs and set(langs) <= {"danmaku"}:
        # 只列出弹幕 = 该站没给任何文本轨，跟"这个视频没有字幕"不是一回事；
        # 但要分清"匿名被挡"和"登录了也没有"，否则用户不知道该补 cookies 还是该换素材
        return {"ok": False, "reason": "danmaku-only", "langs": langs,
                "logged_in": bool(COOKIE_TMP),
                "hint": LOGGED_IN_SUBS_HINT if COOKIE_TMP else ANON_SUBS_HINT}
    if not langs and autos:
        # 只有平台自动字幕（机器生成、滚动行）——不是作者原文，默认不用
        return {"ok": False, "reason": "auto-only", "langs": autos,
                "hint": AUTO_ONLY_HINT}
    return {"ok": False, "reason": "no-subs", "langs": langs}


def try_subtitles(job: Path, url: str, enabled: bool = True,
                  want_auto: bool = False) -> dict:
    """尝试拿站点字幕（B 站 CC 等）。失败不影响主链路，但要把原因记下来。

    单独一次调用、而不是给主下载加 --write-subs：字幕失败会改变主下载的退出码，
    一条拿不到字幕的视频不该因此下不下来。

    人工字幕优先；`want_auto=True` 时人工没有才再试一次平台自动字幕（opt-in，
    自动字幕是机器生成的，且需要 `subs.deroll` 去滚动才可用）。
    """
    if not enabled:
        return {"ok": False, "reason": "disabled"}
    first = _try_subtitles_once(job, url, auto=False)
    if first.get("ok") or not want_auto:
        return first
    second = _try_subtitles_once(job, url, auto=True)
    if second.get("ok"):
        return second
    return first        # 自动那次也没成，报人工那次的原因（更贴切，如 danmaku-only）


def fetch(url_or_path: str, job: Path, force: bool,
          want_subs: bool = True, want_auto: bool = False) -> tuple[Path, dict]:
    meta_path = job / "meta.json"
    media = next((p for p in sorted(job.glob("source.*"))
                  if p.suffix.lower() not in {".json", ".part"}), None)
    meta = store.load_json(meta_path) if meta_path.exists() else None
    if media and meta and not force:
        print(f"[fetch] 已存在，跳过下载：{media.name}", flush=True)
        return media, meta

    src = Path(url_or_path)
    if src.exists():
        job.mkdir(parents=True, exist_ok=True)
        media = job / ("source" + src.suffix)
        if not media.exists() or force:
            shutil.copy2(src, media)
        meta = {"id": src.stem, "title": src.stem, "uploader": "", "duration": None,
                "webpage_url": "", "description": "", "upload_date": "", "local": True,
                "subtitles": {"ok": False, "reason": "local-file"}}
    else:
        job.mkdir(parents=True, exist_ok=True)
        ytdlp("--no-warnings", "--no-playlist", "--write-info-json",
              "--ffmpeg-location", str(FFMPEG_BIN),
              "-f", FORMAT_SELECTOR, "-S", "res:1080",
              "-o", str(job / "source.%(ext)s"), url_or_path)
        info_file = job / "source.info.json"
        info = json.loads(info_file.read_text(encoding="utf-8")) if info_file.exists() else {}
        media = next(p for p in sorted(job.glob("source.*"))
                     if p.suffix.lower() not in {".json", ".part"})
        meta = {
            "id": info.get("id") or job.name,
            "title": info.get("title") or job.name,
            "uploader": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration"),
            "webpage_url": info.get("webpage_url") or url_or_path,
            "description": (info.get("description") or "")[:4000],
            "upload_date": info.get("upload_date") or "",
            "chapters": info.get("chapters") or [],
            "local": False,
        }
        meta["subtitles"] = try_subtitles(job, url_or_path, enabled=want_subs,
                                          want_auto=want_auto)
        if meta["subtitles"].get("ok"):
            kind = (" 平台自动字幕（机器生成）"
                    if meta["subtitles"].get("source") == "auto-subs" else "")
            print(f"[fetch] 拿到站点字幕{kind}：{meta['subtitles']['file']}"
                  f"（{meta['subtitles'].get('lang') or '?'}）", flush=True)
        else:
            note = subtitle_note(meta["subtitles"])
            if note:
                print(f"[fetch] {note}；改用转写", flush=True)
            hint = meta["subtitles"].get("hint")
            if hint:
                print(f"[fetch] {hint}", flush=True)
    store.save_json(meta_path, meta)
    print(f"[fetch] {media.name}  {media.stat().st_size / 1e6:.1f}MB", flush=True)
    return media, meta


# ---------------------------------------------------------------- 2. 分段

def cut_times(timeline: dict) -> list[float]:
    """timeline.json 里 cuts 可能是秒数，也可能是 {"t": 秒}；两种都吃。"""
    out = []
    for c in timeline.get("cuts") or []:
        t = c.get("t") if isinstance(c, dict) else c
        if t is not None:
            out.append(float(t))
    return sorted(out)


def plan_sections(timeline: dict, max_sections: int = 8, mode: str = "cut",
                  auto_even: bool = True) -> list[dict]:
    """切点优先，没有切点就等分；每段保证有足够时长看清新内容。

    mode="even" 时忽略切点直接等分——适合"6 个技巧"这类每段自成一体、
    但画面里几乎没有硬切的素材。

    长片 + 切点过少（>15min 且 ≤3 个）时**自动退化为等分**：那些切点不是章节边界，
    按它们切会得到 118s/355s 这种长短差 3 倍的段，段内采样间隔跑到 44s/帧——
    等分成 192s 反而更接近"每段讲一件事"。要保留旧行为加 --no-auto-even。
    """
    dur = float(timeline.get("duration") or timeline["info"]["duration"])

    def even(n: int) -> list[dict]:
        n = max(2, min(max_sections, n))
        b = [dur * i / n for i in range(n + 1)]
        return [{"idx": i + 1, "t0": round(b[i], 2), "t1": round(b[i + 1], 2)}
                for i in range(n)]

    if mode == "even":
        return even(max_sections)
    cuts = cut_times(timeline)
    if auto_even and dur > 900 and len(cuts) <= 3:
        return even(max(3, math.ceil(dur / 180)))

    bounds = [0.0] + [c for c in cuts if 6.0 < c < dur - 6.0] + [dur]

    def merge(items: list[float], min_len: float) -> list[float]:
        out = [items[0]]
        for b in items[1:]:
            if b - out[-1] < min_len and b != items[-1]:
                continue
            out.append(b)
        return out

    bounds = merge(bounds, 20.0)
    # 单段过长就劈开：1 个切点撑起 4 分钟的视频时，章节会是糊的（教程尤其明显）
    max_span = 120.0
    split: list[float] = [bounds[0]]
    for a, b in zip(bounds, bounds[1:]):
        parts = max(1, math.ceil((b - a) / max_span))
        split += [a + (b - a) * k / parts for k in range(1, parts + 1)]
    bounds = split
    while len(bounds) - 1 > max_sections:  # 合并最短的一段
        spans = [(bounds[i + 1] - bounds[i], i) for i in range(len(bounds) - 1)]
        _, i = min(spans)
        bounds.pop(i + 1 if i + 1 < len(bounds) - 1 else i)
    if len(bounds) < 3:  # 无切点：等分成 3~8 段
        return even(max(3, math.ceil(dur / 90)))
    return [{"idx": i + 1, "t0": round(bounds[i], 2), "t1": round(bounds[i + 1], 2)}
            for i in range(len(bounds) - 1)]


# ---------------------------------------------------------------- 3. 图版

def grid(media: Path, out: Path, t0: float, t1: float, frames: int, cols: int) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    dur = t1 - t0
    pad = min(0.3, dur / 10)
    vw("grid", "--media", str(media), "--t0", f"{t0 + pad:.2f}", "--t1", f"{t1 - pad:.2f}",
       "--frames", str(frames), "--cols", str(cols), "--out", str(out))
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def slim(manifest: dict, name: str, path: Path) -> dict:
    out = {
        "kind": name,
        "image": str(path.relative_to(ROOT)).replace("\\", "/"),
        "cols": manifest["cols"], "rows": manifest["rows"],
        "t0": manifest["t0"], "t1": manifest["t1"],
        "tiles": [{"index": t["index"], "row": t["row"], "col": t["col"],
                   "t": round(t["t"], 2), "frame": t.get("frame")} for t in manifest["tiles"]],
    }
    # 「保留了旧像素」必须跟着传下去，否则 BRIEF 与 research.json 都会丢掉这个事实（D26）
    if manifest.get("kept"):
        out["kept"] = True
        out["regenerated"] = manifest.get("regenerated", "")
    return out


# ---------------------------------------------------------------- 4. 语音

# 繁转简在 asrfix 里（离线重算也要用同一份实现，绝不能两边各写一遍）
to_simplified = asrfix.to_simplified


def asr(media: Path, out: Path, lang: str, model: str,
        sub_path: Path | None = None,
        sub_source: str = "subtitles") -> tuple[list[dict] | None, dict]:
    """跑转写，返回 (段, 元信息)。元信息里的 language 是**请求的**语种（vw.py 直传 whisper），
    不能当检测结果用，只作记录。

    有站点字幕时优先用字幕：作者原文、错字少、带准确时间码，还省掉 1–4 分钟转写。
    字幕解析失败就退回 whisper，并把原因打出来（不静默降级）。
    """
    out.mkdir(parents=True, exist_ok=True)
    if sub_path and sub_path.exists():
        try:
            sub_segs = subs.parse(sub_path)
        except Exception as exc:                      # noqa: BLE001
            print(f"[asr] 字幕解析失败（{type(exc).__name__}: {exc}），改用转写",
                  file=sys.stderr, flush=True)
            sub_segs = []
        if sub_segs:
            shutil.copyfile(sub_path, out / f"subtitles{sub_path.suffix}")
            payload = {"media": media.name, "model": sub_source, "language": lang,
                       "source": sub_source, "segments": sub_segs}
            store.save_json(out / "asr.json", payload)
            label = "平台自动字幕" if sub_source == "auto-subs" else "站点字幕"
            print(f"[asr] 用{label}：{len(sub_segs)} 段（{sub_path.name}）", flush=True)
            return ([{"start": round(float(s["start"]), 2), "end": round(float(s["end"]), 2),
                      "text": to_simplified(s["text"])} for s in sub_segs],
                    {"language": lang, "model": sub_source, "source": sub_source})
    cmd = ["asr", str(media), "--out", str(out), "--model", model]
    if lang and lang != "auto":
        cmd += ["--lang", lang]  # 不传 --lang，交给 whisper 自己判断语言
    try:
        vw(*cmd)
    except SystemExit as exc:  # 依赖/模型缺失不该挡住取证主链路
        print(f"[asr] 跳过：{exc}", file=sys.stderr, flush=True)
        return None, {"failed": str(exc)}
    path = out / "asr.json"
    if not path.exists():
        return None, {"failed": "asr.json 没生成"}
    data = store.load_json(path, default={})
    if not data:
        return None, {"failed": "asr.json 读不动（损坏）"}
    segs = data.get("segments") if isinstance(data, dict) else data
    meta = {"language": data.get("language"), "model": data.get("model"),
            "source": data.get("source") or "whisper"}
    out_segs = [{"start": round(float(s.get("start", 0)), 2),
                 "end": round(float(s.get("end", 0)), 2),
                 "text": to_simplified((s.get("text") or "").strip())} for s in segs or []]
    # 空洞检测：语言判错（英文被判成中文）或音轨没语音时，转写会整段缺失
    if out_segs:
        gaps = [b["start"] - a["end"] for a, b in zip(out_segs, out_segs[1:])]
        worst = max(gaps) if gaps else 0.0
        if worst > 25:
            print(f"[asr] 警告：转写里有 {worst:.0f}s 的空洞，可能是语言判错或音轨无语音。"
                  f"若原声不是中文，加 --asr-lang en 重跑。", file=sys.stderr, flush=True)
    return out_segs, meta


def postprocess_asr(segs: list[dict], timeline: dict, dur: float, note_dir: Path,
                    backend: dict | None = None) -> tuple[list[dict], dict]:
    """术语校正 → 静音一致性 → 体检。**只报告不丢段**。

    ⚠️ 必须在 `pick_stills()` **之后**调用：关键词打分用的是 `CODE_STRONG`，里面有 `setter`。
    如果先把「寒暑」改成「setter」，那 3 段的分数会从 0 升到 2，再来一个弱词就到 3——
    选址会变、文件名会变、`note.md` 引用的图就静默换了一张，而 `note_audit` 查不出来。
    """
    loaded = asrfix.load_terms(ROOT, note_dir)
    fixed = asrfix.apply_terms(segs, loaded["terms"])
    spans = asrfix.silence_spans(timeline.get("silences") or [])
    meta = {
        "terms": {"files": loaded["files"], "errors": loaded["errors"],
                  "count": len(loaded["terms"]), "replaced": fixed["replaced"],
                  "touched": fixed["touched"], "hits": fixed["hits"],
                  "noop": fixed["noop"],
                  "chained": sorted({k for k in loaded["terms"]
                                     if loaded["terms"][k] in loaded["terms"]})},
        "in_silence": asrfix.text_in_silence(fixed["segments"], spans),
        "health": asrfix.health(fixed["segments"], expect_lang=None, media_duration=dur),
        "suspect": asrfix.suspect(fixed["segments"]),
        "t2s": asrfix.t2s_available(),
        "asr_backend": backend or {},
    }
    if fixed["replaced"]:
        print(f"[asr] 术语校正 {fixed['replaced']} 处 / {fixed['touched']} 段"
              f"（{', '.join(loaded['files'])}）", flush=True)
    for err in loaded["errors"]:
        print(f"[asr] ⚠️ 词表错误：{err}", file=sys.stderr, flush=True)
    for flag in meta["health"]["flags"]:
        print(f"[asr] ⚠️ {flag}", file=sys.stderr, flush=True)
    return fixed["segments"], meta


# ---------------------------------------------------------------- 5. 任务书

# ---------------------------------------------------------------- 4.5 定点截图

# 代码 / 编辑器类信号词。强词命中即加分，弱词只补分——避免"设置"这种口水词单独触发。
CODE_STRONG = ("代码", "脚本", "编辑器", "函数", "setter", "变量", "检查器", "面板",
               "节点", "组件", "代码库", "export", "导出变量", "shader")
CODE_WEAK = ("属性", "场景", "参数", "设置", "界面")


def _score(text: str) -> tuple[int, list[str]]:
    t = text.lower()
    strong = [k for k in CODE_STRONG if k in t]
    weak = [k for k in CODE_WEAK if k in t]
    return 2 * len(strong) + len(weak), strong + weak


def _speech_gaps(timeline: dict) -> list[tuple[float, float]]:
    """静音段的补集 = 正在说话的时间段（probe 免费给的）。"""
    dur = float(timeline.get("duration") or 0)
    spans: list[tuple[float, float]] = []
    open_t: float | None = None
    for ev in timeline.get("silences") or []:
        t = float(ev.get("t") or 0)
        if ev.get("kind") == "start":
            open_t = t
        elif open_t is not None:
            spans.append((open_t, t))
            open_t = None
    if open_t is not None:
        spans.append((open_t, dur))
    # 取反：说话段
    gaps, cursor = [], 0.0
    for a, b in spans:
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < dur:
        gaps.append((cursor, dur))
    return [(a, b) for a, b in gaps if b - a >= 3]


def pick_stills(timeline: dict, sections: list[dict], asr_segs: list[dict] | None,
                budget: int, style: str, events: set[str] | None = None,
                focus: list[float] | None = None,
                ocr_segs: list[dict] | None = None) -> list[dict]:
    """按「事件」给候选帧，**不再均匀分配**。

    事件与优先级（高 → 低）：
      9 手工指定（--focus）｜4 冻结段起点（作者停在界面上讲）｜3 强关键词｜2 弱关键词 /
      语音密度峰（段内最长说话段的中点）｜1 切点后 0.8s（新界面首现）

    三道闸，防止密切口素材爆图：
      ① 相邻候选间隔 < 5s 只留优先级最高的一张；
      ② 任一 60s 窗口最多 3 张；
      ③ 总数不超过 budget（0 = 不截断）。

    不做画面识别：边缘密度试过，代码帧并不比游戏画面高，判据不成立（D5）。
    """
    events = set(events or {"freeze", "speech", "keyword", "cut"})
    cuts = cut_times(timeline)
    freezes = [float(x) for x in (timeline.get("freezes") or [])]
    dur = float(timeline.get("duration") or 0)
    raw: list[dict] = []

    def push(t: float, prio: int, reason: str, hits: list[str] | None = None,
             text: str = "") -> None:
        if 0 <= t <= max(dur - 0.2, 0.2):
            raw.append({"t": round(t, 2), "prio": prio, "reason": reason,
                        "hits": hits or [], "text": text})

    if "freeze" in events:
        for f in freezes:
            push(f + 0.4, 4, "冻结段起点（作者停留处）")

    if "keyword" in events and asr_segs:
        for seg in asr_segs:
            sc, hits = _score(seg["text"])
            if sc >= 2:
                push(seg["start"], 3 if sc >= 3 else 2, "转写命中代码/界面", hits, seg["text"])

    if "speech" in events:
        gaps = _speech_gaps(timeline)
        for s in sections:
            inside = [(a, b) for a, b in gaps if s["t0"] <= a and b <= s["t1"]]
            if not inside:
                continue
            a, b = max(inside, key=lambda ab: ab[1] - ab[0])
            if b - a >= 8:                      # 太短的一段不算"讲得密"
                push((a + b) / 2, 2, "语音密度峰（讲话最长段）")

    if "ocr" in events and ocr_segs:
        for seg in ocr_segs:
            txt = (seg.get("text") or "").strip()
            if len(txt) >= 6:                    # 太短的多半是 UI 残片
                push(float(seg["start"]), 2, "字幕文本变化", [], txt)

    if "cut" in events:
        for c in cuts:
            push(c + 0.8, 1, "切点后新界面")

    for t in focus or []:
        push(float(t), 9, "手工指定（--focus）")

    # 闸①：按优先级保留，5 秒内的重复丢掉
    raw.sort(key=lambda c: (-c["prio"], c["t"]))
    kept: list[dict] = []
    for c in raw:
        if all(abs(c["t"] - k["t"]) >= 5.0 for k in kept):
            kept.append(c)

    # 闸②：任一 60 秒窗口最多 3 张
    kept.sort(key=lambda c: (-c["prio"], c["t"]))
    limited: list[dict] = []
    for c in kept:
        win = [k for k in limited if abs(k["t"] - c["t"]) < 60]
        if len(win) < 3:
            limited.append(c)

    # 闸③：总预算（0 = 不截断）。按时间分层取，每层留优先级最高的一张——
    # 直接按优先级砍会把后半段全砍掉（实测 41 张预算只覆盖到前 14 分钟）。
    limited.sort(key=lambda c: c["t"])
    if budget and len(limited) > budget:
        span_total = max(dur, 1e-6)
        buckets: dict[int, dict] = {}
        for c in limited:
            k = min(budget - 1, int(c["t"] / span_total * budget))
            cur = buckets.get(k)
            if cur is None or c["prio"] > cur["prio"]:
                buckets[k] = c
        limited = sorted(buckets.values(), key=lambda c: c["t"])

    # 归属到段：落在哪段算哪段；落在段之外的按"最近的段"
    picks = []
    for c in limited:
        sec = next((s for s in sections if s["t0"] <= c["t"] <= s["t1"]), None)
        if sec is None and sections:
            sec = min(sections, key=lambda s: min(abs(c["t"] - s["t0"]), abs(c["t"] - s["t1"])))
        picks.append({"t": c["t"], "section": sec["idx"] if sec else None,
                      "reason": c["reason"], "hits": c["hits"], "text": c["text"],
                      "prio": c["prio"]})
    return picks


def still_window(t: float, dur: float, cuts: list[float], fps: float,
                 sec: dict | None = None) -> tuple[float, float]:
    """定点截图可以挪动的范围：**不许跨切点、不许跨段**（实现见 sharp.window_between）。"""
    return sharp.window_between(t, dur, cuts, fps,
                                t0=sec["t0"] if sec else None,
                                t1=sec["t1"] if sec else None)


def note_image_refs(note_dir: Path) -> set[str]:
    """note.md 里引用的图，路径**相对笔记目录**（如 `assets/stills/x.png`）。

    用这个基准是因为笔记里就是这么写的；早先拿 `notes/<id>/assets/...` 去比，恒不相等，
    `--refresh-stills` 守卫等于没开（2026-10-31 评审发现）。
    """
    note = note_dir / "note.md"
    if not note.exists():
        return set()
    text = note.read_text(encoding="utf-8", errors="replace")
    return {m.replace("\\", "/").lstrip("./")
            for m in re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)}


def should_keep_image(path: Path, note_dir: Path, referenced: set[str],
                      refresh: bool) -> bool:
    """note.md 已引用这张图、且没加 --refresh-stills 时，**不覆盖**它的像素。

    单独抽出来是为了能测：这条守卫曾经因为路径基准不一致而恒为假（等于没开）。
    截图与图版共用这一条（图版曾经漏在守卫外面，2026-11-02 补）。
    """
    if refresh or not path.exists():
        return False
    rel = str(path.relative_to(note_dir)).replace("\\", "/").lstrip("./")
    return rel in referenced


def sha256_of(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def decide_grid_target(out: Path, note_dir: Path, referenced: set[str],
                       refresh: bool) -> tuple[Path, bool]:
    """图版写到哪：返回 (生成目录, 是否保留了旧图)。

    被 note.md 引用且没加 `--refresh-stills` 时保留旧像素——新图写到 `out/next/`。
    图版以前漏在这条守卫外面（`grid()` 无条件覆盖同名文件），2026-11-02 补上。
    """
    if should_keep_image(out / "grid.png", note_dir, referenced, refresh):
        return out / "next", True
    return out, False


def record_grid(new_manifest: dict, out: Path, kept: bool, note_dir: Path) -> dict:
    """决定 `research.images[]` 该记哪份 manifest。

    保留旧图时要用**旧 manifest**（那里的 `t0/t1/tiles` 才是磁盘上那张图的时间映射），
    并记 `kept` 与 `regenerated`，否则 research 会记一个不存在的覆盖区间。
    """
    if not kept:
        return new_manifest
    old = store.load_json(out / "manifest.json", default={})
    if not old.get("tiles"):                 # 旧 manifest 丢了：只能记新的，但明确标 kept
        return {**new_manifest, "kept": True}
    return {**old, "kept": True,
            "regenerated": str((out / "next" / "grid.png").relative_to(ROOT)).replace("\\", "/")}


def extract_still(media: Path, t: float, dest: Path, *, lo: float | None = None,
                  hi: float | None = None, mode: str = "sharp",
                  count: int = sharp.PICK_COUNT,
                  pad: float = sharp.PICK_PAD) -> dict:
    """取一张定点截图。默认在 [lo, hi] 内取 count 帧、按清晰度选一张（sharp）。

    原时刻那一帧永远在候选里，所以只会不劣于旧行为；`mode="exact"` 与改前完全一致。
    返回挑选记录（写进 research.json 的 `stills[].pick`）。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    return sharp.pick_to(media, t, dest, lo=lo, hi=hi, mode=mode, count=count, pad=pad)


def stamp(sec: float) -> str:
    """`mm:ss`（满一小时给 `h:mm:ss`）。实现只在 `timefmt` 一处。"""
    return timefmt.stamp(sec)


def write_transcript(note_dir: Path, segs: list[dict]) -> Path:
    """把**未校正**的机器转写落成可读、可引用的一行一段（这是"原稿"，别覆盖）。"""
    path = note_dir / "evidence" / "transcript.md"
    lines = ["# 机器转写（faster-whisper，未校对）", "",
             "> 同音错字常见；有画面证据时以画面为准，正文用校正结果，附录并列机器原文。",
             "> 若本目录还有 `transcript.fixed.md`，那是按词表校正过的版本，本文件保持原样。", ""]
    for s in segs:
        lines.append(f"- `{stamp(s['start'])}` → `{stamp(s['end'])}`　{s['text']}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def previous_research(note_dir: Path, refresh_meta: bool) -> dict:
    """成品笔记已存在时读回上一轮 `research.json`。

    用途只有一个：重跑时**复用既有转写**——note.md 里的引文是从那份转写里读出来的，
    静默换掉转写等于把引文证据换掉（同 D14 截图、D17 元信息的道理）。
    `--refresh-meta` 表示"就是要覆盖"，此时返回空字典（照常重跑）。
    """
    if refresh_meta or not (note_dir / "note.md").exists():
        return {}
    return store.load_json(note_dir / "research.json", default={})


def silence_count(timeline: dict) -> int:
    """静音**区间**数。

    `timeline.silences` 是事件流（一次静音 = 一条 start + 一条 end），直接 `len()` 会把
    段数报成两倍——实测 4 篇 BRIEF 的"静音段 46/54/48/76"其实是 23/27/24/38。
    区间折叠与 asrfix 共用同一份实现，别在这里另写一套。
    """
    return len(asrfix.silence_spans(timeline.get("silences") or []))


def sampling_note(images: list[dict], sections: list[dict],
                  asr_segs: list[dict] | None, stills: list[dict] | None) -> list[str]:
    """生成「取证说明」——写作者直接抄进 note.md 附录，不要自己写。

    为什么交给机器：8 篇既有笔记的附录都手写了"5×5 全片图版 + 8 张 3×3"这类话，
    而分段/格数一改（比如这次 3×3→4×4）它们就变成假话，还没有任何检查会发现。
    """
    overview = [i for i in images if str(i.get("kind", "")).startswith("全片")]
    per_sec = [i for i in images if not str(i.get("kind", "")).startswith("全片")]
    lines = ["## 取证说明（**直接抄进附录**，别自己算）", ""]
    if overview:
        o = overview[0]
        lines.append(f"- 采样：5×5 全片图版 1 张（{len(o['tiles'])} 帧，"
                     f"覆盖 0–{o['t1']:.0f}s）")
    if per_sec:
        gaps = []
        for s in sections:
            cells = int(s.get("cells") or 9)
            if cells > 1:
                gaps.append((s["t1"] - s["t0"]) / (cells - 1))
        shape = "、".join(sorted({f"{i['cols']}×{i['rows']}" for i in per_sec}))
        rng = f"{min(gaps):.1f}–{max(gaps):.1f} 秒" if gaps else "—"
        lines.append(f"- 分段图版：{len(per_sec)} 张（{shape}），段内间隔 {rng}")
    if stills:
        sharp_n = sum(1 for s in stills if (s.get("pick") or {}).get("mode") == "sharp")
        extra = f"（其中 {sharp_n} 张是多候选取最清晰）" if sharp_n else ""
        lines.append(f"- 定点截图：{len(stills)} 张{extra}")
    if asr_segs:
        lines.append(f"- 转写：faster-whisper，{len(asr_segs)} 段")
    lines += ["", "  时间以 `research.json` 为准；这一节与机器摘要不一致就是本文件过时了。", ""]
    return lines


def meta_diff(old: dict, new: dict) -> str:
    """两次 research.json 的差异摘要（只报会让人误读附录的那几个数）。"""
    def n(d, key):
        v = d.get(key)
        return len(v) if isinstance(v, (list, dict)) else 0

    def frames(d):
        return sum(len(i.get("tiles") or []) for i in d.get("images") or [])

    def spans(d):
        s = d.get("sections") or []
        if not s:
            return "0"
        lo = min(x["t1"] - x["t0"] for x in s)
        hi = max(x["t1"] - x["t0"] for x in s)
        return f"{round(lo)}–{round(hi)}s"

    bits = []
    if n(old, "sections") != n(new, "sections") or spans(old) != spans(new):
        seg = f"分段 {n(old, 'sections')}→{n(new, 'sections')}"
        if spans(old) != spans(new):
            seg += f"（{spans(old)}→{spans(new)}）"
        bits.append(seg)
    if n(old, "images") != n(new, "images") or frames(old) != frames(new):
        bits.append(f"图版 {n(old, 'images')}→{n(new, 'images')} 张"
                    f"（帧 {frames(old)}→{frames(new)}）")
    if n(old, "stills") != n(new, "stills"):
        bits.append(f"定点截图 {n(old, 'stills')}→{n(new, 'stills')}")
    if n(old, "asr") != n(new, "asr"):
        bits.append(f"转写 {n(old, 'asr')}→{n(new, 'asr')} 段")
    return "；".join(bits) or "无结构性变化"


def image_tokens(width: int, height: int, cell: int = 42) -> int:
    """视觉 token 的近似折算：一格 42px（14px patch 向上取整），tokens = 行×(列+1)+2。

    口径借自 dsh-see-now 的成本表。**绝对值随宿主实现浮动，别拿来当验收阈值**，
    这里只用来比较「图版 vs 全屏截图」的相对开销。
    """
    cols = max(1, math.ceil(width / cell))
    rows = max(1, math.ceil(height / cell))
    return rows * (cols + 1) + 2


def token_hint(images: list[dict]) -> str:
    """给 BRIEF 一行成本参照：图版便宜，全屏截图贵，别拿截图堆证据。"""
    if not images:
        return ""
    grid = image_tokens(765, 440)       # 5×5 全片图版的实际像素
    still = image_tokens(1920, 1080)    # 一张全屏定点截图
    return (f"一张图版 ≈{grid} token、一张全屏截图 ≈{still} token"
            f"（≈{still / grid:.1f} 倍）。图版换更大格数只小幅涨，"
            f"全屏截图留给图版看不清的参数/代码")


def write_brief(note_dir: Path, meta: dict, timeline: dict, images: list[dict],
                sections: list[dict], asr_segs: list[dict] | None,
                stills: list[dict] | None = None, style: str = "study",
                asr_meta: dict | None = None, path: Path | None = None) -> None:
    dur = float(timeline.get("duration") or 0)
    lines = [
        f"# 取证包 · {meta.get('title','')}",
        "",
        f"- 来源：{meta.get('webpage_url','') or '本地文件'}",
        f"- UP 主：{meta.get('uploader','') or '—'}　时长：{int(dur // 60)}分{int(dur % 60)}秒"
        f"　分辨率：{timeline['info']['width']}×{timeline['info']['height']}"
        f"　帧率：{timeline['info']['fps']}",
        f"- 内容判型：{timeline.get('content_kind','')}（{timeline.get('advice','')}）",
        f"- 切点 {len(timeline.get('cuts') or [])} 个｜冻结段 {len(timeline.get('freezes') or [])} 个"
        f"｜静音段 {silence_count(timeline)} 个",
        "",
        "## 图版清单（行列 → 时间 只信 manifest，别信画面印象）",
        "",
    ]
    for img in images:
        lines.append(f"- `{img['image']}`　{img['kind']}　{img['cols']}×{img['rows']}"
                     f"　覆盖 {img['t0']:.1f}s–{img['t1']:.1f}s")
        if img.get("kept"):
            lines.append(f"  ⚠️ 这张被 note.md 引用了，所以**保留了旧像素**"
                         f"（新生成的另存于 `{img.get('regenerated', '')}`）")
    hint = token_hint(images)
    if hint:
        lines += ["", f"成本参考：{hint}"]
    if asr_meta and asr_meta.get("hardsub_ocr"):
        ho = asr_meta["hardsub_ocr"]
        lines += ["", f"⚠️ **检测到硬字幕**：`{ho.get('file')}`（{ho.get('segments')} 段）。"
                      "参数、菜单名、代码**以字幕原文为准**；句子仍以转写为主，冲突时以字幕为证据。"]
    lines += ["", "## 分段（建议的笔记章节骨架）", ""]
    for s in sections:
        mm, ss = divmod(int(s["t0"]), 60)
        lines.append(f"- 第 {s['idx']} 段　{mm:02d}:{ss:02d} → {int(s['t1'])}s"
                     f"　[跳转]({link_at(meta.get('webpage_url',''), s['t0'])})")
    lines += ["", "## 看清某一步：按需密集图版", "",
              "  图版是「每章一张」，段内有十几秒间隔。某一步的顺序/参数看不清时，"
              f"要一张约 1 秒/帧 的密集图版：\n"
              f"  `py -3.14 pipeline/zoom.py {note_dir.name} --at <秒> --window 8`"]
    if asr_segs:
        lines += ["", f"## 语音转写：{len(asr_segs)} 段（机器输出，同音错字以画面为准）", "",
                  f"全文见 `{note_dir.name}/evidence/transcript.md`，"
                  f"结构化数据见 `research.json` 的 `asr`。"]
        sub_note = subtitle_note(meta.get("subtitles") or {})
        if sub_note:
            lines.append(sub_note)
        if asr_meta:
            t, h = asr_meta.get("terms") or {}, asr_meta.get("health") or {}
            if t.get("replaced"):
                lines.append(f"术语校正：{t['replaced']} 处 / {t['touched']} 段"
                             f"（{', '.join(t.get('files') or [])}）；"
                             f"原稿见 `transcript.md`，校正稿见 `transcript.fixed.md`")
            if t.get("errors"):
                lines.append(f"⚠️ 词表读不动：{'；'.join(t['errors'])}")
            if h:
                backend = asr_meta.get("asr_backend") or {}
                src = {"subtitles": "站点字幕", "auto-subs": "平台自动字幕（机器生成）",
                       "whisper": "whisper 转写"}.get(
                    backend.get("source"), backend.get("source") or "未知来源")
                bits = [src, f"{h.get('segments')} 段", f"{h.get('chars_per_sec')} 字/秒",
                        f"最大空洞 {h.get('max_gap')}s"]
                if h.get("script"):
                    bits.append(f"汉字占 {(h['script'].get('cjk_ratio') or 0):.0%}")
                lines.append("转写体检：" + "　".join(bits))
                for flag in h.get("flags") or []:
                    lines.append(f"  ⚠️ {flag}")
    else:
        lines += ["", "## 语音转写：未生成", ""]

    if stills:
        url = meta.get("webpage_url", "") or ""
        suggest = max(6, min(60, int(math.ceil(dur / 60 * 2))))
        need = max(0, suggest - len(stills))
        lines += ["", f"## 定点截图：事件驱动候选 {len(stills)} 张", ""]
        if need:
            lines.append(f"- 按 {suggest} 张的目标（时长×2，6–60 封顶），**建议再补拍 {need} 张**："
                         f"用 `py -3.14 pipeline/still.py {note_dir.name} --at <秒>`")
        for s in stills:
            tag = f"第{s['section']}段" if s.get("section") else "段外"
            p = s.get("pick") or {}
            extra = ""
            if p.get("mode") == "sharp" and p.get("candidates"):
                cands = p["candidates"]
                k = next((j for j, c in enumerate(cands) if c["t"] == p.get("t")), 0) + 1
                extra = f"　（{len(cands)} 帧里取第 {k} 帧，{p['t'] - p['asked_t']:+.2f}s）"
            elif p.get("mode") == "kept":
                extra = "　（成品笔记已引用，本次未覆盖）"
            jump = f"　[跳转]({jump_url(url, s['t'])})" if url else ""
            lines.append(f"- `{s['image']}`　{stamp(s['t'])}　{tag}　{s['reason']}{extra}{jump}")
            if s.get("text"):
                lines.append(f"  > 转写：{s['text']}")
        lines += ["", "  这些是全屏原帧。**要抄的代码、参数、菜单名必须看它们**——"
                      "图版里的同位置一定糊；不够就自己用 `pipeline/still.py` 补拍。",
                  "",
                  "  ⚠️ 定点截图的时刻可能比图版采样时刻差 ±0.25 秒（上面标了「几帧里取第几帧」）。"
                  "写笔记时**以这里的时间为准**；图版只用来定章节。" ]
    elif asr_segs:
        lines += ["", "## 定点截图：未命中", "",
                  "  转写里没有明显的代码/界面讨论。如果是录屏教程，说明判据没覆盖面，"
                  "用 `pipeline/still.py` 手工补。"]

    lines += sampling_note(images, sections, asr_segs, stills)
    lines += [
        "## 写笔记的硬规则",
        "",
        "1. **图版里的文字一律不采信**：分辨率不够时视觉会渲染合理内容而不是报「读不到」。",
        "   要写进笔记的原文，必须用 `pipeline/still.py --at <秒> --crop` 放大看过。",
        "2. **时间映射只信 manifest**：第几行第几列 → 第几秒，见各图版 manifest.json。",
        "3. **不过度取证**：人不会注意到的小抖动不进正文，量测数据放附录。",
        "4. 每条结论给时间戳，并标注置信度（高/中/待核）。",
        "",
        f"写完后产物：`{note_dir.relative_to(ROOT)}/note.md`",
        "  写完跑 `py -3.14 pipeline/finish.py "
        f"{note_dir.name}`：先 note_audit 验收、再导出（FAIL 必须清零）。",
    ]
    if style == "tutorial":
        lines += [
            "", "## 本篇按「图文教程」写（NOTE_CONTRACT §六）", "",
            "1. **每个步骤必须配一张图**，图就是那一秒的画面；没有画面证据的步骤要明说。",
            "2. 步骤标题统一写成 `### 步骤 N｜mm:ss 这一步做什么`。",
            "3. 每步给三样东西：**怎么做**（可执行动作）、**怎么判断做对了**、**坑**。",
            "4. 代码/参数只写从定点截图里**真读到**的；读不清就写\"画面太小，未展开\"。",
            "5. 结尾给自检清单与排错表，别写\"熟能生巧\"这种话。",
        ]
    target = path or (note_dir / "BRIEF.md")
    target.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- 主流程

def slugify(meta: dict, given: str | None) -> str:
    if given:
        return re.sub(r"[^\w\-]+", "_", given).strip("_") or "video"
    raw = str(meta.get("id") or "video")
    return re.sub(r"[^\w\-]+", "_", raw).strip("_")[:60] or "video"


def main() -> int:
    ap = argparse.ArgumentParser(description="视频链接 → 学习笔记取证包")
    ap.add_argument("source", help="视频链接或本地文件路径")
    ap.add_argument("--name", help="产物目录名（默认用视频 id）")
    ap.add_argument("--max-sections", type=int, default=8)
    ap.add_argument("--sections-mode", choices=["cut", "even"], default="cut",
                    help="cut=按切点分段（默认）；even=忽略切点等分，适合每条自成一体、几乎无硬切的素材")
    ap.add_argument("--no-auto-even", action="store_true",
                    help="长片（>15min）切点 ≤3 个时不自动等分，坚持按切点分")
    ap.add_argument("--asr-lang", default="zh", help="转写语言，zh / en / auto（auto=不指定）")
    ap.add_argument("--asr-model", default="base", help="tiny / base / small / medium")
    ap.add_argument("--no-asr", action="store_true")
    ap.add_argument("--no-subs", action="store_true",
                    help="不尝试站点字幕（默认会试：B 站等站点的 CC 比转写准，且省几分钟）")
    ap.add_argument("--auto-subs", action="store_true",
                    help="人工字幕没有时，也接受平台自动字幕（机器生成，需去滚动；默认不用）")
    ap.add_argument("--cookies", metavar="FILE",
                    help="yt-dlp 的 cookies.txt（Netscape 格式），用于拿站点 CC / 会员清晰度；"
                         "B 站匿名字幕接口是关的，要 CC 就得给。脚本复制成临时副本再用，退出即删")
    ap.add_argument("--style", choices=["auto", "study", "tutorial"], default="auto",
                    help="写作体例：auto 按是否命中代码/界面自动选；tutorial 走图文教程模板")
    ap.add_argument("--max-stills", default="auto",
                    help="定点截图预算：auto=按 clamp(时长分钟×2, 6, 60) 算（默认）；0=不截断；"
                         "给整数则按该数封顶")
    ap.add_argument("--still-events", default="freeze,speech,keyword,cut",
                    help="取图事件源，逗号分隔：freeze / speech / keyword / cut / ocr")
    ap.add_argument("--subs-ocr", choices=["auto", "off", "on"], default="auto",
                    help="硬字幕整片 OCR：auto=教程体检测到字幕带就跑（3–8 分钟）；off=不跑")
    ap.add_argument("--focus", default="",
                    help="手工指定必拍时刻（mm:ss 逗号分隔，如 6:23,10:21），优先级最高")
    ap.add_argument("--target-section-len", type=float, default=90.0,
                    help="教程体分段目标段长（秒），只在 sections-mode=cut 时作为合并目标")
    ap.add_argument("--pick", choices=["auto", "sharp", "exact"], default="auto",
                    help="定点截图选帧：auto=冻结帧原样取、其余在 ±window 内挑最清晰（默认）；"
                         "sharp=一律挑；exact=不挑（与旧版完全一致）")
    ap.add_argument("--pick-window", type=float, default=sharp.PICK_PAD,
                    help=f"候选半窗秒数，默认 {sharp.PICK_PAD}（窗口不会跨切点/跨段）")
    ap.add_argument("--pick-count", type=int, default=sharp.PICK_COUNT,
                    help=f"候选帧数，默认 {sharp.PICK_COUNT}")
    ap.add_argument("--refresh-stills", action="store_true",
                    help="覆盖已被 note.md 引用的定点截图与图版（默认保留：截图原地不动、"
                         "图版新图另存 next/，避免成品笔记的图被悄悄换掉）")
    ap.add_argument("--refresh-meta", action="store_true",
                    help="成品笔记已存在时也覆盖 research.json / BRIEF.md（默认改写 *.new 并报差异）")
    ap.add_argument("--force", action="store_true", help="重下 / 重跑")
    args = ap.parse_args()

    if args.cookies:
        stage_cookies(args.cookies)

    for d in (TMP, NOTES, WORK / "jobs"):
        d.mkdir(parents=True, exist_ok=True)

    # 先落在 jobs/<来源指纹>/，拿到 id 后再迁到 notes/<id>/
    import hashlib
    fingerprint = hashlib.md5(args.source.encode("utf-8")).hexdigest()[:10]
    staging = WORK / "jobs" / fingerprint
    if staging.exists() and args.force:
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    media, meta = fetch(args.source, staging, args.force, want_subs=not args.no_subs,
                        want_auto=args.auto_subs)
    slug = slugify(meta, args.name)
    note_dir = NOTES / slug
    note_dir.mkdir(parents=True, exist_ok=True)

    # 素材与元信息移入正式目录（同名已存在则复用）
    target_media = note_dir / media.name
    if not target_media.exists() or args.force:
        shutil.copy2(media, target_media)
    store.save_json(note_dir / "meta.json", meta)

    evidence = note_dir / "evidence"
    assets = note_dir / "assets" / "grids"
    evidence.mkdir(exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)

    t_start = time.time()
    vw("probe", str(target_media), "--out", str(evidence / "probe"))
    timeline = json.loads((evidence / "probe" / "timeline.json").read_text(encoding="utf-8"))
    vw("plan", str(evidence / "probe" / "timeline.json"), "--max", "36", "--skeleton", "16",
       "--out", str(evidence / "shotlist.json"))

    dur = float(timeline.get("duration") or timeline["info"]["duration"])
    images: list[dict] = []
    note_md = note_dir / "note.md"
    # 成品笔记里嵌的图（含图版）——重跑时这些图的像素不能被静默换掉（D14/D22/D26）
    referenced = note_image_refs(note_dir)

    ov_out, ov_kept = decide_grid_target(assets / "overview", note_dir,
                                         referenced, args.refresh_stills)
    m = grid(target_media, ov_out, 0.0, dur, 25, 5)
    m = record_grid(m, assets / "overview", ov_kept, note_dir)
    if ov_kept:
        print("[grid] overview 被 note.md 引用，保留旧图；新图写到 "
              "assets/grids/overview/next/grid.png（要换加 --refresh-stills）", flush=True)
    images.append(slim(m, "全片粗看 5×5", assets / "overview" / "grid.png"))

    sections = plan_sections(timeline, args.max_sections, args.sections_mode,
                             auto_even=not args.no_auto_even)
    # 图版降级（2026-11-11）：教程/录屏只当"定位工具"，每 10 分钟一张、上限 5 张；
    # 口播/观点类画面同质，图版仍是"替代看片"的主力，保持每段一张。
    if args.style == "study":
        grid_sections = sections
    else:
        grid_sections, mark = [], 0.0
        for s in sections:
            if s["t0"] >= mark and len(grid_sections) < 5:
                grid_sections.append(s)
                mark = s["t0"] + 600
    for s in grid_sections:
        # 格数按段长给：图是"按张"计 token 的（3×3 与 5×5 同价），加格子不花钱，加图才花钱。
        # 分档让段内采样间隔落在 ~20s 以内；小字本来就不采信（契约 §二）。
        span = s["t1"] - s["t0"]
        cells, cols = (9, 3) if span <= 150 else (16, 4) if span <= 300 else (25, 5)
        out = assets / f"s{s['idx']:02d}"
        g_out, g_kept = decide_grid_target(out, note_dir, referenced, args.refresh_stills)
        m = grid(target_media, g_out, s["t0"], s["t1"], cells, cols)
        m = record_grid(m, out, g_kept, note_dir)
        if g_kept:
            print(f"[grid] 第 {s['idx']} 段被 note.md 引用，保留旧图；"
                  f"新图写到 {out.name}/next/grid.png（要换加 --refresh-stills）", flush=True)
        s["cells"], s["cols"] = cells, cols
        s["image"] = slim(m, f"第{s['idx']}段 {cols}×{cells // cols}", out / "grid.png")["image"]
        images.append(slim(m, f"第{s['idx']}段 {cols}×{cells // cols}", out / "grid.png"))

    old_research = previous_research(note_dir, args.refresh_meta)
    reused = bool(old_research.get("asr"))

    if args.no_asr:
        asr_segs, asr_meta = None, {}
    elif reused:
        asr_segs = old_research.get("asr") or []
        asr_meta = old_research.get("asr_meta") or {}
        print(f"[asr] 复用既有转写（{len(asr_segs)} 段）：note.md 已存在，"
              f"要按当前模型重跑加 --refresh-meta", flush=True)
    else:
        sub_info = meta.get("subtitles") or {}
        sub_path = (staging / sub_info["file"]) if sub_info.get("ok") and sub_info.get("file") else None
        asr_segs, asr_meta = asr(target_media, evidence / "asr", args.asr_lang,
                                 args.asr_model, sub_path=sub_path,
                                 sub_source=sub_info.get("source") or "subtitles")
        # staging 结尾会整个删掉，原始字幕（尤其双语 CC 的译文）另存一份进 evidence，
        # 否则"解析过什么"只剩 asr.json 里被 pick_bilingual 挑剩下的那一种语言
        if sub_path and sub_path.exists():
            sub_dir = evidence / "subtitles"
            sub_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sub_path, sub_dir / sub_path.name)
            print(f"[asr] 原始字幕已归档：evidence/subtitles/{sub_path.name}", flush=True)

    # 代码 / 界面定点截图：事件驱动（冻结帧 / 语音峰 / 关键词 / 切点首现），不再均匀分配
    kw_hits = sum(1 for s in (asr_segs or []) if _score(s["text"])[0] >= 3)
    freezes = timeline.get("freezes") or []
    style = args.style
    if style == "auto":
        # 静止画面多 = 作者反复停在某个界面上讲 = 教程/演示范畴
        style = "tutorial" if (len(freezes) >= 5 or kw_hits >= 2) else "study"

    # 硬字幕：教程体默认先定位再整片 OCR —— 参数与菜单名以字幕原文为准（比 ASR 准）
    ocr_segs: list[dict] = []
    ocr_meta: dict = {}
    if args.subs_ocr != "off" and style == "tutorial":
        try:
            info = hardsub.detect(target_media, dur, hardsub.DEFAULT_FRAMES)
            if info.get("found"):
                fake = {"timeline": {"height": timeline["info"]["height"],
                                     "width": timeline["info"]["width"]}}
                y0, y1, _w, fh = hardsub.band_pixels(info, fake)
                # 顶部 1/4 的"文字带"多半是播放器叠加层（B 站标题栏/UP 名），不是字幕
                if fh and (y0 + y1) / 2 < fh * 0.25:
                    print(f"[subs] 检测到的带在画面顶部 y={y0}–{y1}，疑似播放器 UI 叠加，"
                          "跳过 OCR（要强制跑：hardsub.py --subs-ocr --y0/--y1）", flush=True)
                    raise StopIteration
                h = y1 - y0
                y1 = min(fh, y1 + h)          # 双语字幕：向下补一行
                print(f"[subs] 检测到硬字幕 y={y0}–{y1}，整片 OCR（约 "
                      f"{int(dur / 2)} 帧，3–8 分钟）…", flush=True)
                segs, nf = hardsub.ocr_range(target_media, y0, y1, 2.0, dur)
                if segs:
                    path = hardsub.write_ocr_md(note_dir, y0, y1, 2.0, segs, nf)
                    ocr_segs = segs
                    ocr_meta = {"band": [y0, y1], "step": 2.0, "frames": nf,
                                "segments": len(segs),
                                "file": str(path.relative_to(note_dir)).replace("\\", "/")}
                    print(f"[subs] {len(segs)} 段字幕 → {ocr_meta['file']}", flush=True)
            else:
                print("[subs] 没检测到硬字幕，转写以 ASR 为准", flush=True)
        except StopIteration:
            pass
        except Exception as exc:                       # 引擎缺失/抽帧失败都不该死在这里
            print(f"[subs] 跳过硬字幕 OCR：{exc}", flush=True)

    # 预算：auto 按"时长分钟×2"给，clamp 到 6–60；0 = 不截断；整数 = 封顶
    raw_stills = str(args.max_stills).strip().lower()
    if raw_stills in {"auto", "", "none"}:
        budget = max(6, min(60, int(math.ceil(dur / 60 * 2))))
    elif raw_stills in {"0", "all"}:
        budget = 0
    else:
        budget = int(raw_stills)
    events = {e.strip() for e in str(args.still_events).split(",") if e.strip()}
    if ocr_segs:
        events.add("ocr")                  # 有字幕文本变化点，就多一类取图事件
    focus_times: list[float] = []
    if args.focus:
        for part in str(args.focus).split(","):
            focus_times += [float(x) for x in timefmt.parse_stamps(part)]
    if style == "study" and not focus_times:
        budget = min(budget or 3, 3)          # 学习笔记仍只取少量

    stills: list[dict] = []
    if asr_segs or style == "tutorial":
        still_dir = note_dir / "assets" / "stills"
        fps = float(timeline["info"].get("fps") or 25.0)
        all_cuts = cut_times(timeline)
        by_idx = {s["idx"]: s for s in sections}
        picks = pick_stills(timeline, sections, asr_segs, budget, style,
                            events=events, focus=focus_times, ocr_segs=ocr_segs)
        print(f"[still] 事件 {len(picks)} 张（预算 {budget or '不截断'}，事件源 "
              f"{'/'.join(sorted(events))}）", flush=True)
        for i, s in enumerate(picks, 1):
            stem = (f"s{s['section']:02d}_{int(s['t']):05d}" if s.get("section")
                    else f"kw_{i:02d}_{int(s['t']):05d}")
            path = still_dir / f"{stem}.png"
            rel = str(path.relative_to(ROOT)).replace("\\", "/")
            # note.md 里的引用是相对笔记目录写的（assets/stills/x.png），这里也要用同一基准比，
            # 否则 rel（notes/<id>/assets/...）永远匹配不上，守卫等于空转。
            rel_in_note = str(path.relative_to(note_dir)).replace("\\", "/")

            # 冻结帧来源：冻结窗内的帧本来就一样，挑帧零收益（评审实测像素差 0.00–0.04/255），
            # 唯一有差异的是冻结**前**那一帧——那正是要避开的过场，所以直接 exact。
            src = "freeze" if s["reason"].startswith("冻结帧") else (
                "keyword" if s.get("hits") else "midpoint")
            mode = args.pick if args.pick in {"sharp", "exact"} else (
                "exact" if src == "freeze" else "sharp")
            lo, hi = still_window(s["t"], dur, all_cuts, fps, by_idx.get(s.get("section")))
            if mode == "sharp" and min(s["t"] - lo, hi - s["t"]) < 0.05:
                mode = "exact"          # 贴着切点/段边界，没地方挑

            if should_keep_image(path, note_dir, referenced, args.refresh_stills):
                # 成品笔记已经引用了这张图：不静默换像素，只记账。
                picked = {"mode": "kept", "asked_t": round(s["t"], 3), "t": round(s["t"], 3),
                          "reason": "已被 note.md 引用，未覆盖（要换加 --refresh-stills）"}
                print(f"[still] 保留既有 {path.name}（note.md 引用中；--refresh-stills 可覆盖）",
                      flush=True)
            else:
                picked = extract_still(target_media, s["t"], path, lo=lo, hi=hi,
                                       mode=mode, count=args.pick_count, pad=args.pick_window)
                picked["source"] = src
                picked["sha256"] = sha256_of(path)

            stills.append({
                "t": round(picked["t"], 2), "section": s.get("section"),
                "reason": s["reason"], "hits": s["hits"], "text": s["text"],
                "pick": picked,
                "image": rel,
            })

    # 术语校正放在选帧之后：先算关键词命中，再改字，保证 picks/文件名不受词表影响
    raw_asr_segs = asr_segs
    if asr_segs and not reused:
        asr_segs, asr_meta = postprocess_asr(asr_segs, timeline, dur, note_dir, asr_meta)

    research = {
        "note_schema": 2,          # 验收规则版本：>=2 走"结构底线 + 图配跳转"的新口径
        "meta": meta, "slug": slug,
        "timeline": {
            "duration": dur, "content_kind": timeline.get("content_kind"),
            "advice": timeline.get("advice"), "fps": timeline["info"]["fps"],
            "width": timeline["info"]["width"], "height": timeline["info"]["height"],
            "motion_p95": timeline.get("motion_p95"),
            "cuts": [round(t, 2) for t in cut_times(timeline)],
            "freezes": timeline.get("freezes") or [],
            "silences": timeline.get("silences") or [],
        },
        "sections": sections,
        "images": images,
        "stills": stills,
        "style": style,
        "asr": asr_segs,
        "asr_meta": {**(asr_meta or {}), **({"hardsub_ocr": ocr_meta} if ocr_meta else {})},
        "elapsed_s": round(time.time() - t_start, 1),
    }
    # 成品笔记已经存在时：不覆盖它的取证包，改写 .new 并报差异。
    # 理由：note.md 的附录写着"5×5 + 8 张 3×3"这类采样口径，分段/格数一改它就成假话，
    # 而 note_audit 只查图片存在性与时间覆盖，查不出这种漂移。
    keep_meta = note_md.exists() and not args.refresh_meta
    suffix = ".new" if keep_meta else ""
    if not keep_meta:                      # 覆盖时顺手清掉上一轮留下的 .new，别让人拿错
        for stale in (note_dir / "research.new.json", note_dir / "BRIEF.new.md"):
            stale.unlink(missing_ok=True)
    research_path = note_dir / f"research{suffix}.json"
    store.save_json(research_path, research)
    if raw_asr_segs and not reused:      # 复用既有转写时不动原稿（它正是 note.md 的引文证据）
        write_transcript(note_dir, raw_asr_segs)          # 原稿：不校正
        if (asr_meta.get("terms") or {}).get("replaced"):
            asrfix.write_fixed_transcript(note_dir, asr_segs, asr_meta)
    write_brief(note_dir, meta, timeline, images, sections, asr_segs, stills, style,
                asr_meta, path=note_dir / f"BRIEF{suffix}.md")
    if keep_meta:
        old = {}
        old_path = note_dir / "research.json"
        if old_path.exists():
            try:
                old = store.load_json(old_path, default={})
            except ValueError:
                old = {}
        print(f"[meta] note.md 已存在，未覆盖取证包：")
        print(f"       research{suffix}.json　{meta_diff(old, research)}")
        print(f"       BRIEF{suffix}.md")
        print(f"       确认要覆盖：--refresh-meta", flush=True)

    if args.force:
        shutil.rmtree(staging, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"取证完成：{note_dir.relative_to(ROOT)}　用时 {research['elapsed_s']}s　体例 {style}")
    print(f"  图版 {len(images)} 张，转写 {len(asr_segs or [])} 段，定点截图 {len(stills)} 张")
    print(f"  下一步：按 {note_dir.relative_to(ROOT)}/BRIEF{suffix}.md 看图写 note.md")
    print(f"  写完后：py -3.14 pipeline/finish.py {slug}   （验收 + 导出，一条命令）")
    print(f"  单帧取图：py -3.14 pipeline/still.py {slug} --at 123.4 --crop 0,0,1280,400")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
