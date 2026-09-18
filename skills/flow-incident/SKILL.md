---
name: flow-incident
description: 线上反哺：线上报警、报错或测试提的 bug → 定位出问题的代码 → 用 flowctl locate 追到当初的变更档案 → 判断当时哪一环漏了 → 生成新 spec 或审查检查项，再按分通道规则修复。用户说「线上出问题了」「分析这个 bug」「/flow-incident」时使用。
---

# flow-incident · 线上问题反哺

目标不只是修掉这一个问题，更要让同一类问题下一次在方案或审查阶段就被拦住。

命令入口：`"$HOME/.ai-flow/bin/flowctl"`（下文简写为 flowctl）。

## 步骤

1. **建档**：`flowctl incident new <slug> --title "<现象一句话>"`，然后把报警、报错栈、复现步骤**原样**贴进「现象」一节。
2. **定位**：
   - 先复现。复现不了的，要写明「未复现」，以及依据是什么。
   - 然后沿着报错栈和调用链，找到出问题的代码位置 `file:line`。找的过程可以派 scout 做（引擎和模型让用户选，做法同 flow-propose 第 3、4 步）。
   - 每一个判断都要有源码或日志作为证据。
3. **追溯**：
   - 执行 `flowctl locate <file>:<line>`，找到引入这一行的变更档案。
   - 找到了，就读那个变更的 proposal、design、tasks、review 以及 review 的轮次表，对照模板里的清单，勾出当时是哪一环漏掉了。
   - 找不到，就写「流程外改动」或「Vibe 通道」。
4. **根因**：用一句话写出技术根因。
5. **回写**，每条教训都要落地：
   - 新 spec：`triggers` 要能匹配到**同类需求**的描述，而不只是这一次问题的描述；
   - 新审查检查项：放进 `.ai/specs/review/`；
   - 修订原有 spec；
   - 不沉淀：写明理由。

   新增的 spec 必须通过 `flowctl specs lint`。
6. **人工门禁**：请用户确认根因和回写的内容。确认后，删掉 `<!-- flow:todo -->`。
7. **修复**：按分通道规则处理。单文件的小修复走 Vibe；其他情况走 `/flow-propose` 新建变更，并在 proposal 里链接这个问题档案。修复本身不在这个 skill 里做。
