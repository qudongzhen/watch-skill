# 参与贡献

欢迎 PR 和 issue。这个仓库有一个不太一样的性质，先说清楚，能省双方很多事。

## 它是镜像，不是主仓库

本项目在一台 Windows 机器上的私有工作区里开发，工作区里还有**几百 MB 的视频帧、转写稿和成品笔记**。
公开仓库由 `scripts/publish.py` 按白名单从那份工作区**投影**出来，每次发布都是一次全量重建。

由此三条：

1. **只改本项目文件**：`pipeline/`、`scripts/`、`docs/`、`README.md`、`AGENTS.md` 等。
   不要指望在这里看到 `notes/`、`assets/`、`exports/`——它们永远不会出现。
2. **PR 请小而独立**：一次解决一件事，尽量不跨越多个子系统。合并后我需要把同一改动回灌到
   本地树，改动越小越不会丢。
3. **合并后我做回灌**：这不是推诿，是流程——不回灌的话，下一次发布会把你的改动覆盖掉。
   发布脚本里有远端漂移检查，会先把这件事拦住，所以不会静默丢。

## 不要提交

| 别提交 | 原因 |
|---|---|
| 视频、截图、图版、转写稿 | 版权属于原作者；且本仓库只放代码 |
| `notes/` 下的任何东西 | 同上，且体积会失控 |
| 含本机绝对路径的 `vw.config.json` | 改发/改 `vw.config.example.json` |
| `cookies.txt`、token、任何凭据 | 需要登录态的功能请提 issue 说明。cookies 走本地 `--cookies <file>`（仓库外路径、只读副本、退出即删，见 D27），**内容不进 issue / PR / 截图** |
| 生成物（`exports/`、`work/`、`pydeps/`） | 可重新生成 |

## 许可

Apache-2.0（见 `LICENSE`）。按第 5 条，你提交的贡献默认以同一许可授权，除非你在 PR 里明确声明。
引入第三方代码或数据时，请在 PR 描述里写清来源与许可，并同步更新 `NOTICE`。

`vendor/video-watch/` 是上游 `CFITCorporation/video-watch-skill` 的**原样副本**（MIT，© 2026 CFITSec），
请勿在这里修改它——升级方式是重新拉取上游，改动请提给上游仓库。

## 提交前请自检

本项目实测环境是 Windows + Python 3.14（`py -3.14`），以下命令应当全绿：

```powershell
py -3.14 pipeline/selfcheck.py     # 环境 + 脚本编译 + 文档一致性 + 既有笔记验收
py -3.14 pipeline/test_sharp.py    # 以下九套是纯函数级自检，不需要网络与素材
py -3.14 pipeline/test_asrfix.py
py -3.14 pipeline/test_meta.py
py -3.14 pipeline/test_subs.py
py -3.14 pipeline/test_hardsub.py
py -3.14 pipeline/test_quote_check.py
py -3.14 pipeline/test_check_docs.py
py -3.14 pipeline/test_shared.py
py -3.14 pipeline/test_notes.py
```

命名与风格没有硬性要求，但有两件事会让 PR 被打回：

- 改阈值/放宽校验去让自检变绿（`note_audit` 的阈值是质量底线，见 `DECISIONS.md` D7）；
- 新增一个开关却没在 `docs/` 里留下为什么（`check_docs.py` 会红）。

## 报问题

请附：复现命令、`py -3.14 pipeline/doctor.py` 的输出、以及出错时的完整报错。
**不要**贴 cookies、token、或整段视频链接之外的隐私信息。
