# Tasks: Entry Drift Hybrid Policy

> 占位骨架 — 详细任务在 `/comet-design` 完成后展开。

## Phase 1: Plan 字段扩展（Judge）

- [x] 在 `agents/trading/judge.py:_build_plan` 添加 `entry_ref` / `sl_pct` / `tp_pct` 字段
- [x] `event_backtest.py` 兼容新字段（缺失时 fail-safe）
- [x] 单测：plan dict 包含 3 个新字段

## Phase 2: Drift Gate 单一函数

- [x] 实现 `executor._classify_entry_drift(plan, live_price) -> DriftDecision`
- [x] 实现 `executor._recompute_plan_for_drift(plan, new_entry) -> dict | None`
- [x] 单测：覆盖 5 种 DriftDecision（accept / recalc_small / recalc_medium / recalc_fail / abandon）

## Phase 3: 双 Gate 接入

- [x] Gate 1: `open_position_with_plan` 入口插入 drift gate
- [x] Gate 2: `_execute_limit_order` fallback 前再次跑 drift gate
- [x] 删除冗余路径：`executor.py:2203-2205` 校准 / `2259-2262` fallback / `1991-1997` TP 修正
- [x] 单测：5/30 XLM 7.2% abandon 场景通过

## Phase 4: partial_tp_1 双源真相

- [x] 落库点统一 `position.take_profit == position.take_profit_levels[0]`
- [x] invariant 检查 + halt fail-closed
- [x] 单测：违反 invariant 触发 halt

## Phase 5: 可观测性

- [x] `trade_decision.v2` / `execution_result.v2` attribution 加 `drift_decision` / `drift_pct`
- [x] 新 reason 枚举 `drift_too_large` / `drift_rr_floor_fail`
- [x] 新 `risk_alert.type` 进 critical_types

## Phase 6: 验收

- [x] 全测试套件 baseline 921 → 新基线
- [x] 验收文档 `docs/audit_remediation_entry_drift_hybrid_policy_acceptance.md`

## 后续（不在本 change 范围）

- Telegram 自定义 drift 类型展示文案（当前走 generic 路径，5 个新 critical_types 已注册）— 留作后续 change
- OKX testnet 冒烟（drift abandon / drift recalc 各一次）— 运维侧验收时执行，不阻断本 change 归档
