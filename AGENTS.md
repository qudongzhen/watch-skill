# AGENTS.md · 接手须知

写给**下一个打开这个工作区的 AI（或人）**。先读这页，再动手；细节在
`README.md`（怎么用）、`docs/PROJECT_MAP.md`（谁吃什么、哪个文件能删）、
`pipeline/NOTE_CONTRACT.md`（怎么写笔记）、`DECISIONS.md`（为什么这么做）、
`docs/README.md`（这轮改动的方案与评审记录）。

## 一句话

视频链接 → 取证包（图版 + 全屏定点截图 + 转写）→ 看图写 `note.md` → 机器验收 → 导出。
**取证是脚本的事，写笔记是模型的事**，两者不许混。

## 开始工作前，跑这三条

```powershell
py -3.14 pipeline/selfcheck.py        # 环境 + 脚本编译 + 文档一致性 + 既有笔记验收（实测约 1s；项数 = 9 环境 + 3 结构 + 每篇笔记 1）
py -3.14 pipeline/doctor.py           # 依赖逐项自检，出问题先看它
py -3.14 pipeline/test_sharp.py; py -3.14 pipeline/test_asrfix.py; py -3.14 pipeline/test_meta.py; py -3.14 pipeline/test_subs.py; py -3.14 pipeline/test_hardsub.py; py -3.14 pipeline/test_quote_check.py; py -3.14 pipeline/test_check_docs.py; py -3.14 pipeline/test_shared.py
```

`selfcheck` 全绿再动手；红了就按它指的项修，不要跳过。八套 `test_*.py` 是纯函数级自检，
合计约 6 秒（`test_hardsub` 2.4s、`test_sharp` 1.4s 占大头，其余各 0.2s）。

## 环境事实（别重新探测，直接信）

| 项 | 值 |
|---|---|
| Python | 3.14.7，用 `py -3.14` 调用（`python` 是 Microsoft Store 的假入口，会弹商店） |
| 依赖位置 | `pydeps/`（不是 site-packages，靠 `PYTHONPATH`/`sys.path` 引入；**有 numpy，但只给 py3.14 用**） |
| ffmpeg | `tools/ffmpeg/bin/ffmpeg.exe`，gyan 9.0.2 essentials，七滤镜齐全 |
| ImageMagick | `tools/imagemagick/magick.exe`，7.1.2-34 便携版 |
| 上游技能 | `vendor/video-watch/`（**只读，不改**） |
| OCR 引擎 | `pydeps/` 里的 rapidocr 1.4.4（+ onnxruntime 1.31）；没有时自动回退 `pipeline/ocr_win.ps1`（Windows 自带 WinRT，零安装） |
| 配置 | `vw.config.json` 里是**绝对路径**；工作区一搬家就要改，否则 `grid` 报找不到 ImageMagick |

## 这个沙箱的三个坑（已内置绕法，别拆）

1. **HTTPS 在受限进程里全挂**：`curl`/`git` 用 schannel，拿不到证书 → 一律走 Python 的 OpenSSL。
   所以下载走 `scripts/bootstrap.py`，依赖走 `scripts/install_pydeps.py`。
2. **`pip install` 在 unpack 阶段被拒写**：所以才有 `install_pydeps.py`（直接取 wheel 解包）。
3. **`tempfile.mkdtemp` 建的 0700 目录当场不可访问**：`pipeline/compat/sitecustomize.py` 拦掉了。

细节与报错原文见 `DECISIONS.md`。**不要试图"清理"这三处绕法**，它们不是历史包袱。

> 另一类**不是**环境事实：由 agent 在 bash executor 里起的 Python 子进程偶尔拿不到管道
> （`CreatePipe` 报 `WinError 5`）。那是执行器故障，换 `pwsh` 跑就正常——**不要因此改代码**
> （`sharp.py` / `asrfix.py` 落文件是设计选择，不是为了绕它）。

## 常用命令

```powershell
py -3.14 pipeline/run.py "<视频链接或本地文件>"          # 取证（自动判体例）
py -3.14 pipeline/run.py "<url>" --style tutorial        # 强制图文教程体
py -3.14 pipeline/run.py "<url>" --no-asr                # 跳过转写
py -3.14 pipeline/run.py "<url>" --no-subs               # 不试站点字幕（默认会试，D18）
py -3.14 pipeline/run.py "<url>" --auto-subs             # 人工字幕没有时也接受平台自动字幕（机器生成，D21）
py -3.14 pipeline/run.py "<url>" --cookies cookies.txt   # 带登录态拿 B 站 CC（只读副本，退出即删，D27）
py -3.14 pipeline/run.py "<url>" --asr-lang en           # 英文原声（自动检测常把英文判成中文，见 D11）
py -3.14 pipeline/run.py "<url>" --sections-mode even --max-sections 6   # "6 个技巧"这类无硬切素材（D12）
py -3.14 pipeline/run.py "<url>" --pick exact            # 关掉选帧（D14）
py -3.14 pipeline/run.py "<url>" --refresh-stills        # 覆盖 note.md 已引用的截图/图版（D14、D26）
py -3.14 pipeline/run.py "<url>" --refresh-meta          # 覆盖成品笔记的 research/BRIEF（D17）
py -3.14 pipeline/still.py <id> --at 424 --crop 0,0,960,540 --zoom 2   # 补拍/读字
py -3.14 pipeline/still.py <id> --at 424 --pick sharp    # 该秒附近挑最清晰的一帧
py -3.14 pipeline/zoom.py <id> --at 613 --window 8       # 8 秒 25 帧（0.33s/帧），某一步看不清时用
py -3.14 pipeline/asrfix.py <id>                         # 转写体检 + 词表会改几处（不写回）
py -3.14 pipeline/asrfix.py <id> --write                 # 写 transcript.fixed.md + research.asr_meta
py -3.14 pipeline/hardsub.py <id> --apply --limit 20     # 硬字幕 OCR 对照 ASR（只报告，见 D19）
py -3.14 pipeline/hardsub.py <id> --apply --y0 900 --y1 1000   # 自动字幕带不对时手动指定（原分辨率像素）
py -3.14 pipeline/quote_check.py <id>                    # 引文出处体检：有出处/仅时刻/无出处（D20）
py -3.14 pipeline/note_audit.py <id>                     # 笔记验收，FAIL 必须清零
py -3.14 pipeline/finish.py <id>                         # ★ 写完笔记只跑这一条：验收 → 导出
py -3.14 pipeline/check_docs.py                          # 文档里提到的开关是否真的存在
py -3.14 scripts/cleanup.py                              # 可再生成的中间物（默认只报体积，--apply 才删）
py -3.14 scripts/cleanup.py --apply                      # 清 work/jobs / work/tmp / 快照 / analysis_v.txt（量以脚本输出为准）
py -3.14 scripts/publish.py                              # 公开发布树：白名单出包 + 体积/敏感报告 + 远端漂移检查
py -3.14 scripts/publish.py --push                       # 提交并推送；报告先看，远端漂移没清会拒绝（--force 才覆盖）
```

## 产物约定

```
notes/<id>/
  note.md             成品（由模型写，格式受契约约束；run.py 永不覆盖它）
  BRIEF.md            任务书：图版清单、分段、定点截图、取证说明、硬规则
  terms.json          这一集的转写词表（可选，本地保留；覆盖 asr-terms.json 的同名键）
  research.json       机器摘要：时间映射、分段、转写、截图挑选记录、asr_meta
  assets/grids/       5×5 全片 + 每段一张（段长 ≤150s→3×3，≤300s→4×4，更长 5×5）
  assets/grids/*/next/grid.png   被 note.md 引用的图版重跑时的"新图"落点（D26，可随时删）
  assets/stills/      全屏定点截图（代码/参数只能从这里读）
  evidence/           probe/timeline.json、shotlist.json、asr/asr.json、transcript.md、transcript.fixed.md
                      subtitles/*.srt（站点字幕原件；双语 CC 只取原文行，译文只在这里）
  *.new.*             仅当 note.md 已存在且没加 --refresh-meta：不会污染上面的成品文件
```

**重跑安全五件套**（都默认开启，别绕过）：

| 守卫 | 保护什么 | 决策 |
|---|---|---|
| `--refresh-stills` | 笔记引用的**截图与图版**不被静默换像素 | D14、D26 |
| `transcript.md` 不被 `asrfix` 覆盖（另写 `transcript.fixed.md`） | 机器原稿 | D15 |
| `--refresh-meta` | 成品笔记的 research/BRIEF 不被静默覆盖，改写 `*.new` 并报差异 | D17 |
| 成品笔记存在时**复用既有转写**（不重跑 ASR） | note.md 的引文是从那份转写读出来的，换掉等于换证据 | D22 |
| 被引用的图版：旧图不动，新图落 `<段>/next/grid.png` 并记 `kept`/`regenerated` | 图版是 note.md 里的证据，换像素＝换证据 | D26 |

## 写笔记的四条铁律（违反即返工）

1. **图版里的文字不采信**：分辨率不足时视觉会渲染合理内容而不是报"读不到"。
   引用的代码、参数、菜单名必须来自 `assets/stills/` 的全屏帧。
2. **时间只信 `manifest.json`**：第几行第几列 → 第几秒，别凭画面印象排序。
3. **每个结论带时间戳 + 置信度**：缺口写"待核/未做"，不要用推断填空。
4. **附录的「取证说明」抄 BRIEF 里那一节**，不要自己算采样口径（D17）。

## 验收标准（不达不算完成）

- `pipeline/finish.py <id>`（= `note_audit` 验收 + 导出）**0 FAIL**；WARN 要能解释为什么留。
  只想看检查结果：`pipeline/note_audit.py <id>`。
- 教程体额外要求：每个 `### 步骤 N｜mm:ss` 至少一张图。
- 自动选出的定点截图**必须人看一眼**：曾抓到过 Loading 画面（见 `DECISIONS.md` D5）。

## 最近几次改动的落点（读文档前先知道这个）

| 轮次 | 改了什么 | 文档 |
|---|---|---|
| 选帧 | 截图在 ±0.25s 内三选一取最清晰；冻结帧不挑；不跨切点 | `docs/plan-sharp-pick.md`、D14 |
| 转写 | 词表校正 / 静音一致性 / 体检；原稿不覆盖 | `docs/plan-asr-postprocess.md`、D15 |
| 长片 | 章节自动等分 + 按段长给格数 + `zoom.py`；**分窗被否决** | `docs/plan-windows.md`、D16 |
| 收尾 | 重跑守卫 + 机器生成取证说明 | `docs/plan-windows.md` §5、D17 |
| 下载 | 竖屏格式（`bv*+ba/b` + `-S res:1080`，**旧的 height 过滤在竖屏上选错**）+ 站点字幕优先（匿名只给弹幕时分开报，原因记 `meta.subtitles`） | `docs/plan-fetch-subtitles.md`、D18 |
| 硬字幕 | rapidocr（缺则 Windows 自带 OCR）跑通 `--apply`，只产候选、不改转写；密度带会裁宽 | `docs/plan-hardsub.md`、D19 |
| 引文 | `quote_check.py`：有出处 / 仅时刻 / 无出处（不判真假） | `docs/plan-quote-check.md`、D20 |
| 字幕 | 去滚动（滚动行压成每句一次）+ `--auto-subs` 可选自动字幕；修掉 `--list-subs` 吞下载等三个 bug | `docs/plan-deroll.md`、D21 |
| 守卫 | 图版并入"引用了就不静默换"（新图落 `next/`）；BRIEF 加一行成本参照（图版 ≈222 / 全屏 ≈1224 token） | `docs/plan-grid-guard.md`、D26 |
| 清理 | 白名单清理脚本（work/jobs、work/tmp、快照、`analysis_v.txt`）回收 664 MB；新增 `docs/PROJECT_MAP.md` | `docs/plan-cleanup.md`、`docs/PROJECT_MAP.md` |
| 登录态 | `run.py --cookies`：只读副本、退出即删、不回写用户文件；开箱能拿 B 站 CC | `DECISIONS.md` D27 |

## 不要做的事

- 不要改 `vendor/video-watch/`（升级方式：`scripts/bootstrap.py --only repo --force`）。
- 不要在受限进程里跑 `curl` / `git clone` / `pip install`。
- 不要用 `python`（Store 假入口），一律 `py -3.14`。
- 不要为过一次验收去放宽 `note_audit` 的阈值——阈值是质量底线，见 `DECISIONS.md` D7。
- **`notes/` 整个不入库**（2026-11-04 起的规矩，含 `terms.json`）。旧规矩"`notes/*/terms.json` 要提交"
  已作废：笔记只作本地留档，远端由 `scripts/publish.py` 的白名单决定，本来就不含 `notes/`。
- 不要手写附录的采样口径，也不要在有 `note.md` 的目录里随手加 `--refresh-meta`。

## 交接检查清单

- [ ] `pipeline/selfcheck.py` 全绿（含"文档与代码"一项）
- [ ] 八套 `test_*.py` 全绿（sharp / asrfix / meta / subs / hardsub / quote_check / check_docs / shared）
- [ ] `git log --oneline | head -5` 有最近提交
- [ ] 随便挑一篇 `notes/*/note.md`，跑 `note_audit` 能复现 0 FAIL
- [ ] 读一遍 `DECISIONS.md` 的 D1–D27，知道哪些方案已被否决
- [ ] 工作区没有明显膨胀：`py -3.14 scripts/cleanup.py` 报的可回收量若超过 ~1 GB 就清一次（数字别写进文档，会过期）
- [ ] 搬过工作区的话，先改 `vw.config.json` 的绝对路径
- [ ] 待办与暂缓项在 `docs/TODO.md`（含"分支保护要先于给写权限"这类顺序陷阱），别重新推导
