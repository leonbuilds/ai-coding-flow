---
name: flow-propose
description: Spec 产线第 1 步：接需求、召回 spec、打包上下文清单，产出 proposal.md 供人工勾选。scout 的引擎和模型每次由用户选择。在 /flow 路由到这里，或用户说「开始一个新需求」「/flow-propose」时使用。
---

# flow-propose · 需求与上下文清单

这一步最关键：该读哪些上下文，不能靠脑补。

命令入口：`"$HOME/.ai-flow/bin/flowctl"`（下文简写为 flowctl）。

## 步骤

1. **建变更**：`flowctl new <slug> --title "<一句话标题>"`。slug 用英文加短横线。
2. **原样保存需求**：把需求原文、issue 或 PRD 贴进 proposal.md 的「需求原文」一节，不要改写。需求是文件或链接时，先读完整内容。
3. **选择 scout 的模型**：
   - 执行 `flowctl models scout --json`；
   - 向用户提问，让用户选择引擎和模型：
     - 第一个选项放「和上次一样」（输出里的 `last` 字段，没有就省略）；
     - 再列出两个引擎下可用的模型，Codex 模型附上推理档位；
     - CLI 不可用的引擎不要列出来；
     - 提示用户：scout 只做检索，用便宜的模型就够了。
4. **召回候选上下文**：
   - **宿主是 Claude，且选的是 claude**：先执行 `flowctl recall --file .ai/changes/<变更>/proposal.md --record`，把召回结果写进 recalled.json。仓库有 `.ai/kb/` 时再执行 `flowctl kb recall --file .ai/changes/<变更>/proposal.md`。然后用 Agent 工具启动 `flow-scout` 子 Agent，`model` 参数传用户选的模型。prompt 里只写四样东西：变更目录路径、需求原文（或需求文件路径）、预估涉及的目录、`kb recall` 的输出（没有知识库就写「无知识库」）。
   - **其他情况**：执行 `flowctl agent scout --host <宿主> --engine <引擎> --model <模型> [--reasoning <档位>] --input .ai/changes/<变更>/proposal.md`。它会先自己完成 recall（含知识库召回）并写好 recalled.json，再启动 scout。结束后读它输出的 `out_file`。

   scout 只读不写：它先查知识库页，再读 recalled.json，定位代码入口，找相似的历史变更，最后返回一份带理由的候选清单。
5. **起草 proposal.md**：
   - 业务意图、边界约束、验收标准（AC-1…）都依据需求原文写。原文没说清的地方，写进「未决问题」，不要自己补设定。
   - scout 的候选填进「上下文清单」，知识库页放「知识库页」小节。确定相关的写 `- [x]`，拿不准的写 `- [ ]`，并附上理由。
6. 执行 `flowctl trace proposal generated`。
7. **人工门禁：停下来**，请用户做三件事：勾掉无关的、补上漏掉的；回答未决问题；确认 AC。用户每改一轮，重复第 6 步。

## 放行

放行条件：未决问题清零（或者标了「不阻塞」），并且用户明确同意。

放行后：
1. 删掉 `<!-- flow:todo -->`；
2. 执行 `flowctl trace proposal accepted`；
3. 告诉用户下一步是 `/flow-design`。

## 注意

- 召回到的 spec，只看 frontmatter 里的 `when` 和正文第一段来判断相关性，不要全文读进来。
- spec 库为空时，照常往下走，在上下文清单里注明「无可用 spec」。
