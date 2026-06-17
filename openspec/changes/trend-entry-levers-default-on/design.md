# Design (high-level): trend-entry-levers-default-on

> 高层方向。深度技术设计 + 范围/验证决策由 comet-design（brainstorming）产出 Design Doc 后定稿。

## 改动点（机械部分）

- `utils/config_loader.py`：把 `ladder_rr_enabled`（lever2）、视决策可能含 `path_evidence_aligned_enabled`（lever1）加入 `DEFAULTS`，值 `True`；加入 `HARD_LIMITS`/env 覆盖映射（与现有 bool flag 一致），保留可经 env 关闭的逃生阀。
- `agents/trading/judge.py:169,174`：兜底默认与 DEFAULTS 对齐（`config.get(..., True)`），保证无 config 时行为一致。
- 不改 lever 本体逻辑（`_compute_ladder_rr`/`_select_rr_floor`/`low_rr_policies` 已实现且接好）。

## 待 brainstorming 定的设计决策

1. **范围**：DEFAULTS 里开一个（lever2）还是两个（lever1+lever2）。
2. **验证**：event_backtest 同构的深度（仅回归/非崩溃 vs 端口 lever 逻辑进 event_backtest 的 `_build_plan`），以及主验证证据栈（rejected 流 A/B + tier 定价 + paper 前向）如何在验证报告中呈现。
3. **灰度策略**：默认开后是否配合 env 逃生阀 + paper 前向先行 + 小额 live 观察窗口。

## 数据流（不变）

`tech_analysis → Judge._build_plan`（effective_rr 走 ladder 口径）`→ _select_rr_floor`（趋势补授 1.30）`→ low_rr_policies`（缩仓/降杠杆/独立 slot）`→ trade_decision.v2`。本 change 只改默认开关与 config 落点，不改链路结构。

## 风险与回滚

- 回滚 = env `LADDER_RR_ENABLED=false` / `PATH_EVIDENCE_ALIGNED_ENABLED=false` 即时关闭，无需改代码（设计须保留 env 逃生阀）。
- 主风险：默认开后对**非趋势单**的 R:R 评分也变（lever2 全局生效），须回归 + event_backtest 确认无意外放开。
