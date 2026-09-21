---
id: {{id}}
type: kb
kind: module
name: {{name}}
module: {{module}}
summary: <!-- kb:todo -->
triggers: {{triggers}}
paths: {{paths}}
sources: {{sources}}
updated: {{date}}
protected: false
---

# {{module}}

<!-- 规则：每条论断带 file:line；没证据写「待确认」；代码块 ≤10 行；只写有代表性的，其余靠 file:line 指路；图用 mermaid；人工补充放 <!-- kb:manual --> … <!-- /kb:manual --> 里；frontmatter 只改 summary。 -->
<!-- 规划目标：{{goal}}；scan 提示：{{hints}} -->

## 模块信息

| 属性 | 值 |
|---|---|
| 目录 | `<dir>/` |
| 运行形态 | <HTTP 服务 / 消费者 / 定时 / 库 / CLI；启动入口 file:line> |
| 存储 | <表 / 缓存 / 外部存储；无则「无」> |
| 测试 | `<测试目录>`，`<单独跑本模块的命令>` |

## 职责

<!-- 两三句：管什么、不管什么、被谁调用。 -->

## 关键入口

<!-- ≤12 行，同前缀归组写「另 N 条」；scan 误报一句话带过。 -->

| 类型 | 入口 | 处理 |
|---|---|---|

## 核心类型

<!-- ≤8 条：实体、状态枚举、关键 DTO；说明不按字面理解的字段。 -->

## 上下游

| 方向 | 对象 | 方式 / 位置 |
|---|---|---|
| 上游 | <模块页> | <file:line> |
| 下游 | <模块页 / 外部系统> | <file:line> |

## 关键流程

<!-- 可选，最多 2 条：`入口 → A.f():line → B.g():line`；复杂的画一张小 flowchart。 -->

## 配置与风险

<!-- 配置只列路径；风险写改动时要小心的地方。 -->

## 引用文件

<!-- 正文出现过的文件各列一次：`- path:line — 作用`；lint 校验路径存在、行号不超范围。 -->

- <!-- kb:todo -->
