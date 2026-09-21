---
id: {{id}}
type: kb
kind: architecture
summary: <!-- kb:todo -->
triggers: {{triggers}}
paths: {{paths}}
sources: {{sources}}
updated: {{date}}
protected: false
---

# 架构

<!-- 规则：每条论断带 file:line；没证据写「待确认」；代码块 ≤10 行；整页 40–80 行，只写有代表性的；人工补充放 <!-- kb:manual --> … <!-- /kb:manual --> 里；frontmatter 只改 summary。 -->
<!-- 规划目标：{{goal}} -->

## 分层与依赖方向

<!-- 有哪些层、允许的方向、谁不能引用谁；每条一个证据。 -->

## 模块依赖图

<!-- 只画 scan edges 里有的边，边上标 import 次数，节点名对应模块页。 -->

```mermaid
graph LR
  a[module-a] -->|3| b[module-b]
```

## 入口总览

<!-- 按模块列类型和数量，每个模块给 1–3 条代表性入口；全量在各模块页。scan 误报的一句话带过，不逐条解释。 -->

| 模块 | 类型与数量 | 代表性入口 |
|---|---|---|
| <module> | HTTP 12 · MQ 2 | `GET /orders/{id}` — <file:line> |

## 数据模型总览

<!-- 核心实体 → 表，≤15 行；DTO 不列。有证据的关系用一句话或 erDiagram。 -->

| 实体 | 表 / 集合 | 模块 | 定义 |
|---|---|---|---|

## 关键横切机制

<!-- 错误处理、日志、事务、鉴权、配置加载各一条，带 file:line。 -->

- 错误处理：<结论> — <file:line>

## 循环依赖与异味

<!-- 来自 scan cycles；没有写「未发现」。 -->

## 引用文件

<!-- 正文出现过的文件各列一次：`- path:line — 作用`；lint 校验路径存在、行号不超范围。 -->

- <!-- kb:todo -->
