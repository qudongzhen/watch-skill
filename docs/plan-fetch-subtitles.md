# 实施方案：下载链路加固（竖屏格式兜底 + 站点字幕优先）

> 来源：`docs/ref-review-update-0.4.md` §2.1 / §2.2
> 状态：**已实施**（决策 D18）；验收见 §3——其中第 5、6 条因网络不通**未验**，如实标注
> 前置：D14 选帧、D15 转写后处理、D16 长片分档、D17 重跑守卫；文档整理与 `check_docs` 已入库

---

## 0. 当前进度（写给下一个接手的人）

已合入 master 的七次提交：

| 提交 | 内容 | 决策 |
|---|---|---|
| `b814154` | 定点截图三候选取最清晰（Laplacian 方差）+ 覆盖守卫 | D14 |
| `169477c` | 转写后处理：词表 / 静音一致性 / 体检，原稿不覆盖 | D15 |
| `28298b8` | 长片章节自动等分 + 按段长给格数 + `zoom.py` | D16 |
| `de10959` | 成品笔记存在时取证包改写 `*.new` + 机器生成取证说明 | D17 |
| `9b26dc3` | 文档整理：AGENTS 重写、NOTE_CONTRACT、docs 索引、`check_docs` | — |
| `1f4ec30` | doctor/selfcheck 补 Pillow 检查 | — |
| `19b2b12` | 参考仓库 0.1→0.4.4 增量观察 | — |

基线状态：`selfcheck` **19 项 0 FAIL / 0 WARN**；`test_sharp` 26 项、`test_asrfix` 33 项、`test_meta` 12 项全绿；
8 篇既有笔记 `note_audit` 均 0 FAIL。

**本次要做的是两件"下载链路"的事**，都在 `pipeline/run.py` 的 `fetch()` / `asr()` 附近，不碰 vendor。

---

## 1. 问题一：竖屏 1080p 会整条下载失败（潜在 bug）

现在（`pipeline/run.py:117`）：

```python
"-f", "bv*[height<=1080]+ba/b[height<=1080]",
```

yt-dlp 把 9:16 视频报成 `360x640 / 480x852 / 720x1280 / 1080x1920`，**`height` 是长边**。
所以 1080×1920 的竖屏：`height=1920`，`[height<=1080]` 一条都不匹配 → `Requested format is not available`。

我们现有竖屏样本 `BV1Xdnc6jEbL` 是 **480×852**（height 852 ≤ 1080），**侥幸通过**——所以这个坑一直没暴露。

**改法**（参考实现 0.4.0 的做法）：选择器末尾加无过滤兜底，并按 `res:`（yt-dlp 的短边指标）排序：

```python
"-f", "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
"-S", "res:1080",
```

`-S` 只影响排序，不改匹配；兜底分支保证"最坏也能下到一版"。

## 2. 问题二：站点字幕轨从来没试过

B 站的字幕是**独立轨道**，下载下来的 mp4 里通常没有，所以"读容器内嵌流"永远为空。
拿不到就退回 whisper（我们现在就是直接 whisper）。而站点字幕是作者原文——不错字、带准确时间码、还省 1–4 分钟。

**现状的关键约束**：B 站字幕**要登录**。没登录时 yt-dlp 只给弹幕，并警告
`Subtitles are only available when logged in`。本轮实测（2026-10-29 23:4x）连 `--list-subs` 都超时，
说明**本机当前网络到 B 站不稳定**——所以本轮验收只能做到离线部分，联网部分要留成"下次网络好的时候验"。

### 2.1 设计

```
fetch()
  ├─ 下载（格式串带兜底）
  └─ 尝试字幕：yt-dlp --skip-download --write-subs --convert-subs srt
        --sub-langs "zh-Hans,zh-CN,zh,ai-zh,en" -o source.%(ext)s
        失败不影响主链路，把原因记进 meta["subtitles"] = {"ok":..,"reason":..}

asr()
  ├─ 有字幕文件且没加 --no-subs → 解析成段，写 evidence/asr/asr.json（source="subtitles"）
  └─ 否则照旧跑 whisper
```

**为什么放在 `fetch()` 里**：只有它知道原始 URL；本地文件没有"站点字幕"这回事。

**为什么单独一次调用**：主下载带 `--write-subs` 时，字幕失败会拖累整次下载的退出码；
分开调用才能做到"字幕拿不到不影响视频"。

### 2.2 新文件 `pipeline/subs.py`（纯函数 + 一个取文件的壳）

```python
def parse_srt(text: str) -> list[dict]      # [{start, end, text}]
def parse_vtt(text: str) -> list[dict]
def parse(path: Path) -> list[dict]         # 按后缀分派
def pick_file(folder: Path) -> Path | None  # 语言优先级 zh-Hans > zh-CN > zh > ai-zh > en
def merge(segs, gap=0.5) -> list[dict]      # 相邻同文本合并（VTT 有重复 cue）
```

解析要处理的真实脏东西：
- SRT 序号行、`-->` 两边的 `,` 与 `.` 两种小数点、`00:00:01,000` 与 `1:02:03.400`；
- 多行文本、HTML 标签 `<i>` / `<c.color>`、ASS 残留 `{\an8}`；
- VTT 的 `WEBVTT` 头、cue 设置（时间戳后面的 `align:start position:0%`）、`NOTE` 块；
- 空 cue（时间跨度但无文本）→ 丢掉，不要让空段进 `asr`。

### 2.3 接入 `run.py`

| 位置 | 改动 |
|---|---|
| `fetch()` | 格式串加兜底 + `-S res:1080`；下载完尝试字幕；把结果写进 `meta["subtitles"]` |
| `asr()` | 改签名收 `sub_offset`/`sub_path`：有字幕先解析；解析失败自动退回 whisper（并打印原因） |
| CLI | 新增 `--no-subs`（默认尝试站点字幕） |
| `research.json` | `asr_meta.asr_backend` 里已有 `language`/`model`；补 `source: "subtitles" \| "whisper"` |
| `BRIEF.md` | 转写体检那一行注明来源（字幕 / whisper） |

**与 D15 的关系**：字幕文本同样要走"繁转简 → 词表校正 → 体检"那条链，**不另起一套**。
字幕通常已经是简体，但 `to_simplified` 幂等，不需要特判。

---

## 3. 验收

| # | 判据 | 判法 | 结果 |
|---|---|---|---|
| 1 | SRT/VTT 解析：小数点是逗号/句点、多行、标签、NOTE、空 cue、小时位、不足三位毫秒 | `pipeline/test_subs.py` 单测 | ✅ 通过 |
| 2 | 语言优先级：同时有 zh-Hans 与 en → 选 zh-Hans；同语言优先 `.srt` | 单测（临时目录放文件） | ✅ 通过 |
| 3 | 相邻重复 cue 合并（含重叠）；空 cue / 时间倒置被丢 | 单测 | ✅ 通过 |
| 4 | 字幕解析失败 → 退回 whisper，原因不吞（`meta["failed"]`） | 单测（替身 `run.vw`） | ✅ 通过 |
| 5 | 字幕优先：有字幕时不启 whisper，写 `asr.json`（`source=subtitles`）并留档原文 | 单测（替身 media，不联网） | ✅ 通过 |
| 6 | 结局分类：`needs-login` / `danmaku-only` / `auto-only` / `no-subs` / `failed: …` / `ok` | 单测（替身 `ytdlp_soft`） | ✅ 通过 |
| 7 | 本地文件不尝试站点字幕，且 `meta.subtitles.reason == "local-file"`；BRIEF 体检行显示"whisper 转写" | 端到端实跑 `notes/BV1uf8e6dEyA/source.mp4`（61s） | ✅ 通过 |
| 8 | 格式串：竖屏/横屏都能给出候选格式 | `yt-dlp -F` 实跑 | ⚠️ **2026-10-31 补验：原格式串在竖屏上选错**（见 §3.1） |
| 9 | 站点字幕：登录 cookie 下能拿到 CC 并写进 `evidence/asr/` | 实跑一次 B 站视频 | ⚠️ **2026-10-31 补验：匿名拿不到，且原因能说清**（见 §3.2） |
| 10 | 不回归：`selfcheck` 19 项、六个 `test_*.py` 全绿 | 实跑 | ✅ 通过 |

> 第 8、9 条原来明写"未验"。联网后按 §4 补验，两条都补了，而且**第 8 条是一个真 bug**。

## 3.1 补验（2026-10-31）：竖屏上原格式串选错了格式

原选择器 `bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b` 的设计假设是"竖屏 height 是长边，
所以过滤会一条都不剩，由末尾兜底接手"。实测**不成立**：过滤会匹配到 480×854，而
`/` 是"优先前者"，兜底根本轮不到。

| 素材 | 分辨率 | 原选择器实际选到 | 新选择器选到 |
|---|---|---|---|
| YouTube Shorts `xWxCtw2ubXw` | 1080×1920 | **231+234（480×854）** | 312+234（1080×1920） |
| YouTube Shorts `9AME0-JP47U` | 1080×1920 | — | 399+251（1080×1920） |
| YouTube 4K 横屏 `VVU3GLgk-R0` | 3840×2160 | — | 399+251（1920×1080） |
| B 站 `BV1EAKH6GE7C` | 1920×1080 | 1080P | 100026+30280（1920×1080，不变） |
| B 站 `BV1Xdnc6jEbL` | **1080×1920** | → 取证包是 **480×852** | 100026+30280（1080×1920） |

**已经在真实语料上造成损失**：`notes/BV1Xdnc6jEbL` 的 `research.json` 记的
`timeline.width×height` 是 **480×852**——正是这个 bug 的结果（面积只有 1080×1920 的 1/5）。

**当天已按新选择器重做这一篇**（`--force --refresh-stills --refresh-meta`）：现在是 1080×1920，
图版与 3 张定点截图重出，引文帧 `t00012_quote.png` 从 480×852 换成 1080×1920（字幕一眼可读）。
切点 172 → 175（分辨率变了，切点检测跟着变），附录采样口径同步；`note_audit` 0 FAIL。

**修法**：不写尺寸过滤，只留 `bv*+ba/b` + `-S res:1080`。因为 `-S res:1080` 里的 `res`
是**短边**（竖屏 1080、横屏 1080），排序自动把两种画幅都排到 1080 档，不需要自己判方向。
实测三种画幅都对（见上表）。

> 老笔记**不自动重做**（`--refresh-stills` 守卫在，见 D14/D17）。要重做某篇：
> `py -3.14 pipeline/run.py <url> --force --refresh-stills --refresh-meta`，跑完人工过一眼。

## 3.2 补验（2026-10-31）：B 站匿名拿不到字幕，但"没有"和"要登录"能分开

`--list-subs` 抽查 4 个 B 站视频（`BV1EAKH6GE7C` / `BV1Xdnc6jEbL` / `BV1W3go6tERZ` /
`BV1uf8e6dEyA`）→ **4/4 只列 `danmaku  xml`**，没有一个 CC/AI 字幕轨。

再看接口层：`api.bilibili.com/x/player/v2`（匿名，带 UA/Referer）对其中 3 个（含标题写着
**「中英文字幕」**的 `BV1YV411w72F`）返回 `subtitle.subtitles = []`、`allow_submit = false`。
所以匿名拿不到不是"这个视频没字幕"，而是**平台对匿名把字幕接口关了**。

**改动**：`try_subtitles()` 新增 `danmaku-only` 结局（只列出弹幕 = 没有可用的文本轨），
和 `no-subs`（真没有字幕轨）分开；`meta.subtitles.hint` 写明原因与出路（导 `cookies.txt`
后 `yt-dlp --cookies`）；`BRIEF.md` 的转写小节会写一句"站点字幕：匿名只拿到弹幕……"。
`subtitle_note()` 是纯函数，单测覆盖全部结局。

## 3.3 补验（2026-10-31）：YouTube 的"自动字幕"是滚动行，这轮不接

`aagPe4dvM2M`（Lexispell 那条片子的英文原版，正好有 `zh-Hans/zh-Hant/en-orig/en`）跑全流程，
`try_subtitles` 却报 `no-subs`。查下来：`--list-subs` 打出的是 **"Available automatic captions"**
（平台自动生成），不是作者上传的人工字幕，`--write-subs` 自然什么都下不到。

顺手量了一下自动字幕值不值得接（同一视频、与我们 whisper-base 的转写比）：

| | 段数 | 词数 | 开头 |
|---|---|---|---|
| whisper-base | 245 | 1828 | `In my game Lexi's bell, I have a lot of UI…` |
| YouTube 自动字幕（en） | 506 | **5357** | `In my game Lexy's Spell, I have a lot of / In my game Lexy's Spell, I have a lot of UI…` |

词数虚高 3 倍，因为 YouTube 的 VTT 是**滚动行**：每句把上一句一起带上（`subs.merge` 只能
合并完全相同的相邻 cue，接不住"逐句变长"的滚动）。两条都听错了游戏名（`Lexispell` 被听成
`Lexy's Spell` / `Lexi's bell`），所以自动字幕并不比 whisper 准。

**结论**：自动字幕**默认不接**——它不是作者原文（D18 的立论前提），还得先写"去滚动"
才能用，而质量并不更高。`try_subtitles` 新增结局 `auto-only` 把这件事说清楚，
BRIEF 里会写"站点字幕：只有平台自动字幕（机器生成、非作者原文），已跳过"。
真要接，前置条件是给 `subs.py` 加去滚动，再去谈质量。

## 4. 联网后怎么验（给下一次）

```powershell
# 1) 格式选得对不对（不下载；竖屏要看 RESOLUTION 列是不是 1080x1920）
py -3.14 -m yt_dlp --skip-download -f "bv*+ba/b" -S res:1080 --print "%(format_id)s %(width)sx%(height)s" "<url>"

# 2) 字幕有没有、是不是只有弹幕
py -3.14 -m yt_dlp --skip-download --list-subs "<url>"

# 3) 真跑一次取证，看 research.json 的 asr_meta.asr_backend.source 与 meta.subtitles.reason
py -3.14 pipeline/run.py "<url>" --name zz-subs
```

## 5. 风险与回退

| 风险 | 缓解 |
|---|---|
| 字幕时间码与视频有偏移（站点字幕常见） | 体检里报"字幕覆盖 0–N 秒 vs 素材时长"；偏移大就 `--no-subs` 重跑 |
| 字幕是机翻/错的（UP 主自己也不太对） | 仍然比 ASR 强（至少带准确时间码）；词表照样能改；BRIEF 注明来源 |
| 多一次 yt-dlp 调用让下载变慢 | 只在 `--no-subs` 没给时跑一次；失败 3 秒内返回 |
| 格式串兜底会下到低清 | `-S res:1080` 优先；真要低清也只是"能看"的降级，比失败好 |
| 改动碰到主链路 | 两处都在 `fetch()`/`asr()` 边缘；`--no-subs` + 老格式串可一键回到旧行为（回退：`git revert` 或 `--no-subs`） |
