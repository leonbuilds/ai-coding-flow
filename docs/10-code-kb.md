# 10 · 代码知识库（`.ai/kb/`）

接入仓库时，除了规则文件和 spec，还要有一份「AI 能从代码读出来、但每次重读都太贵」的仓库地图。这一章说明它长什么样、怎么生成、怎么跟着代码刷新、谁来消费。实现在 `skills/flow/scripts/kb.py`，命令入口是 `flowctl kb <action>`。

## 定位

三份资产各管一段，不重复：

| 资产 | 回答的问题 | 谁维护 |
|---|---|---|
| `CLAUDE.md` / `AGENTS.md` | 代码里读不出来的约定：为什么这么分层、什么不能碰、术语的真实含义 | 人 |
| `.ai/specs/` | 应该怎样：可检查的规则，按 `when` / `triggers` / `paths` 召回 | `/flow-onboard`、`/flow-retro`、`/flow-incident` |
| `.ai/kb/` | 现在是怎样：技术栈版本、模块边界、入口、数据模型、依赖方向、调用链，每条带 `file:line` | `/flow-kb`（脚本抽事实，AI 写页面） |

借鉴了两类做法：Qoder Repo Wiki 的「模块页 + 引用文件 + 漂移检测 + 人工修改保护」，以及 SDD 文档梳理里「证据必须带行号读出、找不到就标待确认」的纪律。区别是这里的事实层由脚本产出，可重复、可测试；AI 只负责叙述。

## 目录

```
.ai/kb/
├── index.md            路由索引：每页一句话 + triggers + paths（kb freeze 生成）
├── overview.md         技术栈与版本、构建/测试/lint 命令、目录地图、配置文件路径
├── architecture.md     分层与依赖方向、模块依赖图（mermaid）、循环依赖、横切机制
├── modules/<模块>.md    职责 / 入口 / 核心类型 / 对外接口 / 依赖 / 关键调用链 / 测试
├── interfaces.md       HTTP / RPC / MQ / 定时 / 事件回调 / CLI 入口清单
├── data-model.md       实体与表 / DTO / 实体关系
├── glossary.md         术语表
├── scan.json           kb scan 的事实
├── plan.json           页面规划（可编辑）
└── _manifest.json      冻结基线：commit、每页引用的文件、内容 hash、protected
```

`.ai/kb/` 跟随 `.ai/` 的提交策略：`flowctl init --local-only` 时它也只留在本地。

## 页面格式

frontmatter 用和 spec 一样的 YAML 子集（单行）：

```yaml
---
id: module-order        # module 页为 module-<文件名>，其他页与文件名一致
type: kb
kind: module            # overview | architecture | module | interfaces | data-model | glossary
module: order           # 仅 module 页
summary: 订单的创建、查询与状态流转   # 一句话，index.md 从这里生成
triggers: [order, 订单, t_order]      # 召回关键词；draft 会从入口路径、实体名预填
paths: ["src/main/java/com/acme/shop/order/**"]
sources: ["src/main/java/.../OrderController.java", ...]   # 本页依据的文件
updated: 2026-09-20
protected: false        # true 之后 kb draft --force 不再覆盖本页
---
```

正文规则：每条论断后面写 `file:line` 或 `file:起-止`；scan 没有、自己也没打开确认的写「待确认」；代码块不超过 10 行；图用 mermaid；每页末尾必有 `## 引用文件`。人工补充放在 `<!-- kb:manual --> … <!-- /kb:manual -->` 之间，重新生成时按同名 `## ` 标题原位保留。

模板在 `skills/flow/templates/kb/`，由 `kb draft` 应用，AI 不直接碰模板。

## 命令

| 命令 | 做什么 | 退出码 |
|---|---|---|
| `kb scan [--json]` | 枚举已跟踪文件（跳过 vendor、target、node_modules 等、>1MB、二进制，按 `plan.json` 的 `scope` 过滤），解析 manifest，划分模块，抽入口 / 模型 / 外部调用 / 配置 / 测试 / import 边，写 `scan.json` | — |
| `kb plan [--json]` | 由 scan 生成页面清单写 `plan.json`；保留用户改过的 `scope`、`notes`、`goal`、`hints`、`paths`、`status`；新模块标 `proposed`，消失的标 `orphan`，`removed` 不复活 | — |
| `kb draft [--page id \| --all] [--force]` | 套模板生成草稿；`--force` 重做已有页时跳过 `protected: true`，保留 `summary`、`triggers`、`paths` 和 `kb:manual` 块 | — |
| `kb facts --page id [--json]` | 该页对应的事实清单（`[证据-接口] GET /orders/{id} → path:41 (getOrder)`），给写作者用 | — |
| `kb lint` | frontmatter 完整、无残留 `kb:todo`、`kb:manual` 成对、有「引用文件」、`sources` 与正文所有 `path:line` 的文件存在且行号不超范围；WARN 代码块 >12 行、正文 >12000 字符、plan 里的页没文件 | 有错 1 |
| `kb freeze` | 先 lint；生成 `index.md`；写 `_manifest.json`（HEAD、每页 sources ∪ 正文引用、sha1、protected） | 有错 1 |
| `kb status [--json]` | 基线到 HEAD 加工作区：引用文件被修改、模块目录有新增/删除 → 过期；内容 hash 变了 → 手改（不算过期）；基线提交不存在 → 全部过期 | 0 新鲜 / 10 有过期 / 11 无基线 |
| `kb recall [--text] [--file] [--paths] [--top] [--json]` | 召回页：`score = 触发词命中 + 2×paths glob 命中 + 3×(--paths 里有文件 ∈ sources) + 模块名命中`；默认 5 页；与 spec 的 `recall` 分开，不影响 spec 打分 | — |

`flowctl status` 会在「下一步」前打印一行知识库状态；`flowctl agent scout` 在有 `index.md` 时把 `kb recall` 结果拼进 scout 的输入。

## scan 识别什么

只对 Java、Go、Python 抽代码事实；其他语言只有文件统计和 manifest（`package.json`、`Cargo.toml`）。所有匹配都是标准库正则，行号是注解或声明所在行。

| 类别 | Java | Go | Python |
|---|---|---|---|
| manifest | `pom.xml`（modules、parent、properties 解析 `${}`、dependencies、plugins）、`build.gradle(.kts)`、`settings.gradle` | `go.mod`（module、go、require） | `pyproject.toml`（project / poetry 依赖、tool 段）、`requirements*.txt`、`setup.py/cfg` |
| 命令（均 `guessed: true`） | `./mvnw` 或 `mvn`；有 checkstyle / spotless 才给 lint | `go build/test ./...`；有 `.golangci.yml` 用 golangci-lint 否则 `go vet` | pytest 迹象则 pytest 否则 unittest；ruff > flake8 > black；poetry / build |
| 模块 | 多模块按 Maven/Gradle 模块目录；单模块按包的公共前缀下一段聚类，分层包名（controller/service/…）占多数时跳过分层词 | `cmd/<x>`、`internal/<x>`、`pkg/<x>`、其他顶层目录 | 根或 `src/` 下的顶层包；包少时按子包拆到二级 |
| 入口 | `@RestController` + `@*Mapping`（含类级前缀）、JAX-RS、`@Scheduled`、`@KafkaListener/@RabbitListener/@JmsListener`、`@EventListener`、`@DubboService`、`main` | `func main`、`http.HandleFunc`、gin/echo/chi/fiber 路由、`Register*Server`、cobra、cron、Subscribe | FastAPI/Flask 装饰器、Django `urls.py`、click、argparse、`__main__`、Celery task、APScheduler |
| 数据模型 | `@Entity`/`@Table`、`@Document`、MyBatis mapper XML | 带 `gorm/db/bson` tag 的 struct、`TableName()` | SQLAlchemy / Django / pydantic / SQLModel 基类、`__tablename__`、dataclass |
| 外部调用 | RestTemplate、WebClient、OkHttp、FeignClient、DubboReference | `http.NewRequest/Get/Post`、`grpc.Dial`、resty | requests / httpx / aiohttp、grpc channel、boto3 |
| 依赖边 | `import` 按包前缀映射到模块 | `import "<module>/…"` 映射到目录 | `from/import` 按顶层包（含相对导入） |

分级：源码文件 ≤150 为 `flat`（最多 6 个模块页，小模块并入 `common`），≤1500 为 `modular`（最多 15），更多为 `large`（只按顶层目录，提示人工在 `plan.json` 里拆）。配置文件只记路径，不读值。

## 生命周期

```
/flow-onboard ──► /flow-kb 生成模式：scan → plan → [门禁] → draft → 写页 → lint → freeze → [门禁]
                                                          ▲
代码改动 ──► flowctl status / /flow-retro 第 6 步 ──► kb status 退出 10 ──► /flow-kb 刷新模式：
                                                          只对过期页 draft --force → 重写 → lint → freeze
```

- **生成**：模块页先写（其他页要引用它们），`overview` 里的命令必须实际跑过才标「已验证」。Claude Code 里每页派一个 Explore 子 Agent 并行写，只带回「已写完」和缺证据的点。
- **消费**：`flow-scout` 第 0 步读 `index.md` 和 `kb recall` 结果，用模块页的入口、调用链做起点，再打开源码核对；`flow-design` 用 `architecture.md` 的依赖图和模块页的依赖节圈影响面；`flow-verifier` 可以用它定位，但证据只能是源码。
- **刷新**：`/flow-retro` 末尾跑 `kb status`，过期就提醒 `/flow-kb`；同时把 proposal 里勾选过的知识库页标注有用 / 没用 / 误导。刷新只重写过期页，`protected: true` 的页和 `kb:manual` 块不动。

## 盲区

- 正则不是编译器：反射注册的路由、代码生成的入口、动态拼出来的表名都抽不到，页面里要靠 AI 自己找并标出处。
- 循环依赖只看模块级 import，看不到运行时调用。
- `kb status` 只看引用文件和模块目录：改了一个没被任何页引用的文件，不会触发过期；这正是要求「引用文件」一节列全的原因。
