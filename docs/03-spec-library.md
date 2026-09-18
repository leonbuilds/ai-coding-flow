# 03 · Spec 库

Spec 是流程里被反复召回、检查的最小知识单元：一条领域规则、一条技术约束、一条测试写法、一条审查检查项。所有实现见 `skills/flow/scripts/flowctl.py`。

## 格式

每条 spec 是一个带 YAML frontmatter 的 Markdown 文件，模板见 `skills/flow/templates/spec.md`：

```yaml
---
id: dev-example-rule              # 全库唯一，建议 <类型>-<主题>
type: dev                         # tech | domain | dev | review
triggers: [关键词A, keywordB]      # 需求文本命中任意一个就召回；英文按整词匹配
paths: ["src/**/order/**"]         # 改动路径命中也召回；可以不写
when: 修改订单模块的金额计算时      # 必填：什么条件下注入，答不上来就别加这条
status: active                    # draft | active | retired
updated: 2026-01-01
# always: true                    # 仅 review 类允许常驻；其他类型会被 lint 警告
---

# 一句话结论（可检查的断言，不是"注意 xx"）

## 为什么（踩过的坑、事故或 CR 意见的出处）

## 正例 / 反例
```

frontmatter 解析器（`parse_frontmatter`）是 YAML 的一个小子集：`key: value`、`key: [a, b]`（逗号分隔，支持引号）、以及 `- 列表项` 续行；不支持多行字符串（`>`、`|` 等块标量）。

`type` 取值固定为 `tech`（技术栈/架构约束）、`domain`（领域知识）、`dev`（研发约定）、`review`（审查检查项）四种；未显式写 `type` 时，取所在目录名作为默认值，目录名不在这四种之内则默认为 `dev`。

## 两层：仓库级 / 全局

| 层级 | 路径 | scope 标记 | 谁写 |
|---|---|---|---|
| 仓库级 | `.ai/specs/{tech,domain,dev,review}/*.md` | `repo` | 具体项目的规则，由 `/flow-onboard`、`/flow-retro`、`/flow-incident` 写 |
| 全局 | `$AI_FLOW_HOME/specs/`（`AI_FLOW_HOME` 未设置时默认 `~/.ai-flow`；也可由 `.ai/config.json` 的 `global_spec_dir` 显式覆盖） | `global` | 跨仓库通用的规则；首次 `wb sync` 用 `specs-starter/` 初始化种子库 |

加载顺序（`load_specs`）：先读仓库级，记下它们的 `id`；再读全局级，**全局里与仓库级同 `id` 的条目被丢弃**（仓库级覆盖全局）。文件名以 `_` 开头或叫 `readme.md`（大小写不敏感）的会被跳过（比如 `_template.md`、`specs-starter/*/README.md`）。

## 准入 lint 规则

`flowctl specs lint`（`cmd_specs_lint`）逐条检查，报错会让命令以非零退出码结束：

| 级别 | 规则 |
|---|---|
| ERROR | 同一层级内 `id` 重复（仓库级覆盖全局级是允许的，不算重复） |
| ERROR | `when` 使用了不支持的多行写法（`>`、`\|`、`>-`、`\|-`），必须写成一行 |
| ERROR | 缺少 `when`——说不清何时注入的 spec 就是噪声 |
| ERROR | `triggers` / `paths` / `always` 三者都没有，永远召回不到 |
| WARN | `always: true` 但 `type` 不是 `review`：会稀释注意力 |
| WARN | `type` 不在 `tech/domain/dev/review` 之内 |
| WARN | 某个触发词长度 `< 2` 字符：容易误召回 |
| WARN | 正文超过 6000 字符：建议拆小 |

## 召回算法

命令：`flowctl recall --text <需求文本> [--file <文件>]... [--paths <路径>...] [--top N] [--json] [--record]`

1. **排除项**：`status: retired` 的 spec 和 `always: true` 的 spec 不参与召回——常驻的 review 检查项由 verifier 每次必查，不需要通过召回机制注入。
2. **触发词整词匹配（`trigger_hit`）**：触发词若全部由 `[A-Za-z0-9_.\-/ ]` 构成（即纯 ASCII），按**整词**匹配——用正则 `(?<![A-Za-z0-9_])<词>(?![A-Za-z0-9_])`（忽略大小写），避免"test"命中"testing"这类误召回；触发词若含非 ASCII 字符（例如中文），退化为**大小写不敏感的子串匹配**，因为中文没有天然的词边界。
3. **glob `**` 语义（`glob_match`）**：`paths` 里的 pattern 与改动路径做 glob 匹配——`**/` 匹配零到多层目录，`**` 匹配任意深度（含跨目录），`*` 和 `?` 不跨越 `/`。
4. **打分**：`score = 命中的 triggers 数 + 2 × 命中的 paths 数`（路径命中权重更高）。
5. **历史命中率加权**：若某条 spec 历史使用次数 `uses >= revise_min_uses`（默认 3），则 `score *= (0.5 + hit_rate)`——命中率高的历史条目排名更靠前，命中率低的被压低但不会归零。
6. 按 `score` 降序取前 `--top`（默认取 `.ai/config.json` 的 `recall_top`，默认 8）条。
7. `--record` 会把本次召回结果追加写入当前变更的 `recalled.json`（去重，按 `id`），并记一条 trace。

历史统计（`spec_stats`）：按 `.ai/specs/_scores.jsonl` 里每条 `flowctl score` 记录汇总每个 spec 的 `uses`（打分次数）、`hit_rate`（`hit` 占比）、`mislead_rate`（`mislead` 占比）、`idle`（从最近一次往前数，连续未命中 `hit` 的次数）。

## 打分与 report 阈值

打分入口：`flowctl score <spec-id> hit|unused|mislead --note "..."`，通常在 `/flow-review` 放行后对 `recalled.json` 里每条 spec 执行一次，追加进 `.ai/specs/_scores.jsonl`。

`flowctl specs report`（`cmd_specs_report`）按下面的规则给每条 spec 打处理建议标签（阈值均来自 `.ai/config.json`，默认值如下）：

| 条件（按代码里判定的先后顺序） | 标签 | 阈值 |
|---|---|---|
| `status == retired` | 已下架 | — |
| `uses >= revise_min_uses` 且 `mislead_rate >= revise_mislead_rate` | 进 backlog 修订（误导） | `revise_min_uses=3`、`revise_mislead_rate=0.2` |
| `always: true` | 不打标签（常驻检查项不按命中率考核） | — |
| `idle >= retire_idle_recalls` | 建议下架（长期不命中） | `retire_idle_recalls=10` |
| `uses >= revise_min_uses` 且 `hit_rate < revise_hit_rate` | 进 backlog 修订（命中低） | `revise_hit_rate=0.3` |

输出按"有标签的排前面，同类再按使用次数降序"排序，列出每条 spec 的类型、使用次数、命中率、误导率、连续未命中次数和处理建议。`/flow-retro` 每次都会跑这个命令，把标了"修订"或"下架"的条目交给用户决定；下架是把 `status` 改成 `retired`，不删文件。
