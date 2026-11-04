# 整合评审：全项目功能复检（2026-10-31）

> 触发：用户要求"对本项目所有功能做一次整合复合审查，主要看文档清晰、功能描述与实现是否冲突、是否重复"。
> 方法：**三路只读评审并行**，各自独立跑命令、各自出报告，互不通气（避免互相确认偏误）。
> 基准：HEAD `ff3a442`（评审期间工作区另有 D21 改动在落地，报告里已标注漂移）。
> 结论：**21 + 127 + 更细的文档条目**，其中 **2 条"严重"都是"文档承诺了、代码没做到"**，
> 已全部修复或明确记为不做（见 D22）。

---

## 1. 三路分工与产物

| 路线 | 审什么 | 原始报告 | 主要方法 |
|---|---|---|---|
| A | 文档描述 vs 代码实现 | `work/tmp/review-a-docs-vs-code.md` | 双向比对 `--flag` ↔ `add_argument`、实跑 8 篇 `note_audit` / `quote_check`、抽数核对项数 |
| B | 重复实现与死代码 | `work/tmp/review-b-duplication.md` | AST 抽 203 个 `def` 统计引用次数、把"同一概念的两份实现"拉进同一进程对比行为 |
| C | 文档可读性与结构 | `work/tmp/review-c-docs-readability.md` | 通读五份主文档、索引覆盖与链接检查、编号顺序、命令可执行性 |

原始报告是**可删的工作产物**（`work/tmp/` 按仓库约定整目录可删）；本文件是留档结论。

## 2. 三条严重：承诺了但没生效

| # | 问题 | 证据 | 处置 |
|---|---|---|---|
| 1 | `--refresh-stills` 守卫**恒为假**：`run.py` 用 `notes/<id>/assets/...` 与 note.md 里的 `assets/...` 比较，永不相等 → "成品笔记引用的截图不被静默覆盖"从未生效 | 8 篇 `research.json` 里 `"kept"` 出现 **0 次**；A 实测重跑照常覆盖 | ✅ 修：按笔记目录取相对路径 + 抽出 `should_keep_still()`，`test_meta.py` 加断言 |
| 2 | **静音段数报成两倍**：`timeline.silences` 是事件流，`len()` 数的是事件 | 4 篇 `BRIEF.md` 写 46/54/48/76，真区间 23/27/24/38 | ✅ 修：改用 `asrfix.silence_spans()` 折叠，BRIEF 数字已订正 |
| 3 | **重跑静默换掉转写**：`--force` 会把 `evidence/asr/` 与 `transcript.md` 按当前模型重写，而 note.md 的引文是从旧转写读的 | 实测 `BV1YV411w72F_p4` 重跑后转写 159 段 → 111 段 | ✅ 修：第四件守卫——成品笔记存在时复用既有转写（`--refresh-meta` 才重跑）；实测二次重跑 `transcript.md` 的 sha256 未变 |

同类的第四条（站点字幕 `--list-subs` 吞下载）在评审期间被另一路先发现并随 D21 修掉了——
A、B 两路独立命中同一个点，说明这不是偶发。

## 3. 口径漂移（文档与实现不一致，已逐条改准）

| 主题 | 文档原话 | 实际（已按实际改） |
|---|---|---|
| `research.asr` 是什么 | "机器输出，有同音错字" | **校正后**文本（繁转简 + 词表）；原稿在 `transcript.md` |
| `note_audit` 读哪个字段 | 某方案文档写"不读 images、读 grids" | 读 `research.get("images")` |
| `transcript.md` | "永不覆盖" | 只对 `asrfix` 成立；`run.py` 重跑会按当前模型重写 |
| `stills[].t` | "一律用 asked_t" | `t` 是**实际取到的帧时刻**（`pick.t`），文件名才用 asked_t |
| 笔记"写完"的终点 | 多处只写到 `note_audit` | 统一为 `finish.py`（= 验收 + 导出），`note_audit` 只做检查 |
| D18 顶部格式串 | 被作废的 `[height<=1080]` 写法 | 顶部加作废提示，指向 `bv*+ba/b` + `-S res:1080` |
| 过时数字 | selfcheck "约 20s"、D2 "每段 3×3"、test_subs "五种结局/43 项" | 1.1s；D16 起按段长 3×3/4×4/5×5；六种结局、62 项 |

## 4. 重复与死代码

**已修**：`check_docs` 的"整行笛卡尔配对"把 `yt-dlp --write-auto-subs` 误算成 `run.py` 的开关
（曾让 selfcheck 变红）→ 改成按命令段归属，并新增 `test_check_docs.py`（10 项）。
真死函数 `hardsub.lcs_len`（全仓 0 调用）删除。

**明确不修**（写进 D22，避免下次重复评估）：

- `note_audit` 与 `quote_check` 各一套 mm:ss 解析（`1:02:03` → 3723 vs 62）：合并要动验收阈值语义。
- `asr_meta` 组装两份、`research.json` 只有一处原子写：清理型重构，不影响产出正确性。
- `selfcheck` 与 `doctor` 重复探测依赖、口径相反（缺 opencc 时一个 exit 1 一个 exit 0）：
  要合并得先定"谁负责严格"，属产品决策。
- 20 个只写不读的 JSON 字段（如 `stills[].pick.sha256`，每张图读全文件算哈希）：留着当证据。

## 5. 未修的文档级建议（低优先，留给下一轮）

- 13 个 argparse 开关在 README/AGENTS 命令表里没有（其中 `--detect`、`quote_check --all`、
  `export --out`、`hardsub --frames` 全仓零引用）——要么补文档，要么删开关，别让它们漂着。
- `DECISIONS.md` 的 D17 排在 D19 之后（编号与位置不一致），查起来要跳。
- PowerShell 示例里混了 `head`、`HF_ENDPOINT=...` 这类 bash 写法，Windows 上不能直接跑。

## 6. 这轮之后的状态

- `selfcheck` 20 项 0 FAIL / 0 WARN；八套 `test_*.py` 全绿（26/33/28/62/30/18/10/35）。
- 8 篇笔记 `note_audit` 全 0 FAIL；4 篇 BRIEF 的静音段数字已订正。
- 新增决策：D21（去滚动 / 自动字幕 opt-in）、D22（本评审与修复口径）、D23（时间戳与依赖探测收口）。
