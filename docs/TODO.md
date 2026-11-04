# 待办（暂缓）

记录"知道该做、但现在不需要做"的事。每条写清三件事：**为什么现在不做**、**真要做时注意什么**、
**什么情况下值得捡起来**。不写排期，只写触发条件。

状态一律是"暂缓"。谁捡起来，做完就把那一条删掉，并在这里留一行去向（搬进了哪个文档/哪条决策）。

| # | 事项 | 暂缓理由 | 触发条件 |
|---|---|---|---|
| 1 | `main` 分支保护 + 协作者写权限 | 现在只有 owner 能合并，正是想要的行为，零配置 | 出现第二个长期维护者 |
| 2 | 合并后自动删源分支 | 只有一个分支、还没有 fork PR，积不起来 | 收到第一个 fork PR |
| 3 | 合并方式只留 squash | 同上，没有历史包袱 | 同上，或 `main` 线性历史开始变乱 |
| 4 | Issue 模板（`.github/ISSUE_TEMPLATE/`） | 还没有 issue，表单化的收益是负的 | 第一个"来回问三次才问清"的 issue |
| 5 | `DECISIONS.md` 补 D28（发布链路） | 三条结论已写在 `scripts/publish.py` 的注释里，够用（D27 已用于登录态，编号后移一格） | 下次改发布流程，或有人问"token 为什么不落盘" |
| 6 | 给 `publish.py` 加半自动回灌（开关名待定，**未实现**） | PR 数为 0，手工回灌更简单 | 合并到第 3 个 PR 还觉得手工烦 |

## 1. 分支保护 + 写权限（有先后顺序）

现在：`collaborators` 只有 `qudongzhen`(admin)，`main` 无 protection、rulesets 为空，
merge/squash/rebase 全开。所以外部 PR 作者对仓库是 **0 权限**，不能合并、不能直推。

**要做时，顺序不能反：**

1. 先给 `main` 上 ruleset：`Require a pull request before merging` + `Require approvals: 1` +
   不允许 bypass（或只允许 admin bypass）
2. 再把人加成 collaborator，给 **Write**

反了的话，中间那段时间对方能**直接 push 到 main**，PR 流程形同虚设。给 Write 的副作用不止合并：
删分支、改 wiki、关别人 PR、触发 Actions 全都有。

另外两条 GitHub 权限事实（免得再查）：

- 没有"只能合并自己的 PR"这种权限。要么 Write，要么没有。
- 上了 `Require approvals` 之后，作者仍然不能批准自己的 PR（GitHub 禁止自批），
  所以"他合并、我批准"是可实现的；但"他不经我批准就合并"实现不了。

## 2. 合并后自动删源分支

仓库设置里叫 **Automatically delete head branches**。开着省事：PR 合并后源分支自动删除，
fork 来的分支也会被清。现在只有一个分支，开不开都没差别。

## 3. 合并方式只留 squash

打了 PR 之后，`main` 上每个 PR 一条提交，回灌本地时更好对。现在还没有外部提交，
改了也没影响；改的时机最好在收到第一个 PR **之前**，免得历史风格前松后紧。

## 4. Issue 模板

想要的字段：复现命令、`py -3.14 pipeline/doctor.py` 输出、完整报错、素材类型（讲座/教程/评测）。
`CONTRIBUTING.md` 里已经用文字写了同样的要求，表单化只是把"要人记得贴"变成"不贴就提交不了"。

## 5. D27：发布链路的三条结论

现在散在 `scripts/publish.py` 的模块注释里，三条是：

1. 发布树**保留 `.git`**（每次发布是同一条历史的新提交，不是重建仓库）；
2. 推之前用 `work/publish/base.txt` 记基线 + `gh api compare` 查远端漂移，有人合了 PR 就拒绝推送；
3. 本沙箱的 schannel 拿不到客户端凭证，推送必须 `http.sslBackend=openssl`，
   token 走一次性 URL、不落盘（`.git/config` 里的 remote 是干净 https）。

按本项目惯例（`DECISIONS.md` 记"为什么"，`docs/` 记"过程"），这三条应该进 D27。

## 6. 半自动回灌

流程是：远端合了 PR → 本地树没有那份改动 → 发布时漂移检查拒绝 → 人工把改动搬回本地。
真到 PR 多了，可以让 `publish.py` 吃一个 PR 号：取 `gh api .../pulls/<n>/files` 的 patch，
`git apply` 到本地工作区（路径在两边是一致的），冲突就退化成人工。**现在不做**：PR 数为 0。

## 顺带记一条维护约定

`vendor/video-watch/` 升级（`scripts/bootstrap.py --only repo --force`）后，
必须同步检查 `NOTICE` 里的版本/版权行与 `vendor/video-watch/LICENSE` 是否仍然一致——
MIT 的署名义务在分发时是硬要求，不是可选项。
