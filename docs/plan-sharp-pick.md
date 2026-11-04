# 实施方案：定点截图「三候选取最清晰」（定稿 v3）

> 来源：`docs/ref-review-dsh-video-storyboard.md` §1-A
> 状态：**已评审 → 已实施 → 已验收**（评审意见见 §2，验收结果见 §5）
> 范围：只做"选定帧之后的取帧质量"，不动切分（`plan_sections`）、不动转写。
> 注（2026-11-02）：文中引用的 `work/tmp/…` 脚本已随清理删除（`docs/plan-cleanup.md`），
> 结论与数字不受影响；要复现按正文描述重写脚本即可。

---

## 1. 要解决的问题

`pipeline/run.py::extract_still()` 原来按 `-ss t` 精确取一帧。t 来自冻结帧（`freezedetect`）
或转写关键词，给的是"这一秒发生了这件事"，不是"这一秒的哪一帧最清楚"。取到运动模糊帧时，
后果不是"看不清"，而是**看起来看清了、其实是错的**——笔记会照抄。

判据借自自动对焦（Pech-Pacheco 2000）：清晰度 = 高频能量 = Laplacian 方差。
`ffmpeg thumbnail=n=` 按直方图挑帧，而**模糊不改变直方图**，所以它会挑到糊的。

## 2. 独立评审结论（对抗性，已实测）

评审在 `notes/BV1EAKH6GE7C` 上跑了一组实验（`work/tmp/review_sharp/exp*.py`），否掉了 v1 方案的四处：

| 编号 | 问题 | 结论 |
|---|---|---|
| M1 | `ImageFilter.Kernel(..., offset=128)` 算 Laplacian：8bit 值域是 **±1020** 不是 ±128，offset 只保住 12% 量程；**Kernel 还不滤波最外 1px**，纯黑图得 186.9 分 | 换成精确 float 实现（只算内部像素） |
| M2 | 对冻结帧来源的 t 做 ±0.25s：`freezedetect` 起点帧与 +0.25s 帧**逐像素相同**（差 0.00–0.04/255），唯一有差异的是冻结**前**那帧——正是要避开的过场 | 冻结来源直接 `exact` |
| M2 | 候选会跨硬切：s7 的 438.49 / 438.74 之间有切点 438.53，选中的是切点前**另一个镜头** | 候选夹在 `[prev_cut+1/fps, next_cut−1/fps]` 且不跨段 |
| M3 | `still.py` 默认 sharp 是错的：用户点名某一秒读字，挪 0.25s 可能菜单/高亮已变；且把 crop/zoom 后的图拿去评分会被重采样带偏 | `still.py` 默认 `exact`；评分只在整帧上做 |
| M4 | 文件名用 picked t：会把**成品笔记引用的图悄悄换掉像素**，而所有验收脚本都查不出来（`note_audit` 只查图片存在） | 文件名/秒数用 `asked_t`；已被 note.md 引用的图默认不覆盖 |
| M5 | §4 的判据多半不可执行（"≥3×"没给半径、"纯色≈0"按旧实现直接挂、`note_audit` 根本不读 stills） | 换成手算 oracle + 可执行断言（见 §5） |
| R1 | 亮度/闪白比噪声影响更大：压暗 10% 掉 13% 分；s5 的 +0.25s 是白闪叠层帧（2761 vs 干净帧 1646） | 加亮度守卫（对**候选组中位数**，不是对原帧——原帧本身可能就是闪白） |

评审同时确认：`note_audit` 只读 `research.timeline.duration` 与 `research.images`，**不读 stills**；
`export.py` / `finish.py` 只读 note.md 的图片引用 → 新增 `pick` 字段安全，但"still 变了"没有任何脚本能发现，
所以幂等必须在本模块里自己做（M4）。

## 3. 最终设计

### 3.1 评分：精确 float Laplacian（`pipeline/sharp.py`）

```python
v(x,y) = 4·c − 上 − 下 − 左 − 右          # 4 邻域，不含对角，只算内部像素
方差   = E[v²] − E[v]²                    # 不存数组，一遍算完
```

- 限宽 480（`LANCZOS`）后评分，只用于**同一组候选内排序**，不跨素材比较；
- 无 8bit 截断、无边框伪影（纯黑/纯白/纯 128 都严格为 0）；
- 480×270 一帧约 40–60ms，3 候选 ≈ 0.15s。

### 3.2 候选与守卫

| 规则 | 值 | 理由 |
|---|---|---|
| 候选 | `[t−pad, t+pad]` 均分 `count` 个，**原时刻排第一**，其余由近到远 | 挑不出来时天然回落原帧 |
| `pad` / `count` | 0.25s / 3（`--pick-window` / `--pick-count` 可调） | 实测 ±1s、±2s 只多几个点收益，画面差 1.7 / 3.2（换内容了） |
| 窗口 | `[max(0, prev_cut+1/fps, sec.t0), min(dur−1/fps, next_cut−1/fps, sec.t1)]` | **不跨切点、不跨段** |
| 无窗口 | 半宽 < 0.05s → 自动退回 `exact` | 贴着切点/片头片尾 |
| 亮度守卫 | 候选亮度偏离**候选组中位亮度** > 10 → 弃用 | 淡入/闪白/自动曝光；对中位数比对原帧稳（s5 的原帧本身是闪白） |
| 来源分派 | `冻结帧` → `exact`；`关键词`/`段中点` → `sharp` | 冻结窗内帧相同，挑帧零收益且会挪走作者停留处 |

### 3.3 幂等与迁移

- 文件名与 `stills[i].t` 用 **`asked_t`**（`int()` 后进文件名）；picked t 只进 `research.json` 与 BRIEF；
- `stills[i].pick` 记录：`mode / asked_t / t / score / method / score_width / resample / pil / source / sha256 / candidates[]`；
- 重跑时若目标图**已被 `note.md` 引用**：默认**不覆盖**，记 `mode="kept"` 并打印提示；要覆盖加 `--refresh-stills`；
- 旧 `research.json` 没有 `pick` 字段 = `exact` 语义（DECISIONS D14）。

### 3.4 CLI

| 命令 | 新增 | 默认 |
|---|---|---|
| `run.py` | `--pick {auto,sharp,exact}`、`--pick-window`、`--pick-count`、`--refresh-stills` | `auto`（冻结 exact / 其余 sharp） |
| `still.py` | `--pick {exact,sharp}`、`--pick-window`、`--pick-count` | `exact`（点名某一秒就要那一秒） |

BRIEF 里每条写"几帧里取第几帧（±多少秒）"，**不印绝对分数**（分数只在同组内有意义，印出去会被当质量分）。

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `pipeline/sharp.py` | 新增（评分 / 候选 / 窗口 / 取帧 / 挑选），约 190 行 |
| `pipeline/test_sharp.py` | 新增（手算 oracle、单调性、退化、亮度守卫、e2e），约 160 行 |
| `pipeline/run.py` | `extract_still()` 改为返回挑选记录；新增 `still_window()` / `note_image_refs()` / `sha256_of()`；stills 循环加来源分派、窗口、覆盖守卫；CLI 4 个开关；BRIEF 措辞 |
| `pipeline/still.py` | 默认 `exact`；`--pick sharp` 可选；从 `research.json` 读时长/切点/帧率夹窗口 |
| `vw.config.json` | 顺手修：里面三条绝对路径还指向一个旧的、已不存在的工作区目录，导致 `vw.py grid` 找不到 ImageMagick、**整条管道在当前工作区跑不起来** |

## 5. 验收（全部已执行）

| # | 判据 | 结果 |
|---|---|---|
| 1 | 手算 oracle：5×5 阶跃图的 Laplacian 方差 = 26666.67 | ✅ 误差 < 0.01 |
| 1 | 纯黑/纯白/纯 128 = 0；1×1、单像素宽不崩 | ✅ |
| 1 | 模糊单调（0 → 3 逐级下降），清晰版 ≥ 6× blur(2) | ✅ 49708 > 18369 > 1056 > 23 > 1 |
| 2 | 候选：原点在首位、对称、无负、无重复、窗口夹紧、count=1 只 1 帧 | ✅ 8 条断言 |
| 3 | 亮度守卫：闪白帧被弃用且不被选中 | ✅ `rejected: luma`，选中 0.85 而非 0.60 |
| 4 | e2e：前半段模糊的合成片，t=0.60 挑到 0.85（1382 vs 4 分） | ✅ |
| 5 | 真实素材：47 张既有 still 全量重跑（新度量、含窗口与守卫） | ✅ **0 张劣于原帧**，32 张改判，中位 1.002×，P90 1.13×，最高 **1.42×** |
| 6 | 端到端跑一遍 `run.py`（教程体，7 张 still） | ✅ 4 张冻结→`exact`，3 张段中点→`sharp`（2 张被挑动 +0.25s） |
| 7 | 覆盖守卫：note.md 引用后再跑一次 | ✅ 该图 `mode=kept` 并打印提示；`--refresh-stills` 后正常重取 |
| 8 | 不回归：`pipeline/selfcheck.py`（含全部 8 篇既有笔记的 `note_audit`） | ✅ 17 项 **0 FAIL / 0 WARN** |
| 9 | 成本 | ✅ 单张 +0.6–0.7s（3 次 seek + 评分），7 张 ≈ +4s |

> 成本与增益的前提：1080p、短 GOP。4K/长 GOP 下每次 seek 解码更贵，`--pick-count 3` 仍可用，
> 但要注意 3 次 seek 是"从关键帧解到目标"，不是常数开销。

## 6. 已知偏差（写出来，别当 bug）

1. **亮度带偏**：方差 ∝ 局部对比度²，压暗 10% 掉 13% 分——亮度守卫只挡住"突变"（闪白/淡入），
   挡不住整体偏暗的素材。真正的解法是归一化，评审实测整帧 z-score 会把洗白闪帧分数**抬高**（方向反），
   所以没做。
2. **只解决"糊"，不解决"抓错内容"**：D5 的 Loading 帧问题属于后者，本模块不管（且窗口夹在切点内后，
   连"跨过切点逃到隔壁镜头"这条歪路也被堵死了）。
3. **人工补拍的 still 不在 `research.json` 里**（`pick_00420` / `ref_00146` 等），"stills 索引 = 全部 still"
   这个假设本来就不成立；本次只用 `note.md` 的引用做覆盖守卫，没有动这个结构。
4. **图版（vendor）不做选帧**：`vw.py grid` 仍按等间隔采样，D9 只读。

## 7. 复现

```powershell
py -3.14 pipeline/test_sharp.py                    # 单测（20 项）
py -3.14 pipeline/selfcheck.py                     # 工作区自检（17 项）
py -3.14 work/tmp/accept_sharp.py                  # 47 张真实 still 的增益统计 + 窗口/引用检查
py -3.14 pipeline/run.py <素材> --name zz-accept --style tutorial --no-asr --force
py -3.14 pipeline/still.py BV1EAKH6GE7C --at 424 --crop 0,0,960,540 --pick sharp
```
