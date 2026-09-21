---
id: {{id}}
type: kb
kind: interfaces
name: {{name}}
summary: <!-- kb:todo -->
triggers: {{triggers}}
paths: {{paths}}
sources: {{sources}}
updated: {{date}}
protected: false
---

# 对外入口总览

<!-- 规则：每条论断带 file:line；没证据写「待确认」；代码块 ≤10 行；只写有代表性的，其余靠 file:line 指路；图用 mermaid；人工补充放 <!-- kb:manual --> … <!-- /kb:manual --> 里；frontmatter 只改 summary。 -->
<!-- 规划目标：{{goal}}；scan 提示：{{hints}} -->

<!-- 按模块分节；每节给类型与数量 + 代表性入口 ≤8 行；全量在模块页或代码。scan 误报一句话带过。 -->

## 汇总

| 模块 | HTTP | RPC | MQ | 定时 | CLI | 文档 |
|---|---|---|---|---|---|---|

## <module>

| 类型 | 入口 | 处理 | 备注 |
|---|---|---|---|
| HTTP | `GET /x/{id}` | `Handler.method` — <file:line> | <鉴权 / 幂等> |

## 引用文件

<!-- 正文出现过的文件各列一次：`- path:line — 作用`；lint 校验路径存在、行号不超范围。 -->

- <!-- kb:todo -->
