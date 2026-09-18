---
name: flow-retro
description: Spec 产线第 6 步：算度量（FPY、AI 代码占比、采纳率、审查级别），把踩过的坑回写成新 spec / review 检查项，处理该修订或下架的 spec，然后关闭变更。审查放行后、用户说「复盘」「回写」「/flow-retro」时使用。
---

# flow-retro · 度量与回写

这一步的目的是让下一个需求进来时，已经带着这一次的资产。

命令入口：`"$HOME/.ai-flow/bin/flowctl"`（下文简写为 flowctl）。

## 步骤

1. **算度量**：提交之后执行 `flowctl metrics`。如果是 squash 合入，执行 `flowctl metrics --to <squash 后的那个提交> --squash`，只统计这一个提交本身，不会混进别人的提交。不要用合入之后的主干作为终点，区间里会混进别人的提交。把输出贴进 retro.md。
2. **点名最差的任务**：找出 FPY 最低的那一个，回答一个问题：首版为什么被大量改写？归因到以下某一项：
   - 缺 spec；
   - spec 有错；
   - 上下文没勾选到；
   - 任务拆得太大；
   - 需求没澄清；
   - 模型能力不够。
3. **看审查**：review.md 的轮次表里，每一轮用的是哪一级（L1、L2、L3）？驳回了几次？审查发现的问题里，哪些本来应该在 design 阶段就拦住？
4. **教训变资产**：每条教训都要落到一个具体去处：
   - 新 spec：从 `.ai/specs/_template.md` 复制，写清楚 `when` 和 `triggers`。写不清的，不要加。
   - 修订 spec：被判为 mislead 的，改掉带偏的那一段。
   - 新审查检查项：放进 `.ai/specs/review/`。每次审查都必须检查的项，可以设 `always: true`。
   - 不沉淀：写明理由。

   跨仓库通用的规则，放进 `~/.ai-flow/specs/`，不要放在当前仓库。
5. **检查 spec 库**：
   - 执行 `flowctl specs lint`，必须零错误；
   - 执行 `flowctl specs report`。标着「修订」或「下架」的条目列给用户决定。下架就是把 status 改成 `retired`，不删文件。
6. 请用户确认 retro.md 和 spec 的改动。然后删掉 `<!-- flow:todo -->`，执行 `flowctl close`。
7. **提醒用户**：合入 2–4 周后，执行 `flowctl aftercare <变更>`，查看 AI 代码的存活率，以及之后有多少次提交又改了这批文件。

## 度量怎么读

- **FPY 衡量的是生成质量，不是代码质量**。FPY 高也可能只是人懒得改；所以要结合丢弃率一起看。
- **这些指标都在提交那一刻结算**。合入之后系统有没有变好，要靠 aftercare 来看。
- **单个变更就是一条样本**。趋势看 `flowctl report`。
