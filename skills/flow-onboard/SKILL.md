---
name: flow-onboard
description: 仓库首次接入：初始化 .ai/，按 /flow-kb 生成代码知识库 .ai/kb/，再以知识库为素材生成仓库级规则文件（CLAUDE.md / AGENTS.md）和 3–8 条初始 spec 草稿。在一个新仓库第一次使用本流程、或用户说「接入这个仓库」「/flow-onboard」时使用。
---

# flow-onboard · 仓库接入

原文里新人培训和统一配置做的事，个人版在这里一次做完：让 AI 在这个仓库里有一个「不用每次重新摸索」的起点。

命令入口：`"$HOME/.ai-flow/bin/flowctl"`（下文简写为 flowctl）。

## 步骤

1. **问一个问题**：`.ai/` 目录（spec 库和变更档案）要不要提交进这个仓库？
   - **提交**（推荐，spec 和档案都是资产）：执行 `flowctl init`。
   - **只在本地保留**（比如公司仓库不方便提交）：执行 `flowctl init --local-only`。它会把 `.ai/` 加进 `.git/info/exclude`。
2. **生成代码知识库**：按 `/flow-kb` 的生成模式走完（`flowctl kb scan` → `flowctl kb plan` → 规划门禁 → `flowctl kb draft --all` → 逐页写作 → `flowctl kb lint` 零错误 → `flowctl kb freeze` → 人工过目）。知识库落在 `.ai/kb/`，每条事实带 `file:line`。它覆盖了技术栈版本、目录与模块职责、构建测试命令、入口、数据模型、依赖方向。
   - 除了知识库覆盖的事实，这一步还要顺带留意两类**代码里读不出来的东西**，留给第 3 步用：仓库自己的特殊约定（错误处理、日志、命名、分层依赖方向为什么这么定），以及明显的禁区（生成代码、对外契约、迁移脚本等）。
3. **写仓库规则文件**：
   - 模板在 `"$HOME/.ai-flow/kit/rules/project-template.md"`。如果这个路径不存在，就按模板的章节结构来写：概览、代码知识库、目录地图、构建与测试命令、编码约定、禁区、领域术语。
   - 素材是 `.ai/kb/overview.md`：技术栈版本直接引用它；构建、测试命令只抄标了「已验证」的；目录地图只写知识库没覆盖的注意事项，其余一句「详见 `.ai/kb/overview.md`」。
   - 「代码知识库」一节写明：先读 `.ai/kb/index.md`，再按需读页；页里的 `file:line` 仍要打开核对。
   - 宿主是 Claude 时写 `CLAUDE.md`，宿主是 Codex 时写 `AGENTS.md`。如果两个都需要，写一份，另一份只放一行引用。
   - 仓库里已经有规则文件时，**只补充缺的部分，不覆盖原有内容**。
   - 只写 AI 从代码里读不出来的东西；读得出来的都在知识库里，不要抄第二遍。
4. **起草 3–8 条 spec**，放到 `.ai/specs/<类型>/`：
   - 从 `.ai/specs/_template.md` 复制；
   - `status: draft`；
   - 必须写 `when`，以及 `triggers` 或 `paths`。

   优先写这几类：分层依赖规则（素材：`.ai/kb/architecture.md` 的「分层与依赖方向」和「循环依赖」）、错误处理约定（素材：「关键横切机制」）、测试写法、领域里容易混淆的概念（素材：`.ai/kb/glossary.md`）。spec 是「应该怎样」的规则，知识库是「现在是怎样」的事实，不要把事实抄成 spec。
5. 执行 `flowctl specs lint`，必须零错误。
6. **人工门禁：停下来**，请用户逐条过规则文件和 spec。用户确认过的 spec，把 status 改成 `active`；不对的删掉，或者按用户意见改。
7. 告诉用户：接入完成，之后用 `/flow` 开始任务。
