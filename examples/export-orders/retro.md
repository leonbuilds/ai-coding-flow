# 订单批量导出 CSV · 回写

> 变更：20260918-export-orders

## 度量

见 `metrics.json`，由 demo.sh 运行 `flowctl metrics` 生成。

T1 的 FPY 最低。原因是首版直接输出了 `amount_cents`，违反了 domain-money-cents。这条 spec **已经被召回并勾选**，但实现时没有遵守。所以归因是「spec 在场但没有被执行」，不是「缺 spec」。

## 教训 → 资产

| 教训 | 落到哪里 | 文件 |
|---|---|---|
| spec 被召回了，实现时仍然可能被忽略 | 新 review 检查项：审查时逐条核对已勾选的 domain spec 是否被遵守 | .ai/specs/review/checked-domain-specs.md |

## spec 库变动

- 新增 review-checked-domain-specs，已通过 `flowctl specs lint`。
