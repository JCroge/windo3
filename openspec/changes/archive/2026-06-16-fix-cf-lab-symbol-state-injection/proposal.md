## Why

反事实实验室 L3b **sequential 臂** baseline_fidelity = **0.798**（< 0.8）→ untrustworthy，驱动 `cf_direction_recommendation.py` 仍给不出可信方向；而**直接 L2**（录制快照）已 **0.914**。差距 100% 坐实在一处：

`utils/sequential_perturbation.py::_inject_cf_state` 用 `cf.to_snapshot()` 替换录制 `state_snapshot`，其中 **`_symbol_state` 被清空为 `{}`**。但 `_symbol_state` 含 per-symbol **决策输入上下文** `{last_decision_time, last_tech, last_force_close_time, last_open_time, trend_streak}`；Judge 信号强度路径读 `trend_streak`/`last_tech` → 空 `{}` 致 **"信号强度不足"→ hold_other**，而非录制的 `rr_below_floor`。

**证据**：全量 779 条 v2/v3，L2 fidelity 0.911 vs sequential 0.798；残差 **90 条**全是「L2 复现但 inject 没复现」，其中 86 `rr_below_floor→hold_other`；override 还原 `_symbol_state` 后 **90/90（100%）复现**。其它字段（`_available_balance`/`_archetype_cooldown`/`_open_positions`/`_recent_win_rate`）override 均不复现 → **非根因**（已证伪）。config-parity 已在上个 change 修。

## What Changes

- `_inject_cf_state` 的 `_symbol_state` **基于录制快照**：还原 CF 无法重建的市场决策输入字段（`last_tech`/`trend_streak`/`last_decision_time`），使 baseline 臂忠实复现录制 gate。
- CF 只 **overlay 它自己开过仓的 position-outcome 字段**（`last_open_time`/`last_force_close_time`），以保 perturbed 臂级联真实；baseline 臂（cf_open=0）无 overlay → 录制值原样 → 100% 复现。
- 目标：sequential baseline_fidelity 0.798 → ≥0.85（坐实 ~0.91），untrustworthy 解除，实验室端到端可信。

## Capabilities

### New Capabilities
<!-- 无新增 -->

### Modified Capabilities
- `sequential-perturbation-driver`: `_inject_cf_state` 注入的 `_symbol_state` 须保留录制的 per-symbol 决策输入上下文（市场/信号连续性字段），不得清空致信号强度路径发散；仅 CF 自身开仓影响的 position-outcome 字段由 CF overlay。

## Impact

- 代码：`utils/sequential_perturbation.py`（`_inject_cf_state`），可能 `utils/cf_portfolio.py::to_snapshot`（若 `_symbol_state` 由 to_snapshot 产出空），及对应测试。
- 行为：仅提升 L3b sequential 臂回放保真；**不改 live Judge 决策逻辑、不改 live 生产 config、不改 choppy R:R 地板 1.50、无需 event_backtest**。
- 红线：observability-only write-only 不变；还原的是**市场决策输入**（非 reality 的 EV/胜率交易结果累计），不触 L3b "绝不 per-record 注入 reality 演化计数" 反模式。
- 非目标：不动决策输入以外的 CF 状态语义；不追已 0.91 的直接 L2。
