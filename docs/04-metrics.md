# 04 · 度量

所有度量由 `flowctl metrics`、`flowctl report`、`flowctl aftercare` 计算，实现见 `skills/flow/scripts/flowctl.py` 的 `task_metrics` / `cmd_metrics` / `adoption` / `cmd_aftercare`。原则：只相信能复算的数字，看趋势不看单个数值（`flowctl report` 汇总所有变更）。

## 快照时机（是后续所有度量的地基）

每个任务在 `/flow-build` 里打三个快照（`flowctl snapshot T<n> <phase>`），存成隐藏 ref `refs/flow/<变更>/<任务>/{base,v1,final}`，不动真实分支和暂存区：

| 阶段 | 时机 |
|---|---|
| `base` | 开工前，工作区里只应有已完成任务的改动 |
| `v1` | AI 写完首版**立即**打，在跑测试、自我修正、人工修改**之前**——晚打会让 FPY 失真 |
| `final` | 验收通过且用户认可**之后** |

## FPY（首次正确率）

对每个任务：

```
v1_add   = diff(base, v1) 的新增行（按 (文件, 去空白行内容) 计数）
final_add = diff(base, final) 的新增行
rewritten = diff(v1, final) 的新增行数（v1 到 final 之间又新增/替换的行，视为"被改行"）
discarded = diff(v1, final) 的删除行数

fpy = max(0, (final行数 - 被改行数) / final行数)      # final行数为 0 时 fpy 记为 None
discard_rate = discarded / v1行数                       # v1行数为 0 时记为 None
```

变更级别的 **按行加权 FPY**：`Σ(任务 fpy × 任务 final行数) / Σ(任务 final行数)`，只统计三个快照齐全的任务。

**盲区**：FPY 衡量的是生成质量，不是代码质量——FPY 高也可能只是人懒得改，必须结合丢弃率一起看。

## AI 代码占比

先汇总"AI 行池"：对每个任务，`diff(base, v1)` 的新增行合并进一个 `Counter[(文件, 行内容)]`（这就是 AI 首版实际写出的所有行，跨任务累加，同一行内容出现多次时取最大计数）。

再算终点（默认 `--to HEAD`，可指定其他提交）相对起点的新增行：`diff(起点, to_sha)`。起点通常是**最早开工任务的 base**；如果是 squash 合入，加 `--squash`，起点改为终点提交自己的父提交（`to_sha^`），即只统计 `diff(to_sha^, to_sha)`——squash 会把多个任务的所有 commit 压成一个，`bases[0]` 那个起点已经不在历史里了，也没必要用它，因为 squash 提交本身就是这次变更的完整增量。不加 `--squash` 而是把 `--to` 指向主干最新提交，会把主干上其他人在这之后提交的改动一并算进终点新增行里，需要避免。

```
AI 代码占比 = Σ min(该行在终点新增次数, 该行在 AI 行池里的次数) / 终点新增总行数
```

即：终点新增的每一行，如果内容能在某个任务的 AI 首版里逐字匹配上，就算作 AI 贡献；用 `min` 是为了避免重复行被过度计数。

**边界情况**：

- `--to` 留空且 `HEAD` 还落在最早的 `base` 之前（任务还没提交）：自动退回用最后一个 `final` 快照作为终点，并在输出里注明"最后一个 final 快照（尚未提交）"。
- `--to HEAD` 且工作区有未提交改动：仍按 `HEAD` 计算，但会提示"AI 代码占比只按已提交的 HEAD 计算"。
- 找不到 `--to` 指定的提交：跳过 AI 代码占比计算。

**盲区**（来自 `flow-retro` skill 与 PLAN 的度量说明）：

- Vibe 通道的改动完全不计入分子分母之外的统计范围（它不建变更、不打快照）。
- 恰好和 AI 首版写得一样的手写行，会被误算成 AI 贡献——这是逐行文本匹配的固有局限，不做语义区分，即"三层签名的个人近似"：文件、行内容、出现次数三者组合作为一行 AI 代码的"签名"，签名相同即视为同一贡献，不追踪具体是谁敲的。

`flowctl metrics` 还会把 `ai_signature`（`[[文件, 行内容, 计入的次数], ...]`）、`files`（涉及的文件集合）、`commits`（本变更区间内的提交列表）写入 `metrics.json`，供 `flowctl aftercare` 和 `flowctl locate` 使用。`--squash` 模式下 `commits` 只有一项：`[to_sha]`（squash 提交本身），因为区间内的原始 commit 已经不在历史里了。

## 采纳率

来自 `.ai/changes/<变更>/trace.jsonl` 里各步骤（`proposal`/`design`/`tasks`/`review`/`retro`）记录的 `generated`/`accepted`/`rejected` 事件（由各 skill 在产出一版、用户采纳、或被驳回时调用 `flowctl trace <stage> <event>`）：

```
采纳率（某步骤） = accepted 次数 / generated 次数
```

`flowctl metrics` 按步骤输出"生成 / 采纳 / 驳回"三个计数；`flowctl report` 汇总为整个变更的 `采纳/生成` 比值。

**盲区**：依赖 skill 如实记录——如果某一步该调用 `flowctl trace` 时没调用，这个比例就会偏高或偏低而不自知。

## AI 代码占比的"个人近似"含义

因为不引入编辑器插件或击键追踪，这套系统只能通过"文本内容能否匹配 AI 首版输出"来近似判断一行代码是不是 AI 写的。三个层次的近似依次放宽：

1. 精确匹配整行文本（当前实现，见上）；
2. 允许行内小改动仍算 AI 贡献（未实现）；
3. 按语义等价判断（未实现）。

当前只做到第一层，这也是为什么"和 AI 写的内容恰好相同的手写行会被算成 AI 的"这条盲区无法消除。

## 审查独立级别

由 `independence(host, engine, model, host_model)` 判定（`flowctl verify start` 时计算并记入 `review/round-N.json` 与 `review.md` 的轮次表）：

| 级别 | 判定条件 | 含义 |
|---|---|---|
| L1 | `engine == host` 且 `model == host_model`（或未提供 host_model） | 同一模型，新开一个上下文 |
| L2 | `engine == host` 且 `model != host_model` | 同厂商，不同模型 |
| L3 | `engine != host` | 跨厂商 |

`flowctl report` 汇总每个变更的"审查轮次/级别"（例如 `2 L2/L3` 表示这个变更审查了 2 轮，用过 L2 和 L3）。

**盲区**：级别只反映用户当轮**声明**的引擎/模型选择，不做任何自动检测；样本少时，"哪个级别驳回率更高、之后是否在 incident 中暴露漏检"这类统计没有参考价值。

## Aftercare（合入后追踪）

命令：`flowctl aftercare [变更] [--at <提交>]`（默认 `HEAD`），依赖变更已经跑过 `flowctl metrics` 且提交之后重新跑过一次（这样 `metrics.json` 才有 `ai_signature`）。

**AI 代码存活率**：

```
对 metrics.json 的 ai_signature 里每一行 (文件, 行内容, 计入次数 n)：
  取 --at 指定提交里该文件的当前行内容集合（Counter）
  alive += min(n, 该行内容在当前文件里出现的次数)
  total += n

存活率 = alive / total
```

即：当初被判定为 AI 贡献的那些行，有多少在若干天后仍然一字不差地存在于文件里。

**后续改动提交数**：从变更关闭时刻（`trace.jsonl` 里最后一条 `closed` 事件的时间，没有就用 `metrics.json` 的计算时间）起，`git log --since=<关闭时间> -- <本变更涉及的文件>`，排除本变更自己的提交（`metrics.json.commits`），列出之后还改过这批文件的提交。

**盲区**：

- 后续改动不一定是返工——可能是同一批文件上的全新需求，存活率下降也未必是坏事。
- 存活率按"行内容是否还在文件里"判定，代码被移动到另一个文件、或被格式化改变了空白但语义不变，都会被算作"未存活"。

## 度量怎么读（汇总原则）

| 指标 | 命令 | 结算时机 |
|---|---|---|
| FPY / 丢弃率 / 采纳率 / AI 代码占比 | `flowctl metrics` | 提交那一刻 |
| 全部变更的趋势 | `flowctl report` | 随时，汇总所有已算过 metrics 的变更 |
| spec 命中率 / 误导率 | `flowctl specs report` | 随时，基于累计的打分记录 |
| 存活率 / 后续改动数 | `flowctl aftercare` | 合入 2–4 周后 |

单个变更只是一条样本，规律要靠 `flowctl report` 看趋势得出。
