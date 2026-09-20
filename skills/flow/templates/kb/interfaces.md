---
id: {{id}}
type: kb
kind: interfaces
summary: <!-- kb:todo -->
triggers: {{triggers}}
paths: {{paths}}
sources: {{sources}}
updated: {{date}}
protected: false
---

# 对外入口清单

<!-- 写作规则：每条论断后面写 file:line 或 file:起-止；scan 事实里没有、你也没亲自打开确认的，一律写「待确认」。代码块不超过 10 行，知识库只放引用不放实现。图用 mermaid。人工补充放在 <!-- kb:manual --> … <!-- /kb:manual --> 之间，重新生成时会按同名标题原位保留。frontmatter 只改 summary，其余由 flowctl kb draft 维护。 -->

<!-- 规划目标：{{goal}}。以 flowctl kb facts --page interfaces 为底，每条打开确认。没有的类型整节写「无」。 -->

## HTTP

| 入口 | 模块 | 处理函数 | 备注 |
|---|---|---|---|
| GET /x | [module-x](modules/x.md) | `Handler.method` — <file:line> | <鉴权 / 幂等 / 分页等> |

## RPC

## 消息队列

| Topic / 队列 | 模块 | 消费者 | 备注 |
|---|---|---|---|

## 定时任务

| 触发 | 模块 | 处理函数 | 备注 |
|---|---|---|---|

## 事件 / 回调

## CLI / main

## 引用文件

<!-- 本页每一处 file:line 都要在这里出现一次；格式 `- path:line — 作用`。flowctl kb lint 会校验路径存在、行号不超范围。 -->

- <!-- kb:todo -->
