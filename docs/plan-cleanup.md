# 实施方案：工作区清理 + 项目地图

> 状态：**已实施**（2026-11-02；回收 663.7 MB，工作区 3.30 → 2.64 GB）
> 触发：D26 落地后工作区到 3.3 GB，中间物与再生成物混在一起；`docs/` 只有"每轮方案"，
> 没有一处把"哪个脚本吃什么、吐什么、谁调谁"串起来。
> 口径：**只删"能再生成"的东西**；删不掉的成本（重下视频、重装依赖、重跑 ASR）一律不进这一轮。

---

## 0. 盘点到的东西（2026-11-02 实测）

| 位置 | 体积 | 是什么 | 结论 |
|---|---|---|---|
| `work/jobs/*` | 487.8 MB | 8 个 staging 目录，各含一份 `source.mp4`（与 `notes/*/source.*` **重复**） | 删 |
| `work/tmp/*` | 14.1 MB | 317 个一次性脚本/日志（评审报告、探针、复验脚本） | 删（两个复验脚本挪去 `work/refs/`，见 §2） |
| `work/refs/*-2026-*/` | 10.3 MB | 参考仓库快照（`dsh-see-now`、`dsh-video-storyboard`） | 删（`fetch*.py` 可重取） |
| `notes/*/evidence/probe/analysis_v.txt` | 151.5 MB | `vw.py probe` 的逐帧量测原始表；`pipeline/` 里**没有任何代码读它** | 删（重跑 probe 会再生成） |
| `work/hf/` | 679.3 MB | whisper 模型缓存（`small`），重下要几分钟 + 流量 | **留**，文档标注可删 |
| `pydeps/` `tools/` | 1 031 MB | 依赖与 ffmpeg/ImageMagick 便携版 | **留**（重装成本最高，且 `vw.config.json` 指绝对路径） |
| `notes/*/source.*` | 546.8 MB | 视频本体，重跑/复验/导出都要读 | **留**（删了就得重下 + 无法离线复验） |
| `notes/*/assets/` | 132.1 MB | 图版与定点截图（笔记证据本体） | **留** |
| `exports/` | 153.8 MB | 导出物；其中 2026-10-26 的三个打包 zip 共 87.9 MB | **留**（随时可重新导出，但删了要重跑 9 篇；不在本轮范围内） |
| `.acl-recovery/` | 46 KB | 沙箱 ACL 修复的备份记录 | **留**（回滚用） |

合计本轮回收 ≈ **664 MB**（487.8 + 14.1 + 10.3 + 151.5），工作区 3.3 GB → 约 2.6 GB。

## 1. 清理脚本（可重复）

`scripts/cleanup.py`，**默认 dry-run**：

```powershell
py -3.14 scripts/cleanup.py                 # 只报各类体积与将删的路径
py -3.14 scripts/cleanup.py --apply         # 真删
py -3.14 scripts/cleanup.py --apply --include-sources   # 连 notes/*/source.* 一起删（默认关，需显式要）
```

白名单式删除：只删 §0 表里"删"的那四类，路径写死在脚本里，不做通配扫描——
**清理脚本自己不能变成误删来源**。

## 2. 删之前要先搬走的东西

`docs/plan-grid-guard.md` §6 引用了 `work/tmp/prep_guard.py` 与 `work/tmp/check_guard.py`
（D26 的实跑复验脚本）。搬去 `work/refs/guard-2026-11-02/` 并改文档里的路径，
免得"文档指着一个已删的目录"。

同理 `docs/plan-integral-sections.md` 已声明 `work/refs/*.py` 可删、算法与度量写在正文里，
所以 `integral_sections.py` / `hsv_scan.py` / `fetch*.py` / `spot.py` 四个原型**留在 `work/refs/` 根下**（合计 ~40 KB）。

## 3. 文档产出

| 文件 | 内容 |
|---|---|
| `docs/plan-cleanup.md`（本文） | 盘点、处置、复验 |
| `docs/PROJECT_MAP.md`（新） | 一页看懂：数据流图、26 个脚本各自的输入/输出/被谁调用、`notes/<id>/` 里每类文件的**生命周期**（谁写、谁读、能不能删）、八套测试各管什么 |
| `README.md` | 「目录约定」补体积与清理命令；「已知限制」补"视频本体与模型缓存不入库但占地方" |
| `AGENTS.md` | 「产物约定」补一行清理入口；交接清单加一条"工作区体积是否正常（≈2.6 GB）" |

`PROJECT_MAP.md` 不重复 README 的用法、也不重复 DECISIONS 的理由，只回答三个问题：
**哪个文件是证据、哪个是缓存、哪个能删。**

## 4. 验收

| # | 判据 | 判法 |
|---|---|---|
| 1 | 删完 `selfcheck` 仍 21 项 0 FAIL / 0 WARN | 实跑 |
| 2 | 9 篇笔记 `note_audit` 全绿、导出的 md 仍能生成 | 实跑 `finish.py` 一篇 + 全量 `note_audit` |
| 3 | 八套 `test_*.py` 全绿 | 实跑 |
| 4 | 工作区体积降到 ≈2.6 GB，`git status` 干净 | `Get-ChildItem` 统计 + `git status` |
| 5 | 文档里不再指向已删路径 | 三份历史方案（`plan-sharp-pick` / `plan-quote-check` / `ref-review-update-0.4`）加了"已随清理删除"的注记；其余 `work/tmp` 提及都是"可整目录删"这类说明 |
| 6 | `PROJECT_MAP.md` 里列的每个脚本都真实存在 | `check_docs.py` 顺带覆盖 + 人工过一遍 |

## 5. 这一轮不做

- 不动 `pydeps/` / `tools/`（重装成本高，且是运行前提）。
- 不删 `notes/*/source.*` 与 `exports/`（要删得单独决定，代价是重下/重导）。
- 不改任何 `pipeline/` 行为——本轮零功能变更（除了新增 `scripts/cleanup.py`）。

## 6. 进度记录

- [x] 盘点与方案落纸（2026-11-02）
- [x] `scripts/cleanup.py`（dry-run 默认，白名单式）
- [x] 搬 `prep_guard.py` / `check_guard.py` 到 `work/refs/guard-2026-11-02/` 并改文档
- [x] 执行清理（`--apply`，回收 663.7 MB）
- [x] 复验（selfcheck 21 项 / 九篇 audit / 八套测试 / `finish.py` 真导出一篇）
- [x] `docs/PROJECT_MAP.md` + README/AGENTS 同步

## 7. 实测结果

| 判据 | 结果 |
|---|---|
| dry-run 预估 | 663.7 MB（与 §0 的 664 MB 一致） |
| `--apply` 后 | `work/jobs` + `work/tmp` = 0 B；`work/` 1 192 → 680 MB（只剩 `hf/` 679 MB + 原型） |
| 工作区总量 | 3.30 GB → **2.64 GB**（notes 832 → 680.5 MB，`analysis_v.txt` 九份清空） |
| `selfcheck.py` | 21 项 0 FAIL / 0 WARN |
| 八套 `test_*.py` | 全部 exit 0（255 项） |
| `note_audit` | `BV1Xdnc6jEbL` 0 FAIL；`selfcheck` 内其余 8 篇同样 0 FAIL |
| `finish.py BV1oi416bERx_p1` | 验收 + 导出正常（md/html/zip 重新生成，说明清 `analysis_v.txt` 不影响导出） |
| `git status` | 干净（删除物都在 `.gitignore` 范围内，没有把跟踪文件删掉） |

**顺带发现的文档与实现不符**（已修）：`AGENTS.md` 原写 `evidence/subtitles.srt（有站点字幕时）`，
但代码里站点字幕解析后只写 `evidence/asr/asr.json`，原始 `.srt` 留在 staging——九篇现役笔记一张 `.srt`
也没有（全是 ASR）。已改成实际路径，并在 `PROJECT_MAP.md` 里注明"清 `work/` 后原字幕文件不保留"。

**留下的两点**：
1. 清掉 staging 后，下次 `run.py` 会重新下载视频（`[fetch]` 命中不了缓存）。
2. `work/hf/` 679 MB 是 whisper `small` 模型，删了下次转写要重下；需要腾地方时单独删。

**追加（同日，按用户决定）**：`exports/` 里 2026-10-26 的三个旧打包 zip
（`全部笔记-20261026.zip`、`笔记分卷-1.zip`、`笔记分卷-2.zip`，共 87.9 MB）已删除——
「想要就重新打包」，`export.py` 可逐篇重出。工作区 2.64 → **2.55 GB**，`exports/` 154 → 66 MB。
这三个 zip 是 `exports/` 下**唯一**不随 `cleanup.py` 走的东西（exports 整目录本来就不入库、可重生成），
所以没有给它加开关。
