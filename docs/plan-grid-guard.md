# 实施方案：图版也纳入「引用了就不静默换图」的守卫（+ token 成本提示）

> 来源：`docs/ref-review-dsh-see-now.md` §2.3（token 公式）+ 本轮自查发现的守卫缺口
> 状态：**已实施**（2026-11-02；实跑复验通过，见 §5）
> 前置：D14（截图守卫，D22 修好）、D17（`*.new` 守卫）、D22（第四件守卫：复用转写）

---

## 0. 缺口是什么

`--refresh-stills` 守卫（`run.py:990` → `should_keep_still`）只护 `assets/stills/`。
但成品笔记**也会嵌图版**——现在有 1 篇嵌了 2 张：

```
notes/BV1Xdnc6jEbL/note.md → assets/grids/overview/grid.png、assets/grids/s04/grid.png
```

而 `run.py:921` / `:932` 调 `grid()` 是**无条件写同一个路径**（`grid()` 只 `mkdir` + 落 `grid.png`）。
所以一次**不带任何开关**的重跑就会把那两张图的像素静默换掉，正文却不变；`note_audit` 只查
"图存在 + 时间可回溯"，查不出来。这与 D22 修掉的 `kept` 恒假、转写被换是同一类问题，
只是第三处。

## 1. 做法（方案 A）

| 情形 | 行为 |
|---|---|
| 图版**没被** note.md 引用 | 照旧写 `<段>/grid.png` |
| 被引用、且没加 `--refresh-stills` | **旧文件一个字节不动**；新图写进 `<段>/next/grid.png`；`research.images[]` 用**旧 manifest** 的 `t0/t1/tiles`，并记 `kept: true` + `regenerated: <新图相对路径>` |
| 被引用、且加了 `--refresh-stills` | 照旧覆盖（用户明确要求） |

为什么用「保留旧文件 + 新图另存」而不是「干脆不生成」：sections 变了以后，
旧图的 `t0/t1` 与新的分段对不上，若只跳过生成，`research.json` 会记一个**不存在的覆盖区间**；
保留旧 manifest 才能让"第几行第几列 → 第几秒"继续成立，同时把新图留在旁边供人对比。

## 2. 顺带：token 成本提示

参考仓库给了公式（14px patch → 42px 一格）：`tokens = rows×(cols+1)+2`。
量我们的图：图版 765×440 / 762×432 → **222**；全屏截图 1920×1080 → **1224**。
→ BRIEF 的图版清单下面加一行成本提示；并修 D2 里"756×756 ≈ 346 token"的旧估算
（我们的图版不是正方形，按公式是 222；实测 9 张约 3100 说明宿主实际更高，
所以只写"相对关系"：**一张全屏 ≈ 5.5 张图版**）。

## 3. 验收

| # | 判据 | 判法 | 目标 |
|---|---|---|---|
| 1 | 被引用 + 不刷新 → 旧图字节不变、新图落在 `next/` | 单测 `decide_grid_target` + 实跑一次重跑比 sha256 | 一致 |
| 2 | 加 `--refresh-stills` → 旧路径被覆盖 | 单测决策 | 一致 |
| 3 | `research.images[]` 记旧区间 + `kept/regenerated` | 单测 `record_grid` | 字段齐全 |
| 4 | 9 篇笔记 `note_audit` 不回归 | 实跑 | 0 FAIL |
| 5 | BRIEF 出现成本提示，且数字与公式一致 | `py -3.14 -c` 抽查 | 222 / 1224 |

## 4. 这一轮不做

- 不动 `plan_sections` / `integral`（D25 已定）。
- 不动 `--refresh-meta` 的 `.new` 机制。
- 不清理 `next/` 目录（按约定 `notes/*/assets` 可人工整理；守卫只保证"不静默换"）。

## 5. 进度记录

- [x] 方案落纸（2026-11-02）
- [x] `should_keep_image` / `decide_grid_target` / `record_grid` + 接入 overview 与分段图版
- [x] BRIEF 成本提示 + D2 估算修正
- [x] `test_meta.py` 断言（34 → 41 项）+ 实跑复验（sha256 不变）
- [x] 文档同步（D26、AGENTS 四件套 → 五件套）

## 6. 实跑复验（2026-11-02，`BV1Xdnc6jEbL`，不带任何开关）

| 判据 | 结果 |
|---|---|
| 被引用的两张图版 sha256 | 前后一致：`48141ee2a94bd5f9`（overview）、`cba7bb72696fcaf4`（s04） |
| `next/grid.png` | 两张都生成；**与旧图逐字节相同**（分段没变 → 渲染是确定性的） |
| `research.new.json` | `images` 9 张，`kept=2`，`regenerated` 指向 `next/grid.png`，`t0/t1/tiles` 取旧 manifest |
| `BRIEF.new.md` | 2 条 ⚠️ 保留说明 + `成本参考：一张图版 ≈222 token、一张全屏截图 ≈1224 token（≈5.5 倍）` |
| 其余 7 张图版 | 照常覆盖，不出现 `kept` 字段 |

**过程中发现并修掉的 bug**：`slim()`（`research.json` / BRIEF 的唯一出口）原先只挑字段，
把 `kept`/`regenerated` 丢了——守卫生效了，但产物里一点痕迹都没有。已补传并加单测
（`slim 保留 kept/regenerated`、`没保留时 slim 不带 kept`）。

复验后删掉了这次生成的 `research.new.json` / `BRIEF.new.md` / 两个 `next/` 目录
（新图与旧图逐字节相同，没有对比价值；`next/` 本来就是重跑副产物）。
**副作用记录**：为免重新下载 57MB，把 `notes/BV1Xdnc6jEbL/source.mp4` 与 `meta.json`
拷进了 `work/jobs/71eec2552b/`（= `md5(URL)[:10]`），让 `run.py` 走 `[fetch] 已存在` 分支；
该目录在 `work/` 下，可随时删。整次重跑约 2 分钟（转写按 D22 复用，没有重跑 ASR）。

脚本：`work/refs/guard-2026-11-02/prep_guard.py`（准备 staging）、
`work/refs/guard-2026-11-02/check_guard.py`（比对 sha256 与产物）。
（2026-11-02 清理时从 `work/tmp/` 搬来，见 `docs/plan-cleanup.md`。）
