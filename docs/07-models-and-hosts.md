# 07 · 模型策略与双端支持

**没有 `workbench/models.json`，也没有任何per-role 的固定模型配置文件。** 模型在**每一次**使用 scout 或 verifier 时现场选择：skill 先跑 `flowctl models <role> --json` 拿到可选项，向用户提问，再把用户的选择通过 `--engine/--model/--reasoning` 传给 `flowctl agent scout` 或 `flowctl verify start`。每一轮审查都重新问一次，选择结果记入 `review.md` 的轮次表。这是实现落地时对 `PLAN.md` 第 6.2 节（`models.json` 方案）的修正，`docs/` 以本章为准。

## 三级独立性

由 `independence(host, engine, model, host_model)` 判定，见 `skills/flow/scripts/flowctl.py`：

| 级别 | 判定条件 | 能防住什么 |
|---|---|---|
| L1 上下文独立 | `engine == host` 且模型和你（实现者）当前用的相同 | 被主 Agent 的错误结论带节奏（原文要求的底线） |
| L2 模型独立 | `engine == host` 但模型不同 | 同上，加上一部分模型特有的偏差 |
| L3 厂商独立 | `engine != host` | 同上，加上同一家族共有的知识盲区 |

选型规则（来自 `agents/flow-verifier.md`、`skills/flow-review/SKILL.md`）：

- **审查者的能力不能低于实现者。** 审查要做的是 grep、读源码、跑测试、对照方案，最需要推理和工具调用，不能为了省钱降成小模型。
- **scout 可以用便宜的模型**——它只提供候选，最后由人勾选。
- **每轮审查都新开**一个进程或子 Agent，不复用上一轮的上下文。
- `flow-review` 的选项呈现顺序会引导你选跨厂商：第 2 轮起第一项是"和上一轮一样"，然后是另一家厂商的模型（标 L3，独立性最高）、同厂商其他模型（标 L2）、最后才是同模型（标 L1）。这只是 UI 排序上的引导，`flowctl` 本身不强制默认引擎——`--engine` 是每次调用的必填参数。

## 每次选择的交互流程

1. Skill 执行 `flowctl models <role> --json`（`role` 为 `scout` 或 `verifier`），得到：
   - `cli_available`：`{"claude": bool, "codex": bool}`，用 `shutil.which` 检测；
   - `claude`：固定四档 `fable`/`opus`/`sonnet`/`haiku`；
   - `codex`：从 `$CODEX_HOME/models_cache.json` 读取（`CODEX_HOME` 未设置时默认 `~/.codex`；按 `visibility == "list"` 过滤），每项带 `model`（slug）、`note`（描述）、`reasoning`（支持的推理档位列表）、`default_reasoning`；再把 `$CODEX_HOME/config.toml` 顶层当前配置的 `model` 补进列表（如果不在里面），标注"config.toml 当前默认模型"；
   - `codex_default`：`config.toml` 顶层的 `model` 和 `model_reasoning_effort`（只解析第一个 `[section]` 之前的内容）；
   - `last`：这个角色上一次的选择（读 `$AI_FLOW_HOME/last_models.json`，`AI_FLOW_HOME` 未设置时默认 `~/.ai-flow`），没有就省略。
2. Skill 用 AskUserQuestion（Claude Code）或直接对话（Codex）向用户提问，`cli_available` 为 `false` 的引擎不展示。
3. 用户选定引擎/模型（Codex 还要选推理档位）后，skill 调用：
   - scout：`flowctl agent scout --host <宿主> --engine <引擎> --model <模型> [--reasoning <档位>] --input <文件>`；
   - verifier：`flowctl verify start --host <宿主> --engine <引擎> --model <模型> [--reasoning <档位>] --host-model <你自己当前用的模型>`。
4. 调用成功后，`run_role()` 会把这次的选择写入 `last_models.json`（`save_last`），作为下次同角色提问时的"和上次一样"选项。这一步只是便利功能：如果运行环境（比如 Codex 的沙箱）不允许写 `$AI_FLOW_HOME`，写入失败会被静默忽略，不影响本次调用本身。

## 四种组合，`flowctl` 实际执行什么

宿主（`--host`，你当前在哪个工具里运行）× 引擎（`--engine`，选定去跑角色的那一方）：

| 组合 | 触发条件 | `flowctl` 实际执行 |
|---|---|---|
| **Claude → Claude** | `host=claude`，`engine=claude` | `run_role()` 直接返回 `{"mode": "native", "prompt_file", "out_file"}`，**不启动任何外部进程**。skill 收到这个结果后，自己用 Agent 工具启动 Claude 原生子 Agent（`flow-scout` 或 `flow-verifier`），`model` 参数按用户选择传入，prompt 取 `prompt_file` 全文；子 Agent 的完整回复写入 `out_file`，然后（verifier）再跑一次 `flowctl verify record` |
| **Claude → Codex** | `host=claude`，`engine=codex` | `flowctl` 自己同步运行外部进程：`codex exec --ephemeral -s <workspace-write|read-only> -C <仓库根目录> -o <out_file> [-m <模型>] [-c model_reasoning_effort="<档位>"] -`，prompt 通过 stdin 传入，阻塞等待（默认超时 1800s，可用 `--timeout` 调整） |
| **Codex → Claude** | `host=codex`，`engine=claude` | `flowctl` 自己同步运行外部进程：`claude -p --agent flow-<role> --allowedTools Read,Grep,Glob,Bash [--model <模型>]`，prompt 通过 stdin 传入；`claude -p` 的结果在 stdout，`flowctl` 读取后写入 `out_file` |
| **Codex → Codex** | `host=codex`，`engine=codex` | 与"Claude → Codex"一行相同的 `codex exec` 命令，只是发起方是 Codex 会话——新起的 `codex exec` 进程天然是全新上下文 |

沙箱选择：`verifier` 用 `workspace-write`（要跑测试、需要写构建产物）；`scout` 用 `read-only`（只做检索，不需要写权限）。

外部进程运行在**独立的进程组**里（`start_new_session=True`）：命令超时时，`flowctl` 用 `os.killpg` 杀掉整个进程组，而不只是杀主进程——避免 `codex exec`/`claude -p` 自己派生出的测试或子进程超时后变成孤儿继续跑。失败处理：命令超时——先尝试整组杀掉，再 `die()` 报错并中止（"`<engine> 超过 <N>s 未返回，本轮作废`"）；命令非零退出且没有产出任何内容——同样 `die()` 中止；命令非零退出但产出文件里有内容——照常继续（容忍某些 CLI 用非零退出码但仍有有效输出的情况）。

## 工作区校验（审查专属）

只有 `verify`（审查）有这一步，`agent`（scout）没有——scout 本来就不需要限制写操作，只是按约定不应该写。`verify start` 的执行过程：

```
1. checkpoint：把当前工作区（含未跟踪文件）存成本轮审查的终点提交，同时用 update-ref 存进
   refs/flow/<变更>/review/<轮次>（审查者用 Bash 改不到这个 ref，比较时以它为准，不看 JSON 里的记录）
2. 生成 prompt：agents/flow-verifier.md 去 frontmatter 后的正文
   + 仓库根目录、变更目录、diff 起止 sha、验收命令
   + 上一轮审查者自己提的发现（第 2 轮起）
3. 启动审查者（见上面四种组合）
4. record_round：审查结束后重新打一次工作区快照，用
   git diff --name-status --no-renames <refs/flow/.../review/<轮次>> <新快照>
   分成「被修改或删除的已有文件」和「新增的未忽略文件」两类：
   - 已有文件被改动或删除 → 本轮判定为 INVALID（"审查者改动了已有文件：<清单>"）
   - 只有新增文件，没有已有文件被动 → 结论仍然有效，但记一条 warning（"审查期间新增了未忽略的文件（多半是测试产物）：<清单>"）
   - 结论解析（见下）不出唯一一个 PASS/REJECT → 判 INVALID
5. 追加写入 review.md 的轮次表，同时记 trace
```

**结论解析**（`parse_verdict`）：正则只认**独占一行**的「结论：PASS」或「结论：REJECT」（允许前后有 `*` 加粗、冒号用全角或半角、结尾带句号），并且要求全文只能匹配出一种结论——同时出现"结论：PASS"和"结论：REJECT"两行（比如审查者先写了草稿又改了主意但没删干净）同样判 `INVALID`，不会取"最后一个"或"任意一个"。

为什么不用只读沙箱：审查者要跑测试，需要写构建产物（比如编译缓存），所以退而求其次用第 4 步的"事后比对已有文件"来兜底，并且只在**修改/删除已有文件**时才判 `INVALID`——新增的构建缓存类文件不应该让一整轮有效的审查作废，只是提醒事后清理。

**重新记录**：`flowctl verify record` 默认拒绝覆盖已经有结论的轮次（"第 N 轮已记录为 ...；需要重新审查请 verify start 开新一轮"）；确实需要重新解析同一轮输出时加 `--force`，轮次表的备注列会注明"（重新记录）"。

## 降级时怎么选

`flowctl` 不做任何自动降级或 fallback 列表（`PLAN.md` 里设想的 `fallback: [...]` 配置项没有被实现）。降级完全靠人工重新选择：

- 引擎的 CLI 不可用（`shutil.which(engine)` 返回空）：`die(f"{engine} CLI 不可用，请换一个引擎重选")`，退出码非零，skill 应该回到"问用户选择引擎/模型"这一步，重新提问（这次 `flowctl models --json` 的 `cli_available` 会显示这个引擎不可用，理应一开始就不会被展示为选项）。
- 外部进程超时：同样中止，交回用户决定要不要换一个模型重试，或加大 `--timeout`。
- Codex 模型档位（tier）命名（`sol`/`terra`/`luna`/`5.5` 等，`PLAN.md` 第 10.6 条提出但没有核实）在实现里已经不需要人工核对——`flowctl models` 直接读取本机 `~/.codex/models_cache.json` 和 `~/.codex/config.toml` 的实时内容，展示当前真实可用的模型名和推理档位，不使用写死的档位表。

## 前置条件

- 跨厂商审查依赖 `codex` CLI 可用；`wb doctor` 会检查它的版本（`codex --version`）。
- Codex 调用 Claude 需要 `claude` CLI 支持非交互模式（`-p`）：实测本机版本 `2.1.232` 支持 `--agent`、`--model`、`--allowedTools`。
- 实测 `codex-cli` 版本 `0.155.0`；两个 CLI 都支持 prompt 走 stdin。
