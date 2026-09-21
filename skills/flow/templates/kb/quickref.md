---
id: {{id}}
type: kb
kind: quickref
name: {{name}}
summary: <!-- kb:todo -->
triggers: {{triggers}}
paths: {{paths}}
sources: {{sources}}
updated: {{date}}
protected: false
---

# {{name}}

> **模型首选入口**。总导航：[README.md](README.md)。
<!-- 规则：每条论断带 file:line；没证据写「待确认」；代码块 ≤10 行；只写有代表性的，其余靠 file:line 指路；图用 mermaid；人工补充放 <!-- kb:manual --> … <!-- /kb:manual --> 里；frontmatter 只改 summary。 -->
<!-- 规划目标：{{goal}}；scan 提示：{{hints}} -->

## 1. 三句话理解系统

- <系统是什么、主链路是什么、最容易搞混的一件事；每句带页链接或 file:line>

## 2. 按问题找文档

| 问题 | 文档 |
|---|---|
| 全局架构 / 链路 | [architecture/overview.md](architecture/overview.md)、[business-flows.md](architecture/business-flows.md) |
| 谁依赖谁、怎么排查 | [architecture/module-dependencies.md](architecture/module-dependencies.md) |
| 对外入口 | [architecture/interfaces.md](architecture/interfaces.md) |
| 表与实体 | [architecture/data-model.md](architecture/data-model.md) |
| 构建 / 测试 / 加接口 | [architecture/dev-guide.md](architecture/dev-guide.md) |
| 某个模块 | [modules/README.md](modules/README.md) |
| 某个业务流程 | [domains/README.md](domains/README.md) |

## 3. 模块速查

| 模块 | 文档 | 运行形态 | 一句话 |
|---|---|---|---|

## 4. 关键入口 / 消息 / 任务

<!-- 最重要的 5–10 条：路径或 topic、模块、file:line。 -->

## 5. AI 检索顺序

1. 本卡 → 拆关键词
2. `flowctl kb recall --text <需求>` 命中的页
3. 对应 domains/<域>/README.md
4. 对应 modules/ 页 → 打开 file:line 核对

## 6. 边界提醒

<!-- 容易踩的坑、不要混的概念，每条一句。 -->

## 引用文件

<!-- 正文出现过的文件各列一次：`- path:line — 作用`；lint 校验路径存在、行号不超范围。 -->

- <!-- kb:todo -->
