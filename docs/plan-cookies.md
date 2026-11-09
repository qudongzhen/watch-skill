# 方案：登录态（`--cookies`）——只读副本，不碰用户文件

> 状态：**已实施并实测**（2026-11-03）；决策见 `DECISIONS.md` D27。

## 1. 问题：出路写到了"导"，没写到"用"

D18 把 B 站字幕的结论写成「匿名拿不到，用户自己导 `cookies.txt`，然后 `yt-dlp --cookies`」。
但管线自己**从来没传过** `--cookies`：`ytdlp()`、`ytdlp_soft()`、以及字幕那三次调用，
argv 里都没有它。于是用户把文件导好了，跑出来仍然是 `danmaku-only`——**看起来像"这个视频没字幕"，
其实是管线没带登录态**。

## 2. 方案

1. `run.py` 加 `--cookies <file>`；`ytdlp()` / `ytdlp_soft()` 统一在 argv 前缀里注入；
2. 注入的不是原文件，而是**系统临时目录里的副本**：`%TEMP%/watch-skill-cookies/cookies-<pid>.txt`；
3. `atexit` 注册清理，`SystemExit` / 异常也会走到；
4. 原文件**只读不写**，凭据不进工作区（`.gitignore` 加 `cookies*.txt` / `*cookies.txt`）。

## 3. 为什么非要副本：yt-dlp 会回写

yt-dlp 退出时调用 `save_cookies()`，把（可能已被服务器 refresh 的）jar 写回 `--cookies` 指的文件。
实测把用户放在仓库外的 cookies 文件直接传进去（路径略），结尾是这样：

```
File "...\yt_dlp\cookies.py", line 1305, in open
    with open(file, 'w' if write else 'r', encoding='utf-8') as f:
PermissionError: [Errno 13] Permission denied: '<cookies 文件>'
[sandbox: file access denied under workspace-write mode]
```

**活干完了，命令还是红的**（exit 1）。三条路里选第三条：

| 做法 | 评价 |
|---|---|
| 放开仓库外的写权限 | 为了让工具"用一下"，去改用户的凭据文件，不值 |
| 忽略退出码 | 会把真正该报的错也吞掉 |
| **复制成临时副本** | 回写落在副本上，原文件零改动；进程结束副本即删 |

副作用是好的：同一次运行里几个 yt-dlp 子进程共享同一份已刷新的 jar，不会各写各的。

## 4. 验收

| 场景 | 期望 | 结果 |
|---|---|---|
| 不给 `--cookies` | argv 里没有 `--cookies` | ✅ `test_subs` |
| 给了之后再 `ytdlp()` | argv 含 `--cookies` 且指向副本 | ✅ `test_subs`（断言整个 argv） |
| 匿名 `--list-subs`（`BV1mMxG61EWD`） | `danmaku-only` | ✅ |
| 带 cookies（同一条） | `ok / zh / subtitles` | ✅ 实跑 `try_subtitles` |
| 带 cookies 但素材本身没 CC（`BV1uf8e6dEyA`） | `danmaku-only` + `logged_in=true`，说法指向素材 | ✅ 实跑 `run.py` |
| 原文件 | 存在、字节不变 | ✅ |
| 进程退出 | 副本消失，`cookie_args()` 退回空 | ✅ |

## 5. 顺带修掉的两处"拿到之后才发现"的问题

**（1）`danmaku-only` 的说法不实。** 原来只有一句"匿名被挡"。带登录态实测后这个说法就不对了——
**是这条视频没传 CC**。现在分档：

- 匿名 → 「匿名只拿到弹幕，没有 CC/AI 字幕」（附怎么导 cookies）；
- 带登录态仍只有弹幕 → 「带登录态仍只有弹幕（这条视频没传 CC）」，不再建议补 cookies。

这不是文字游戏：用户看到前一句会去折腾 cookies，看到后一句会去换素材。

**（2）双语 CC 解析成中英混排。** B 站这条 CC 是「英文原文 + 机翻中文」两行一条 cue，
原来的 `_clean()` 直接 `" ".join(lines)`，于是每个 segment 变成
`'These are the enemies… 这些是 我维多利亚时代恐怖游戏中的敌人。'`——**两种语言黏在一句里**，
写笔记时既不能引用也不能检索。

现在 `subs.pick_bilingual()` 在拼行前判一次：恰好两行、都够长、且一行汉字占比 ≥0.5 而另一行 ≤0.05
时，**只留第一行（原文）**。中文视频的双语 CC 是中文在前，英文视频是英文在前，同一条规则都成立；
普通"同一句被折成两行"因为两行同语言，不受影响。

实测（`BV1mMxG61EWD`）：修前 192 段全是混排；修后 192 段只剩英文原文，与 whisper 的单语输出一致。
被丢掉的译文没有丢——原始 `.srt` 仍归档在 `notes/<id>/evidence/subtitles/`。

## 6. 否决

- **环境变量隐式注入**（`YTDLP_*`）：出问题时没人知道 cookie 是谁加的，与 D3「显式优于隐式」冲突；
- **副本放 `work/`**：`work/` 虽然 gitignore，但进程被强杀时可能留残骸；系统临时目录清得更干净；
- **自动抓浏览器 cookie**：同 D18，Chromium 127+ 的 App-Bound Encryption 让
  `--cookies-from-browser` 报 `Failed to decrypt with DPAPI`，这条路在本机不可用。
