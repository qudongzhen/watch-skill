# watch-skill · 视频链接 → 学习笔记

把一个视频链接变成一篇**能核对**的中文学习笔记：带时间戳大纲、分章要点、关键帧图，
以及"这条结论是从第几秒的第几张图看出来的"。

核心不是"让模型看视频"，而是**分工**：

```
ffmpeg 逐帧量测（零 token） →  定位用图版 + 事件驱动取帧（几十张全屏帧） →  模型看图 + 写笔记
```

一个 7 分钟视频，模型通常只看 **20–40 张**：1 张全片图版定位，其余是"冻结段 / 语音峰 / 切点首现 / 字幕变化"
这些事件处的全屏帧，参数步骤再配一张裁切放大。这仍然比"逐帧喂多模态模型"便宜一到两个数量级。
方法论来自 [`CFITCorporation/video-watch-skill`](https://github.com/CFITCorporation/video-watch-skill)，
本仓库把它的取证能力接成了"链接进、笔记出"的管道。

功能清单与每条的实现位置见 [`docs/FEATURES.md`](docs/FEATURES.md)；为什么这么做、否决过什么见 [`DECISIONS.md`](DECISIONS.md)。

> **平台**：Windows + Python 3.14 实测（`py -3.14`）。便携版 ffmpeg/ImageMagick、`ocr_win.ps1`、
> `pipeline/compat/` 都是按这台机器写的，Linux / macOS 需自行替换。
> **许可**：Apache-2.0（见 [`LICENSE`](LICENSE)）；第三方来源与设计借鉴见 [`NOTICE`](NOTICE)。

## 适用范围与现状

**当前主攻：教程 / 录屏**（软件操作、参数讲解、代码演示）。这条管线不是"什么视频都能吃"，边界如下：

| 维度 | 状态 |
|---|---|
| 素材类型 | 教程/录屏为主；口播讲座次之；剧情/评测只零星跑过 |
| 时长 | 实测 5–40 分钟，20–40 分钟最佳 |
| 画幅 / 编码 | 16:9 与 9:16 都跑过；1080p AV1 正常 |
| 语言 | 中英原声都跑过（英文要 `--asr-lang en`，自动检测会把英文判成中文，见 D11） |
| **B 站** | **已兼容并实测**：下载、分段、图版、转写、`?p=N&t=秒` 跳转全链路 |
| **YouTube** | **只测过下载**（Shorts 1080×1920）；站点字幕、`&t=秒` 跳转、教程体全流程未验证 |
| 其他站点 | 未支持：yt-dlp 能下，但字幕解析与跳转规则没适配 |
| 硬字幕 | 教程体默认检测；命中就整片 OCR 到 `evidence/subtitles_ocr.md`，参数以字幕为准 |
| 导出 | md / html / zip；**不做 docx**（需另装 pandoc，且 Word 的排版不可控） |
| 运行环境 | Windows + Python 3.14 |

## 快速开始

> 换人或换 AI 接手：先读 [`AGENTS.md`](AGENTS.md)（操作须知、禁区、交接清单），
> 再跑 `py -3.14 pipeline/selfcheck.py` 确认工作区是好的。为什么这么做见 [`DECISIONS.md`](DECISIONS.md)。

```powershell
# 0. 装环境：ffmpeg / ImageMagick 便携版 + Python 依赖（仓库不含 tools/ 与 pydeps/，新克隆必须先跑这两条）
py -3.14 scripts/bootstrap.py
py -3.14 scripts/install_pydeps.py yt-dlp faster-whisper "av==18.1.0" Pillow rapidocr-onnxruntime opencc-python-reimplemented

# 0.5 配置：复制一份，改成你机器上的 ffmpeg / ImageMagick / 字体路径（该文件不入库）
copy vw.config.example.json vw.config.json

# 1. 自检（缺什么、怎么补，都会说）
py -3.14 pipeline/doctor.py

# 2. 取证：下载 → 量测 → 分段 → 图版 → 转写（约 1–3 分钟/7 分钟视频）
py -3.14 pipeline/run.py "https://www.bilibili.com/video/BV1Xdnc6jEbL"

# 3. 按 BRIEF.md 看图，写 notes/<id>/note.md
#    需要核对某句话的原文时：
py -3.14 pipeline/still.py BV1Xdnc6jEbL --at 12 --crop 0,655,480,195 --zoom 2.2

# 3.5 某一步的顺序/参数看不清（图版间隔太大）时，要一张密集图版：
py -3.14 pipeline/zoom.py BV1Xdnc6jEbL --at 613 --window 8

# 4. 写完只跑这一条：验收（形式 + 可回溯性）→ 导出
py -3.14 pipeline/finish.py BV1Xdnc6jEbL
#    只想看检查结果不导出：py -3.14 pipeline/note_audit.py BV1Xdnc6jEbL
```

第 2 步跑完会打印"下一步"。写作规范在 [`pipeline/NOTE_CONTRACT.md`](pipeline/NOTE_CONTRACT.md)。

## 产物长什么样

```
notes/BV1Xdnc6jEbL/
├─ note.md            ← 成品（写作者填；run.py 永不覆盖）
├─ BRIEF.md           ← 任务书：图版清单、分段、定点截图、取证说明、硬规则
├─ research.json      ← 机器摘要：元信息 + 每格图版的时间映射 + 转写 + 截图挑选记录
├─ terms.json         ← 这一集的转写词表（可选）
├─ assets/grids/      ← 图版（定位用）：overview 5×5 全片 + 每 10 分钟 1 张（教程体；口播体每段一张），都带 manifest.json
├─ assets/stills/     ← 全屏定点截图：事件驱动候选（冻结/语音峰/关键词/切点）+ 手工补拍，代码/参数只能从这里读
├─ evidence/          ← timeline.json / shotlist.json / asr.json / transcript.md / transcript.fixed.md
└─ source.mp4
```

`note.md` 已存在时重跑，取证包会写成 `research.new.json` / `BRIEF.new.md` 并打印差异，
不会动成品；确认要覆盖再加 `--refresh-meta`。

格式样例：[`examples/sample-note.md`](examples/sample-note.md)（**虚构模板**：标题、来源、引文全是占位，
只演示结构、时间戳写法与置信度分级）。**本仓库不含 `notes/`**——成品笔记、截图、转写稿与视频本体都在本地生成，
不随代码分发。

## 两条最容易踩的规则（完整四条见 `pipeline/NOTE_CONTRACT.md` §二）

另外两条写作约定，验收器会查：

- **每张图下面一行图注，按内容手写**（`*图 N：这张图证明了什么、该看哪几个数（mm:ss）*`）。
  照抄 Alt、或写成"图 N：画面（时间）"会被判为机械图注。
- **验收按 `note_schema` 分档**：新取证包（`research.json` 里 `note_schema >= 2`）查"开头结构 / 图注 / 图配跳转 / 裁切图成对 / 可读性"，
  字数不设限；旧包沿用旧规则，不补不改。

1. **图版里的文字一律不采信。** 上游项目实测过：3×3 图版里同一个时间码被"读"出九个不同值；
   还"读"出过一组画面上根本不存在的标题。分辨率不足时视觉不会报"读不到"，它会**渲染一个合理内容**。
   → 要写进笔记的原文，必须 `still.py --crop` 放大看过。示例笔记里"贵是贵点，但吃了不烧心"
   就是从原帧读的，转写稿里那句是错的。
2. **时间映射只信 manifest。** 图里烧的索引只是给人眼核对用的，"第几行第几列 = 第几秒"
   以 `assets/grids/*/manifest.json` 为准。

## 把笔记带走

笔记写完后**只跑一条命令**：

```powershell
py -3.14 pipeline/finish.py BV1EAKH6GE7C
```

它先验收，0 FAIL 才导出；验收不过就直接停，不会产出半成品。

输出按**笔记标题**建文件夹，一个文件夹里三种形态：

```
exports/
  我是如何在我的游戏《Lexispell》中轻松制作 UI 动画的/
    我是如何在我的游戏《Lexispell》中轻松制作 UI 动画的.md      ← 图片内嵌，0.9 MB
    我是如何在我的游戏《Lexispell》中轻松制作 UI 动画的.html    ← 浏览器打开可打印 PDF
    我是如何在我的游戏《Lexispell》中轻松制作 UI 动画的.zip     ← md + 图片文件夹，解压即用
```

| 形态 | 什么时候用 |
|---|---|
| `.md` | 发消息、丢进笔记软件；单文件、图内嵌、零依赖 |
| `.html` | 想排版好看或存 PDF |
| `.zip` | 要拿到原始 PNG，或在 Obsidian/VSCode 里按文件夹打开 |

图默认压成 webp（限宽 1600、q88，8 张图 5.0 MB → 0.93 MB，代码截图仍逐行可读）；
要无损原图：`py -3.14 pipeline/finish.py <id> --no-compact`。

## 命令参考

| 命令 | 作用 |
|---|---|
| `pipeline/doctor.py` | 环境自检（ffmpeg 滤镜 / ImageMagick / 字体 / yt-dlp / 转写） |
| `pipeline/run.py <url\|文件>` | 全流程取证，产物落 `notes/<id>/` |
| `pipeline/run.py <url> --no-asr` | 跳过语音转写（口播不重要的素材，省 1–4 分钟） |
| `pipeline/run.py <url> --no-subs` | 不尝试站点字幕（默认会试：站点的 CC 是作者原文，比转写更准也更快；B 站匿名只给弹幕，见 D18） |
| `pipeline/run.py <url> --auto-subs` | 人工字幕没有时也接受**平台自动字幕**（机器生成；VTT 滚动行已由去滚动压平，D21） |
| `pipeline/run.py <url> --cookies cookies.txt` | 带登录态跑：B 站匿名字幕接口是关的，给 Netscape 格式的 cookies.txt（**放在仓库外**）才能拿到 CC。脚本复制成临时副本、退出即删，不回写原文件（D27） |
| `pipeline/run.py <url> --asr-model small` | 换更大的 Whisper（更准、更慢，CPU 上约为 0.6× 实时） |
| `pipeline/run.py <url> --max-sections 5` | 控制分段数（决定笔记章节粒度） |
| `pipeline/run.py <url> --style tutorial` | 图文教程体例：一步一件事，事件驱动抓全屏图，写成"步骤"式笔记 |
| `pipeline/run.py <url> --max-stills auto` | 取图预算（默认 `auto` = 时长分钟×2，6–60 封顶；`0` = 不截断） |
| `pipeline/run.py <url> --focus 6:23,10:21` | 手工指定必拍时刻（优先级最高，不受预算截断） |
| `pipeline/run.py <url> --still-events freeze,speech,keyword,cut` | 取图事件源（默认四项全开；`ocr` 需先跑字幕 OCR） |
| `pipeline/run.py <url> --pick exact` | 关掉选帧（默认 `auto`：在 ±0.25s 内三选一取最清晰，冻结帧不挑）。见 `DECISIONS.md` D14 |
| `pipeline/run.py <url> --refresh-stills` | 重跑时覆盖**已被 note.md 引用**的定点截图（默认保留，避免成品笔记的图被悄悄换掉） |
| `pipeline/run.py <url> --refresh-meta` | 重跑时覆盖**成品笔记的** `research.json` / `BRIEF.md`（默认改写成 `*.new` 并打印结构性差异） |
| `pipeline/still.py <id> --at 12,95 --crop x,y,w,h --zoom 3` | 取单帧 / 裁切放大（读字唯一可信通道） |
| `pipeline/still.py <id> --at 424 --pick sharp` | 该秒附近挑最清晰的一张（不会跨切点，会打印挪了多少） |
| `pipeline/crop.py <id> --at 326` | 找候选裁切框（顶部参数条 / 右侧属性栏 / 底部字幕带），只报告不产图 |
| `pipeline/crop.py <id> --at 326 --write` | 按候选 1 生成 `*_zoom1.png`（输出宽约 1400px、2–4×、高 ≤1000，按需生成不铺满） |
| `pipeline/test_sharp.py` | 选帧模块的自检（手算 oracle + 亮度守卫 + e2e，26 项） |
| `pipeline/asrfix.py <id>` | 转写后处理报告：词表能改几处、哪几句音量低到听不见、转写体检（不写回） |
| `pipeline/asrfix.py <id> --write` | 写 `evidence/transcript.fixed.md` + `research.json.asr_meta`（**不动** `transcript.md` 原稿） |
| `pipeline/test_asrfix.py` | 转写后处理的自检（词表语义 + 静音区间 + 体检，33 项） |
| `pipeline/test_subs.py` | 字幕链路自检（SRT/VTT 解析 + 六种结局分类 + 去滚动 + 双语 CC + `--cookies` 副本，79 项） |
| `pipeline/hardsub.py <id> --detect` | 找画面里的硬字幕带（**实验性**：带会裁宽、混进相邻 UI 文字，见 D19） |
| `pipeline/hardsub.py <id> --apply --limit 20` | 硬字幕 OCR 对照 ASR："原/改"两行逐条列出（引擎 rapidocr，缺则 Windows 自带；**只报告，不改转写**） |
| `pipeline/hardsub.py <id> --apply --y0 900 --y1 1000` | 自动带不对时手动指定字幕带（原分辨率像素） |
| `pipeline/hardsub.py <id> --subs-ocr` | 整片字幕 OCR → `evidence/subtitles_ocr.md`（参数、菜单名以字幕为准；约 3–8 分钟/20 分钟素材） |
| `pipeline/hardsub.py <id> --subs-ocr --line top` | 字幕只有一行时指定读哪行（`auto` 默认按有没有中文选） |
| `pipeline/test_hardsub.py` | 硬字幕自检（定位 + LCS 判据 + 去带外文字 + 引擎选择，30 项） |
| `pipeline/quote_check.py <id>` | 引文出处体检：每条引文是「有出处 / 仅时刻 / 无出处」（不判真假，D20） |
| `pipeline/test_quote_check.py` | 引文体检自检（抽取 + 分类 + 退出码，18 项） |
| `pipeline/test_check_docs.py` | 文档体检器自检（命令段切分 + 开关归属 + 全库一致，10 项） |
| `pipeline/test_shared.py` | 共享工具自检（时间戳/依赖/JSON 读写三件共享工具，35 项） |
| `pipeline/test_notes.py` | 取图与验收自检（三道闸、`--focus`、跳转链接分平台、`note_schema` 分档，21 项） |
| `pipeline/test_meta.py` | 取证说明生成 / 重跑差异摘要的自检（41 项） |
| `pipeline/zoom.py <id> --at 613 --window 8` | 按需密集图版：8 秒 25 帧（0.33s/帧），某一步看不清时用 |
| `pipeline/run.py <url> --no-auto-even` | 长片（>15min）切点 ≤3 个时默认自动等分；加这个保持按切点分 |
| `pipeline/note_audit.py <id>` | 笔记验收：时间戳、图片存在、秒数可回溯、置信度段、占位符；有 FAIL 退出码为 1 |
| `pipeline/finish.py <id>` | **写完笔记只跑这条**：验收 → 导出到 `exports/<笔记标题>/`（md + html + zip） |
| `pipeline/finish.py <id> --no-compact` | 同上，但保留无损原图（不压 webp） |
| `pipeline/export.py <id>` | 单独导出（finish 的第二步；日常不用） |
| `pipeline/selfcheck.py` | 工作区自检：环境 + 脚本编译 + 既有笔记验收 |
| `scripts/bootstrap.py` | 重装 ffmpeg / ImageMagick / vendor 仓库 |
| `scripts/install_pydeps.py yt-dlp faster-whisper` | 装 Python 依赖到 `pydeps/` |

底层 `vw.py` 还有 `probe / plan / sheet / seq / read / ocr / gif / report` 等子命令，
见 `vendor/video-watch/SKILL.md`——需要逐帧读动作、盯某个 HUD 区域、或做差分取证时用它们。

## 三类素材怎么写

| 素材 | 笔记重心 | 默认档位 |
|---|---|---|
| 讲座 / 口播 | 论点与论证链 | 转写为主证据，图版定章节（`--style study`） |
| 教程 / 录屏 | 可复现步骤 | 事件驱动全屏 still（一步至少一张）+ 图文教程体（`--style tutorial`，自动判定） |
| 评测 / 产品 | 结论与依据 | 关键数字必须回原帧核对 |

### 教程类的图是怎么来的

`--style tutorial`（或自动判定：冻结帧 ≥5 个）按**事件**取图，不是按段平均分：冻结段起点（作者停在
界面上讲的那一帧）、语音密度峰、转写命中"代码/脚本/节点"、切点后 0.8 秒、以及字幕文本变化点。
每段只保证"有图可看"，不强迫每段一张；预算按 `clamp(时长分钟×2, 6, 60)`，`--focus` 可以手工指定必拍时刻。

判据是"静止画面 + 关键词 + 语音密度"，**不是图像识别**：试过用边缘密度区分代码帧和游戏画面，
代码帧反而更低，判据不成立，没采用。所以自动选点**必须人工复核**——示例里
`BV1EAKH6GE7C` 的第 7 段就抓到了 Loading 画面，改用补拍的 `pick_00420.png`。

参数类步骤请用 `pipeline/crop.py` 另裁一张放大图（见命令参考），并在图下写按内容手写的图注。

## 已知限制

- **采样盲区**：全片图版 25 帧，40 分钟就是每 96 秒一帧；教程体的分段图版已降级为"每 10 分钟 1 张"（上限 5），
  1–2 秒级的动作仍会漏。**要看清某一步，用 `pipeline/zoom.py <id> --at <秒> --window 8`**
  （8 秒 25 帧 = 0.33 秒/帧），不要指望粗看图版。
- **转写质量**：**先试站点字幕**（B 站 CC 是作者原文，不错字、带时间码、还快）；拿不到才用 whisper。
  注意 B 站对匿名只给弹幕（实测 4/4，`meta.subtitles.reason == "danmaku-only"`），要站点字幕得导
  `cookies.txt`。`base` 模型中文同音错字不少（示例里出现过"寒暑/setter""案号/暗号""二低/2D"）。
  重要引文回原帧核对；专有词写进 `notes/<id>/terms.json` 让 `asrfix.py` 批量校正；
  要更准就 `--asr-model small/medium`，代价是时间。
- **平台限制**：B 站、YouTube 均可直连（实测 2026-10-31：YouTube Shorts 1080×1920 正常下载）；
  走代理时设 `HTTPS_PROXY` 环境变量。**站点 CC 和 1080P 以上 / 大会员内容都要登录态**：
  浏览器导出 Netscape 格式的 `cookies.txt`，再 `pipeline/run.py <url> --cookies <cookies.txt>`
  （只读副本、退出即删，见 D27）。
- **本机沙箱的三处坑**（本仓库已内置绕法，换机器可复用）：
  - 受限进程拿不到系统证书 → `curl`/`git` 的 HTTPS 全挂，所以一律走 Python 自己的 OpenSSL；
  - `pip install` 在 unpack 阶段会被拒写 → 改用 `scripts/install_pydeps.py` 下载 wheel 直接解包；
  - `tempfile.mkdtemp` 建的 0700 目录当场不可访问 → `pipeline/compat/sitecustomize.py` 拦截。

## 目录约定

```
pipeline/     管道脚本（run / still / crop / zoom / asrfix / hardsub / quote_check / note_audit / finish / export / doctor / check_docs + 写作契约 + 兼容层）
scripts/      环境准备与维护（bootstrap / install_pydeps / cleanup / publish / publish_api）
docs/         方案与评审记录（含被否决的方案，见 docs/README.md；PROJECT_MAP.md 是"谁吃什么、哪个文件能删"）
examples/     格式样例（sample-note.md）
vendor/       上游 video-watch 原样放置，不改代码（升级=重新跑 bootstrap --force）
tools/        ffmpeg、ImageMagick 便携版（不入库）
pydeps/       Python 依赖（不入库）
notes/        每个视频一个目录（本地生成，**不在本仓库**；terms.json 是这一集的转写词表）
asr-terms.json  跨片复用的转写词表（入库；每篇的 terms.json 会覆盖同名键）
work/         临时区：下载暂存、HF 模型缓存、temp（可整目录删）
```

体积：大头是 `tools/`+`pydeps/`（重装成本最高）、`work/hf/` 的 whisper 模型、`notes/*/source.*` 的视频本体；
中间物（`work/jobs`、`work/tmp`、参考仓库快照、`analysis_v.txt`）随时可再生成，用
`py -3.14 scripts/cleanup.py` 看当前能回收多少、`--apply` 清掉（默认 dry-run，白名单式）。
**具体数字不写在这里**——它会随笔记增减而变，免得每篇都要改一次；要看就现跑这条命令。

转写词表的写法：`{"寒暑": "setter"}` —— 键是"听错的写法"，值是"正确写法"；键以 `_` 开头会被忽略。
改完先 `py -3.14 pipeline/asrfix.py <id>` 看会改几处，确认再 `--write`。

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `doctor` 说缺滤镜 | ffmpeg 是精简构建 | 跑 `scripts/bootstrap.py --only ffmpeg` 换成完整版 |
| 下载失败、`ffmpeg` 找不到 | yt-dlp 没拿到 ffmpeg 路径 | 管道调用时已把 ffmpeg 路径传给 yt-dlp；手工调 yt-dlp 记得加 `--ffmpeg-location` |
| 转写报 `metadata_errors` | PyAV 19 移除了该参数 | `install_pydeps.py "av==18.1.0"` |
| 转写模型下不动 | HuggingFace 直连慢 | 设 `HF_ENDPOINT=https://hf-mirror.com` |
| 图版索引是默认字体 | drawtext 的 fontfile 没被读到 | `vw.py doctor` 会报"生效"与否；用正斜杠或双引号包路径 |
