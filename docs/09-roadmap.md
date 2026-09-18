# 09 · 演进路线与风险

## 明确不做的事（当前范围之外）

- **团队管理**：下发给他人、准入审计、组织形态——本方案定位始终是个人使用，不面向团队治理。
- **异步云端 Agent**：所有角色调用都是同步阻塞的本地/CLI 进程（见 [07-models-and-hosts.md](07-models-and-hosts.md)），没有云端排队、异步任务队列。
- **v1 不做 hooks**：`PLAN.md` 曾提出一个可选项——会话结束时若有任务打了 `v1` 快照但还没打 `final`，提醒一下。**v1 没有实现，也没有任何 hook 机制**。这是一个待评估的未来项，见下表。
- **`workbench/models.json`**：`PLAN.md` 设计过的按角色固定模型配置文件已被放弃，改为每次现场选择（见 [07-models-and-hosts.md](07-models-and-hosts.md)），这不是"还没做"，而是确认后的设计变更。

## 已落地的部分

对照 `PLAN.md` 第 9 节的里程碑划分，当前代码已经覆盖：

| 里程碑（PLAN.md 原计划） | 状态 |
|---|---|
| M1 工具层：`verify`/`aftercare`/`locate`/`incident`，metrics 记录提交/文件/AI 行签名 | 已实现（`skills/flow/scripts/flowctl.py`），`tests/test_flowctl.py` 覆盖端到端场景 |
| M2 工作台：`wb.py` 的 sync/doctor/diff/mcp/uninstall，受管区块读写 | 已实现（`workbench/wb.py`），`tests/test_wb.py` 在临时 `HOME` 下跑，不碰真实配置 |
| M3 流程层：9 个 skill、`flow-onboard`、`flow-incident`，verifier/scout 双端适配 | 已实现（`skills/*/SKILL.md`、`agents/*.md`） |
| M4 规则与手册：`rules/` 两份、`playbooks/` 五份 | 已实现 |
| M5 文档与样例：`docs/` 九章、`examples/export-orders`、README | 已实现：`README.md`、`examples/demo.sh` + `examples/export-orders/`（`examples/src/` 是 `demo.sh` 的输入素材，`export-orders/` 才是产出的完整档案，两者都在，和 `PLAN.md` 的目录命名一致） |
| M6 验证：临时仓库里完整演练一次需求 + 一次事故，再派独立 Agent 审查 | 本轮文档梳理未做这项验证，留给下一次实际使用时执行 |

## 待评估的未来项

| 项目 | 现状 | 建议 |
|---|---|---|
| Hooks（会话结束提醒未打 final 快照） | `PLAN.md` 提过，未实现 | 低优先级；`flowctl status` 已能查到未完成任务，人工习惯性跑一次即可替代 |
| 异步/云端执行 | 不在范围内 | 如果未来要支持长任务排队执行，需要重新设计 `flowctl agent`/`verify start` 的同步阻塞模型 |
| Codex 模型档位表 | 已用运行时读取 `~/.codex/models_cache.json` 替代静态表 | 依赖该缓存文件的格式稳定性；Codex CLI 升级后如果缓存结构变化，`codex_models()` 的解析需要跟着更新 |
| 三层 AI 代码占比近似 | 当前只实现第一层（整行文本精确匹配） | 更细粒度的行内改动匹配、语义等价匹配仍是设想，未实现（见 [04-metrics.md](04-metrics.md)） |

## 风险表

| 风险 | 影响 | 缓解 |
|---|---|---|
| 本机 `codex` CLI 不可用或版本不兼容 | 无法进行跨厂商审查（L3），退化为只能 L1/L2 | `wb doctor` 检查 CLI 版本；`flowctl models` 会隐藏不可用的引擎，不会静默选中一个跑不通的引擎 |
| 审查有效性统计样本量小 | L1/L2/L3 的驳回率、漏检率对比在样本少时没有参考价值 | `flowctl report` 长期积累后再看趋势，不依赖单个变更下结论 |
| Spec 触发词误判 | ASCII 触发词整词匹配、非 ASCII 触发词退化为子串匹配，中文触发词更容易产生噪声召回 | `flowctl specs lint` 对过短触发词报警；`flowctl specs report` 的误导率/命中率机制持续淘汰质量差的 spec（详见 [03-spec-library.md](03-spec-library.md)） |
| Aftercare 的"后续改动提交数"是弱信号 | 同一批文件被后续提交修改，不一定是返工，可能是叠加新需求 | 需要人工结合提交信息判断，工具本身只提供线索不下结论（见 [04-metrics.md](04-metrics.md)） |
| MCP env 值解析依赖至少一端已配置 | 两端都没配置过某个 env key 时，`wb mcp apply` 无法解析 `<from-host>` 占位符，会报错跳过该 server | 先在任意一端手动配置一次，或直接在 `workbench/mcp.json` 里明文填写该 server（牺牲一次性的脱敏保证） |
| 符号链接冲突不自动解决 | 目标位置已有同名非本仓库文件/链接时，`wb sync` 只报告不覆盖，可能导致某个 skill 迟迟没装上 | `wb doctor`/`wb diff` 会持续报告冲突，需要人工确认后手动清理 |
| 审查者绕过工作区校验的边界情况 | 校验只比较 git 可见的内容（含未跟踪但未被 gitignore 的文件），审查者对被 gitignore 的产物做的改动不会触发 INVALID；新增未忽略文件只记 warning、不作废整轮，理论上可以被滥用成"顺手留一个文件" | 依赖 `agents/flow-verifier.md` 里的纪律约束（"不要修改任何受 git 跟踪的文件，也不要新建文件"），而非纯技术强制；warning 会写进轮次表，人工复核时能看到 |

## 何时重新评估

- Codex CLI 有大版本更新时，重新验证 `flowctl models`/`codex exec` 的调用参数（`-s`、`-c model_reasoning_effort=`）是否仍然兼容。
- 团队场景需求出现时（多人共享同一个 spec 库、需要审计谁做过什么），需要重新设计"个人真相源"这一假设，届时应该是一个新方案而不是本方案的简单扩展。
