# 实施方案：JSON 读写收口（原子写 + 读坏文件不崩）

> 来源：整合评审（`docs/review-2026-10-31-integration.md` §4）里"刻意没做"的最后一条技术债：
> `research.json` 只有 `asrfix.py` 一处是原子写，`run.py` / `hardsub.py` 是裸 `write_text`；
> 而下游有 6 处裸 `json.loads`，半截文件会直接抛 `JSONDecodeError`。
> 状态：**方案 → 实施中**
> 前置：D23；`selfcheck` 20 项、八套 `test_*.py` 全绿

---

## 0. 为什么值得做（不是洁癖）

`research.json` 是**唯一**记录"时间映射 + 分段 + 转写 + 截图挑选"的机器摘要，写它要花几十毫秒
（几十 KB 到几百 KB）。这段时间里如果进程被 Ctrl-C、被杀、或者磁盘满：

- 裸 `write_text` 会留下**半个 JSON**；
- 之后 `note_audit` / `quote_check` / `still` / `zoom` / `hardsub` 全都会在这一步抛
  `JSONDecodeError`——不是"报错说文件坏了"，而是**整条链路看起来像脚本有 bug**；
- 更糟的是 `run.py` 重跑时读 `research.json` 做"复用既有转写"（D22 第四件守卫），
  读到半个文件会判定为"没有既有转写"，于是**重跑 ASR、换掉引文证据**——守卫被文件损坏绕过。

原子写（写临时文件 + `os.replace`）把"半个文件"这个状态从可能变成不可能；
读侧则把"崩"变成"警告 + 按空处理"，让工具还能跑、并且**说清楚哪里坏了**。

## 1. 做法

新增 `pipeline/store.py`（唯一一份 JSON 读写约定）：

- `save_json(path, obj)`：`mkdir -p` → 写 `*.tmp` → `os.replace`（同盘原子）；
  统一 `ensure_ascii=False, indent=1`（与既有文件格式一致，避免无谓 diff）。
- `load_json(path, default=None)`：文件不存在 → 返回 `default`；存在但读不动 →
  **stderr 打一行警告**并返回 `default`（不静默、不崩）。`strict=True` 时改为抛异常。

接入点（写入 6 处、读取 9 处）：

| 位置 | 现在 | 改成 |
|---|---|---|
| `run.py` meta / asr.json / research / research.new | 裸 `write_text` | `store.save_json` |
| `hardsub.py` research | 裸 `write_text` | `store.save_json` |
| `asrfix.py` research | 自己一份 tmp+replace | `store.save_json`（同一实现） |
| `note_audit` / `quote_check` / `still` / `zoom` / `hardsub` / `asrfix` / `run` 读 research、meta、asr | 裸 `json.loads` | `store.load_json` |

## 2. 验收

| # | 判据 | 判法 | 目标 |
|---|---|---|---|
| 1 | 写一半被杀不会留下坏文件 | 单测：模拟"临时文件已写、replace 前抛异常" | 原文件内容不变 |
| 2 | 读坏 JSON 不崩 | 单测：写入 `{坏 json` | 返回 default + stderr 有警告 |
| 3 | 读不存在不崩 | 单测 | 返回 default |
| 4 | 中文不被转义、缩进与旧文件一致 | 单测对比字符串 | 含中文原样、`indent=1` |
| 5 | 8 篇笔记 `note_audit` 不变 | 实跑 | 0 FAIL |
| 6 | 重跑守卫仍生效 | 实跑 `--force`（复用转写） | 打印"复用既有转写" |
| 7 | 全套自检不回归 | `selfcheck` + 八套测试 | 全绿 |

## 3. 风险

| 风险 | 缓解 |
|---|---|
| `os.replace` 在 Windows 上被占用文件挡住 | 只在**同一目录**内 replace（同盘），且读侧已改成容错；写失败会抛异常，不静默 |
| `load_json` 容错把"真损坏"变成"看起来没数据" | 损坏时**stderr 明说**；`strict=True` 给需要强校验的调用方（测试与 `previous_research` 用它） |
| 统一 indent/ensure_ascii 导致全库大 diff | 先对比一个文件的字节：现有文件就是 `indent=1` + 中文原样，写出来应逐字节相同 |

## 4. 这一轮不做

- 不做"写前备份上一版"（`.bak`）——`os.replace` 已消除半个文件，备份属于额外策略。
- 不动 `notes/*/research.json` 的历史内容（只改写法，不改数据）。
- 不动 `exports/` 的导出逻辑。

## 5. 进度记录

- [x] 方案落纸（2026-10-31）
- [x] `store.py` + 6 处写入 / 9 处读取接入
- [x] `test_shared.py` 扩展（原子性 / 容错 / 格式）→ 25 → 35 项
- [x] 8 篇 `note_audit` 全 0 FAIL；重跑守卫复验（`previous_research` 仍返回 159 段）
- [x] 文档同步（D24、`docs/README` 索引、README 测试表）

**结论**：两个既有 `research.json` 读进来再写出去**逐字节相同**，所以这次改动零数据 diff；
真正的收益是"半截文件 → 整条链路像脚本坏了"这个状态被消除了，而且损坏会**说出来**。
