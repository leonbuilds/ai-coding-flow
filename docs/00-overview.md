# 00 · 总览

## 这是什么

`ai-coding-flow` 是一套**个人**使用的 AI Coding 方案：适用于任意 git 仓库，Claude Code 和 Codex 双端通用。它把「怎么判断该不该走完整流程」「上下文该给多少」「谁来审查」「有没有变好」这几件事，从个人手感变成文件里能查、能复算的状态。

参考来源：货拉拉技术文章《个人提效，攒不成组织提效：货拉拉 AI Coding 落地实践》。原文面向团队和组织，本项目把同样的问题收窄到个人可以独立落地的范围（详见 [appendix-article.md](appendix-article.md)）。

## 与原文的对应关系

原文把团队的 AI Coding 落地分成三个阶段，外加一个统一工作台。个人版逐一对应：

| 原文 | 要解决的问题 | 个人版对应 | 详见 |
|---|---|---|---|
| 统一工作台 AiBox | AI 配置散落在个人手里，各自为政 | **个人工作台**：本仓库是唯一真相源，`wb sync` 同步到 Claude Code 和 Codex，`wb doctor` 检查漂移 | [08-workbench.md](08-workbench.md) |
| 一、普及：把下限抬起来 | 会用工具，但不知道该怎么让 AI 干活 | **全局规则 + 场景手册（playbooks）**：统一的编码约束，照着就能跑的高频场景 | [01-lanes.md](01-lanes.md) |
| 二、规范驱动：让 AI 按规范写 | 能用了，但不稳定，方案质量看个人状态 | **分通道 + 六步 spec 产线** | [01-lanes.md](01-lanes.md)、[02-pipeline.md](02-pipeline.md)、[03-spec-library.md](03-spec-library.md) |
| 三、AI Native：全生命周期闭环 | 做过的需求没有沉淀，踩过的坑还会再踩 | **retro 回写 + 合入后度量 + 线上反哺** | [05-closed-loop.md](05-closed-loop.md) |
| 坑三：主从双 Agent 需要独立审查 | AI 幻觉需要第二只眼睛 | **独立审查者（scout/verifier）+ 每次现场选择的模型策略**，可跨厂商 | [07-models-and-hosts.md](07-models-and-hosts.md) |

另有一个横向目标：**可度量**。用 FPY、AI 代码占比、采纳率、spec 命中率等指标判断哪个环节需要改进（[04-metrics.md](04-metrics.md)）。

**明确不做的事**：团队管理（下发给他人、准入审计、组织形态）、异步云端 Agent、v1 不做 hooks（见 [09-roadmap.md](09-roadmap.md)）。

## 四层架构

```
┌───────────────── 个人工作台（本仓库 = 唯一真相源）───────────────────┐
│  rules/   skills/   agents/   playbooks/   workbench/mcp.json           │
│                     │ wb sync / wb doctor / wb diff                     │
│         ┌───────────┴────────────┐                                     │
│         ▼                        ▼                                     │
│   ~/.claude/…              ~/.codex/…       两端各自安装、互相检查漂移  │
│   ~/.ai-flow/bin/{flowctl,wb}、~/.ai-flow/kit（两端共用的命令入口）      │
└──────────────────────────────────────────────────────────────────────────┘
┌────────────── Claude Code 或 Codex（宿主，二选一或同时用）───────────────┐
│ ① 规则层  全局规则（受管区块，仅在含 .ai/ 的仓库生效）+ 仓库级规则文件    │
│          CLAUDE.md / AGENTS.md                                          │
├──────────────────────────────────────────────────────────────────────────┤
│ ② 流程层  9 个 Skill（两端通用 SKILL.md）                                │
│   入口 /flow   接入 /flow-onboard   反哺 /flow-incident                 │
│   产线 /flow-propose → design → tasks → build → review → retro          │
│   角色 scout（召回上下文）  verifier（独立审查）                        │
│   引擎/模型：每次使用时现场选择，不落配置文件                           │
├──────────────────────────────────────────────────────────────────────────┤
│ ③ 工具层  flowctl：生命周期、召回、打分、快照、度量、审查调度（verify）、│
│          合入后追踪（aftercare）、定位（locate）、事故档案（incident）   │
├──────────────────────────────────────────────────────────────────────────┤
│ ④ 资产层  spec 库（.ai/specs + ~/.ai-flow/specs）                        │
│          变更档案（.ai/changes/<日期-slug>/）                           │
└──────────────────────────────────────────────────────────────────────────┘
        ▲ 线上问题 / 复盘教训 ── 回写成 spec ── 下次需求自动召回 ┘
```

## 设计原则

每一条都对应原文的一个坑或一条经验：

1. **通道按硬标准来分**：数步数，不凭手感（[01-lanes.md](01-lanes.md)）。
2. **上下文精准召回**：每条 spec 都要能回答「什么条件下注入」（触发词 / 路径 / `when`，见 [03-spec-library.md](03-spec-library.md)）。
3. **审查者必须独立**：上下文独立是底线，模型独立或厂商独立更好（[07-models-and-hosts.md](07-models-and-hosts.md)）。
4. **人在环里**：提案（propose）和方案（design）两步是硬门禁，其余步骤视情况轻门禁或人工过目。
5. **一切从文件恢复**：流程状态全部落在 `.ai/` 下，换宿主、换会话都能靠 `flowctl status` 接着做。
6. **度量能逐行对账**：只相信能复算的数字，每个指标都写清楚盲区（[04-metrics.md](04-metrics.md)）。
7. **配置只有一个真相源**：两端都从本仓库安装，不在 `~/.claude` 或 `~/.codex` 里手改受管部分（[08-workbench.md](08-workbench.md)）。

## 目录结构

```
ai-coding-flow/
├── README.md                       一页纸：组成、安装、日常用法、样例
├── Makefile                        test / doctor / sync-dry / sync / uninstall / demo / demo-codex
├── install.sh                      等价于 python3 workbench/wb.py sync（参数原样透传）
├── PLAN.md                         最初的设计方案稿（docs/ 是它的确认稿+实现后的修订版）
├── workbench/
│   └── wb.py                       sync / doctor / diff / mcp / uninstall，只依赖标准库
├── rules/
│   ├── global.md                   全局规则：同步到 CLAUDE.md / AGENTS.md 的受管区块
│   └── project-template.md         仓库级规则模板，flow-onboard 用
├── playbooks/                      场景手册：read-code / add-field-api / bugfix / unit-test / code-review
├── skills/
│   ├── flow/                       入口 skill + flowctl.py + 6 个产线模板
│   ├── flow-onboard/ flow-propose/ flow-design/ flow-tasks/
│   ├── flow-build/ flow-review/ flow-retro/ flow-incident/
├── agents/
│   ├── flow-scout.md               Claude 子 Agent 定义，也是 Codex 调用时的 prompt 来源
│   └── flow-verifier.md            同上
├── specs-starter/                  首次 wb sync 时复制到 ~/.ai-flow/specs（全局 spec 种子库）
├── docs/                           本文档目录
├── examples/
│   ├── demo.sh                     在临时仓库里完整跑一遍产线，重新生成 export-orders/（审查可用测试替身或真实 Codex）
│   ├── src/                        demo.sh 的输入素材：一个订单玩具服务 + 已填写好的产线文档模板
│   └── export-orders/              demo.sh 跑出来的完整变更档案（含真实 Codex 审查记录，见 06-manual.md）
└── tests/
    ├── test_flowctl.py             flowctl 的端到端测试
    ├── test_wb.py                  wb 的同步 / 漂移检测测试（临时 HOME 下跑）
    └── test_consistency.py         文档/skill 里出现的 flowctl、wb 子命令和长参数必须真实存在
```

仓库里唯一还缺的是 `workbench/models.json`——`PLAN.md` 的目录清单里出现过，但不是当前实现的一部分：模型改为每次现场选择（见 [07-models-and-hosts.md](07-models-and-hosts.md)）。测试用 `make test`（等价于 `python3 -m unittest discover -s tests -v`）跑（见 [06-manual.md](06-manual.md)）。
