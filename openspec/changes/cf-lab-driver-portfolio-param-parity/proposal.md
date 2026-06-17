# Proposal: cf-lab-driver-portfolio-param-parity

## Why

2026-06-17 诊断「CF opens 恒 2」（证伪"组合层 slot/EV 瓶颈"假设）时发现一个潜伏的保真 gap：

反事实实验室的一次性分析驱动 `cf_direction_recommendation.py` 与 `cf_rr_fidelity_ab.py` 调用
`build_delta_report` / `sweep_knob` / `sweep_grid` 时**不传组合参数**，于是落到库函数默认值
`daily_pnl_hard_stop=-50` / `initial_equity=1000`。而 live 运行时实际值为：

| 参数 | 库默认（驱动现用） | live 实际（`load_config()`） | 来源 |
|---|---|---|---|
| `daily_pnl_hard_stop` | **-50** | **-300** | `config.yaml: risk.max_daily_loss=300` |
| `initial_equity` | **1000** | **300** | `.env: EFFECTIVE_BALANCE_CAP=300` |
| `max_slots` | 3 | 3 | `max_concurrent_positions` 默认 3（已一致） |
| `consecutive_loss_limit` | 3 | 3 | 默认 3（已一致） |

`daily_pnl_hard_stop` 偏紧 6×。当前 reject-only 磁带 0 次熔断（本次诊断 `day_halted=0` 实证），
故对既有结论无影响；但未来磁带 accept 变多、CF 真开仓增加后，−50 会比 live 更早熔断、
系统性污染 perturbed-vs-baseline 的 PnL/胜率/回撤 delta。属应在影响显现前修掉的保真债。

## What

驱动层从 `utils.config_loader.load_config()` 读 live 组合参数，显式传入 `build_delta_report` /
`sweep_knob` / `sweep_grid` 的既有 kwarg（这些函数已接受并透传到 `run_arm` / `CounterfactualPortfolio`）。

- `initial_equity = cfg['effective_balance_cap'] or 1000.0`
- `max_slots = cfg['max_concurrent_positions']`
- `daily_pnl_hard_stop = cfg['daily_pnl_hard_stop']`
- `consecutive_loss_limit = cfg['consecutive_loss_limit']`

## Scope

- **改**：仅 repo 根的 2 个一次性分析驱动 `cf_direction_recommendation.py`、`cf_rr_fidelity_ab.py`。
- **不改**：`utils/` 库（`sequential_perturbation.py` / `knob_sweep.py` / `joint_knob_sweep.py` /
  `cf_portfolio.py`）的签名与默认值保持不变；`agents/` 生产路径零触碰；红线守卫
  `tests/test_cf_red_line_guard.py` 不变。
- **性质**：observability-only。无须 `event_backtest`（不改任何交易决策公式/门）。

## Capabilities

无。本变更不新增/修改任何 capability，不产生 delta spec —— 仅对齐分析驱动的入参，
库的可信结论合约与红线不变。

## Impact

- 对当前诊断结论**零影响**（已实证 0 熔断；本次三臂 baseline/-50 与 -300 逐字节相同）。
- 对未来重跑：CF 熔断行为与 live 一致，delta 不再被 −50 提前熔断污染。
- 回归风险极低：仅改驱动入参，库行为不变；全量 pytest 应保持基线绿。
