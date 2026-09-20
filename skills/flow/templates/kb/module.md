---
id: {{id}}
type: kb
kind: module
module: {{module}}
summary: <!-- kb:todo -->
triggers: {{triggers}}
paths: {{paths}}
sources: {{sources}}
updated: {{date}}
protected: false
---

# 模块：{{module}}

<!-- 写作规则：每条论断后面写 file:line 或 file:起-止；scan 事实里没有、你也没亲自打开确认的，一律写「待确认」。代码块不超过 10 行，知识库只放引用不放实现。图用 mermaid。人工补充放在 <!-- kb:manual --> … <!-- /kb:manual --> 之间，重新生成时会按同名标题原位保留。frontmatter 只改 summary，其余由 flowctl kb draft 维护。 -->

<!-- 规划目标：{{goal}}；scan 提示：{{hints}} -->

## 职责

<!-- 两三句：管什么、不管什么、被谁调用。 -->

## 入口

<!-- 以 flowctl kb facts 的入口事实为底，逐条打开确认；scan 漏掉而你找到的补上并注明。 -->

| 类型 | 路径 / 触发 | 处理函数 |
|---|---|---|
| HTTP | GET /x/{id} | `Handler.method` — <file:line> |

## 核心类型

<!-- 实体、聚合、状态枚举、关键 DTO；说明不按字面理解的字段。 -->

- `TypeName` — <作用> — <file:line>

## 对外接口

<!-- 本模块暴露给其他模块调用的 service / 接口 / 事件。 -->

## 依赖（上游 / 下游）

<!-- 上游：谁调用本模块；下游：本模块依赖哪些模块与外部系统（HTTP、RPC、MQ、DB）。 -->

- 上游：<模块页> — <file:line>
- 下游：<模块页 / 外部系统> — <file:line>

## 关键调用链

<!-- 最多 3 条，从入口走到落库或对外调用，每一跳带行号：`入口 → A.f():line → B.g():line`。 -->

1. <入口> → <A.f()> (<file:line>) → <B.g()> (<file:line>)

## 测试

<!-- 测试在哪、覆盖了什么、单独跑本模块测试的命令。 -->

## 引用文件

<!-- 本页每一处 file:line 都要在这里出现一次；格式 `- path:line — 作用`。flowctl kb lint 会校验路径存在、行号不超范围。 -->

- <!-- kb:todo -->
