# 实施方案：收口两处重复实现（时间戳解析 / 依赖探测）

> 来源：整合评审（`docs/review-2026-10-31-integration.md`）里 B 路的两条 P1——
> ①`note_audit` 与 `quote_check` 各一套 `mm:ss` 解析，`1:02:03` 一个算 3723、一个算 62；
> ②`selfcheck` 与 `doctor` 重复探测依赖且口径相反（缺 opencc 时一个 exit 1、一个 exit 0）。
> 状态：**方案 → 实施中**
> 前置：D22；`selfcheck` 20 项、八套 `test_*.py` 全绿

---

## 0. 为什么要收口（不是"看着像就合并"）

两处都已**实测漂移**：

| 概念 | A 实现 | B 实现 | 差异 |
|---|---|---|---|
| `1:02:03` | `note_audit.parse_times` → **3723** | `quote_check.TS_RE` → **62**（只吃掉 `1:02`） | 差 60 倍，且 `12:345` / `12:60` 两边结论相反 |
| 缺 `opencc` | `selfcheck` → **exit 1**（红） | `doctor` → **exit 0**（只提示） | 同一台机器两种结论 |
| PyAV≥19 | `selfcheck` 拦 | `doctor` 不拦 | 只有一边知道这个坑 |

第一条会直接影响结论：引文体检的时刻若被算成 62 秒，**相邻原帧匹配就会错误**，
把"有出处"判成"无出处"（或反之）。

## 1. 做法

### 1.1 `pipeline/timefmt.py`：一套时间戳语义

- `parse_stamps(text) -> list[int]`：抓 `h:mm:ss` 与 `mm:ss`（同一行可多个），
  语义沿用 `note_audit`（小时可省；分秒都是 1–2 位但秒必须 2 位，`\b` 边界防止从 `12:345` 里切出 `12:34`）。
- `stamp(sec) -> str`：`h:mm:ss`（≥1 小时）或 `mm:ss`。
- `note_audit.parse_times` 与 `quote_check.stamps_of` 都改成薄封装调用它；`run.stamp`、
  `quote_check._stamp` 改为 `timefmt.stamp` 的别名（逐字节相同的两份实现，B 路指出）。

### 1.2 `pipeline/deps.py`：一套依赖探测与"必需 / 可选"分类

- `probe() -> list[dict]`：每项 `{name, ok, detail, required, fix}`，**唯一**的版本号/修复命令来源。
- 分类口径（这次要定死）：
  - **必需**：python、ffmpeg（七滤镜）、ImageMagick、yt-dlp、Pillow —— 缺了取证主链路直接不可用；
  - **可选**：faster-whisper / PyAV / opencc（`--no-asr` 可绕过）、OCR 引擎（只影响 `hardsub --apply`）。
- `worst(items) -> int`：有必需项缺失才返回 1；可选项缺失只给提示。
- `doctor.py` 与 `selfcheck.py` 都消费它：doctor 打印明细，selfcheck 把"必需缺失"记 FAIL、
  "可选缺失"记 WARN。**两边退出码从此一致**。

### 1.3 测试

新增 `pipeline/test_shared.py`（一套，管两个模块）：
- `1:02:03` 与 `12:03`、`0:05`、`1:02:03–1:02:05` 两种解析器结果**一致**；
- `12:345` / `12:60` 不产生时间戳（不误切）；
- `stamp(3723) == "1:02:03"`、`stamp(62) == "01:02"`；
- `deps.probe()` 的条目都有 `required` 字段；`worst()` 对"只有可选缺失"返回 0；
- `note_audit.parse_times` 与 `quote_check.stamps_of` 对同一输入结果相同（回归护栏）。

## 2. 验收

| # | 判据 | 判法 | 目标 |
|---|---|---|---|
| 1 | 两个解析器对同一行给出相同秒数 | 单测（含 `1:02:03`） | ✅ 完全相等 |
| 2 | 8 篇笔记 `note_audit` 结论不变 | 实跑 | 0 FAIL，与收口前一致 |
| 3 | 8 篇 `quote_check` 分类不变 | 实跑 | 与收口前逐条一致 |
| 4 | 缺可选依赖时 selfcheck 与 doctor 退出码一致 | 单测（`deps.worst`）+ 人工 | 都是 0 |
| 5 | 缺必需依赖时两者都是 1 | 单测（`deps.worst`） | 都是 1 |
| 6 | 全套自检不回归 | `selfcheck` + 八套测试 | 全绿 |

## 3. 风险

| 风险 | 缓解 |
|---|---|
| 合了时间戳语义导致既有笔记的秒数解释变化 | 先跑 8 篇 `note_audit` + `quote_check` 对比收口前后输出，**逐条对齐才算过**（§2 第 2/3 条） |
| 依赖分类改口径让 selfcheck 从红变绿，掩盖真问题 | 只有"可选"项能降级为 WARN；必需项集合写死在 `deps.REQUIRED` 并由测试钉住 |
| 两个脚本行为差异是"有意的" | 逐条对照过：这三处差异没有一处是设计意图（`1:02:03` 显属 bug，缺 opencc 一个红一个绿是漂移） |

## 4. 这一轮不做

- 不动 `note_audit` 的阈值（篇幅 700/800 字那条是 D7 范围，评审只说文档与代码不一致，已改文档）。
- 不动 `asr_meta` 双份组装、`research.json` 原子写（属清理型重构，收益低）。

## 5. 进度记录

- [x] 方案落纸（2026-10-31）
- [x] `timefmt.py` + 三处调用点收口（`note_audit` / `quote_check` / `run`）
- [x] `deps.py` + `doctor` / `selfcheck` 收口（退出码只由必需项决定）
- [x] `test_shared.py`（35 项，含 D24 的 store）
- [x] 前后对比：8 篇 `quote_check` **0 处差异**、8 篇 `note_audit` 全 0 FAIL
- [x] 文档同步（AGENTS 八套测试、README 测试表、`docs/README` 索引、D23）

**结论**：这次收口**没有改变任何既有产出**（8 篇逐篇比对一致），修掉的是"以后有人在长片里
写 `1:02:03` 就会算错"和"两个入口给两种结论"——属于给未来上保险，不是给现状打补丁。
