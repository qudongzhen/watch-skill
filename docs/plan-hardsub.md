# 实施方案：硬字幕定位与校正（已端到端验通）

> 来源：`docs/ref-review-update-0.4.md` §2.3（参考实现 0.4.4 的硬字幕 OCR 校正）
> 状态：**已实施并验证**——Windows 自带 OCR（WinRT）跑通 `--apply`；**一条原判作废**（见 §0.6）
> 前置：D14–D18；`selfcheck` 19 项、六个 `test_*.py` 全绿

---

## 0. 进度记录

已合入 master 的八次提交（D14–D18）：选帧、转写后处理、长片分档、重跑守卫、文档整理、
Pillow 检查、参考仓库增量观察、下载链路（竖屏兜底 + 站点字幕）。

本轮要做的本来是一件：**用画面里的硬字幕纠正 ASR 的错字**。动手前探到两件事：

| 探测 | 结果 |
|---|---|
| 装 OCR 引擎 | `scripts/install_pydeps.py rapidocr-onnxruntime` → **PyPI 握手超时**；只装上 `onnxruntime`，`rapidocr` / `cv2` 缺失 |
| 本地有没有别的引擎 | PATH 上没有 tesseract；`pydeps/` 里只有 onnxruntime |

## 0.5 中途判断：密度分不出「字幕」和「UI 栏」（后经 OCR 推翻）

原计划用"每帧都忙 + 位置固定"来定位字幕带。当时认为真实素材上**不成立**：

| 素材 | 检测到的带 | 当时的判读 |
|---|---|---|
| `BV1EAKH6GE7C` | y≈912–1004（5/7 帧命中） | 判为"编辑器状态栏 + 一行代码，不是字幕"——**错，见 §0.6** |
| `BV1Xdnc6jEbL` | y≈572–585 | 窄条，未确认 |
| `BV1W3go6tERZ` | y≈884–948 | 未确认 |
| `BV1uf8e6dEYa` | y≈56–112 | 顶部字幕，看着合理 |

补了一个判别器（带内**相邻帧的平均灰度差**，字幕每句不同、UI 栏逐像素相同，实测 UI 栏 0.2、
真字幕 4–12），但 `BV1EAKH6GE7C` 那条仍通过——因为它含**会滚动的那行代码**，确实在变。

当时据此判定"这条带不是字幕"。**这个判定是错的，见 §0.6。**

## 0.6 后续验证（2026-10-31）：Windows 自带 OCR 把 `--apply` 跑通了

PyPI 拿不到 rapidocr，但本机有 **WinRT `Windows.Media.Ocr`**（`en-US` + `zh-Hans-CN` 语言包）。
写了 `pipeline/ocr_win.ps1`（ASCII 脚本 + UTF-8 BOM，走 `InMemoryRandomAccessStream`——
`StorageFile::GetFileFromPathAsync` 在非打包进程里会失败），`engine()` 优先 rapidocr、否则回退它。

**撤回原判**：把 `BV1EAKH6GE7C` 的候选带 y≈912–1004 裁出来看，里面**就是硬字幕**——
"我收到了很多关于这方面的问题，所以我想做一个视频，分享"，只是带裁宽了，把相邻的游戏 UI
（`HOME` / `SETTINGS` / `DICTIONARIES`）一并框了进来。**密度定位没错，错的是当时的判读。**

| 素材 | 检测带 | `--apply` 结果 |
|---|---|---|
| `BV1EAKH6GE7C` | y≈912–1004 | 12 段 → **6 条候选**（另 2 段 OCR 与 ASR 一字不差，自动略过），覆盖 75–100%，耗时 11s |
| `BV1uf8e6dEYa` | y≈56–112（顶部） | 6 段 → 2 条，但 OCR 被裁短（"如何制作?"→"如何制"，ratio 0.75） |
| `BV1Xdnc6jEbL` | y≈572–585（13px） | 6 段 → **0 条**（带太窄）——"失败时不产出"成立 |

真实候选样例（`BV1EAKH6GE7C`，`--min-cover 0.7 --limit 12`）：

| 原（ASR） | 改（OCR，已剪带外文字） | 覆盖 | ratio |
|---|---|---|---|
| 在我的游戏Laxies Bell中我有很多用户介面 | 我的游戏飞 Lexi ' s BeII》 中我有很多用户界面 | 75% | 0.96 |
| 而且几乎所有内容都有动画效果 | 乎所有内容都有动画效果 | 79% | 0.79 |
| 所以我想做一个视频 | 所以我想做 ” 个频 | 78% | 0.78 |
| 添加动画的一些技巧和方法 | 加动画的 = 些技巧和方法 | 83% | 0.83 |
| 需要注意的是 | 需要注意的是，我用的不是 | 100% | 1.83 |
| 链接在视频描述里 | 链接在视频描述 | 88% | 0.88 |

**质量结论**：能纠错（`介面`→`界面`），也会引入新错（`添`→`忝`、`一`→`=`、`一个`→` 个`）。
所以**只报告、不写回转写**：`--apply --write` 只往 `research.asr_meta.hardsub` 记候选，
`transcript.md` 一个字不动。带外文字按 LCS 配对下标剪掉首尾（`ocr_raw` 留原样备查）；
与 ASR 一字不差的候选直接略过（实测 12 段里 2 段属此类，留着只是噪音）。

**成本**：≈0.9 秒/段（ffmpeg 取帧 + 一次 PowerShell 启动各约一半），默认只查前 30 段。

## 0.7 补验（2026-10-31 联网后）：rapidocr 装上，A/B 比较

PyPI 通了，`scripts/install_pydeps.py rapidocr-onnxruntime` 装上 1.4.4（+ pyclipper / opencv 5.0 /
shapely / onnxruntime 1.31）。`engine()` 不用改——本来也是 rapidocr 优先、Windows 自带兜底。

同一篇 `BV1EAKH6GE7C`、同一条带 `y≈912–1004`、同样 12 段、同样 `--min-cover 0.7`：

| 引擎 | 耗时 | 候选 | 首条输出（覆盖） |
|---|---|---|---|
| Windows 自带（WinRT） | 11s（0.9s/段） | 6 条 | `我的游戏飞 Lexi ' s BeII》 中我有很多用户界面`（75%，标点全丢） |
| rapidocr 1.4.4 | 29s（2.4s/段） | 5 条（另 4 段与 ASR 一致） | `在我的游戏 《LexisBell》中，我有很多用户界面`（88%，书名号/逗号都在） |

差别集中在**标点与大小写**，不是认字：rapidocr 更接近原文，代价是 2.7× 时间。
两者都只产候选，顺序保持 rapidocr → Windows 自带（后者零安装，可作为没依赖时的兜底）。

顺带修了一处：`ocr_band()` 把引擎自身的异常（rapidocr 的 `LoadImageError` 等）也包成
`OcrUnavailable`，调用方只面对一种"OCR 没成"；对应单测改成"藏掉两个引擎"的写法。

## 0.8 实跑（2026-10-31）：`BV1oi416bERx_p1`（动物井渲染拆解，中配 + 双语硬字幕）

这是一条**真正需要 OCR** 的素材：whisper 把"动物井"听成五种写法（动物器/动物锦/动物仅/动物经/动物简），
而画面上有中英双语字幕可对。按流程跑下来：

| 步骤 | 结果 |
|---|---|
| `--detect` 自动带 | y≈984–1020（4/7 帧命中），`--apply` **0 条候选** —— 带偏下，没框到字幕 |
| `--apply --y0 870 --y1 1030` | 10 段出 **8 条候选**，且都是真错：`会智→绘制`、`动物锦→动物井`、`这一阵→这一帧`、`自演→自研` |
| 把候选写成 `terms.json`（20 条）后 `asrfix --write` | 命中 **29 处 / 28 段** |
| 再跑一次 `--apply` | **0 条候选（10 段 OCR 与 ASR 完全一致）** —— 两条链路互相验证 |

**结论**：自动定位在这条素材上又失败了一次（带偏下），但 `--y0/--y1` 这条逃生口很管用；
更重要的是它验证了「OCR 候选 → 词表 → 转写校正」这条链是通的——**修完之后 OCR 与 ASR 就一致了**，
这正是"候选只报告、人写词表"的分工该有的样子。`needs_fix()` 的去重也在这里发挥了作用：
修完 10 段全部被略过，不会刷一堆"原/改相同"的噪音。

## 1. 最终交付

| 部分 | 状态 | 价值 |
|---|---|---|
| `judge()`：LCS 覆盖 ≥60% 且长度比合理才接受，并按配对下标剪掉带外文字 | ✅ 已实现、可验 | 它是**自校验闸门**：带选错了，校正自然不成立，不会写坏转写 |
| `ocr_band()`：rapidocr → Windows 自带 OCR，两个都没有才抛 `OcrUnavailable` | ✅ 已实现、已跑通 | 不静默返回空串，避免"以为没字幕" |
| `detect()/--detect` | ⚠️ 实验性 | 只报候选 + 帧间差异；**会裁宽**（`BV1EAKH6GE7C` 带上混进游戏 UI），所以只给人看，不进 BRIEF |
| `--apply [--y0 --y1]` | ✅ 已端到端验证 | 3 篇实测：6 条 / 2 条 / 0 条候选；候选只进 `research.asr_meta.hardsub`，**不改转写** |

**为什么只产出候选、不做自动替换**：定位只是候选生成器，最终由 `judge()` 把关；而 OCR 自身
有误字（`添`→`忝`、`一`→`=`），自动写进 `transcript.md` 会把"ASR 的错"换成"OCR 的错"。
写作者看到 `原/改` 两行自己判断，成本最低、风险最小。

## 2. 接入

| 位置 | 改动 |
|---|---|
| `pipeline/hardsub.py` | 新增：`row_density` / `band_from_densities` / `band_temporal_diff` / `detect` / `judge` / `lcs_pairs` / `normalize_ocr` / `ocr_band` / `band_pixels` / CLI（`--detect`、`--apply`、`--y0/--y1`、`--limit`、`--min-cover`、`--write`） |
| `pipeline/ocr_win.ps1` | 新增：WinRT OCR 包装（ASCII + UTF-8 BOM；`-Path` / `-Lang`） |
| `run.py` / BRIEF | **不接**（会误导写作者）|
| `pipeline/test_hardsub.py` | 30 项断言（定位 5 + 判据 16 + `normalize_ocr` 5 + 引擎选择 4） |

## 3. 验收

| # | 判据 | 判法 | 结果 |
|---|---|---|---|
| 1 | 合成图：已知 y 的字幕条被找到（±5px） | 单测 | ✅ |
| 2 | 合成图：顶部条也能找到（没写死底部） | 单测 | ✅ |
| 3 | 纯色/整幅都忙的图不误报 | 单测 | ✅ |
| 4 | 多帧下仍锁定不动的字幕带 | 单测 | ✅ |
| 5 | LCS：相同=1、缺字仍过、无关/乱序/空/中间夹垃圾被拒、标点不影响 | 单测 | ✅ |
| 6 | 带外文字被剪掉、`raw` 留原样、`trim=False` 不剪、重复句剪成一句 | 单测 | ✅ |
| 7 | `normalize_ocr`：CJK 空格并回、英文空格保留、中英混排只并 CJK 侧 | 单测 | ✅ |
| 8 | 引擎：有 rapidocr 用它、否则回退 `ocr_win.ps1`、都没有抛 `OcrUnavailable`（含安装命令） | 单测 | ✅ |
| 9 | 真实素材 `--apply` 端到端 | 实跑 3 篇 24 段 | ✅ **6 条 / 2 条 / 0 条候选**；0 条那篇是带太窄，符合"失败不产出" |
| 10 | 与 ASR 一字不差的候选被略过（`needs_fix`） | 单测 + 实跑 | ✅ 单测 3 条；实跑 12 段里略过 2 段 |
| 11 | 写回不碰转写 | 检查 diff | ✅ 只加 `research.asr_meta.hardsub`（验完已复原，未提交该文件） |

## 4. 怎么用

```powershell
py -3.14 pipeline/hardsub.py <id> --detect                    # 先看候选带（实验性，会裁宽）
py -3.14 pipeline/hardsub.py <id> --apply --limit 20          # 看"原/改"两行，人工判断
py -3.14 pipeline/hardsub.py <id> --apply --y0 900 --y1 1000  # 自动带不对时手动指定（原分辨率像素）
py -3.14 pipeline/hardsub.py <id> --apply --min-cover 0.8     # 只想看高把握的
py -3.14 pipeline/hardsub.py <id> --apply --write             # 写进 research.asr_meta.hardsub
```

读报告：`cover` 是 OCR 覆盖 ASR 的比例，`ratio` = 剪完的 OCR 长度 / ASR 长度。
`ratio` 明显 < 1 说明**带太窄、字幕被裁短**（`BV1uf8e6dEYa` 就是这样），这时候改 `--y0/--y1`
再看，别拿裁短的文本当答。

## 5. 风险

| 风险 | 缓解 |
|---|---|
| 带选错 / 裁宽混入 UI 文字 | `judge` 的 LCS 闸门（覆盖不足就归零）+ 按配对下标剪首尾 |
| 带太窄把字幕裁短 | 报告里 `ratio` < 1 会暴露；`--y0/--y1` 手动校正 |
| OCR 读错比 ASR 更离谱 | 覆盖 ≥60% + 长度比 [0.5, 2] + 只报告不写转写，人工复核 |
| 双语字幕（中英两行） | 定位会当一条宽带，OCR 结果中英混排；目前照原样报出，未做分行 |
| 每段一次 PowerShell 启动 | ≈0.9s/段；默认 `--limit 30`，不够再放大 |
| 没有 Windows / 没装语言包 | `engine()` 抛 `OcrUnavailable` 并给 rapidocr 安装命令 |


