# 功能与实现路径

给"想知道它到底做了什么、每一块在哪实现"的读者。每条能力都能落到具体文件；
右边一列是决策编号，去 `DECISIONS.md` 看为什么这么做、否决了什么。

## 1. 它做什么

一句话：**视频链接进，可核对的中文图文笔记出**。核心不是"让模型看视频"，而是把"看"拆成两件互不重叠的事：

```
链接 / 本地文件
   │ ① 取源（yt-dlp）
   ▼
媒体文件
   │ ② 量测（ffmpeg：切点 / 冻结段 / 静音段 / 运动量）── 零 token
   ├─ ③ 图版（tile 拼图，5×5 全片 + 每段 3×3/4×4）─── manifest：第几行第几列 = 第几秒
   ├─ ④ 定点截图（原分辨率单帧，读字唯一可信通道）
   └─ ⑤ 转写（站点字幕 → whisper → 词表校正）
                            ▼
                     BRIEF.md（任务书：图版清单 / 分段 / 截图 / 取证说明）
                            │ ⑥ 模型看图 + 写 note.md
                            ▼
              note_audit（形式验收）→ export（md / html / zip）
```

7 分钟的片子，全程模型只看 9 张图。**取证是脚本的事，命名是模型的事**（D1）。

## 2. 功能表

| # | 能力 | 入口 | 实现路径 | 决策 |
|---|---|---|---|---|
| 1 | 取源（URL / 本地文件） | `pipeline/run.py <url\|file>` | `run.py` `ytdlp()`；竖屏兜底 `bv*+ba/b` + `-S res:1080` | D18 |
| 2 | 站点字幕优先（含登录态 / 双语 CC），自动字幕 opt-in | 默认；`--no-subs` `--auto-subs` `--cookies` | `subs.py` `parse/merge/deroll/pick_bilingual`；`run.py` 选路，结局写 `meta.subtitles`；`--cookies` 走 `stage_cookies()`（临时副本、退出即删） | D18 D21 D27 |
| 3 | 语音转写 + 词表校正 | 默认；`--no-asr` `--asr-lang` `--asr-model` | faster-whisper（默认 `base`）+ `asrfix.py`（繁转简 / 词表 / 体检） | D6 D11 D15 |
| 4 | 逐帧量测（切点、冻结、静音、运动量） | 内部调用 | `vendor/video-watch/vw.py probe\|timeline` | D1 D9 |
| 5 | 章节划分 | 默认按切点；`--max-sections` `--sections-mode even` `--no-auto-even` | `run.py` 分段计划（长片自动等分，按段长给格数） | D12 D16 |
| 6 | 图版生成 + 时间映射 | 内部调用 | `vw.py sheet` + `run.py` 落 `assets/grids/*/manifest.json`；**教程体降级为定位工具**（全片 1 + 每 10 分钟 1，上限 5） | D2 D26 D29 |
| 7 | 取图：事件驱动 + 三道闸 | `--max-stills auto` / `--focus` / `--still-events` | `run.py` `pick_stills`：冻结段起点 / 语音密度峰 / 关键词 / 切点首现 / 字幕变化；5s 去重、每分钟 ≤3、预算 `clamp(分钟×2,6,60)` | D14 D29 |
| 8 | 定点截图、裁切放大 | `pipeline/still.py --at --crop --zoom`；`pipeline/crop.py --at [--write] [--box]` | `still.py` + `crop.py`（投影找候选框：顶部参数条/右侧属性栏/字幕带；按需生成 `*_zoom1.png`） | D5 D14 |
| 9 | 密集图版（0.33s/帧） | `pipeline/zoom.py --at <秒> --window <秒>` | `zoom.py` | D16 |
| 10 | 硬字幕：定位 + 整片 OCR | `hardsub.py --detect / --apply / --subs-ocr`；`run.py --subs-ocr auto` 自动跑 | `hardsub.py` `band_from_densities / ocr_range / write_ocr_md`；rapidocr，缺则 Windows WinRT；双语字幕自动选中文行 | D19 D29 |
| 11 | 引文出处体检 | `pipeline/quote_check.py <id>` | `quote_check.py` `extract / classify`（有出处 / 仅时刻 / 无出处） | D20 |
| 12 | 写作契约 | `pipeline/NOTE_CONTRACT.md` | 人工阅读的规范，不是代码 | D7 D10 |
| 13 | 机器验收（按 `note_schema` 分档） | `pipeline/note_audit.py`，或 `finish.py` 第一步 | v2 包查：开头结构（标题/结论/来源）、步骤时间、图注数量与质量、图配跳转、裁切图成对、可读性；字数不设限 | D7 D29 |
| 14 | 导出 md / html / zip | `pipeline/finish.py`（`--no-compact` 保原图） | `export.py`：图片内嵌、webp 压缩、按标题建目录 | D13 |
| 15 | 重跑守卫（不静默换证据） | 默认开启 | `run.py` + `sharp.py`：截图 / 图版 / `research.json` / `BRIEF.md` / 转写 五处拦截 | D14 D17 D22 D26 |
| 16 | 环境自检 | `pipeline/doctor.py` `selfcheck.py` `check_docs.py` | `deps.py` 单一口径（必需 / 可选只写一处） | D23 |
| 17 | 工具链安装 | `scripts/bootstrap.py [--only ffmpeg\|imagemagick\|repo]` | 下载官方构建到 `tools/`，vendor 仓库 `--force` 重取 | D3 |
| 18 | Python 依赖安装 | `scripts/install_pydeps.py <pkg...>` | 取 wheel 直接解包到 `pydeps/`（沙箱里 `pip install` 在 unpack 阶段被拒写） | D4 |
| 19 | 中间物清理 | `scripts/cleanup.py [--apply]` | 白名单：`work/jobs`、`work/tmp`、快照、`analysis_v.txt`；默认只报体积 | — |
| 20 | 发布（白名单出包 + 漂移检查） | `scripts/publish.py`，`--push` 提交推送 | 只复制项目本体；出体积与敏感项报告；远端有本地没有的改动时拒绝推送 | — |

## 3. 三条实现主线

**取证层**（`vendor/video-watch/`，只读，D9）：量测与拼图的原语。`vw.py` 负责 `probe / plan / sheet / seq / read / ocr / gif`，本项目不改它一行，升级方式是用 `scripts/bootstrap.py --force` 整目录重取。

**管道层**（`pipeline/`）：`run.py` 是唯一的编排者，按"取源 → 量测 → 分段 → 图版 → 截图 → 转写"顺序调用上述原语，把结果落成 `research.json` + `BRIEF.md` + `assets/`。子能力各自单文件、可单独跑：`sharp`（选帧）、`still`（定点截图）、`zoom`（密集图版）、`subs`（字幕）、`asrfix`（转写后处理）、`hardsub`（硬字幕）、`quote_check`（引文）。共享工具收口在 `timefmt.py`（时间戳）、`store.py`（原子 JSON）、`deps.py`（依赖探测），避免同一逻辑写两遍（D23 D24）。

**交付层**：`NOTE_CONTRACT.md` 定格式，`note_audit.py` 检形式，`export.py` 出三种形态，`finish.py` 把"验收 → 导出"合成一条命令，验收不过就停（D13）。`note_audit` 只查"能不能追溯"，不查观点对不对——内容质量是写作者的责任（D7）。

## 4. 被否决的方案（同样是结论）

| 方案 | 为什么不采用 | 决策 |
|---|---|---|
| 长片分窗替换分章 | 窗口边界和作者章节对不上，图版与时间映射两套口径 | D16 |
| 用"变化积分"替换等分分段 | 实测只好 1.15–1.67×，代价是段长不齐、图版格数不可预算 | D25 |
| 用关键词 / 图像识别自动选截图点 | 边缘密度判据在录屏语料上不成立（代码帧反而更低）；只能靠冻结段 + 关键词，且必须人工复核 | D5 |
| 硬字幕直接改转写 | OCR 会混进相邻 UI 文字，只产"候选"，人工确认 | D19 |
| 图版里的文字当证据 | 分辨率不足时视觉会"渲染"合理内容而不是报读不到 | D7 |

## 5. 边界

- **平台**：Windows + Python 3.14 实测（`ocr_win.ps1`、便携版 ffmpeg/ImageMagick、`compat/sitecustomize.py` 都是按这台机器写的）。Linux/macOS 需要自己替换这几处。
- **不做**：不做画面内容识别（谁在画面里、说了什么概念）；不自动改写笔记正文；不判断引文真假（`quote_check` 只查有没有出处）。
- **素材版权**：下载的视频、截图、转写稿、成品笔记都不随本仓库分发。

## 6. 自检

```powershell
py -3.14 pipeline/doctor.py      # 依赖逐项：缺什么、怎么补
py -3.14 pipeline/selfcheck.py   # 环境 + 脚本编译 + 文档一致性 + 既有笔记验收
py -3.14 pipeline/test_sharp.py  # 以下九套是纯函数级自检
py -3.14 pipeline/test_asrfix.py
py -3.14 pipeline/test_meta.py
py -3.14 pipeline/test_subs.py
py -3.14 pipeline/test_hardsub.py
py -3.14 pipeline/test_quote_check.py
py -3.14 pipeline/test_check_docs.py
py -3.14 pipeline/test_shared.py
py -3.14 pipeline/test_notes.py   # 取图三道闸 + 验收分档（D29）
```
