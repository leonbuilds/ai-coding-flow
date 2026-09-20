# 06 · 使用手册

## 安装（一次）

`Makefile` 包了标准操作，都可以直接跑 `python3 workbench/wb.py <子命令>` 等价：

```bash
cd ~/Documents/personal/ai-coding/ai-coding-flow

make test        # 先跑测试（38 个用例，覆盖 flowctl、wb 的端到端场景，以及文档一致性检查）
make doctor      # 体检两端环境：CLI 版本、已装的 skills/agents 链接状态、规则区块状态、spec 库、MCP 清单
make sync-dry    # 预览 sync 会做什么，不实际写入
make sync        # 安装 / 更新到 Claude Code 和 Codex（幂等，可重复跑；等价于 ./install.sh）
make uninstall   # 卸载：只移除 wb 自己装的东西

# MCP 是单独一步，默认只显示差异：
python3 workbench/wb.py mcp import   # 第一次：从两端当前配置生成 workbench/mcp.json（不提交，已在 .gitignore）
python3 workbench/wb.py mcp diff     # 之后：对比清单与实际
python3 workbench/wb.py mcp apply    # 逐条确认后写入
```

`test_consistency.py`（4 个用例，计入总数）会扫描 `skills/*/SKILL.md`、`agents/*.md`、`docs/*.md`、`playbooks/*.md`、`rules/*.md`、`README.md` 里出现的每一条 `flowctl`/`wb` 子命令和 `flowctl` 的长参数，确认它们在当前代码里真实存在，并检查文档间的相对链接都能解析——这也是本次文档修订必须先重读代码再改的原因。

`wb sync` 会顺带创建两端通用的命令入口（`~/.ai-flow/bin/flowctl`、`~/.ai-flow/bin/wb`、`~/.ai-flow/kit` 三个符号链接）和全局 spec 种子库（`~/.ai-flow/specs`，首次从 `specs-starter/` 复制）。之后所有 skill 都通过 `"$HOME/.ai-flow/bin/flowctl"` 调用，与安装在哪一端无关。

## 样例：完整跑一遍产线

`examples/demo.sh` 在一个临时仓库里把"订单批量导出 CSV"这个需求完整走一遍六步产线，产出 `examples/export-orders/` 这份可以直接翻阅的档案（`proposal.md`/`design.md`/`tasks.md`/`review.md`/`retro.md`/`metrics.json`/`trace.jsonl`，以及改动前后的完整仓库快照）：

```bash
make demo                                             # 审查用测试替身，不消耗 token
make demo-codex MODEL=gpt-5.6-luna REASONING=low      # 审查用真实 Codex
```

现有的 `examples/export-orders/` 就是用 `make demo-codex MODEL=gpt-5.6-luna REASONING=low` 生成的：T1 首版漏了"分转元"，验收失败后修正，FPY 因此被拉低；审查交给真实的 Codex（`gpt-5.6-luna`，`low` 推理档位，独立级别 L3），结论 PASS，完整输出在 `examples/export-orders/review/round-1.out.md`。

验证这套机制时还做过一次反向测试：故意在首版里写了一次调用不存在方法的代码（`repo.list_by_customer`，仓储层实际没有这个方法），而且单元测试全部通过——因为没有测试覆盖到这条调用路径。真实的 Codex 审查者回源码逐个核对新引用的符号后，判定为"阻断 · 幻觉"并 REJECT，这正是 [07-models-and-hosts.md](07-models-and-hosts.md) 里"审查者必须自己回源码核对，不能只看测试是否通过"这条纪律要解决的问题。

## 接入一个仓库（每个仓库一次）

对 AI 说 `/flow-onboard`。它会问 `.ai/` 要不要提交进这个仓库，按 `/flow-kb` 生成代码知识库（你确认页面规划、抽查页面），再生成规则文件和 spec 草稿，你逐条确认。详见 [02-pipeline.md](02-pipeline.md#flow-onboard-接入)。

## 日常场景

| 场景 | 你说 | 会发生什么 |
|---|---|---|
| 改个文案、修个单文件小 bug | `/flow 把 xx 的超时改成 5s` | Vibe → 直接改 → 跑测试 → 结束 |
| 不熟悉的代码 | 按 `playbooks/read-code.md` 提问 | 输出分析报告，不改代码 |
| 一个跨文件的需求 | `/flow` + 需求原文，或需求文件路径 | 进入产线 → propose → 停下等你勾选上下文 |
| 继续昨天没做完的需求（可以换个工具） | `/flow` | 按 `flowctl status` 的"下一步"接着做 |
| 线上报警、测试提了 bug | `/flow-incident` + 报错信息 | 定位 → 追到变更 → 根因 → 生成规则 → 需要修复时转入 Vibe 或产线 |
| 换审查引擎/模型 | 什么都不用改，下一轮 `/flow-review` 会重新问一次 | 每轮审查都现场选择，见 [07-models-and-hosts.md](07-models-and-hosts.md) |
| 每周复盘（约 10 分钟） | `flowctl report`、`flowctl specs report`、`wb doctor` | 看趋势、处理 spec、检查漂移 |
| 合入几周后 | `flowctl aftercare <变更>` | 看 AI 代码的存活率和后续改动 |

## 一个需求走完产线，你需要介入的地方

```
/flow-propose  → 勾选上下文、回答未决问题                （约 5 分钟）
/flow-design   → 评审方案                                （约 10 分钟）
/flow-tasks    → 过一眼任务列表                           （约 1 分钟）
/flow-build    → 每个任务过目，决定是否提交                （每个任务约 1-2 分钟）
/flow-review   → 每轮选一次审查引擎/模型；自动进行；3 轮不过才找你
/flow-retro    → 确认回写的 spec                          （约 3 分钟）
```

## 每周复盘（约 10 分钟）

1. `python3 "$HOME/.ai-flow/bin/wb" doctor`——检查两端安装状态、CLI 是否可用、规则区块是否漂移。
2. `"$HOME/.ai-flow/bin/flowctl" report`（在某个已接入的仓库里）——看这段时间各变更的 FPY、AI 代码占比、采纳率、审查轮次/级别趋势。
3. `"$HOME/.ai-flow/bin/flowctl" specs report`——处理标记"进 backlog 修订"或"建议下架"的 spec。
4. 检查有没有 2–4 周前关闭、还没跑 `flowctl aftercare` 的变更（`flowctl list` 查看已关闭的变更）。

## 换宿主继续

流程状态全部在 `.ai/` 目录里，不依赖对话记忆。在 Claude Code 里做到 `/flow-build`，换到 Codex 里说一句 `/flow`，它会执行 `flowctl status` 读取产物文件状态，给出下一步该跑哪个 skill。唯一要注意的是：审查（`/flow-review`）和 scout 召回，每次都会重新问你要用哪个引擎和模型，不会记住"当前用的是哪个宿主"就自动决定。

## 常见问题

**1. Codex CLI 不可用怎么办？**
`flowctl models <role>` 会显示两端 CLI 是否可用（`shutil.which` 检测），不可用的引擎不会列给你选。如果你在选择时仍然指定了不可用的引擎，`flowctl agent` / `flowctl verify start` 会直接报错退出（"`<engine> CLI 不可用，请换一个引擎重选`"），不会静默降级——回到选择步骤换一个可用的引擎即可。`wb doctor` 也会报告两端 CLI 的版本和可用性。

**2. 审查判 `INVALID` 怎么办？**
`INVALID` 只有两种成因，判定依据是开审时存进 git ref `refs/flow/<变更>/review/<轮次>` 的那个终点提交（不是 JSON 里记的值，审查者改不到这个 ref）：
- **审查者改动或删除了已有文件**：`git diff --name-status refs/flow/<变更>/review/<轮次> <重新打的工作区快照>` 会把这些文件列出来，`git status`/`git diff` 检查改了什么，恢复后重开一轮。
- **结论格式不对**：必须有且只有一行**独占一行**的「结论：PASS」或「结论：REJECT」（允许加粗、星号和结尾的句号，但全文只能出现一种结论），格式不对或者同时出现两种结论都判 `INVALID`。检查审查者的完整输出 `review/round-N.out.md`，多数是没有遵守输出格式，重开一轮，必要时精简 prompt 里的验收命令数量。

只新增了未被 git 忽略的文件（不修改、不删除任何已有文件）不算 `INVALID`：这种情况结论仍然有效，但会在轮次表的备注里记一条 warning，提示这些多半是测试缓存，确认后删除或加进 `.gitignore`。`INVALID` 不计入 `review.md` 的正式轮次判定，但仍会占用一个 round 号；已经记录过结论的轮次默认不能重新记录，需要 `flowctl verify record --force` 才会覆盖，且会在轮次表里注明"重新记录"。

**3. 快照忘了打怎么办？**
如果发现漏打 `v1`（比如已经跑了测试、改了代码之后才想起来）：这个任务的 FPY 会失真（`v1` 到 `final` 之间的"被改行"会算成没发生），`flowctl status` 会提示该任务快照不全（`需要 base/v1/final`）。补救办法是把这个任务标记为"快照不全"，在 `retro.md` 里注明，不要事后伪造一个 `v1`。彻底漏打 `base`（开工前忘了打）会导致这个任务无法计算 FPY（`task_metrics` 要求三个快照齐全）；如果任务还没开始改代码，可以立刻补打 `base`。

**4. 如何清理 `refs/flow`？**
快照存成隐藏 ref `refs/flow/<变更>/<任务>/{base,v1,final}` 和 `refs/flow/<变更>/checkpoint`、`refs/flow/<变更>/review/<轮次>`，不会自动清理。变更关闭很久之后想清理磁盘空间：
```bash
git for-each-ref --format='%(refname)' refs/flow/<变更>/ | xargs -n1 git update-ref -d
git gc
```
清理前确认这个变更已经 `flowctl close` 且不再需要回退到某个快照——`flowctl aftercare`/`flowctl locate` 不依赖这些 ref，只依赖 `.ai/changes/*/metrics.json`，所以清理 ref 不影响度量和追溯。

**5. 如何卸载？**
```bash
python3 "$HOME/.ai-flow/bin/wb" uninstall --host all
```
只移除 `wb` 自己安装的东西：两端的 skills/agents 符号链接、`CLAUDE.md`/`AGENTS.md` 里的受管区块（移除前会备份）、`~/.ai-flow/bin` 下的命令入口。`~/.ai-flow` 目录本身（spec 库、备份、上次模型选择）保留不动，需要的话手动删除。

**6. `wb sync` 报"冲突"是什么意思？**
目标位置（比如 `~/.claude/skills/flow`）已经存在同名的**非符号链接**文件或目录，或者是指向别处的符号链接。`wb` 从不覆盖既有内容，只会跳过并报告冲突，需要你手动处理（备份后删除，或确认那确实是别的工具装的东西）。`wb doctor` 会把这类冲突算进"问题"计数。

**7. 两端的 skill 各放了一份拷贝、内容可能已经不一致，怎么办？**
`wb diff` 会列出"两端各放了一份拷贝"的 skill 名单（判定依据：同名 skill 在两端都存在，但都不是指向本仓库的符号链接）。建议把这类 skill 收进本仓库的 `skills/` 目录统一管理，再跑 `wb sync`。

**8. MCP 的敏感值（token、密钥）会不会被写进仓库？**
不会。`workbench/mcp.json` 本身就在 `.gitignore` 里，不会被提交。`wb mcp import` 生成它时，`env` 以及 `headers`/`cwd`/`bearer_token_env_var`/`http_headers` 等"额外字段"（`env`/`command`/`args`/`url`/`transport` 之外的一切，统一放进 `extra`）的值都替换成占位符 `<from-host>`；`wb mcp apply` 执行时才从两端**已经配置好**的那一端读取真实值填回去。如果两端都没有配置过某个 env key，`apply` 会报错并跳过这个 server，需要你手动在清单里填写或先在一端配置好。`wb mcp import` 还会扫描 `args`/`url` 里是否像是包含凭据（`token`/`secret`/`password`/`api-key`/`sk-` 前缀等关键词），发现了会提示"请勿提交或外传"。打印给人看的命令里，env 的值也一律打码成 `***`。

`wb mcp diff` 现在会区分四种动作：`+ add`（缺失，直接可以自动配置）、`~ update`（配置不一致，但默认**不会**自动覆盖——`wb mcp apply` 要加 `--allow-update` 才会执行，且仅当覆盖不会丢失任何 env key 或 extra 字段时才允许）、`! manual`（要么覆盖会丢字段，要么目标端缺失但清单里带了 `extra` 字段——这类交给你手动配置）、`! unsupported`（`sse` 等 codex 不支持的传输类型）、`· unmanaged`（不在清单里，永不触碰）。另外，如果 `wb mcp import` 时发现同名 server 在两端配置不同，只会管理先出现的那一端，另一端保持原样，清单里会记一条 `note` 说明，终端也会打印警告。

**9. `.ai/` 目录该不该提交进仓库？**
默认建议提交（spec 和变更档案都是资产，值得团队共享/长期保留）。公司仓库不方便提交的话，`/flow-onboard` 里选 `flowctl init --local-only`，会自动把 `.ai/` 加进 `.git/info/exclude`，只在本地保留、不会被提交，也不会污染 `.gitignore`（后者通常本身要提交）。

**10. 规则受管区块被我自己手动改了会怎样？**
`wb doctor`/`wb diff` 会把这种情况报告为 `edited`（"受管区块被手动改过"）——区块内容的 sha 和记录的不一致。下一次 `wb sync` 会**覆盖**这个区块，用 `rules/global.md` 的最新内容重新生成（写入前会自动备份旧文件到 `~/.ai-flow/backups/<时间戳>/`）。如果你想保留手动改动，应该把改动内容合并回仓库的 `rules/global.md`，而不是直接改 `~/.claude/CLAUDE.md`。文件读写全程保留原始字节（比如 Windows 上的 CRLF 换行不会被悄悄改成 LF）；`wb uninstall` 移除区块时，连同追加时自动加的那一行空行分隔符一起删掉，其余内容原样保留。

**11. `wb sync` 说规则文件"damaged"是什么意思？**
如果 `CLAUDE.md`/`AGENTS.md` 里能找到 `ai-coding-flow:` 字样但正则匹配不出完整的 `begin ... end` 区块（比如只剩下开始标记、或者中间被手动删了一段），状态会报 `damaged`。这种情况下 `wb sync` **拒绝**动这个文件，避免在结构已经损坏的地方做进一步破坏——需要你先手动检查这个文件，恢复出完整的 `<!-- ai-coding-flow:begin sha=... -->` … `<!-- ai-coding-flow:end -->` 区块（或者干脆删掉这段残留），再重新跑 `wb sync`。

**12. 在 Codex 里跑 `flowctl snapshot`/`verify` 提示权限不足怎么办？**
`snapshot`、`checkpoint`、`verify`、`agent` 都要写 `.git`（存快照/审查终点的隐藏 ref），其中 `verify` 和 `agent` 在选了非同宿主引擎时还会另起一个 CLI 进程（`codex exec` 或 `claude -p`，需要联网）。Codex 的沙箱策略较严时会拦截这些操作——遇到拦截就申请提升权限（写权限、网络权限）后重试，不要因为一次拦截就跳过打快照或审查这一步，否则后续的 FPY、AI 代码占比、审查独立级别都会缺数据。

## 相关文档

- 分通道标准与例子：[01-lanes.md](01-lanes.md)
- 六步产线细节：[02-pipeline.md](02-pipeline.md)
- 模型选择与双端支持：[07-models-and-hosts.md](07-models-and-hosts.md)
- 工作台命令与漂移检测：[08-workbench.md](08-workbench.md)
