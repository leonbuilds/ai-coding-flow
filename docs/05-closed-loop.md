# 05 · 闭环

这一章把 [02-pipeline.md](02-pipeline.md) 里提到的 retro 回写、spec 生命周期、aftercare、incident 串成一条完整链路：一次需求做完之后，下一次相关需求进来时，已经带着这一次的资产。

## 全景

```
retro 回写 ──┬─→ 新 spec / 修订 spec ──→ 下次 /flow-propose 的 recall 自动召回
             └─→ 新审查检查项（.ai/specs/review/，可 always: true）──→ 下次 /flow-review 必查

合入 2-4 周 ──→ flowctl aftercare ──→ 存活率、后续改动提交数 ──→ 人工判断是否要开新变更或补 spec

线上问题 ──→ /flow-incident ──→ flowctl locate ──→ 追到当初的变更档案
         └─→ 对照 proposal/design/tasks/review 逐环核对 ──→ 根因 ──→ 回写 spec / 审查项
```

## Retro 回写

`/flow-retro` 是 spec 库获得新知识的主入口。核心动作：

1. `flowctl metrics`（提交后跑，squash 合入用 `--to <squash 提交> --squash`，只统计这一个提交本身）算出这次变更的 FPY、丢弃率、AI 代码占比、采纳率。
2. **点名 FPY 最低的任务**，归因到六类原因之一：缺 spec、spec 有错、上下文没勾选到、任务拆得太大、需求没澄清、模型能力不够。
3. **回看审查**：这一轮用了哪个独立级别（L1/L2/L3）？驳回了几次？哪些问题本该在 design 阶段就被拦住？
4. **每条教训必须落到一个具体去处**，不允许"提了但没处理"：
   - 新 spec（跨仓库通用的放 `~/.ai-flow/specs/`，不放当前仓库）；
   - 修订 spec（被判 `mislead` 的，改掉带偏的那一段）；
   - 新审查检查项（`.ai/specs/review/`，每次都必须检查的可设 `always: true`）；
   - 不沉淀（写明理由）。
5. `flowctl specs lint` 零错误、`flowctl specs report` 处理标记"修订"或"下架"的条目。
6. 人工确认后 `flowctl close`。

## Spec 生命周期

```
draft ──(人工逐条确认)──→ active ──(specs report 标记)──→ 被修订，留在 active
                                    └──(idle 过高)──→ retired（status 改掉，不删文件）
```

| 阶段 | 谁创建 / 谁改状态 | 判定依据 |
|---|---|---|
| `draft` | `/flow-onboard` 起草，或 `/flow-retro`、`/flow-incident` 新增时的初始状态 | 未经人工确认前一律是 draft |
| `active` | 人工逐条确认后手动改 | 确认过的才参与召回（`draft` 状态的 spec 仍会被 `flowctl recall` 召回并展示状态标记，但意味着还没定稿） |
| 修订 | `/flow-retro`、`/flow-incident` 编辑正文 | `flowctl specs report` 标"进 backlog 修订"：误导率过高（`mislead_rate >= 0.2` 且用过 ≥3 次）或命中率过低（`hit_rate < 0.3` 且用过 ≥3 次），见 [03-spec-library.md](03-spec-library.md) |
| `retired` | 人工确认后改状态 | 连续 `idle >= 10` 次未命中（`always: true` 的常驻检查项不参与这项考核） |

这条生命周期完全靠 `.ai/specs/_scores.jsonl` 的历史打分记录驱动——没有人在 `/flow-review` 放行后老实执行 `flowctl score`，这条链路就转不动。

## Aftercare（合入后追踪）

`/flow-retro` 收尾时会提醒：合入 2–4 周后执行 `flowctl aftercare <变更>`。它回答两个问题：

- **AI 代码存活率**：当初被判定为 AI 贡献的那些行，现在还有多少字面完整地留在文件里；
- **后续改动提交数**：变更关闭之后，还有哪些提交碰过这批文件。

这不是自动触发的——需要用户记得回来跑一次（每周复盘时检查是否有到期的变更未做 aftercare，见 [06-manual.md](06-manual.md)）。存活率低不直接等于"这次做得不好"，需要结合"后续改动提交数"和提交信息一起判断：是返工，还是叠加了新需求。算法细节见 [04-metrics.md](04-metrics.md)。

## 线上反哺（incident + locate）

`/flow-incident` 是把线上问题变成流程资产的入口，`flowctl locate` 是它的核心追溯工具：

1. **建档**：`flowctl incident new <slug>`，原样贴入报警/报错栈/复现步骤。
2. **定位**：先复现（复现不了要写明依据），沿调用链找到出问题的代码位置 `file:line`。
3. **追溯**：`flowctl locate <file>:<line>`（`<file>` 可以是相对当前目录的路径，会自动换算成相对仓库根目录的路径）——先 `git blame` 找到最后修改这一行的提交，再遍历所有已算过 `metrics.json` 的变更，看这个提交是否属于某个变更的 `commits` 列表（"引入这一行的提交属于该变更"），或者这个文件是否在某个变更的 `files` 列表里（"该变更改动过这个文件"，命中优先级更低）。找不到任何匹配就说明这是"流程外改动"或"Vibe 通道"产生的。
4. **对照当时的产物**：读追溯到的变更的 `proposal.md`/`design.md`/`tasks.md`/`review.md`，用 `skills/flow/templates/incident.md` 里的清单勾出当时是哪一环漏掉的（需求没澄清 / 上下文没召回 / spec 缺失或有错 / 方案设计缺陷 / 任务验收不充分 / 审查漏检 / 流程外改动）。
5. **根因**：一句话技术结论。
6. **回写**：新 spec 的 `triggers` 要能匹配**同类需求**的描述（不只是这一次问题的字面描述），否则下次同类问题进来时召回不到；新审查检查项确保同类代码模式下次审查会被拦住。
7. **人工门禁**确认根因和回写内容后，修复本身按分通道规则处理（不在 `flow-incident` 里做）。

这一步是整个方案里"AI Native：全生命周期闭环"的落地点——出问题不只是修一次，而是让 `flowctl locate` 能找到当初漏检的那个环节，回写成下次自动生效的 spec 或审查项。
