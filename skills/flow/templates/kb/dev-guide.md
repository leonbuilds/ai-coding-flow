---
id: {{id}}
type: kb
kind: dev-guide
name: {{name}}
summary: <!-- kb:todo -->
triggers: {{triggers}}
paths: {{paths}}
sources: {{sources}}
updated: {{date}}
protected: false
---

# 开发与排查指引

<!-- 规则：每条论断带 file:line；没证据写「待确认」；代码块 ≤10 行；只写有代表性的，其余靠 file:line 指路；图用 mermaid；人工补充放 <!-- kb:manual --> … <!-- /kb:manual --> 里；frontmatter 只改 summary。 -->
<!-- 规划目标：{{goal}}；scan 提示：{{hints}} -->

## 构建 / 测试 / lint

<!-- 每条标「已验证」或「猜测，未跑」；区分跑全部和只跑一块。 -->

```bash
<构建>            # 已验证 / 猜测，未跑
<跑全部测试>
<只跑某个模块或包>
<lint>
```

## 加一个接口 / 命令

<!-- 从入口注册到 handler 到 service 到存储，各在哪个文件、照着哪个现有例子改。 -->

## 加一个字段 / 表

## 排查顺序

<!-- 报错、超时、数据不对分别先看哪。 -->

## 引用文件

<!-- 正文出现过的文件各列一次：`- path:line — 作用`；lint 校验路径存在、行号不超范围。 -->

- <!-- kb:todo -->
