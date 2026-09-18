# 订单批量导出 CSV · 任务拆解

> 变更：20260918-export-orders

- [x] T1 新增 to_csv 及其单测
  - 输入：orders/model.py:5（Order 的字段）、spec domain-money-cents
  - 输出：orders/export.py、tests/test_export.py
  - 依赖：无
  - 验收：python3 -m unittest tests.test_export

- [x] T2 OrderService.export_csv 及集成用例
  - 输入：orders/service.py、orders/repo.py:10、T1 的 to_csv
  - 输出：orders/service.py、tests/test_service.py
  - 依赖：T1
  - 验收：python3 -m unittest discover -s tests
