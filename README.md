# AI Coding Flow

一套**个人**用的 AI Coding 方案，适用于任何 git 仓库，**Claude Code 和 Codex 两端通用**。参考了货拉拉的《个人提效，攒不成组织提效：货拉拉 AI Coding 落地实践》。

> 简单任务直接做；复杂任务走「提案 → 方案 → 拆任务 → 逐任务实现 → 独立审查 → 回写」六步。
> 每一步留痕、可度量。审查可以跨厂商（Claude 写、Codex 审，或者反过来）。
> 踩过的坑回写成 spec，下一次需求自动召回。

> **完整方案与使用手册（单文档）**：[方案与使用手册.md](方案与使用手册.md)

## 组成

| 层 | 内容 | 位置 |
|---|---|---|
| 工作台 | 唯一真相源，同步到 `~/.claude` 和 `~/.codex`，并检查漂移（个人版 AiBox） | `workbench/wb.py` |
| 规则 | 全局规则（受管区块）、仓库规则模板 | `rules/` |
| 场景手册 | 读代码、加字段接口、修 bug、补单测、CR | `playbooks/` |
| 流程 | 9 个 skill：`/flow` 入口、`onboard`、`propose`、`design`、`tasks`、`build`、`review`、`retro`、`incident` | `skills/` |
| 角色 | `flow-scout`（召回上下文）、`flow-verifier`（独立审查），两端共用同一份定义 | `agents/` |
| 工具 | `flowctl`：状态、spec 召回与打分、快照、度量、审查调度、合入后追踪、问题定位 | `skills/flow/scripts/flowctl.py` |

只依赖 Python 3.9+ 标准库和 git。

## 安装

```bash
git clone https://github.com/leonbuilds/ai-coding-flow.git   # 放在一个固定的位置：skills 以软链接安装，指向这里
cd ai-coding-flow
make test        # 全部测试都在临时目录里跑
make doctor      # 体检：两端 CLI、链接、规则区块、MCP
make sync-dry    # 预览会改哪些东西
make sync        # 安装：两端的 skills 链接、Claude 的 agents、两端的规则区块、~/.ai-flow/bin/{flowctl,wb}
```

`sync` 只做下面这些，其他一概不碰：

- 在 `~/.claude/CLAUDE.md` 和 `~/.codex/AGENTS.md` 中，只写 `<!-- ai-coding-flow:begin … end -->` 之间的内容，改前备份到 `~/.ai-flow/backups/`；
- 遇到同名的 skill，跳过并报冲突，不覆盖；
- 卸载用 `make uninstall`，只移除它自己装的东西。

MCP 同步是单独的一步，默认只显示差异：

```bash
python3 workbench/wb.py mcp import   # 用两端当前配置生成 workbench/mcp.json（env 值会脱敏）
# 编辑每个 server 的 hosts，表示「希望它出现在哪几端」
python3 workbench/wb.py mcp diff
python3 workbench/wb.py mcp apply    # 逐条确认后才写入
```

## 使用

| 场景 | 对 Claude Code 或 Codex 说 |
|---|---|
| 第一次在某个仓库里用 | `/flow-onboard` |
| 任何开发任务 | `/flow <任务描述>`：先判断通道，小任务直接做，其余进入产线 |
| 继续之前没做完的（可以换一个工具继续） | `/flow` |
| 线上问题、测试提的 bug | `/flow-incident <现象>` |

在产线里，你要介入的地方：

| 步骤 | 你做什么 |
|---|---|
| propose | ✋ 勾选上下文 |
| design | ✋ 评审方案 |
| tasks | 👀 过目 |
| build | 👀 每个任务过目 |
| review | 选择审查者的引擎和模型（每轮都会问你） |
| retro | ✋ 确认回写的 spec |

需要选模型的地方（scout、每一轮审查）都会先列出两端可用的模型，Codex 的模型会附带推理档位，由你来选。上一次的选择会作为第一个选项。

常用命令：

```bash
F="$HOME/.ai-flow/bin/flowctl"
"$F" status               # 当前进度和下一步
"$F" report               # 所有变更的 FPY、AI 代码占比、采纳率、审查级别
"$F" specs report         # spec 命中率、误导率，以及修订或下架建议
"$F" aftercare <变更>      # 合入几周后：AI 代码存活率、后续改动
"$F" locate <文件>:<行>    # 这一行来自哪个变更档案
```

## 样例

[`examples/export-orders/`](examples/export-orders/) 是一个完整走完产线的档案：订单批量导出 CSV，两个任务。其中 T1 的首版漏掉了「分转元」，改完后 FPY 是 96%。审查由真实的 Codex（gpt-5.6-luna）执行，结论 PASS，结果在 `review/round-1.out.md`。

重新生成这份档案：

```bash
make demo                                             # 审查用测试替身
make demo-codex MODEL=gpt-5.6-luna REASONING=low      # 审查用真实 Codex
```

在验证过程中还做过一次反向测试：故意写了一段调用不存在方法的代码，而且测试全部通过（因为没有测试覆盖到它）。Codex 审查者回源码核对后判为「阻断 · 幻觉」并驳回。这正是原文第三个坑「幻觉要靠第二只眼睛」要解决的问题。

## 文档

| | |
|---|---|
| [docs/00-overview.md](docs/00-overview.md) | 方案总览 |
| [docs/01-lanes.md](docs/01-lanes.md) | 分通道 |
| [docs/02-pipeline.md](docs/02-pipeline.md) | 六步产线 |
| [docs/03-spec-library.md](docs/03-spec-library.md) | spec 库 |
| [docs/04-metrics.md](docs/04-metrics.md) | 度量 |
| [docs/05-closed-loop.md](docs/05-closed-loop.md) | 闭环与线上反哺 |
| [docs/06-manual.md](docs/06-manual.md) | 使用手册与常见问题 |
| [docs/07-models-and-hosts.md](docs/07-models-and-hosts.md) | 双端与模型选择 |
| [docs/08-workbench.md](docs/08-workbench.md) | 个人工作台 |
| [docs/09-roadmap.md](docs/09-roadmap.md) | 演进路线 |
| [docs/appendix-article.md](docs/appendix-article.md) | 原文要点与取舍 |
| [PLAN.md](PLAN.md) | 经过确认的方案稿（v2） |

## License

MIT，见 [LICENSE](LICENSE)。
