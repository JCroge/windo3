# Tasks: trend-entry-shadow-decision-logger

> 影子=复用 replay_decision 前向 both-levers；fire-and-forget fail-safe；observability-only write-only。

- [x] 1. 设计定稿：brainstorming 定 D1（复用 replay_decision 前向）/D2（影子−实盘=lever1 增量）/D3（红线）/D4（fail-safe + config flag）/D5（结局离线结算）；Design Doc + delta spec `shadow-decision-logger` 4 requirements。
- [x] 2. 影子决策记录器：`config_loader` 加 `shadow_decision_logger_enabled: True` + env；`utils/shadow_decision_logger.py`（`log_shadow_decision` 跑 replay both-levers + `compute_flip_kind` + write-only jsonl + 内部 fail-safe）；坐实 replay 从真实 chokepoint bundle 跑通（TRUMP-USDT 产出影子决策、不抛/不重复 record）。
- [x] 3. judge chokepoint hook：`_schedule_shadow`（sync, fire-and-forget create_task, 无 loop fail-safe）+ `_maybe_log_shadow`（async）；accept 在 publish 后（零 live 延迟）/reject（sync `_record_rejected_plan`）旁路；失败安全测试坐实影子异常不破 live。
- [x] 4. 隔离红线守卫：`test_cf_red_line_guard.py` 加 `test_decision_paths_do_not_read_shadow_products`（executor/halt/riskguard/reviewer/position_analyst 禁读影子产物，Judge 写路径豁免）。
- [x] 5. 离线对比驱动 `cf_shadow_lever1_compare.py`：筛 flip_kind=shadow_opens（lever1 解锁）→ resolve_counterfactual+klines 结算 lever1 增量净 R + 诚实门；空日志优雅拒答。
- [x] 6. 全量回归 **1298 passed**（1288+10 新：9 shadow + 1 红线）；失败安全（无 loop/异常/flag-off 皆不破 live）+ schema sanity（含 real+shadow+tech_context）已测。
