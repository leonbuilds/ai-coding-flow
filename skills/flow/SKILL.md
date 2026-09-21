---
name: flow
description: 个人 AI Coding 流程入口。先按任务步数判断走 Vibe 通道还是 Spec 产线，再把当前变更路由到下一步（onboard / propose / design / tasks / build / review / retro / incident）。当用户说 /flow、「走流程」「开个变更」「这个需求按 spec 做」「现在该干嘛了」时使用。
---

# flow · 入口与分通道

## 约定（所有 flow-* skill 通用）

- **命令入口**：`"$HOME/.ai-flow/bin/flowctl" <子命令>`，下文简写为 `flowctl`。每条命令都要写完整路径；Bash 调用之间不保留变量。
- **宿主**：你在 Claude Code 里运行时，host 是 `claude`；在 Codex 里运行时，host 是 `codex`。需要传 `--host` 的命令，照此填写。
- **向用户提问**：在 Claude Code 里用 AskUserQuestion 工具，在 Codex 里直接在对话中提问。提问后等用户回答，再继续。
- **Codex 沙箱**：`flowctl` 的 `snapshot`、`checkpoint`、`verify`、`agent` 要写 `.git`，其中 `verify` 和 `agent` 还会启动另一个 CLI（需要联网）。这些命令被沙箱拦截时，申请提升权限后重试，不要跳过这一步。
- **状态都在文件里**：`.ai/` 目录保存全部进度，不要依赖会话记忆。会话中断、上下文被压缩，或者换了宿主，都先执行 `flowctl status`。它同时会显示代码知识库是新鲜、过期还是缺失。
- **先查知识库再 grep**：仓库有 `.ai/kb/` 时，找代码先读 `.ai/kb/ai-quick-reference.md` 或执行 `flowctl kb recall`，只读命中的页；页里的 `file:line` 仍要打开核对。

## 第一步：分通道（硬标准，不凭手感）

**Vibe 通道**：下面三条**同时**满足。

1. 步数 ≤ 2（阈值见 `.ai/config.json` 的 `lane_step_threshold`）。一步指一处独立的改动，加上对它的验证。
2. 只改一个文件。
3. 验收标准一句话说得清，没有需要澄清的地方。

走 Vibe 通道时：直接动手，不建变更，不召回 spec。如果 `"$HOME/.ai-flow/kit/playbooks/"` 里有对应的场景（read-code、add-field-api、bugfix、unit-test、code-review），按场景手册做。改完跑一次相关的测试或编译就结束。

**Spec 产线**：其余情况一律走产线。拿不准时也走产线，并告诉用户是哪一条没满足。用户明确指定了通道时，按用户说的办。

先用一句话说出你的判断，再往下走。

## 第二步：路由

| 状态 | 下一步 |
|---|---|
| 用户描述的是线上问题、报警，或测试提的 bug | `/flow-incident` |
| 仓库里没有 `.ai/`（不管有没有 CLAUDE.md / AGENTS.md） | `/flow-onboard`（它会问 `.ai/` 是否提交，不要自己直接 `flowctl init`） |
| 有 `.ai/` 但没有 `.ai/kb/_manifest.json`（知识库没生成完） | `/flow-kb`；用户明确说先不做知识库时才跳过 |
| 没有进行中的变更 | `/flow-propose` |
| 其他情况 | 以 `flowctl status` 输出的「下一步」为准 |

**一次只加载当前这一步的 skill**。前面步骤的细节从产物文件里读。

## 贯穿全程的规矩

- **上下文要精准**：后续步骤只读 proposal.md 里勾选过的 spec 和代码。
- **人工门禁**：proposal、design 两步写完必须停下，等用户明确确认。tasks 可以简单确认。
- **留痕**：AI 每产出一版产物，执行 `flowctl trace <stage> generated`；用户采纳后，执行 `flowctl trace <stage> accepted`。
- **方案变了就回退**：build 过程中发现 design 有错，先改 design.md，重新过门禁，再继续。
- **flow:todo 标记**：产物里的 `<!-- flow:todo -->` 要等用户确认后才能删除。flowctl 靠这个标记判断进度。
