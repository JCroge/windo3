# Tasks: cf-lab-driver-portfolio-param-parity

- [x] 1. `cf_direction_recommendation.py`：加 `_live_portfolio_kwargs()`（从 `load_config()` 派生
  `initial_equity`/`max_slots`/`daily_pnl_hard_stop`/`consecutive_loss_limit`），用 `**_live_portfolio_kwargs()`
  展开到 baseline 探针 `build_delta_report`、两处 `sweep_knob`、`sweep_grid` 调用点。
  → 实测 helper 返回 `{initial_equity:300.0, max_slots:3, daily_pnl_hard_stop:-300.0, consecutive_loss_limit:3}`。
- [x] 2. `cf_rr_fidelity_ab.py`：同样加/复用 `_live_portfolio_kwargs()`，展开到其 `build_delta_report` 调用点。
- [x] 3. 验证：全量 `python3 -m pytest -q` → **1285 passed / 4 deselected / 1 warning（183.85s）基线绿**；
  helper 实测返回 live `daily_pnl_hard_stop=-300.0`/`initial_equity=300.0`/`max_slots=3`/`consecutive_loss_limit=3`；
  两驱动 `py_compile` OK；未新增/改动任何 `utils/` 库与红线守卫（仅驱动加 `load_config` import 是 prod→驱动方向，不违 observability 红线）。
