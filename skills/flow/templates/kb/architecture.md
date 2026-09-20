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

<!-- 写作规则：每条论断后面写 file:line 或 file:起-止；scan 事实里没有、你也没亲自打开确认的，一律写「待确认」。代码块不超过 10 行，知识库只放引用不放实现。图用 mermaid。人工补充放在 <!-- kb:manual --> … <!-- /kb:manual --> 之间，重新生成时会按同名标题原位保留。frontmatter 只改 summary，其余由 flowctl kb draft 维护。 -->

<!-- 规划目标：{{goal}} -->

## 分层与依赖方向

<!-- 有哪些层、允许的依赖方向、谁不能引用谁。每条给证据（典型文件的 import 位置）。 -->

## 模块依赖图

<!-- 只画 scan edges 里存在的边，边上标 import 次数；节点名与模块页 id 对应。 -->

```mermaid
graph LR
  a[module-a] -->|3| b[module-b]
```

## 循环依赖与异味

<!-- 来自 scan 的 cycles；没有就写「未发现」。 -->

## 关键横切机制

<!-- 错误处理、日志、事务、鉴权、配置加载各是怎么做的，每条一个 file:line。 -->

- 错误处理：<结论> — <file:line>
- 日志：<结论> — <file:line>

## 引用文件

<!-- 本页每一处 file:line 都要在这里出现一次；格式 `- path:line — 作用`。flowctl kb lint 会校验路径存在、行号不超范围。 -->

- <!-- kb:todo -->
