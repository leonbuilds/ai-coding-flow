# AI Coding Flow · 实现方案与使用手册（待确认稿 v2）

> 参考：货拉拉技术《个人提效，攒不成组织提效：货拉拉 AI Coding 落地实践》
> 定位：**个人**使用，适用于**任何 git 仓库**，**Claude Code 和 Codex 双端通用**的一整套 AI Coding 方案
> 状态：方案稿，确认后再实现。目录里现有的 skills/、agents/ 等文件是从上一版复制过来的底稿，会按本方案调整。
>
> v2 相比 v1 新增：第 5 节「个人工作台」、第 6 节「模型策略与双端支持」
>
> **确认结果（2026-09-18）**：
> - 第 4、5 项照方案执行；
> - 第 6 项改为**每次使用时由用户选择模型**，不再有 `models.json` 和按角色的 engine 配置，6.2 节作废，以 docs/07 为准；
> - 第 7 项已修复；
> - 第 3 项改为在 `/flow-onboard` 时按仓库询问；
> - 第 8 项 v1 不加 hooks；
> - 其余照方案执行。
>
> 实现后的权威文档在 `docs/`。

---

## 1. 目标

原文把团队的 AI Coding 落地分成三个阶段。个人版把这三个阶段都覆盖到，并把原文的「统一工作台」也落成个人版本：

| 原文 | 要解决的问题 | 个人版对应 |
|---|---|---|
| 统一工作台 AiBox | AI 配置散落在个人手里，各自为政 | **个人工作台**：一个仓库作为唯一真相源，统一管理规则、skills、agents、MCP、模型角色，同步到 Claude Code 和 Codex 两端，并检查漂移 |
| 一、普及：把下限抬起来 | 会用工具，但不知道手上这个活该怎么让 AI 干 | **规则层 + 场景手册**：统一的编码约束，以及照着就能跑的高频场景 |
| 二、规范驱动：让 AI 按规范写 | 能用了，但不稳定；方案质量看个人状态 | **分通道 + 六步 spec 产线** |
| 三、AI Native：全生命周期闭环 | 做过的需求没有沉淀，踩过的坑还会再踩 | **回写 + 合入后度量 + 线上反哺** |
| 坑三：主从双 Agent | AI 幻觉需要第二只眼睛 | **独立审查者 + 可跨厂商的模型策略**（Claude 写、Codex 审，或者反过来） |

另外还有一个横向目标：**可度量**。用 FPY、AI 代码占比、采纳率、spec 命中率这几个指标，判断哪个环节需要改进。

**不做的事**：团队管理（下发给他人、准入审计、组织形态），以及异步云端 Agent（放在演进路线里）。

---

## 2. 整体架构

```
┌───────────────────── 个人工作台（本仓库 = 唯一真相源）──────────────────────┐
│  rules/   skills/   agents/   workbench/mcp.json   workbench/models.json       │
│                         │ wb sync / wb doctor                                  │
│             ┌───────────┴────────────┐                                         │
│             ▼                        ▼                                         │
│      ~/.claude/…              ~/.codex/…            两端各自安装、互相检查漂移 │
└────────────────────────────────────────────────────────────────────────────────┘
┌────────────────── Claude Code 或 Codex（宿主，二选一或同时用）──────────────────┐
│ ① 规则层  全局规则（受管区块）+ 仓库 CLAUDE.md / AGENTS.md                     │
├──────────────────────────────────────────────────────────────────────────────────┤
│ ② 流程层  Skills（9 个，两端通用 SKILL.md）                                    │
│   入口 /flow   接入 /flow-onboard   反哺 /flow-incident                          │
│   产线 /flow-propose → design → tasks → build → review → retro                   │
│   角色 scout（召回上下文）  verifier（独立审查，可以是另一家的模型）            │
├──────────────────────────────────────────────────────────────────────────────────┤
│ ③ 工具层  flowctl：状态、召回、打分、快照、度量、审查调度（verify）、合入后追踪 │
├──────────────────────────────────────────────────────────────────────────────────┤
│ ④ 资产层  spec 库（.ai/specs + ~/.ai-flow/specs）  变更档案（.ai/changes/…）    │
└──────────────────────────────────────────────────────────────────────────────────┘
        ▲ 线上问题 / 复盘教训 ─────── 回写成 spec ─────── 下次需求自动召回 ┘
```

**设计原则**（每一条都来自原文的坑或经验）：

1. **通道按硬标准来分**：数步数，不凭手感。
2. **上下文精准召回**：每条 spec 都必须能回答「什么条件下注入」。
3. **审查者必须独立**：上下文独立是底线，模型独立或厂商独立更好（第 6 节）。
4. **人在环里**：提案和方案两步是硬门禁。
5. **一切从文件恢复**：流程状态都在文件里，换宿主、换会话都能继续。
6. **度量能逐行对账**：只相信能复算的数字，并把每个指标的盲区写清楚。
7. **配置只有一个真相源**：两端都从本仓库安装，不在 `~/.claude` 或 `~/.codex` 里直接改。

---

## 3. 交付物清单（最终目录）

```
ai-coding-flow/
├── README.md                      一页纸：是什么、怎么装、怎么用
├── PLAN.md                        本文件（确认后转成 docs/）
├── Makefile                       make test / make sync / make doctor
├── workbench/                     ★ 个人工作台
│   ├── wb.py                      sync / doctor / diff / mcp，只依赖 Python 标准库
│   ├── mcp.json                   MCP 清单：想要两端都有哪些 server
│   └── models.json                模型角色配置（第 6 节），同步到 ~/.ai-flow/config.json
├── rules/
│   ├── global.md                  全局规则：同步到 CLAUDE.md 和 AGENTS.md 的受管区块
│   └── project-template.md        仓库级规则模板（flow-onboard 用）
├── playbooks/                     场景手册：Vibe 通道和日常小活照着做
│   ├── read-code.md  add-field-api.md  bugfix.md  unit-test.md  code-review.md
├── skills/                        9 个 skill，两端通用
│   └── flow/{SKILL.md, scripts/flowctl.py, templates/}
├── agents/
│   ├── flow-scout.md              Claude 子 Agent 定义，也是 Codex 调用时的 prompt 来源
│   └── flow-verifier.md           同上
├── specs-starter/                 首次安装时复制到 ~/.ai-flow/specs
├── docs/                          00-overview … 08-workbench-and-models、appendix-article
├── examples/export-orders/        一个填写完整的变更档案样例
└── tests/
    ├── test_flowctl.py            快照、度量、边界情况
    └── test_wb.py                 同步、漂移检测、受管区块读写（在临时 HOME 下跑）
```

---

## 4. Skills 与角色清单

### 4.1 Skills（9 个）

| # | Skill | 什么时候用 | 做什么 | 产物 | 人工门禁 |
|---|---|---|---|---|---|
| 1 | `/flow` | 任何开发任务的入口；或者问「现在该干嘛」 | 数步数、选通道；有进行中的变更时，给出下一步 | 一句话的判断 | — |
| 2 | `/flow-onboard` | 第一次在某个仓库里用 | 探索代码库，生成仓库规则文件和 3–8 条初始 spec 草稿 | `CLAUDE.md` 或 `AGENTS.md`、`.ai/specs/*`（均为 draft） | 逐条确认后改成 active |
| 3 | `/flow-propose` | 开始一个复杂需求 | 建变更；scout 召回 spec、代码入口、相似历史变更；起草提案 | `proposal.md`、`recalled.json` | **硬**：勾选上下文、未决问题清零 |
| 4 | `/flow-design` | 提案通过后 | 基于勾选的上下文写方案，每个引用都标 `file:line` | `design.md` | **硬**：评审方案 |
| 5 | `/flow-tasks` | 方案通过后 | 拆成原子任务，每个任务带可执行的验收 | `tasks.md` | 轻：过目 |
| 6 | `/flow-build` | 任务拆完后 | 逐任务实现：base 快照 → 首版 → v1 快照 → 验收 → final 快照 | 代码，每个任务一次提交 | 每个任务过目 |
| 7 | `/flow-review` | 所有任务完成后 | `flowctl verify` 按模型策略启动独立审查者；驳回就修；放行后给 spec 打分 | `review.md` | 审查者给出 PASS |
| 8 | `/flow-retro` | 审查放行后 | 算度量；教训回写成 spec；处理该修订、下架的 spec；关闭变更 | `retro.md`、新增或修订的 spec | 确认回写内容 |
| 9 | `/flow-incident` | 线上出了问题、发现 bug | 从出问题的代码行追到当初的变更档案，做根因分析，生成新 spec 或审查检查项 | `.ai/incidents/…md`、新 spec | 确认根因与规则 |

在 Codex 里怎么调用：Codex 同样读 `~/.codex/skills/*/SKILL.md`。在对话里点名 skill 即可，比如「用 flow-propose 处理这个需求」，或者按描述自动触发。这个调用方式在 M2 里会实测确认。

### 4.2 角色（子 Agent）

| 角色 | 被谁调用 | 职责 | 为什么独立出来 | 默认模型策略 |
|---|---|---|---|---|
| scout | propose、incident | 召回 spec、定位代码入口、找相似变更，返回带理由的候选清单 | 保持主会话的上下文干净（原文坑二） | 和宿主同一家，用较便宜的模型 |
| verifier | review | 回源码核对 API、字段、依赖是否存在；对照方案；跑验收 | 审查独立（原文坑三） | **跨厂商**，能力不低于实现者 |

### 4.3 和其他 skill 的关系

本方案不依赖其他 skill，可以选择搭配：`/flow-build` 的单个任务可以交给 TDD 类的 skill 来写；需求特别模糊时，可以先用需求澄清类的 skill 把需求问清楚。

### 4.4 flowctl 命令

| 命令 | 用途 | 现状 |
|---|---|---|
| `init` / `new` / `use` / `list` / `status` / `close` | 变更的生命周期 | 已有 |
| `recall` / `score` / `specs lint` / `specs report` | spec 召回、打分、准入检查、修订与下架建议 | 已有 |
| `snapshot` / `checkpoint` | 任务快照、审查用的 diff 终点 | 已有 |
| `trace` / `metrics` / `report` | 过程留痕、度量、趋势 | 已有 |
| `verify --host claude\|codex [--round N]` | **新增**：按模型策略启动审查者，收集输出，校验审查者没有改动工作区 | 待实现 |
| `aftercare [变更] [--days N]` | **新增**：AI 代码存活率、后续改动提交数 | 待实现 |
| `locate <文件>[:行]` | **新增**：从代码行追到它来自哪个变更档案 | 待实现 |
| `incident new <slug>` | **新增**：建立问题档案 | 待实现 |

---

## 5. 个人工作台（个人版 AiBox）

### 5.1 原文的 AiBox 是什么

原文的统一工作台做三件事：

1. **集中管理四类配置**：Rules（编码约束）、Skills（多步工作流）、Commands（快捷指令）、MCP（外部服务接入）。由平台统一维护，下发到每个人的 IDE。
2. **治理**：工具准入、用量统计、合规检查、审计。
3. **流程入口**：复杂任务从这里进入，全程留痕；spec 也接进来，研发处理需求时能直接拿到相关规范。

原文的定位是：**把散落在个人手里的 AI 配置，收成组织资产**。

### 5.2 个人版要解决的真实问题

同时用 Claude Code 和 Codex 时，配置很快就会漂移，典型情况如下：

| | Claude Code | Codex |
|---|---|---|
| 全局规则 | `~/.claude/CLAUDE.md` 内容丰富（常被其他插件管理） | `~/.codex/AGENTS.md` 往往是空的 |
| skills | 数量多 | 数量少，其中一部分是和 Claude 端各放一份的拷贝 |
| MCP | 一套 | 另一套，彼此不一致 |
| 模型 | 会话里选 | `config.toml` 里设置 |

### 5.3 工作台管什么、落到哪里

| 配置 | 真相源（本仓库） | Claude Code 落点 | Codex 落点 | 同步方式 |
|---|---|---|---|---|
| Rules | `rules/global.md` | `~/.claude/CLAUDE.md` 中的受管区块 | `~/.codex/AGENTS.md` 中的受管区块 | 只改标记之间的内容，改前备份 |
| Skills | `skills/*` | `~/.claude/skills/` 软链接 | `~/.codex/skills/` 软链接 | 同名不覆盖，报冲突 |
| Commands | 由 skill 承载 | `/flow-xxx` | 点名 skill | 不单独维护 |
| Agents | `agents/*.md` | `~/.claude/agents/` 软链接 | 没有原生对应，由 `flowctl verify` 用同一份文件生成 prompt，交给 `codex exec` | — |
| MCP | `workbench/mcp.json` | `claude mcp add --scope user …` | `config.toml` 的 `[mcp_servers.x]` | **默认只显示差异、打印命令**，加 `--apply` 并确认后才写入 |
| 模型角色 | `workbench/models.json` | 读 `~/.ai-flow/config.json` | 同左 | 复制 |

受管区块的格式如下。区块之外的内容（比如其他插件写进去的部分）一律不碰：

```
<!-- ai-coding-flow:begin v1 -->
…rules/global.md 的内容…
<!-- ai-coding-flow:end -->
```

### 5.4 wb 命令

| 命令 | 作用 |
|---|---|
| `wb sync [--host claude\|codex\|all]` | 安装或更新 skills、agents 链接、规则区块、模型配置；幂等 |
| `wb doctor` | 体检：两端的安装状态、断开的链接、同名冲突、有人直接改了 `~/.claude`/`~/.codex` 里的受管文件、两个 CLI 能不能用（比如检测到本机 codex CLI 当前坏了）、规则区块的版本 |
| `wb diff` | 列出两端的差异：skill 只在一端有、规则区块不一致、MCP 不一致 |
| `wb mcp diff` / `wb mcp apply` | MCP 清单与两端实际配置的对比；apply 前逐条确认 |
| `wb uninstall` | 只移除 wb 自己装的链接和区块 |

治理的个人版对应关系：

- 准入：只有进了本仓库的 skill 才会被同步；
- 合规：`wb doctor` 检查漂移；
- 审计和留痕：变更档案的 trace；
- 用量统计：不重复造轮子，用 Claude Code 的 `/cost` 和 Codex 自带的用量统计。效能看 `flowctl report`。

---

## 6. 模型策略与双端支持

### 6.1 审查者的独立性分三级

| 级别 | 做法 | 能防住什么 |
|---|---|---|
| L1 上下文独立 | 同一个模型，新开一个 Agent 或进程 | 被主 Agent 的错误结论带节奏（原文要求的底线） |
| L2 模型独立 | 同一家、不同模型 | 同上，加上一部分模型特有的偏差 |
| L3 厂商独立 | Claude 写、Codex 审，或者反过来 | 同上，加上同一家族共有的知识盲区 |

选型规则：

- **审查者的能力不能低于实现者。** 审查要做的是 grep、读源码、跑测试、对照方案，正是最需要推理和工具调用的地方，不能为了省钱降成小模型。
- **scout 可以用便宜的模型。** 它只提供候选，最后由人来勾选。
- **每轮审查都新开。**
- **每一轮在 review.md 里记录实际使用的引擎、模型和独立级别**，这样事后能按级别对比审查的有效性。

### 6.2 配置

`workbench/models.json` 同步到 `~/.ai-flow/config.json`，仓库里的 `.ai/config.json` 可以覆盖：

```json
{
  "roles": {
    "verifier": {
      "engine": "cross",
      "claude": { "model": "opus" },
      "codex":  { "model": "gpt-5.5", "reasoning": "high" },
      "fallback": ["other-model-same-family", "same-model-new-context"]
    },
    "scout": {
      "engine": "same",
      "claude": { "model": "sonnet" },
      "codex":  { "model": "gpt-5.6-luna", "reasoning": "low" }
    }
  }
}
```

- `engine` 的取值：`same`（和宿主同一家）、`cross`（另一家）、`claude`、`codex`（强制指定）。
- 表里的模型名只是占位。Codex 各模型的档位（sol、terra、luna、5.5、codex-auto-review 之间的差别）我没有核实，由你来填。
- 宿主由 skill 调用时显式传入（`--host claude` 或 `--host codex`），不靠猜环境变量。

### 6.3 三种使用方式

| 你的用法 | verifier 实际怎么跑 | 切换模型的方式 |
|---|---|---|
| **只用 Claude Code** | 原生子 Agent `flow-verifier`。frontmatter 里的 `model` 是默认值，skill 调用时用 Agent 工具的 `model` 参数按配置覆盖 | 改 `models.json` 里的 `verifier.claude.model`（fable、opus、sonnet、haiku 或完整模型 ID）。主会话用什么模型，审查就配另一个，这样能到 L2 |
| **只用 Codex** | 另起一个进程 `codex exec -m <模型> -c model_reasoning_effort=<档> -C <仓库> -o <输出文件>`，prompt 由 `agents/flow-verifier.md` 生成。新进程天然是全新上下文 | 改 `verifier.codex.model`；也可以在 `config.toml` 里定义 `[profiles.flow-review]`，用 `-p` 引用 |
| **Claude + Codex（推荐）** | `engine: cross`。在 Claude 里写代码，就调用 `codex exec` 审查；在 Codex 里写代码，就调用 `claude -p --agent flow-verifier --model <模型> --allowedTools …` 审查 | 两边的模型分别配置；另一家不可用时按 fallback 降级，并记录实际级别 |

两边共用同一份审查规则：`agents/flow-verifier.md` 既是 Claude 的子 Agent 定义，也是 Codex 的 prompt 来源。flowctl 负责去掉 frontmatter，再拼上本轮的 diff 范围和上一轮的发现。

### 6.4 `flowctl verify` 的执行过程

```
1. checkpoint：把当前工作区（包括未跟踪文件）存成审查终点，并记下工作区的哈希
2. 读取模型配置和宿主，决定引擎、模型、独立级别
3. 生成 prompt：flow-verifier.md 的正文 + 起点和终点 sha + 验收命令 + 上一轮的发现
4. 启动审查者：
   - 宿主是 Claude 且 engine 为 same：不在这里启动，交回 skill，由 skill 用 Agent 工具调用子 Agent
   - 其他情况：外部进程（codex exec 或 claude -p），有超时限制，输出落到文件
5. 事后校验：工作区哈希有变化 → 本轮作废（审查者不允许改代码）
6. 解析「结论：PASS | REJECT」，追加写入 review.md 的第 N 轮，同时记 trace
```

为什么不用只读沙箱：审查者要跑测试，需要写构建产物，所以用第 5 步的哈希校验来兜底。

### 6.5 前置条件

- 跨厂商审查需要 `codex` CLI 可用；不可用时重装：`npm install -g @openai/codex@latest`。`wb doctor` 会检查这一项。
- Codex 调用 Claude 需要 `claude` CLI 能在非交互模式（`-p`）下运行，并支持 `--agent`、`--model`、`--allowedTools`（2.1.232 已验证）。

---

## 7. 流程

### 7.1 总流程

```
任务进来
 ├─ 线上问题 ──→ /flow-incident：locate 追到当初的变更档案 → 根因 → 新 spec 或审查项
 ├─ 新仓库 ────→ /flow-onboard：生成规则文件和初始 spec
 └─ 开发任务 ──→ /flow 分通道
      ├─ ≤2 步 且 单文件 且 验收一句话说得清 → Vibe：照场景手册直接做 → 跑测试 → 结束
      └─ 其余 / 拿不准 → 六步产线
           ① propose  scout 召回               ✋ 你勾选上下文
           ② design   引用标到源码行            ✋ 你评审
           ③ tasks    原子任务                  👀
           ④ build    base → v1 → final 快照   👀 每个任务
           ⑤ review   flowctl verify（默认跨厂商）→ 驳回就修，最多 3 轮 → spec 打分
           ⑥ retro    度量 → 教训回写成 spec ──→ 下次需求自动召回
                          └─ 合入 2–4 周后：flowctl aftercare（存活率、后续改动）
```

### 7.2 分通道（硬标准）

走 **Vibe 通道**需要同时满足三条：

1. 步数 ≤ 2（阈值可配置）；
2. 只改一个文件；
3. 验收标准一句话说得清。

其余情况一律走 **Spec 产线**，拿不准时也走产线，并说明是哪一条没满足。你明确指定了通道时，按你说的办。

### 7.3 六步产线：每一步的产物

| 步 | 给人评审的 | 交给下一步 AI 的 |
|---|---|---|
| propose | 需求原文、意图、边界、AC、上下文清单、未决问题 | 意图、边界、AC、**勾选过的**上下文 |
| design | 方案、模块边界、接口契约、数据变更、Delta | 模块边界、接口约定、架构约束 |
| tasks | T1…Tn，每个写明输入、输出、依赖、验收 | 单个任务的完整说明 |
| build | 每个任务的代码和验收结果 | 代码变更、三个快照 |
| review | 每轮的结论、发现（带证据）、引擎、模型、独立级别、spec 反馈 | 放行结论、spec 打分 |
| retro | 度量、教训和它落到哪条资产、spec 库的变动 | 下一轮的上下文基线 |

几条关键规矩：

- **方案错了就回退**：build 中发现 design 有错，回到 design 重新过门禁，不在代码里绕过去。
- **失败只回退一个任务**：用这个任务的 base 快照恢复，其他任务不受影响。
- **审查最多 3 轮**：连续 3 轮驳回，交给你判断。
- **可以中途换宿主**：流程状态全在文件里，在 Claude 里做到 build，换到 Codex 执行 `/flow` 也能接着做。

### 7.4 闭环

| 环节 | 触发 | 动作 |
|---|---|---|
| 复盘回写 | 每个变更的 retro | FPY 最低的任务必须归因；每条教训都要落到某条 spec、某个审查项，或者写明不沉淀的理由 |
| spec 生命周期 | review 打分；retro 时看报告 | 误导率高的修订，命中率低的收紧触发词，长期不命中的下架 |
| 合入后追踪 | 合入 2–4 周后 | `flowctl aftercare`：AI 代码存活率、后续改动提交数 |
| 线上反哺 | `/flow-incident` | 出问题的行 → `locate` 找到变更档案 → 对照当时的方案和审查，看哪一环漏了 → 生成 spec 或审查项 |

---

## 8. 使用手册

### 8.1 安装（一次）

```bash
cd ~/Documents/personal/ai-coding/ai-coding-flow
make test                 # 先跑测试
python3 workbench/wb.py doctor             # 体检两端环境
python3 workbench/wb.py sync --host all    # 安装到 Claude Code 和 Codex
python3 workbench/wb.py mcp diff           # 看两端 MCP 差异，按需 apply
```

### 8.2 接入一个仓库（每个仓库一次）

对 AI 说 `/flow-onboard`。它会生成规则文件和 spec 草稿，你逐条确认。

### 8.3 日常场景

| 场景 | 你说 | 会发生什么 |
|---|---|---|
| 改个文案、修个单文件小 bug | `/flow 把 xx 的超时改成 5s` | Vibe → 直接改 → 跑测试 → 结束 |
| 不熟悉的代码 | 按 `playbooks/read-code.md` 提问 | 输出分析报告，不改代码 |
| 一个跨文件的需求 | `/flow` + 需求原文，或者需求文件的路径 | 进入产线 → propose → **停下等你勾选** |
| 继续昨天没做完的需求（可以换个工具） | `/flow` | 根据 `flowctl status` 接着做下一步 |
| 线上报警、测试提了 bug | `/flow-incident` + 报错信息 | 定位 → 追到变更 → 根因 → 生成规则 → 需要修复时转入 Vibe 或产线 |
| 换审查模型 | 改 `workbench/models.json` → `wb sync` | 下一轮审查生效 |
| 每周复盘（10 分钟） | `flowctl report`、`flowctl specs report`、`wb doctor` | 看趋势、处理 spec、检查漂移 |
| 合入几周后 | `flowctl aftercare <变更>` | 看 AI 代码的存活率和后续改动 |

### 8.4 一个需求走完产线，你需要介入的地方

```
/flow-propose  → ✋ 勾选上下文、回答未决问题        （约 5 分钟）
/flow-design   → ✋ 评审方案                        （约 10 分钟）
/flow-tasks    → 👀 过一眼任务列表                  （约 1 分钟）
/flow-build    → 👀 每个任务过目，决定是否提交       （每个任务约 1–2 分钟）
/flow-review   →    自动进行（跨厂商审查），3 轮不过才找你
/flow-retro    → ✋ 确认回写的 spec                  （约 3 分钟）
```

### 8.5 度量怎么读

| 指标 | 算法 | 盲区 |
|---|---|---|
| FPY（首次正确率） | (终版行数 − 被改行数) ÷ 终版行数，按任务计算、按行数加权 | 衡量的是生成质量，不是代码质量；另外补一个丢弃率 |
| AI 代码占比 | 终版新增行中，能和 AI 首版逐行匹配上的比例 | Vibe 通道的改动不计入；和 AI 写的内容恰好相同的手写行，会被算成 AI 的 |
| 采纳率 | 采纳次数 ÷ 生成次数（按步骤统计） | 依赖 skill 如实记录 |
| spec 命中率 / 误导率 | review 时打的分 | 依赖人工判断 |
| 审查有效性 | 按独立级别（L1/L2/L3）统计：驳回率、之后是否在 incident 中暴露漏检 | 样本少时没有参考价值 |
| 存活率 / 后续改动数 | 合入 N 天后复查 | 后续改动不一定是返工 |

看趋势，不看单个数值。

---

## 9. 实现计划（确认后执行）

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 工具层 | flowctl 新增 `verify`、`aftercare`、`locate`、`incident`；metrics 额外记录提交列表、文件列表、AI 行签名 | `tests/test_flowctl.py` 通过，覆盖之前审查发现的所有边界情况；`verify` 用假的审查者命令测试降级和哈希校验 |
| M2 工作台 | `wb.py` 的 sync、doctor、diff、mcp、uninstall；受管区块读写；`models.json` | `tests/test_wb.py` 在临时 HOME 下通过；**不碰真实的 ~/.claude 和 ~/.codex，直到你确认** |
| M3 流程层 | 调整现有 7 个 skill；新增 `flow-onboard`、`flow-incident`；verifier 和 scout 的 prompt 做双端适配 | 自动核对每个 skill 里的命令和 flowctl 一致；在 Codex 里实测 skill 的调用方式 |
| M4 规则与手册 | `rules/` 两份、`playbooks/` 五份 | — |
| M5 文档与样例 | `docs/` 九章、`examples/export-orders`、README | 样例能被 `flowctl status` 和 `metrics` 正常读取 |
| M6 验证 | 在临时仓库里完整演练一个需求（Claude 实现、Codex 审查）和一次事故；再派一个独立 Agent 审查全部内容 | 审查发现全部处理完 |

---

## 10. 需要你确认的问题

1. **目录位置**：`~/Documents/personal/ai-coding/ai-coding-flow/`，可以吗？
2. **skill 命名**：统一用 `flow-` 前缀，可以吗？
3. **`.ai/` 要不要提交进仓库**：默认建议提交。公司仓库不方便提交的话，可以加进 `.git/info/exclude`，只保留在本地。你倾向哪种？
4. **全局规则的受管区块**：可以往 `~/.claude/CLAUDE.md` 和 `~/.codex/AGENTS.md` 里写入带标记的区块吗（改前备份，只改标记之间的内容）？还是只打印出来，由你手动粘贴？
5. **MCP 同步**：默认只显示差异、打印命令，加 `--apply` 并逐条确认后才写入。这样可以吗？
6. **模型**：
   - verifier 默认用跨厂商（`cross`），可以吗？
   - Codex 那边审查和 scout 分别用哪个模型？我没有核实 sol、terra、luna、5.5、codex-auto-review 之间的档位差别。
7. **Codex CLI**：跨厂商审查依赖它，需要你先执行 `npm install -g @openai/codex@latest` 修好（这个命令我不会替你执行）。
8. **hooks**：有一个可选项：会话结束时，如果有任务打了 v1 快照但还没打 final，就提醒一下。要不要？
9. **范围**：9 个 skill 和 5 个场景手册，有没有要增加或删减的？
