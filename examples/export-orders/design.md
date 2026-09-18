# 订单批量导出 CSV · 技术方案

> 变更：20260918-export-orders

## 方案概述

新增一个纯函数 `orders/export.py:to_csv(orders)`，负责把订单列表格式化成 CSV 文本，金额转换和转义都在这里完成。然后在 `OrderService` 上加 `export_csv(status)`，调用 `repo.list_by_status`（orders/repo.py:10）取数，再交给 `to_csv`。格式化单独成函数，是为了不依赖仓储就能测试。

## 模块边界

| 模块 / 文件 | 职责 | 本次是否改动 |
|---|---|---|
| orders/export.py | 订单列表 → CSV 文本（新增） | 新增 |
| orders/service.py | 编排：取数 + 格式化 | 修改：新增一个方法 |
| orders/repo.py | 取数 | 不改 |

## 接口契约

- `orders.export.to_csv(orders: List[Order]) -> str`：返回的 CSV 文本以 `\n` 换行。
- `OrderService.export_csv(status: str) -> str`。

## 数据变更

无。

## Delta

### ADDED
- orders/export.py：`to_csv`，包含金额的分转元，依据 domain-money-cents。
- `OrderService.export_csv`（orders/service.py）。
- tests/test_export.py：覆盖 AC-1、AC-2、AC-3。

### MODIFIED
- tests/test_service.py：增加 `export_csv` 的集成用例。

### REMOVED
- 无

## 架构约束与风险

- 金额必须按 domain-money-cents 转换成元。风险在于直接输出 `amount_cents`。
- 用标准库 `csv` 生成文本，不手工拼逗号。

## 验证方式

`python3 -m unittest discover -s tests` 覆盖 AC-1 到 AC-4。
