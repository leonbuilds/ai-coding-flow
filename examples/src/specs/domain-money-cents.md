---
id: domain-money-cents
type: domain
triggers: [金额, amount, 价格, 导出, 报表]
paths: ["orders/**"]
when: 读写、展示或导出订单金额时
status: active
updated: 2026-09-18
---

# 金额在存储层一律以「分」为单位的整数，对外展示时才转换成「元」并保留两位小数

`Order.amount_cents` 是分。任何面向人的输出（导出、报表、页面）都必须转换成元，格式是 `f"{cents / 100:.2f}"`；内部计算不允许用浮点数表示金额。

## 为什么

以前有一个导出功能直接输出了 amount_cents，财务核对时差了 100 倍。
