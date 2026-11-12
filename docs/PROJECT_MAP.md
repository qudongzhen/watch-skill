# PROJECT_MAP · 一页看懂

> 这页只回答三个问题：**谁吃什么、谁吐什么、哪个文件能删**。
> 用法看 `README.md`，为什么这么做看 `DECISIONS.md`，每轮改动看 `docs/README.md`。

---

## 0. 一条链路（`run.py` 内部顺序）

```
URL / 本地文件
   │
   ├─ fetch ......... yt-dlp → source.mp4 + meta.json（+ 站点字幕，落在 staging）
   ├─ vw probe ...... ffmpeg 逐帧量测 → evidence/probe/{timeline.json, analysis_v.txt}
   ├─ vw plan ....... 切点/静音/冻结 → evidence/shotlist.json
   ├─ plan_sections . 切点派生 或 自动等分 → research.sections
   ├─ vw grid ....... 全片 5×5 + 定位图版（教程体每 10 分钟 1 张） → assets/grids/<dir>/{grid.png, manifest.json}
   ├─ asr ........... 硬字幕 OCR 优先（教程体）/ 站点字幕 / whisper → evidence/{subtitles_ocr.md, asr/asr.json, transcript.md}
   ├─ pick_stills ... 事件驱动（冻结/语音峰/关键词/切点/字幕）+ 三道闸 → assets/stills/*.png
   └─ write_brief ... BRIEF.md + research.json（note.md 已存在时改写 *.new）
          │
          ▼
   模型读 BRIEF.md + 图版 + 全屏截图 → 写 note.md
          │
          ▼
   finish.py = note_audit.py（验收）→ export.py（md + html + zip）
```

## 1. `pipeline/` 脚本一览

| 脚本 | 吃 | 吐 | 谁调它 |
|---|---|---|---|
| `run.py` | URL / 本地文件 | 整个 `notes/<id>/` 取证包 | 人（入口） |
| `still.py` | 素材 + 时刻（可 `--crop`/`--zoom`/`--pick`） | `assets/stills/*.png` + 记 `research.json` | 人（补拍） |
| `zoom.py` | 素材 + 时刻 + 窗口 | 一张密集图版（约 1 秒/帧） | 人（某步看不清时） |
| `sharp.py` | 时刻附近 N 帧 | 最清晰那一帧的时间与分数 | `run.py`、`still.py` |
| `subs.py` | SRT / VTT | `[{start, end, text}]`（与 whisper 同形）+ 去滚动 | `run.py`、`asrfix.py` |
| `asrfix.py` | `asr.json` + `terms.json` | `transcript.fixed.md` + `research.asr_meta`（**不覆盖原稿**） | 人 |
| `hardsub.py` | 素材 + `asr.json` | 候选更正（OCR 对照，只报告） | 人（`--apply` 才写） |
| `quote_check.py` | `note.md` + `research.json` | 引文出处分级（有出处/仅时刻/无出处） | 人、`note_audit` |
| `note_audit.py` | `note.md` + `research.json` + `assets/` | FAIL / WARN 清单 | 人、`finish.py`、`selfcheck` |
| `export.py` | `note.md` + `assets/` | `exports/<标题>/`：md + html + zip | 人、`finish.py` |
| `finish.py` | 笔记 id | 验收 → 导出（一条命令收尾） | 人 |
| `selfcheck.py` | 整个工作区 | 21 项：环境 9 + 结构 3 + 每篇笔记 1 | 人（动手前） |
| `doctor.py` | `pydeps/` `tools/` | 依赖逐项状态 | 人（排错第一步） |
| `deps.py` | — | 必需的/可选的依赖清单与探测口径 | `selfcheck`、`doctor` |
| `store.py` | — | 原子写 JSON + 读坏文件不崩 | 几乎所有脚本 |
| `timefmt.py` | — | `h:mm:ss` ↔ 秒（唯一实现） | `note_audit`、`quote_check`、`run` |
| `check_docs.py` | `*.md` | 文档提到的脚本/开关是否真的存在 | `selfcheck` |
| `ocr_win.ps1` | 图片 | WinRT OCR 结果（rapidocr 缺失时回退） | `hardsub` |
| `test_*.py` ×9 | — | 293 项纯函数级断言 | 人（改完就跑） |

`NOTE_CONTRACT.md` 不是脚本，是**写笔记的契约**（章节骨架、结构底线、图与跳转、引文格式）。

## 2. `notes/<id>/` 每个文件的寿命

**判断标准：删了以后，这篇笔记还能不能复核。**

| 文件 | 谁写 | 谁读 | 能删？ | 删了会怎样 |
|---|---|---|---|---|
| `note.md` | **模型/人** | `note_audit`、`quote_check`、`export` | ❌ 绝不 | 成品没了；`run.py` 永不覆盖它 |
| `BRIEF.md` | `run.py` | 模型 | ❌ | 重跑能再生成（有 `note.md` 时只写 `BRIEF.new.md`） |
| `research.json` | `run.py` / `asrfix` / `hardsub` | `note_audit`、`quote_check`、`still`、`export` | ❌ | 时间映射与验收依据没了，重跑可再生成但分段可能变 |
| `meta.json` | `run.py`（fetch） | `run.py`（判 id/标题/字幕） | ❌ | 重跑会重新拉元信息（要联网） |
| `terms.json` | **人**（词表） | `asrfix` | ❌ 要入库 | 转写校正与 OCR 一致性判据失效 |
| `source.mp4` | `run.py`（yt-dlp） | probe/grid/asr/still/export | ✅ 可删 | 要重下才能重跑；**离线复验做不了** |
| `assets/grids/*/grid.png` | `run.py`（vw grid） | 模型（读图）、`note.md` 引用 | ⚠️ 被引用就不能动 | 笔记里的图会裂；守卫会拦（D26） |
| `assets/grids/*/manifest.json` | `vw.py grid` | `run.py`、模型的"第几行→第几秒" | ❌ | 时间映射没了，`note_audit` 查不了可回溯性 |
| `assets/grids/*/next/grid.png` | `run.py`（守卫保留时） | 人（对比） | ✅ 随时删 | 只是"这次重跑本想换成什么样"的留档 |
| `assets/stills/*.png` | `run.py` / `still.py` | 模型、`note.md` 引用 | ⚠️ 被引用就不能动 | 同上，守卫会拦（D14） |
| `evidence/probe/timeline.json` | `vw probe` | `run.py`、`asrfix` | ⚠️ | 重跑 probe 可再生成（几十秒） |
| `evidence/probe/analysis_v.txt` | `vw probe` | **没人读**（纯中间件） | ✅ 随时删 | 无影响；重跑 probe 会再生成（9 篇共 152 MB） |
| `evidence/shotlist.json` | `vw plan` | `run.py` | ✅ | 重跑可再生成 |
| `evidence/asr/asr.json` | `run.py` | `asrfix`、`hardsub`、`note_audit` | ⚠️ | 重跑 ASR 可再生成（1–4 分钟/篇） |
| `evidence/transcript.md` | `run.py` | 模型（找原话）、`asrfix` | ⚠️ | 重跑可再生成；**`asrfix` 只写 `.fixed.md`，不覆盖它**（D15） |
| `evidence/transcript.fixed.md` | `asrfix --write` | 模型、`quote_check` | ⚠️ | 重跑 `asrfix` 可再生成 |
| `evidence/subtitles/source.*.srt` | `run.py`（站点字幕原件） | 人（核对、看被丢掉的译文） | ✅ | 解析后的转写在 `asr.json` 里；原件只是出处（D27） |
| `*.new.*` | `run.py`（守卫） | 人 | ✅ | 就是"想覆盖但没敢覆盖"的候选，看完即删 |

站点字幕的 `.srt` 从 staging（`work/jobs/<指纹>/`）来，解析后写进 `evidence/asr/asr.json`，
**同时在 `evidence/subtitles/` 留一份原件**——双语 CC 会被 `pick_bilingual()` 只留原文那一行，
译文只在原件里查得到（D27）。删掉 `evidence/subtitles/` 不影响验收，只影响"当初那份字幕长什么样"。

## 3. 守卫：为什么重跑不会毁掉笔记

五件套（`--refresh-stills` / `transcript.md` / `--refresh-meta` / 复用转写 / 图版 `next/`）见
`AGENTS.md` 的「产物约定」，逐条理由见 `DECISIONS.md` D14、D15、D17、D22、D26。
一句话：**笔记引用过的东西，重跑只能新增，不能改**。

## 4. 测试矩阵（255 项 + `selfcheck` 21 项）

| 套件 | 项数 | 主要断言 |
|---|---|---|
| `test_sharp.py` | 26 | 清晰度评分语义、退化规则、不跨切点、守卫 |
| `test_asrfix.py` | 33 | 繁简、词表语义（`terms.json` 覆盖全局）、静音一致性、体检 |
| `test_meta.py` | 41 | 取证说明措辞、重跑差异、四类守卫（截图/图版/转写/元信息）、token 折算 |
| `test_subs.py` | 79 | SRT/VTT 解析、合并、去滚动、语言挑选 |
| `test_hardsub.py` | 30 | 字幕带定位、LCS 判据、OCR 引擎缺失时的行为 |
| `test_quote_check.py` | 18 | 引文抽取、分级、CLI 退出码 |
| `test_check_docs.py` | 10 | 命令段切分与开关归属（避免把 `yt-dlp --flag` 算成本项目的） |
| `test_shared.py` | 35 | `timefmt` 解析、`deps` 探测口径 |
| `test_notes.py` | 21 | 取图三道闸、`--focus`、`jump_url` 分平台、`note_schema` 分档 |

`selfcheck.py` = 环境 9 + 结构 3 + 每篇笔记 1（跑 `note_audit`）。

## 5. 体积构成（2026-11-02 快照）

> **数字会随笔记增减而变**，这页只在"哪天清理过"这个语境下有意义；判断某个文件要不要留，
> 看"能不能删"那一列，别背数字。要现成的数值，跑 `py -3.14 scripts/cleanup.py`。

| 位置 | 体积 | 能不能删 |
|---|---|---|
| `tools/` + `pydeps/` | 1.03 GB | ❌ 运行前提，重装要几分钟且受沙箱限制 |
| `work/hf/` | 679 MB | ⚠️ whisper 模型，删了下次转写要重下 |
| `notes/*/source.*` | 547 MB | ⚠️ 删了要重下、且无法离线复验 |
| `notes/*/assets/` | 132 MB | ❌ 笔记证据本体 |
| `notes/*/evidence/` | 1.1 MB（清掉 `analysis_v.txt` 后） | ✅ 除 `asr.json` 外基本可再生成（见 §2）；重跑 probe 会让每篇再涨 ~17 MB |
| `exports/` | 66 MB | ✅ 随时可重新导出（2026-10-26 的三个旧打包 zip 已删，要打包重跑 `export.py`） |
| `work/jobs/` `work/tmp/` | 0（已清） | ✅ staging 与一次性脚本 |

清理入口：`py -3.14 scripts/cleanup.py`（默认 dry-run，`--apply` 才删）；
白名单与实测明细见 `docs/plan-cleanup.md`。

## 6. 想改什么，改哪里

| 想改 | 落点 |
|---|---|
| 图版格数 / 采样密度 | `run.py` 主流程里 `cells, cols = (9,3)…(25,5)` 那一段；`vw.py grid` 只认格子数 |
| 分段规则（切点派生 / 等分 / opt-in 积分） | `plan_sections()`；D12、D16、D25 |
| 体例判定（教程 vs 闲谈） | `pick_stills()` 上方的 `style == "auto"` 判定；`--style` 可强改 |
| 定点截图的挑选与守卫 | `pick_stills()`、`should_keep_image()`、`decide_grid_target()` |
| 验收阈值 | `note_audit.py` 的常量——**别为过一次验收去放松**（D7） |
| 引文格式 / 字数上限 | `pipeline/NOTE_CONTRACT.md` + `note_audit.py` 的解析 |
| 导出形态 | `export.py`（md / html / zip 三件套） |
| 依赖清单 | `deps.py`（`REQUIRED` / `OPTIONAL`），`doctor` 与 `selfcheck` 共用 |
| 新增一个开关 | 先加 `add_argument`，再让 `check_docs.py` 能在文档里找到它 |
