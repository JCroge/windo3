# Tasks: cf-lab-driver-portfolio-param-parity

- [ ] 1. `cf_direction_recommendation.py`：加 `_live_portfolio_kwargs()`（从 `load_config()` 派生
  `initial_equity`/`max_slots`/`daily_pnl_hard_stop`/`consecutive_loss_limit`），用 `**_live_portfolio_kwargs()`
  展开到 baseline 探针 `build_delta_report`、两处 `sweep_knob`、`sweep_grid` 调用点。
- [ ] 2. `cf_rr_fidelity_ab.py`：同样加/复用 `_live_portfolio_kwargs()`，展开到其 `build_delta_report` 调用点。
- [ ] 3. 验证：全量 `python3 -m pytest -q` 保持基线绿（1285）；smoke 跑 `cf_direction_recommendation.py`
  前几行确认组合参数已变为 live（−300/300，可临时打印或日志核验）；不新增/改动任何 `utils/` 库与红线守卫。
