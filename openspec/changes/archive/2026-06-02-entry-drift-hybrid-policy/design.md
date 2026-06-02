# Design: Entry Drift Hybrid Policy

> 占位文件 — 实际设计在 `/comet-design` 阶段填充。
>
> 关联 proposal: `proposal.md`
> Capability: `entry-drift-policy`（new）

## 待 design 阶段细化

- 阶梯阈值（0.5% / 2% / 5%）落地常量位置
- medium band R:R floor 加成数值（候选：+0.20 / floor*1.10 / 不加成）
- `_classify_entry_drift` / `_recompute_plan_for_drift` 函数签名与返回结构
- Gate 1 / Gate 2 在 `executor.py` 中的精确插入位置与既有逻辑删除清单
- `plan.entry_ref` 在 Judge 缺字段时的 fail-safe 回退策略
- `partial_tp_1` 双源真相 invariant 的失败处理（halt symbol vs 仅日志）
- attribution 字段命名空间与 risk_alert critical_types 接入点
