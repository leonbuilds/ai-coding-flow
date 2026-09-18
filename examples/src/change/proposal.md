# 订单批量导出 CSV · 提案

> 变更：{{change}} · 创建于 {{date}}

## 需求原文

> 运营需要按订单状态把订单批量导出成 CSV，给财务对账。字段：订单号、客户、状态、金额（元）。

## 业务意图

运营按状态导出订单明细，交给财务对账。核心诉求是金额准确，而且格式能直接用 Excel 打开。

## 边界约束

- 做：按单个状态导出；输出 CSV 文本，第一行是表头。
- 不做：分页、异步导出、上传文件存储、多状态组合筛选。
- 不能动：`OrderService.count_by_status` 的现有行为；`Order` 的数据结构。

## 验收标准

- AC-1：导出的内容第一行是表头 `id,customer,status,amount_yuan`。
- AC-2：金额从分转换成元，保留两位小数（例如 12345 分 → `123.45`）。
- AC-3：没有该状态的订单时，只输出表头。
- AC-4：现有测试全部通过。

## 上下文清单

### spec

- [x] domain-money-cents：金额以分存储，对外展示要转换成元（when：读写、展示或导出订单金额时）
- [x] review-grounding：审查时核对新引用的符号是否存在（常驻检查项）

### 代码入口

- [x] orders/repo.py:10：`InMemoryOrderRepo.list_by_status`，按状态取订单，直接复用
- [x] orders/model.py:5：`Order` 的字段定义，其中金额字段是 `amount_cents`
- [x] orders/service.py:4：`OrderService`，导出方法加在这里
- [x] tests/test_service.py:9：现有测试的写法，新测试照此写

### 历史方案 / 相似变更

- 无

## 未决问题

- 无（客户名里可能含逗号，由标准库 `csv` 处理转义，不阻塞）
