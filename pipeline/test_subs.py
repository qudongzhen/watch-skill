#!/usr/bin/env python
"""`pipeline/subs.py` 的自检：SRT / VTT 解析、合并、语言挑选。

    py -3.14 pipeline/test_subs.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import subs  # noqa: E402

FAILED: list[str] = []
SRT = """1
00:00:01,000 --> 00:00:03,500
<i>大家好</i>，今天讲按钮动画

2
00:00:03,500 --> 00:00:06,000
先看这段代码

3
00:00:06,000 --> 00:00:08,000
{\\an8}注意这里的 setter
"""

VTT = """WEBVTT
Kind: captions

NOTE 这是注释，不该被解析

cue-1
00:00:01.000 --> 00:00:03.500 align:start position:0%
大家好，今天讲按钮动画

00:00:03.500 --> 00:00:06.000
先看这段代码

00:00:03.500 --> 00:00:04.000
先看这段代码

00:00:06.000 --> 00:00:07.000
&nbsp;
"""

# `yt-dlp --list-subs` 的真实输出样式（B 站匿名只剩弹幕）
SUB_LIST_DANMAKU = """[info] Available subtitles for BV1YV411w72F_p3:
Language Formats
danmaku  xml
"""
SUB_LIST_REAL = """[info] Available subtitles for X:
Language Formats
danmaku  xml
zh-Hans  srt, vtt
en       vtt
"""
# YouTube 只有"自动字幕"时：人工表空的，自动表一大串
SUB_LIST_AUTO = """[info] Available automatic captions for Y:
Language Formats
en-orig  vtt, srt
en       vtt, srt
zh-Hans  vtt, srt
"""
SUB_LIST_BOTH = """[info] Available subtitles for Z:
Language Formats
zh-Hans  srt
[info] Available automatic captions for Z:
Language Formats
en       vtt
"""

# YouTube 自动字幕的真实形状：滚动行（每句把上一句一起带上）
ROLLING_VTT = """WEBVTT
Kind: captions
Language: en

00:00:01.900 --> 00:00:03.800
In my game Lexy's Spell, I have a lot of

00:00:02.000 --> 00:00:03.800
In my game Lexy's Spell, I have a lot of UI, and pretty much everything is

00:00:03.800 --> 00:00:05.200
UI, and pretty much everything is

00:00:03.800 --> 00:00:06.000
UI, and pretty much everything is animated.

00:00:06.000 --> 00:00:07.500
animated.
"""


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILED.append(name)


def tmpdir() -> Path:
    d = ROOT / "work" / "tmp" / "test_subs"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def test_srt() -> None:
    segs = subs.parse_srt(SRT)
    check("SRT 三段", len(segs) == 3, str(len(segs)))
    check("时间去标签都对", segs[0] == {"start": 1.0, "end": 3.5, "text": "大家好，今天讲按钮动画"},
          str(segs[0]))
    check("ASS 残留被清掉", segs[2]["text"] == "注意这里的 setter", segs[2]["text"])
    check("按时间排序", [s["start"] for s in segs] == sorted(s["start"] for s in segs))


def test_vtt() -> None:
    segs = subs.parse_vtt(VTT)
    check("VTT 忽略 NOTE/头", all("注释" not in s["text"] for s in segs), str(segs))
    check("cue 设置不影响时间", segs[0]["end"] == 3.5, str(segs[0]))
    check("重复 cue 被合并", len(segs) == 2, str([s["text"][:8] for s in segs]))
    check("空 cue 被丢掉", all(s["text"] for s in segs), str(segs))


def test_edge() -> None:
    check("空文本不崩", subs.parse_srt("") == [])
    check("没有箭头不产出", subs.parse_srt("1\n随便写写\n") == [])
    check("时间倒置的 cue 被丢", subs.parse_srt("1\n00:00:05,000 --> 00:00:02,000\n反向\n") == [])
    long_day = "1\n01:02:03,400 --> 01:02:05,000\n一小时后的字幕\n"
    s = subs.parse_srt(long_day)
    check("小时位解析正确", s and s[0]["start"] == 3723.4, str(s[:1]))
    no_ms = "1\n00:00:01,5 --> 00:00:02,25\n短毫秒\n"
    s2 = subs.parse_srt(no_ms)
    check("毫秒不足三位也能解析", s2 and s2[0]["start"] == 1.5 and s2[0]["end"] == 2.25, str(s2[:1]))


def test_pick() -> None:
    d = tmpdir()
    for name in ("source.en.srt", "source.zh-CN.srt", "source.zh-Hans.vtt"):
        (d / name).write_text("1\n00:00:01,000 --> 00:00:02,000\nx\n", encoding="utf-8")
    picked = subs.pick_file(d)
    check("优先 zh-Hans（即使它是 vtt）", picked is not None and picked.name == "source.zh-Hans.vtt",
          picked.name if picked else "None")
    (d / "source.zh-Hans.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nx\n", encoding="utf-8")
    check("同语言优先 .srt", subs.pick_file(d).name == "source.zh-Hans.srt")
    check("语言解析", subs.lang_of(Path("source.zh-Hans.srt")) == "zh-hans")
    empty = tmpdir() / "empty"
    empty.mkdir()
    check("没有字幕文件返回 None", subs.pick_file(empty) is None)


def test_parse_file() -> None:
    d = tmpdir()
    p = d / "source.zh-CN.srt"
    p.write_text(SRT, encoding="utf-8-sig")
    segs = subs.parse(p)
    check("按后缀分派 + 去 BOM", len(segs) == 3 and segs[0]["start"] == 1.0)
    v = d / "source.en.vtt"
    v.write_text(VTT, encoding="utf-8")
    check(".vtt 走 VTT 分支", len(subs.parse(v)) == 2)
    shutil.rmtree(d, ignore_errors=True)


def test_asr_prefers_subs() -> None:
    """离线验证"字幕优先"：不跑 whisper、写 asr.json、留档原字幕。"""
    import json
    import run

    base = tmpdir()
    out = base / "asr"
    out.mkdir()
    sub = base / "source.zh-CN.srt"
    sub.write_text(SRT, encoding="utf-8")
    fake = base / "fake.mp4"
    fake.write_bytes(b"\x00" * 32)

    segs, meta = run.asr(fake, out, "zh", "base", sub_path=sub)
    check("走字幕不走 whisper", meta.get("source") == "subtitles", str(meta))
    check("段数与文本正确", len(segs or []) == 3 and (segs or [{}])[0]["text"].startswith("大家好"),
          str((segs or [])[:1]))
    payload = json.loads((out / "asr.json").read_text(encoding="utf-8"))
    check("asr.json 标了来源", payload.get("source") == "subtitles"
          and payload.get("model") == "subtitles")
    check("原字幕文件留档", (out / "subtitles.srt").exists())


def test_asr_falls_back() -> None:
    """字幕坏了要退回 whisper，并把原因打出来（不静默降级）。"""
    import run

    base = tmpdir()
    out = base / "asr2"
    out.mkdir()
    bad = base / "source.zh-CN.srt"
    bad.write_text("这不是字幕文件\n", encoding="utf-8")
    fake = base / "fake.mp4"
    fake.write_bytes(b"\x00" * 32)

    called = []
    orig = run.vw

    def fake_vw(*args, **kw):
        called.append(args)
        raise SystemExit("模拟：whisper 不可用")

    run.vw = fake_vw
    try:
        segs, meta = run.asr(fake, out, "zh", "base", sub_path=bad)
    finally:
        run.vw = orig
    check("字幕解析不出段 → 退回 whisper", bool(called) and segs is None, str(meta))
    check("退回时记下失败原因", "failed" in (meta or {}), str(meta))


def test_try_subtitles() -> None:
    """`run.try_subtitles` 的四种结局（不联网，靠替身 yt-dlp）。"""
    import run

    base = tmpdir()
    job = base / "job"
    job.mkdir()
    fake = job / "fake.mp4"
    fake.write_bytes(b"\x00")
    orig = run.ytdlp_soft

    try:
        run.ytdlp_soft = lambda *a, **k: (0, "", "WARNING: Subtitles are only available when logged in")
        check("要登录 → needs-login", run.try_subtitles(job, "u")["reason"] == "needs-login",
              str(run.try_subtitles(job, "u")))

        run.ytdlp_soft = lambda *a, **k: (0, "", "")
        check("没有字幕 → no-subs", run.try_subtitles(job, "u")["reason"] == "no-subs")

        run.ytdlp_soft = lambda *a, **k: (1, "", "ERROR: boom")
        reason = run.try_subtitles(job, "u")["reason"]
        check("调用失败 → failed 且带原因", reason.startswith("failed") and "boom" in reason, reason)

        (job / "source.zh-Hans.srt").write_text(SRT, encoding="utf-8")
        run.ytdlp_soft = lambda *a, **k: (0, "", "")
        info = run.try_subtitles(job, "u")
        check("拿到文件 → ok 且带语言", info["ok"] and info["lang"] == "zh-hans", str(info))

        check("--no-subs 时不调用", run.try_subtitles(job, "u", enabled=False)["reason"] == "disabled")

        # 只列出弹幕 = 匿名拿不到文本轨，跟"这个视频没有字幕"分开报
        (job / "source.zh-Hans.srt").unlink()
        run.ytdlp_soft = lambda *a, **k: (0, SUB_LIST_DANMAKU, "")
        info = run.try_subtitles(job, "u")
        check("只列弹幕 → danmaku-only", info["reason"] == "danmaku-only", str(info))
        check("danmaku-only 带 cookies 提示", "cookies" in (info.get("hint") or ""), "")
        check("匿名时不标 logged_in", not info.get("logged_in"), str(info))

        # 带登录态仍只列弹幕 = 素材没传 CC，说法要换（D27）
        ck = base / "ck.txt"
        ck.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        run.stage_cookies(str(ck))
        try:
            info = run.try_subtitles(job, "u")
            check("登录后仍只弹幕 → 标 logged_in 且提示指向素材",
                  info.get("logged_in") is True and "没传 CC" in (info.get("hint") or ""), str(info))
            check("登录后的说明不再怪匿名", "匿名" not in run.subtitle_note(info),
                  run.subtitle_note(info))
        finally:
            run.cleanup_cookies()

        run.ytdlp_soft = lambda *a, **k: (0, SUB_LIST_REAL, "")
        info = run.try_subtitles(job, "u")
        check("列出真字幕但没下到 → no-subs（不是 danmaku-only）",
              info["reason"] == "no-subs" and "zh-Hans" in info["langs"], str(info))

        run.ytdlp_soft = lambda *a, **k: (0, SUB_LIST_AUTO, "")
        info = run.try_subtitles(job, "u")
        check("只有自动字幕 → auto-only", info["reason"] == "auto-only", str(info))
        check("auto-only 带质量提示", "whisper" in (info.get("hint") or ""), "")

        run.ytdlp_soft = lambda *a, **k: (0, SUB_LIST_BOTH, "")
        info = run.try_subtitles(job, "u")
        check("人工与自动都有 → 仍按 no-subs（人工没下到）",
              info["reason"] == "no-subs" and info["langs"] == ["zh-Hans"], str(info))
    finally:
        run.ytdlp_soft = orig


def test_subtitle_notes() -> None:
    """语言表解析 + 结果翻成人话。"""
    import run

    check("从 --list-subs 表里捞语言", run.listed_langs(SUB_LIST_DANMAKU) == ["danmaku"],
          str(run.listed_langs(SUB_LIST_DANMAKU)))
    check("多语言都能捞到",
          run.listed_langs(SUB_LIST_REAL) == ["danmaku", "zh-Hans", "en"],
          str(run.listed_langs(SUB_LIST_REAL)))
    check("表头之前的行不算语言", run.listed_langs("Language Formats\n\n[download] 1 item") == [])
    check("空输入安全", run.listed_langs("", None) == [])

    # 人工 / 自动两张表必须分开数：混在一起会把"只有自动字幕"误判成"有字幕"
    both = run.parse_subs_listing(SUB_LIST_BOTH)
    check("人工表只收人工", both["manual"] == ["zh-Hans"], str(both))
    check("自动表只收自动", both["auto"] == ["en"], str(both))
    check("只有自动表时人工为空", run.listed_langs(SUB_LIST_AUTO) == [])
    check("自动语言代码能捞到",
          run.auto_caption_langs(SUB_LIST_AUTO) == ["en-orig", "en", "zh-Hans"],
          str(run.auto_caption_langs(SUB_LIST_AUTO)))

    check("拿到了就不写提示", run.subtitle_note({"ok": True, "file": "a.srt"}) == "")
    check("本地文件不写提示", run.subtitle_note({"reason": "local-file"}) == "")
    note = run.subtitle_note({"reason": "danmaku-only"})
    check("danmaku-only 的提示说清是弹幕", "弹幕" in note, note)
    check("auto-only 的提示说清是机器字幕", "自动字幕" in run.subtitle_note({"reason": "auto-only"}))
    check("needs-login 的提示说清要登录", "登录" in run.subtitle_note({"reason": "needs-login"}))
    check("no-subs 的提示说清没字幕轨", "没有字幕轨" in run.subtitle_note({"reason": "no-subs"}))
    check("失败原因原样带出", "boom" in run.subtitle_note({"reason": "failed: boom"}))


def test_deroll() -> None:
    """滚动字幕（每句带上一句）要压成"每句只说一次"，且不能误伤正常字幕。"""
    # 取最长重叠：naive 的首个匹配会只剪掉 "c"，留下 "defghij"
    check("取最长后缀-前缀重叠",
          subs.strip_rollover("abcdefgh", "cdefghij") == "ij",
          subs.strip_rollover("abcdefgh", "cdefghij"))
    check("重叠不足 4 字不剪",
          subs.strip_rollover("看这段代码", "代码写错了") == "代码写错了",
          subs.strip_rollover("看这段代码", "代码写错了"))
    check("剪完去掉句首标点",
          subs.strip_rollover("一二三四五六七八", "五六七八，九十") == "九十",
          subs.strip_rollover("一二三四五六七八", "五六七八，九十"))

    rolling = [
        {"start": 0.0, "end": 2.0, "text": "第一句说的是这个"},
        {"start": 2.0, "end": 4.0, "text": "第一句说的是这个，然后接第二句"},
        {"start": 4.0, "end": 4.5, "text": "然后接第二句"},
        {"start": 4.5, "end": 6.0, "text": "然后接第二句，第三句开始"},
        {"start": 6.0, "end": 7.0, "text": "第三句开始"},
    ]
    check("滚动样本被识别", subs.looks_rolling(rolling))
    out = subs.deroll(rolling)
    check("压成三句、每句只说一次",
          [s["text"] for s in out] == ["第一句说的是这个", "然后接第二句", "第三句开始"],
          str([s["text"] for s in out]))
    check("整条重复的 cue 时间并入上一条",
          [s["end"] for s in out] == [2.0, 4.5, 7.0], str([s["end"] for s in out]))
    check("幂等：再跑一遍不变", subs.deroll(out) == out)

    # 正常字幕：相邻句尾撞下句开头（2 字）不该被当成滚动
    normal = [
        {"start": 0.0, "end": 1.0, "text": "先看这段代码"},
        {"start": 1.0, "end": 2.0, "text": "代码写错了"},
        {"start": 2.0, "end": 3.0, "text": "改成正确的写法"},
        {"start": 3.0, "end": 4.0, "text": "然后保存"},
    ]
    check("正常字幕不被判成滚动", not subs.looks_rolling(normal))
    check("正常字幕逐字不变", subs.deroll(normal) == normal)

    # 真实形状的 YouTube 自动字幕
    segs = subs.parse_vtt(ROLLING_VTT)
    check("真实滚动 VTT 压成三句",
          [s["text"] for s in segs] == ["In my game Lexy's Spell, I have a lot of",
                                        "UI, and pretty much everything is",
                                        "animated."],
          str([s["text"] for s in segs]))
    words = sum(len(s["text"].split()) for s in segs)
    check("词数不再被滚动行放大", words <= 20, f"{words} 词")


def test_bilingual() -> None:
    """B 站双语 CC（原文 + 机翻）：只留第一行，普通折行不受影响。"""
    en_zh = ["These are the enemies of my Victorian horror game.",
             "这些是 我维多利亚时代恐怖游戏中的敌人。"]
    check("英文在前 → 只留英文", subs.pick_bilingual(en_zh) == en_zh[:1], str(subs.pick_bilingual(en_zh)))

    zh_en = ["这些是我维多利亚时代恐怖游戏中的敌人。", "These are the enemies of my game."]
    check("中文在前 → 只留中文", subs.pick_bilingual(zh_en) == zh_en[:1], str(subs.pick_bilingual(zh_en)))

    wrap = ["An undead gentleman who howls to call the others,", "and a ghost who crawls along the walls."]
    check("同语言折行不动", subs.pick_bilingual(wrap) == wrap, str(subs.pick_bilingual(wrap)))

    check("两行都太短不动", subs.pick_bilingual(["是的", "对"]) == ["是的", "对"])

    single = ["只有一行字幕"]
    check("单行不动", subs.pick_bilingual(single) == single)

    bili = ("1\n00:00:00,000 --> 00:00:03,330\n"
            "These are the enemies of my Victorian horror game.\n"
            "这些是 我维多利亚时代恐怖游戏中的敌人。\n")
    segs = subs.parse_srt(bili)
    check("SRT 端到端：双语 cue 只剩原文",
          len(segs) == 1 and segs[0]["text"].startswith("These are"), str(segs))


def test_cookies() -> None:
    """`--cookies` 的临时副本：不写原文件、进程退出即删。"""
    import run

    run.cleanup_cookies()
    check("没给 --cookies 时不加任何参数", run.cookie_args() == [], str(run.cookie_args()))

    src = tmpdir() / "cookies_src.txt"
    src.write_text("# Netscape HTTP Cookie File\n"
                   ".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tfake\n", encoding="utf-8")
    tmp = run.stage_cookies(str(src))
    check("副本不是原文件、落在 watch-skill-cookies 目录",
          tmp != src and tmp.parent.name == "watch-skill-cookies", str(tmp))
    check("副本内容与原文件一致", tmp.read_text(encoding="utf-8") == src.read_text(encoding="utf-8"))
    check("cookie_args 指向副本", run.cookie_args() == ["--cookies", str(tmp)])
    check("原文件仍存在（不搬走用户的东西）", src.exists())

    # 参数要真的落进 yt-dlp 的 argv（不只是拼了个 list 没人用）
    seen: dict = {}
    orig_run = run.run
    run.run = lambda cmd, desc: (seen.setdefault("cmd", cmd), "")[1]
    try:
        run.ytdlp("--skip-download", "u")
    finally:
        run.run = orig_run
    check("ytdlp() 把 --cookies 带进 argv",
          "--cookies" in seen["cmd"] and str(tmp) in seen["cmd"], str(seen.get("cmd")))

    run.cleanup_cookies()
    check("清理后副本消失、参数退回空", not tmp.exists() and run.cookie_args() == [])

    try:
        run.stage_cookies(str(tmpdir() / "no_such.txt"))
        check("路径不存在时报错退出", False)
    except SystemExit:
        check("路径不存在时报错退出", True)


def main() -> int:
    print("pipeline/subs.py 自检\n")
    test_srt()
    test_vtt()
    test_edge()
    test_pick()
    test_parse_file()
    test_asr_prefers_subs()
    test_asr_falls_back()
    test_try_subtitles()
    test_subtitle_notes()
    test_deroll()
    test_bilingual()
    test_cookies()
    print(f"\n{len(FAILED)} 项失败" if FAILED else "\n全绿")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
