---
name: flow-kb
description: 生成或刷新仓库代码知识库 .ai/kb/：flowctl kb scan 抽取 Java/Go/Python 的构建、模块、入口、数据模型、依赖边等事实，AI 按模板逐页写作并标 file:line，kb lint 校验引用，kb freeze 记录基线，kb status 检测代码漂移后只重写过期页。首次接入时由 /flow-onboard 调用；之后用户说「更新知识库」「知识库过期了」「/flow-kb」，或 /flow-retro 提示过期时使用。
---

# flow-kb · 代码知识库

知识库是「AI 能从代码读出来、但每次重读太贵」的东西：技术栈、模块边界、入口、数据模型、依赖方向、调用链。它和 CLAUDE.md 分工明确：CLAUDE.md 只写代码里读不出来的约定，知识库写代码里读得出来的事实。每条事实都带 `file:line`，找不到出处就写「待确认」。

命令入口：`"$HOME/.ai-flow/bin/flowctl"`（下文简写为 flowctl）。没有 `.ai/` 时先按 `/flow-onboard` 的第 1 步初始化。

## 判断模式

- 没有 `.ai/kb/_manifest.json`：**生成模式**。
- 有：先执行 `flowctl kb scan`，再执行 `flowctl kb status`。退出码 0 说明知识库和代码一致，告诉用户后结束；退出码 10 进入**刷新模式**。

## 生成模式

1. **扫描**：`flowctl kb scan`。读它打印的摘要：语言、构建系统、模块表、入口/模型数量、循环依赖警告。事实落在 `.ai/kb/scan.json`。
2. **规划**：`flowctl kb plan`。它按扫描结果给出三层页面清单，写入 `.ai/kb/plan.json`：
   - `ai-quick-reference.md`：AI 速查；
   - `architecture/` 十页：overview、business-flows、module-dependencies、interfaces、data-model、tech-stack、config-and-dependencies、patterns、domain-concepts、dev-guide；
   - `modules/<模块>.md`：每个模块一页；
   - `domains/<域>/`：按入口分组（HTTP 路由前缀、CLI 命令组、MQ topic）推荐 3–5 个业务域候选，每个域四页：`README.md`（SDD）、`核心流程.md`、`术语梳理.md`、`配置清单.md`。
   
   另有 4 个 README（根、architecture、modules、domains）由 `kb freeze` 自动生成，不用写。页多但每页短：模块页和架构页 40–100 行，域 SDD 最多 200 行。
3. **人工门禁：停下来**，把规划表给用户看。用户可以直接改 `plan.json`：
   - `limits`：`max_module_pages`（默认 20）、`max_domains`（默认 4）、`min_domain_entries`（默认 2）、`max_sources`（默认 30）；
   - `scope.exclude` / `scope.include`：缩小扫描范围（gitignore 风格 glob）；
   - **业务域是重点**：scan 只能按入口分组猜，域名（`name`）、范围（`modules`）要用户定。改 `name` 即改域名；不要的域把 README 那条标 `removed`，四页一起删；手加一个域：`{"id": "domain-<slug>", "kind": "domain", "name": "<名字>", "modules": ["<模块>"], "status": "planned"}`；
   - 某页 `status` 改成 `removed`：不生成这页；
   - 改 `goal`、加 `hints`：告诉写作者重点。
   
   规划表下面若列出「不在规划内的已有页」（更早版本的页），把有用内容并进对应页后删除。
   
   用户改过就重跑 `flowctl kb scan` 和 `flowctl kb plan`（用户改动会被保留），直到用户确认。`proposed`、`orphan` 状态的页必须由用户决定留还是删。
4. **草稿**：`flowctl kb draft --all`。每页套好模板和 frontmatter，正文留待填写。
5. **写作**：按下面的顺序写，后面的页要引用前面的：
   1. 每个 `modules/*.md`；
   2. `architecture/` 十页：tech-stack、config-and-dependencies、interfaces、data-model、module-dependencies 先（事实多），再 business-flows、overview、patterns、domain-concepts、dev-guide；
   3. 每个 `domains/<域>/`：README（SDD）→ 核心流程 → 术语梳理 → 配置清单；
   4. `ai-quick-reference.md`。

   **图是硬要求**：`architecture/overview.md`（系统架构图）、`business-flows.md`（每条链路一张）、`module-dependencies.md`（依赖图）、`data-model.md`（ER 图）、每个域的 `README.md` 和 `核心流程.md`（业务流程图）必须有 mermaid 图，`kb lint` 没图就报错。依赖图和 ER 图的骨架在 `flowctl kb facts --page <id>` 输出末尾，粘进去补分层和关系即可；流程图要自己沿入口读代码画，节点写模块或关键方法。

   写每一页之前先执行 `flowctl kb facts --page <id>`，拿到这页对应的事实清单。写作规则（也写在模板注释里）：
   - **短**：模块页与架构页 40–100 行，域 SDD 最多 200 行；入口表最多 12 行，同前缀的归成一行写「另 N 条」；只写有代表性的，其余靠 `file:line` 指路。事实清单里 scan 明显误报的条目一句话带过，不逐条解释；
   - 每条论断后面写 `file:line` 或 `file:起-止`。事实清单里的位置也要亲自打开确认，不能照抄；
   - 事实清单没有、自己也没在代码里找到的，写「待确认」，不要编；
   - 代码块不超过 10 行，知识库只放引用不放实现；图用 mermaid；
   - `overview.md` 的构建、测试、lint 命令必须实际跑过一次才能标「已验证」，跑不了就标「猜测，未跑」并写原因；
   - frontmatter 只改 `summary`（一句话，四个 README 靠它生成）；不要碰 `<!-- kb:manual -->` 块；
   - 每页末尾的「引用文件」一节把正文出现过的每个文件列一次。

   在 Claude Code 里，用 Agent 工具给每一页派一个 Explore 子 Agent 并行写作，prompt 里只放三样：页路径、`flowctl kb facts --page <id>` 的输出、上面的写作规则。子 Agent 只带回「已写完」和自己没找到证据的点，不要把页面内容贴回主会话。在 Codex 里按顺序自己写。
6. **校验**：`flowctl kb lint`，必须零错误。它检查 frontmatter、残留的 `kb:todo`、引用的路径是否存在、行号是否超出文件长度。有错就改，改完重跑。
7. **冻结**：`flowctl kb freeze`。它生成四个 README（根导航、architecture、modules、domains 各一个），并把当前提交和每页引用的文件写进 `_manifest.json`。
8. **人工门禁：停下来**，请用户看 `README.md`、`ai-quick-reference.md` 和随便抽两页（建议一页模块、一页域 SDD 看图）。用户手动改过并希望以后不被覆盖的页，把 frontmatter 的 `protected` 改成 `true`；只想保留某一段，放进 `<!-- kb:manual --> … <!-- /kb:manual -->`。改完再执行一次 `flowctl kb freeze`。
9. 告诉用户：之后 `flowctl status` 会显示知识库是否过期；scout、design 会先查 `.ai/kb/ai-quick-reference.md` 再看代码。

## 刷新模式

1. `flowctl kb status` 已经列出过期页和原因（引用的文件被改、模块目录里有新增或删除）。
2. 对每个过期页执行 `flowctl kb draft --force --page <id>`。`protected: true` 的页会被跳过，其他页的 `kb:manual` 块和 `summary` 会保留，正文重置为模板。
3. 只重写这些页，规则同生成模式第 5 步。架构页过期时，先看 `flowctl kb plan` 是否新增了 `proposed` 模块页或业务域；有就先问用户要不要一起生成。
4. `flowctl kb lint` 零错误，`flowctl kb freeze`。
5. 请用户过目改动的页。

## 注意

- 知识库不替代读代码。scout 和 verifier 引用知识库里的位置时，仍然要打开源码核对。
- `scan.json` 只识别 Java / Go / Python 的入口、模型和依赖；其他语言只有文件统计和 manifest，页面内容要靠自己探索，同样每条标 `file:line`。
- 不要把配置值（密码、密钥、连接串）写进知识库，只列配置文件路径。
