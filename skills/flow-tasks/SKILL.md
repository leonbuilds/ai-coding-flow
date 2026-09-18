---
name: flow-tasks
description: Spec 产线第 3 步：把 design 的 Delta 拆成原子任务（tasks.md），每个任务有输入/输出/依赖/可执行的验收。design 已放行、用户说「拆任务」「/flow-tasks」时使用。
---

# flow-tasks · 原子任务拆解

一次性写完所有改动，失败了就全部作废，也说不清是哪一步走偏的。所以要拆成 N 个原子 delta，每个都能单独验证、单独回退。

## 拆分规则

- 每个任务只对应 design 里的一条 Delta，或其中的一部分。**不允许**一个任务横跨两条不相关的 Delta。
- 粒度：AI 一次就能生成，人几分钟就能审完。一个任务预计超过 3 个文件或 150 行时，继续往下拆。
- 「验收」写成一条能直接运行的命令（跑某个测试、编译、curl），或一条具体可检查的断言。「功能正常」这种写法不算。
- 按依赖排序，被依赖的放前面。测试可以单独成为一个任务，也可以和实现放在同一个任务里（TDD）。
- 行首格式固定为 `- [ ] T<n> 标题`，flowctl 靠这个格式统计进度。

## 步骤

1. 按规则写好 tasks.md。
2. `"$HOME/.ai-flow/bin/flowctl" trace tasks generated`
3. 请用户过目。这一步是轻门禁，用户回一句「可以」就行。
4. 删掉 `<!-- flow:todo -->`，执行 `"$HOME/.ai-flow/bin/flowctl" trace tasks accepted`，然后提示下一步是 `/flow-build`。
