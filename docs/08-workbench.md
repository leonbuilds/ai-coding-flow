# 08 · 个人工作台

实现见 `workbench/wb.py`（只依赖 Python 3.9+ 标准库）；测试见 `tests/test_wb.py`（在临时 `HOME` 下跑，绝不碰真实的 `~/.claude`、`~/.codex`）。

## 原文的 AiBox 是什么

货拉拉原文的统一工作台 AiBox 做三件事：

1. **集中管理四类配置**：Rules（编码约束）、Skills（多步工作流）、Commands（快捷指令）、MCP（外部服务接入），由平台统一维护，下发到每个人的 IDE。
2. **治理**：工具准入、用量统计、合规检查、审计。
3. **流程入口**：复杂任务从这里进入，全程留痕；spec 也接进来，研发处理需求时能直接拿到相关规范。

定位是"把散落在个人手里的 AI 配置，收成组织资产"。

## 个人版管什么、落到哪里

个人版要解决的真实问题：同时用 Claude Code 和 Codex，两端的配置会各自漂移（skill 各装一份、规则内容不同步、模型选择方式不一致）。工作台把本仓库定为唯一真相源，两端都从这里安装：

| 配置类型 | 真相源（本仓库） | Claude Code 落点 | Codex 落点 | 同步方式 |
|---|---|---|---|---|
| Skills | `skills/*`（含 `SKILL.md` 的目录） | `~/.claude/skills/<name>` 符号链接 | `~/.codex/skills/<name>` 符号链接 | `wb sync`；目标位置已有同名**非本仓库**文件/链接时跳过并报冲突，绝不覆盖 |
| Agents | `agents/*.md` | `~/.claude/agents/<name>.md` 符号链接（Claude 原生子 Agent 机制） | 没有落点；Codex 侧由 `flowctl agent` / `flowctl verify` 在运行时直接读取仓库里的同一份文件生成 prompt（`role_prompt()` 去掉 frontmatter 后拼上本次输入），交给 `codex exec` 或 `claude -p` | Claude 侧走符号链接；Codex 侧不需要安装动作 |
| Rules（全局规则） | `rules/global.md` | `~/.claude/CLAUDE.md` 中 `<!-- ai-coding-flow:begin sha=... -->…<!-- ai-coding-flow:end -->` 之间的受管区块 | `~/.codex/AGENTS.md` 同样的受管区块 | `wb sync`；只替换标记之间的内容，改前自动备份；区块之外的内容（比如其他插件写进 `CLAUDE.md` 的部分）一律不碰 |
| Commands | 由 skill 承载，不单独存在 | `/flow-xxx` 直接调用，或按 `SKILL.md` 的 `description` 自动触发 | 对话里点名 skill 名称即可调用 | 不单独维护，随 Skills 一起同步 |
| MCP | `workbench/mcp.json`（首次用 `wb mcp import` 从两端当前配置生成；仓库里默认不自带这个文件，且已在 `.gitignore` 里，不会被提交） | `claude mcp add-json -s user <name> '<json>'`（更新前先 `claude mcp remove -s user <name>`） | `codex mcp add <name> [--env k=v]... -- <command> <args>`，或 `codex mcp add <name> --url <url>` | `wb mcp diff` 只显示差异；`wb mcp apply` 逐条打印命令并确认（`--yes` 跳过确认）后才执行；清单之外的 server（`unmanaged`）永不改动；`headers`/`cwd`/`bearer_token_env_var` 等标准字段之外的配置统一放进 `extra`（值同样脱敏为 `<from-host>`） |
| 模型选择 | 无配置文件——每次使用 scout / verifier 时现场问用户 | 通过 `--model` 传给 Agent 工具 | 通过 `--model`/`-c model_reasoning_effort=` 传给 `codex exec` | 不存在"同步"这个动作；`~/.ai-flow/last_models.json` 只是每个角色"上次选择"的缓存，不属于工作台管理范围 |
| 命令入口 | `skills/flow/scripts/flowctl.py`、`workbench/wb.py`、仓库根目录本身 | `~/.ai-flow/bin/flowctl`、`~/.ai-flow/bin/wb`、`~/.ai-flow/kit` 三个符号链接（与安装到哪一端无关，`wb sync` 无论 `--host` 传什么都会处理这三个链接） | 同左 | `wb sync` |
| 全局 spec 种子库 | `specs-starter/` | — | — | 首次 `wb sync` 时若 `~/.ai-flow/specs` 不存在，整体**复制**（非链接）过去，之后由 `/flow-retro`、`/flow-incident` 直接编辑 `~/.ai-flow/specs` 下的文件，不再和仓库里的 `specs-starter/` 保持关联 |

受管区块的具体格式：

```
<!-- ai-coding-flow:begin sha=1a2b3c4d -->
…rules/global.md 的内容（去除首尾空白后加一个换行）…
<!-- ai-coding-flow:end -->
```

`sha` 是区块内容的 SHA-1 前 8 位（`sha8()`），用来判断区块是"最新"“落后于源文件"还是"被手动改过"（详见下面的漂移检测）。

## wb 命令

| 命令 | 作用 |
|---|---|
| `wb sync [--host claude\|codex\|all] [--no-rules] [--dry-run]` | 安装或更新 skills、agents 符号链接、规则受管区块、命令入口、全局 spec 种子库；幂等；`--dry-run` 只打印将要做的动作 |
| `wb doctor` | 体检：两端 CLI 版本/可用性、skills+agents 链接状态、规则区块状态、命令入口链接状态、全局 spec 库是否存在、MCP 清单一致性；返回非零表示有问题 |
| `wb diff` | 列出：skill 只在一端安装、两端各放一份非链接拷贝（可能已漂移）、两端规则区块状态、MCP 差异（有清单时） |
| `wb mcp import [--force]` | 从两端当前实际配置生成 `workbench/mcp.json`；`env` 和 `extra` 的值一律替换成 `<from-host>` 占位符，不落库；已存在时默认拒绝覆盖，加 `--force` 才重新生成；参数或 URL 里像是带凭据时会打印警告；同名 server 两端配置不同时只管理先出现的一端，另一端的名字记进该条目的 `note` 字段 |
| `wb mcp diff` | 对比清单与两端实际安装，五种动作：`+ add` 缺失待添加、`~ update` 配置不一致（默认不会被 `apply` 自动执行）、`! manual` 需要手动配置（覆盖会丢失 env/extra 字段，或目标端缺失但清单带了 `extra` 字段）、`! unsupported` codex 不支持的传输类型（只支持 `stdio`/`http`）、`· unmanaged` 不在清单中的 server（永不触碰） |
| `wb mcp apply [--host ...] [--yes] [--allow-update]` | 只自动执行 `add`；`update` 默认只打印说明、不执行，加 `--allow-update` 才会去更新（且仅当不会丢字段时）；逐条打印将要执行的命令（env 值打码为 `***`）并等待确认，`--yes` 跳过确认；`manual`/`unsupported`/`unmanaged` 的条目永远不会被 apply |
| `wb uninstall [--host claude\|codex\|all]` | 只移除 wb 自己装的 skills/agents 链接和规则区块（移除前备份），以及命令入口链接；`~/.ai-flow` 目录本身保留不动 |

路径可以用环境变量覆盖（测试用）：`CLAUDE_HOME`、`CODEX_HOME`、`AI_FLOW_HOME`、`CLAUDE_JSON`、`WB_MCP_MANIFEST`。

## 备份位置

任何一次对 `CLAUDE.md`/`AGENTS.md` 受管区块的写入或移除（`wb sync`、`wb uninstall`）之前，都会先把该文件完整备份到：

```
~/.ai-flow/backups/<YYYYMMDD-HHMMSS>/<父目录名>-<文件名>
```

例如 `~/.ai-flow/backups/20260918-153000/claude-CLAUDE.md`。备份只在文件已存在时发生（`backup()` 对不存在的文件直接返回 `None`），每次写入都会重新生成一份带时间戳的备份，不会覆盖旧备份。

规则文件的读写全程保留原始字节（`read_keep`/`write_keep` 用 `newline=""` 打开），不会把 Windows 上的 CRLF 换行悄悄转成 LF，也不会改变文件里已有内容的编码细节。`wb uninstall` 移除受管区块时，连同追加时自动加的那一行空行分隔符一起删掉，区块之外的字节原样保留。

## 漂移检测

`wb doctor` 和 `wb diff` 是检测漂移的两个工具，判定逻辑都在 `link_state()` 和 `rules_state()`：

**链接状态**（`link_state(src, dst)`）：

| 状态 | 含义 |
|---|---|
| `ok` | `dst` 是指向 `src` 的符号链接，且目标存在 |
| `broken` | `dst` 是指向 `src` 的符号链接，但目标已不存在（`wb sync` 会自动修复：先 `unlink` 再重新链接） |
| `conflict-link` | `dst` 是符号链接，但指向别处 |
| `conflict-file` | `dst` 存在，但不是符号链接（普通文件或目录） |
| `missing` | `dst` 不存在 |

**规则区块状态**（`rules_state(host)`）：

| 状态 | 含义 |
|---|---|
| `ok` | 区块存在，且内容 sha 和源文件 sha 一致 |
| `no-source` | 仓库里没有 `rules/global.md` |
| `missing` | 目标文件里没有找到受管区块标记 |
| `edited` | 区块内容的 sha 和标记里记录的 sha 不一致——说明有人手动改过区块内容，下次 `wb sync` 会覆盖它 |
| `stale` | 区块内容和标记完整一致，但记录的 sha 落后于当前 `rules/global.md` 的 sha——说明源文件更新了，还没同步过去 |
| `damaged` | 文件里出现过 `ai-coding-flow:` 字样，但匹配不出完整的 `begin ... end` 区块（比如标记被手动删了一半）——`wb sync` 拒绝动这个文件，需要人工修复结构后再同步 |

`wb doctor` 把这几类状态汇总成一个问题计数（非 `ok` 都算一个问题），另外还检查：命令入口链接状态、全局 spec 库是否已初始化、MCP 清单一致性（有清单文件时）。`wb diff` 额外检查"两端各放了一份同名但都不是符号链接的拷贝"（很可能是历史遗留、已经彼此不一致的重复配置），建议收进本仓库统一管理。

## 治理的个人版对应

| 原文的治理动作 | 个人版对应 |
|---|---|
| 准入 | 只有进了本仓库 `skills/` 目录的 skill 才会被 `wb sync` 同步 |
| 合规 | `wb doctor` 检查两端漂移 |
| 审计和留痕 | 变更档案的 `trace.jsonl`（见 [02-pipeline.md](02-pipeline.md)） |
| 用量统计 | 不重复造轮子：用 Claude Code 自带的 `/cost` 和 Codex 自带的用量统计；效能（不是用量）看 `flowctl report` |
