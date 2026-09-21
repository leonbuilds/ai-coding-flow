---
id: {{id}}
type: kb
kind: module-dependencies
name: {{name}}
summary: <!-- kb:todo -->
triggers: {{triggers}}
paths: {{paths}}
sources: {{sources}}
updated: {{date}}
protected: false
---

# 模块依赖关系

<!-- 规则：每条论断带 file:line；没证据写「待确认」；代码块 ≤10 行；只写有代表性的，其余靠 file:line 指路；图用 mermaid；人工补充放 <!-- kb:manual --> … <!-- /kb:manual --> 里；frontmatter 只改 summary。 -->
<!-- 规划目标：{{goal}}；scan 提示：{{hints}} -->

## 依赖图

<!-- kb facts --page module-dependencies 已给出由 import 边生成的图骨架，粘进来并给节点标分层、给关键边标协议。 -->

```mermaid
graph LR
```

## 依赖边

| 起点 | 目标 | 方式 | 说明 |
|---|---|---|---|
| <module> | <module> | import ×N / HTTP / MQ | <file:line> |

## 循环依赖与异味

<!-- scan 的 cycles；每个环说明是否可接受、由哪个包打破。没有写「未发现」。 -->

## 排查顺序

<!-- 报错落在某模块时，沿依赖往上/下看什么。 -->

## 引用文件

<!-- 正文出现过的文件各列一次：`- path:line — 作用`；lint 校验路径存在、行号不超范围。 -->

- <!-- kb:todo -->
