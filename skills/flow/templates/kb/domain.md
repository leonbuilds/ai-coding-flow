---
id: {{id}}
type: kb
kind: domain
name: {{name}}
domain: {{domain}}
summary: <!-- kb:todo -->
triggers: {{triggers}}
paths: {{paths}}
sources: {{sources}}
updated: {{date}}
protected: false
---

# {{name}} · 业务域 SDD

<!-- 规则：每条论断带 file:line；没证据写「待确认」；代码块 ≤10 行；只写有代表性的，其余靠 file:line 指路；图用 mermaid；人工补充放 <!-- kb:manual --> … <!-- /kb:manual --> 里；frontmatter 只改 summary。 -->
<!-- 规划目标：{{goal}}；scan 提示：{{hints}} -->
<!-- 涉及模块：{{modules}}。配套：核心流程.md、术语梳理.md、配置清单.md。 -->

## 一、业务概述

<!-- 业务本质、核心价值、参与角色（系统使用者 / 领域对象）。 -->

## 二、业务入口

| 入口 | 类型 | 模块 | 代码位置 |
|---|---|---|---|

## 三、业务流程

### 3.1 流程图

<!-- 必须有：flowchart 或 sequenceDiagram，节点是模块 / 关键方法。 -->

```mermaid
flowchart LR
```

### 3.2 分步说明

| 步骤 | 做什么 | 代码位置 | 关键参数 / 判断 |
|---|---|---|---|

## 四、数据流转

<!-- 读写哪些表 / 缓存 / 外部系统，各在哪一步。 -->

## 五、异常处理

<!-- 按场景：什么情况下怎么处理、在哪处理。 -->

## 六、证据清单

| 编号 | 结论 | 证据位置 | 类型 |
|---|---|---|---|
| E-001 | | <file:line> | 起点 / 接口 / 实现 / 调用 / 数据 / 配置 / 异常 |

## 引用文件

<!-- 正文出现过的文件各列一次：`- path:line — 作用`；lint 校验路径存在、行号不超范围。 -->

- <!-- kb:todo -->
