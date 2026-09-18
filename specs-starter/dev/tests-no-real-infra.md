---
id: dev-unit-test-isolation
type: dev
triggers: [单测, 单元测试, unit test, 测试用例, mock]
when: 编写或修改单元测试时
status: draft
updated: 2026-09-18
---

# 单元测试不依赖真实数据库、网络和容器

外部依赖一律通过接口注入替身（fake / stub / mock）。只有集成测试才可以连接真实的基础设施，并且要放在单独的目录或 tag 下。

## 为什么

依赖真实设施的单测又慢又不稳定，AI 一跑就失败。它会去「修」测试，而不是修代码。

<!-- 这是一条示例 spec。按你的技术栈改成具体的写法后，把 status 改成 active -->
