---
id: dev-example-rule              # 全库唯一，建议 <类型>-<主题>
type: dev                         # tech | domain | dev | review
triggers: [关键词A, keywordB]      # 需求文本命中任意一个就召回；英文按整词匹配
paths: ["src/**/order/**"]         # 改动路径命中也召回；可以不写
when: 修改订单模块的金额计算时      # 必填：什么条件下注入。答不上来就别加这条
status: active                    # draft | active | retired
updated: 2026-01-01
# always: true                    # 仅 review 类允许常驻；其他类型会被 lint 警告
---

# 一句话结论

<!-- 规则本身，写成可检查的断言，而不是「注意 xx」 -->

## 为什么

<!-- 踩过的坑、线上事故或 CR 意见的出处 -->

## 正例 / 反例

```
```
