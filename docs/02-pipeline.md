# 02 · 六步产线

不满足 Vibe 通道标准的任务（见 [01-lanes.md](01-lanes.md)）都走这条产线。命令入口统一是 `"$HOME/.ai-flow/bin/flowctl"`（由 `wb sync` 创建的符号链接，指向仓库内的 `skills/flow/scripts/flowctl.py`）。

## 总览

```
任务进来
 ├─ 线上问题 / 测试提的 bug ──→ /flow-incident（旁路，见本文末）
 ├─ 仓库里没有 .ai/（不管有没有 CLAUDE.md / AGENTS.md）──→ /flow-onboard（旁路，见本文末）
 └─ 开发任务 ──→ /flow 分通道
      ├─ Vibe：照 playbook 直接做 → 跑测试 → 结束
      └─ Spec 产线：
           ① propose  scout 召回上下文             ✋ 人工勾选、清未决问题
           ② design   写方案，引用标 file:line       ✋ 人工评审
           ③ tasks    拆原子任务                    👀 人工过目
           ④ build    逐任务 base → v1 → final 快照  👀 每个任务过目
           ⑤ review   flowctl verify 独立审查        自动进行，最多 3 轮驳回
           ⑥ retro    算度量、教训回写成 spec         ✋ 人工确认回写
                          └─ 合入 2–4 周后：flowctl aftercare
```

每一步只加载对应的 skill（`skills/flow-<step>/SKILL.md`），前面步骤的细节从产物文件里读，不依赖对话记忆。任意时刻执行 `flowctl status`（对应 `cmd_status`）可以看到六个产物文件各自的状态（缺失 / 待填写 / 已完成）、任务进度、已打的快照、召回的 spec 数量，以及推荐的下一步命令。

在 Codex 里跑：`snapshot`/`checkpoint`/`verify`/`agent` 都要写 `.git`，其中 `verify` 和 `agent` 还会另起一个 CLI 进程（需要联网）。这几个命令被沙箱拦截时，申请提升权限后重试，不要因为拦截就跳过快照或审查这一步。

## 每一步详解

### ① propose（`/flow-propose`）

| | 内容 |
|---|---|
| 输入 | 需求原文 / issue / PRD |
| 动作 | `flowctl new <slug> --title "..."` 建变更；原样保存需求原文；选择 scout 的引擎和模型（`flowctl models scout --json`，见 [07-models-and-hosts.md](07-models-and-hosts.md)）；召回环节先跑 `flowctl recall --record` 把结果写进 `recalled.json`（原生 Claude 路径由 skill 自己先跑一次，`flowctl agent scout` 则自己先完成这一步再启动 scout），scout 只读 `recalled.json`、定位代码入口、找相似历史变更，本身不需要写权限；起草 proposal.md 的业务意图、边界约束、验收标准（AC-1…） |
| 产物 | `proposal.md`、`recalled.json`（scout 召回的候选清单） |
| 门禁 | **硬门禁**：人工勾选上下文清单（`- [x]` / `- [ ]`）、未决问题清零（或标注"不阻塞"）、用户明确同意后才放行 |
| 反模式 | 需求原文没说清的地方自己补设定；scout 召回的 spec 全文读进主上下文（应只看 `when` 和正文第一段判断相关性） |

放行动作：删掉 `<!-- flow:todo -->`、`flowctl trace proposal accepted`。

### ② design（`/flow-design`）

| | 内容 |
|---|---|
| 输入 | `proposal.md`（业务意图、边界、AC、**已勾选**的上下文条目）；勾选的 spec 全文、勾选的代码入口及沿调用链需要读的代码 |
| 动作 | 先读源码再写方案；每个引用的类/方法/字段/表/配置都标 `file:line`，源码里找不到的标"新增"；按 `skills/flow/templates/design.md` 填写模块边界、接口契约、数据变更、Delta（ADDED/MODIFIED/REMOVED）、架构约束与风险、验证方式 |
| 产物 | `design.md` |
| 门禁 | **硬门禁**：人工评审方案 |
| 反模式 | 凭印象写接口签名；方案里出现"可能""应该有"这类措辞；顺手做 proposal 没要求的重构（应写进风险一节交给用户决定） |

放行动作：删掉 `<!-- flow:todo -->`、`flowctl trace design accepted`。

### ③ tasks（`/flow-tasks`）

| | 内容 |
|---|---|
| 输入 | `design.md` 的 Delta |
| 动作 | 每个任务只对应一条 Delta 或其中一部分，不允许横跨两条不相关的 Delta；粒度约束：预计超过 3 个文件或 150 行要继续拆；验收写成一条能直接运行的命令或具体可检查的断言；按依赖排序；行首格式固定 `- [ ] T<n> 标题`（flowctl 靠这个格式统计进度） |
| 产物 | `tasks.md` |
| 门禁 | 轻门禁：用户回一句"可以"即可 |
| 反模式 | 验收写成"功能正常"这类不可执行的描述；一个任务塞进两条不相关的 Delta |

放行动作：删掉 `<!-- flow:todo -->`、`flowctl trace tasks accepted`。

### ④ build（`/flow-build`）

| | 内容 |
|---|---|
| 输入 | 当前任务列出的文件、design 对应的 Delta、相关 spec（不重读整个变更） |
| 动作 | 每个任务一轮循环：`flowctl snapshot T<n> base` → 严格按 design 一次写完首版 → `flowctl snapshot T<n> v1`（必须在跑测试/自我修正/人工修改**之前**打，否则 FPY 失真）→ 跑验收命令，失败就修（计入"被改行"）→ 人工过目 → 验收通过且用户认可后 `flowctl snapshot T<n> final`，在 tasks.md 勾成 `- [x]` |
| 产物 | 代码改动；每个任务的 `refs/flow/<变更>/<任务>/{base,v1,final}` 三个快照（隐藏 ref，不动真实分支和暂存区） |
| 门禁 | 每个任务人工过目，决定是否提交 |
| 反模式 | 在代码里绕过方案错误（应回到 design 重新过门禁）；同一任务改动超出它自己列出的文件范围；打快照的时机不对（比如自我修正后才打 v1） |

失败与回退：同一任务修 3 轮验收还过不了，停下来交给用户判断是任务拆得不对还是方案本身有问题。回退只回退这一个任务：先用 `git diff --name-status refs/flow/<变更>/T<n>/base` 区分改动的文件（`M`）和新建的文件（`A`），`M` 用 base 快照恢复，`A` 直接删除（`git restore` 对 base 里不存在的新建文件会报 pathspec 错误）：
```bash
git restore --source=refs/flow/<变更>/T<n>/base -- <被修改的文件…>
rm <这个任务新建的文件…>
```
这两步都会改动工作区，执行前先把文件清单给用户确认。方案本身错了（接口对不上、漏约束）：回到 `/flow-design`，受影响任务重新拆，不在代码里绕过去。

全部任务完成后提示下一步是 `/flow-review`，不自行宣布"完成"。

### ⑤ review（`/flow-review`）

| | 内容 |
|---|---|
| 输入 | `flowctl checkpoint` 之前打过的任务快照；`tasks.md` 里的验收命令；上一轮审查者自己提出的发现（第 2 轮起） |
| 动作 | 每轮都问用户选择审查引擎和模型（`flowctl models verifier --json`）；`flowctl verify start` 新开一轮：把工作区（含未跟踪文件）存成本轮终点（同时记入 git ref `refs/flow/<变更>/review/<n>`），生成只含 diff 范围/验收命令/上一轮发现的 prompt（不夹带实现思路），启动独立审查者；审查者必须单独一行输出「结论：PASS」或「结论：REJECT」，并附带证据表 |
| 产物 | `review.md` 的轮次表（引擎/模型/独立级别/结论）、`review/round-N.out.md`、`review/round-N.json` |
| 门禁 | 自动进行；REJECT 逐条修复后回到第 1 步；连续 3 轮 REJECT 交给用户判断；PASS 后对召回的每条 spec 打分（`flowctl score <id> hit\|unused\|mislead`） |
| 反模式 | 审查者改动或删除了已有文件（会被判 `INVALID`，见 [07-models-and-hosts.md](07-models-and-hosts.md) 的工作区校验）；输出里结论格式不规范或出现不止一种结论（同样判 `INVALID`）；照审查意见的字面改而不回源码确认；夹带"我已确认 X 存在"这类未经验证的结论进 prompt |

审查期间只新增了未被忽略的文件（多半是测试缓存）：结论仍然有效，但会在这一轮记一条 `warning`，提示确认后删除或加进 `.gitignore`。已经记录过结论的轮次默认不能重新记录，`flowctl verify record --force` 才会覆盖，并在轮次表里注明"重新记录"。

详细的审查执行机制（native 子 Agent vs 外部进程、独立级别判定、工作区校验）见 [07-models-and-hosts.md](07-models-and-hosts.md)。

### ⑥ retro（`/flow-retro`）

| | 内容 |
|---|---|
| 输入 | `flowctl metrics`（提交之后跑，squash 合入时传 `--to <squash 提交> --squash`，只统计这一个提交本身；不加 `--squash` 直接传 `--to <主干最新提交>` 会把主干上别人的提交也混进 diff 区间）；`review.md` 的轮次表 |
| 动作 | 点名 FPY 最低的任务并归因（缺 spec / spec 有错 / 上下文没勾选到 / 任务拆太大 / 需求没澄清 / 模型能力不够）；每条教训落到具体去处：新 spec、修订 spec、新审查检查项（放 `.ai/specs/review/`）、或不沉淀（写理由）；`flowctl specs lint` 必须零错误；`flowctl specs report` 处理标记为"修订"或"下架"的条目 |
| 产物 | `retro.md`、spec 库的新增/修订/下架 |
| 门禁 | 人工确认回写内容后 `flowctl close` |
| 反模式 | 教训不落到任何资产就结束；跨仓库通用的规则写进当前仓库的 `.ai/specs`（应放 `~/.ai-flow/specs/`） |

后续：提醒用户合入 2–4 周后执行 `flowctl aftercare <变更>`，见 [04-metrics.md](04-metrics.md)、[05-closed-loop.md](05-closed-loop.md)。

## 关键规矩（贯穿全程）

- **方案错了就回退**：build 中发现 design 有错，回到 design 重新过门禁，不在代码里绕过去。
- **失败只回退一个任务**：用这个任务的 base 快照恢复，其他任务不受影响。
- **审查最多 3 轮**：连续 3 轮驳回，交给用户判断。
- **可以中途换宿主**：流程状态全在 `.ai/` 文件里，Claude 里做到 build，换到 Codex 执行 `/flow` 也能接着做（`flowctl status` 给出下一步）。
- **一次只加载当前这一步的 skill**。

## 两个旁路

### /flow-onboard（接入）

只要仓库里没有 `.ai/`，`/flow` 就会路由到这里——不管仓库里已经有没有 `CLAUDE.md`/`AGENTS.md`，都不应该绕过这一步直接跑 `flowctl init`。做的事：

1. 问 `.ai/` 要不要提交进仓库：`flowctl init`（推荐）或 `flowctl init --local-only`（加进 `.git/info/exclude`，只留本地）。
2. 按 `/flow-kb` 生成代码知识库 `.ai/kb/`：`flowctl kb scan` 抽事实 → `flowctl kb plan` 规划页面（门禁）→ `flowctl kb draft --all` → 逐页写作（每条带 `file:line`）→ `flowctl kb lint` 零错误 → `flowctl kb freeze`（见 [10-code-kb.md](10-code-kb.md)）。
3. 写仓库规则文件（`CLAUDE.md` 或 `AGENTS.md`，参考 `rules/project-template.md`），以 `.ai/kb/architecture/` 的 tech-stack、dev-guide 为素材，只写 AI 从代码里读不出来的东西，并指向 `.ai/kb/index.md`；已有规则文件时只补充不覆盖。
4. 起草 3–8 条 `status: draft` 的 spec，必须写 `when` 以及 `triggers` 或 `paths`；分层与依赖规则从 `.ai/kb/architecture/module-dependencies.md` 起草。
5. `flowctl specs lint` 零错误。
6. **人工门禁**：逐条确认规则文件和 spec，确认过的 spec 改成 `active`。

### /flow-kb（代码知识库）

有 `.ai/` 但没有 `.ai/kb/_manifest.json` 时 `/flow` 路由到这里；之后代码漂移（`flowctl status` 或 `/flow-retro` 第 6 步的 `flowctl kb status` 退出码 10）时再进来只刷新过期页。细节见 [10-code-kb.md](10-code-kb.md)。

### /flow-incident（线上反哺）

线上报警、报错或测试提的 bug 触发。做的事：

1. `flowctl incident new <slug> --title "..."` 建档，原样贴入现象。
2. 定位：先复现（复现不了要写明依据），沿报错栈找到出问题的代码位置 `file:line`（可派 scout 做）。
3. 追溯：`flowctl locate <file>:<line>` 找到引入这一行的变更档案，读它的 proposal/design/tasks/review 判断当时哪一环漏了；找不到就写"流程外改动"或"Vibe 通道"。
4. 一句话写出技术根因。
5. 回写：新 spec（`triggers` 要匹配同类需求，不只是这次问题）、新审查检查项、修订原有 spec，或写明不沉淀的理由；新增 spec 必须过 `flowctl specs lint`。
6. **人工门禁**：确认根因和回写内容。
7. 修复本身按分通道规则处理（单文件小修复走 Vibe，其余走 `/flow-propose` 新建变更并链接问题档案），不在本 skill 里做。

完整闭环链路见 [05-closed-loop.md](05-closed-loop.md)。
