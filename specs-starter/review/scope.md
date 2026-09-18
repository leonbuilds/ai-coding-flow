---
id: review-scope
type: review
when: 每次审查都检查
always: true
status: active
updated: 2026-09-18
---

# diff 不得超出 design.md 的 Delta

diff 里每一处改动，都要能对应到 design.md 中的某条 Delta。对应不上的改动，比如顺手重构、格式化无关文件、升级依赖，一律判为越界，要求拆出去单独提。

## 为什么

越界改动会让评审失焦，出了问题也没法按任务粒度回退。
