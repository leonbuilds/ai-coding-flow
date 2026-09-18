# {{title}} · 审查记录

<!-- flow:todo -->
> 变更：{{change}}
> 每一轮由 `flowctl verify start` 新开一个独立审查者（引擎和模型每轮由你选择），结果自动追加到下表，完整输出在 `review/round-N.out.md`。
> 最后一轮 PASS、spec 反馈填完后，删掉 flow:todo 标记。

## 审查轮次

| 轮 | 结论 | 引擎 / 模型 | 独立级别 | 输出 | 备注 |
|---|---|---|---|---|---|
<!-- flow:rounds -->

> 独立级别：L1 同模型新上下文 · L2 同厂商不同模型 · L3 跨厂商

## 修复记录

<!-- 每轮 REJECT 后修了哪些问题：对应发现编号 → 改动位置 → 如何确认（回源码核对，不照审查意见的字面改） -->

## spec 反馈

> 对 recalled.json 里的每条 spec 给出一个判定：hit（用上了且有帮助）、unused（召回了但没用）、mislead（把实现带偏了）。
> 每条都要执行一次 `flowctl score <id> <判定>`。

| spec | 判定 | 说明 |
|---|---|---|
