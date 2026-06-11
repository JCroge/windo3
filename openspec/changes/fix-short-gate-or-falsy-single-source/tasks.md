# Tasks: fix-short-gate-or-falsy-single-source

## P1-02：`or`-falsy → 哨兵合并
- [x] 新增 `_coalesce_float(*vals, default)` helper（区分 present 0.0 与 absent None；@staticmethod，judge.py:2620）
- [x] `_classify_short_entry_risk`（range_pos/pre_move/rsi_val）改用 `_coalesce_float`（commit 23d1b87/2612028）
- [x] `_check_entry_position_policy` long overheat range_pos 改用 `_coalesce_float`（commit 7fb697d）
- [x] attribution 写点（entry_range_pos_24h / entry_pre_12h_return_pct）改用 `_coalesce_float`（commit 7fb697d）

## P1-03：`_apply_regime_policy` delegate + 保留 probe 外壳
- [x] `_apply_regime_policy` 短单结构段改 delegate 到 `_classify_short_entry_risk`，删第二份内联实现（commit 645cf41/0bf2a23）
- [x] 保留 `daily_bearish_required` 的 probe 路由外壳（probe_ok → `_route_to_probe` 不拒；否则拒单）
- [x] 其它结构 reason（range/pre_move/rsi/score/htf）直接透传拒单
- [x] `llm_result` 传入 delegate；`_apply_short_gate_attribution` 四字段不回归（attribution 由 caller 持有 813/936/1058/1536/1700，delegate 不触碰；spec review 已核 + 全量回归绿）

## 测试（tests/test_short_main_path_risk_guard.py）
- [x] present `range_position_24h=0.0` + bearish + 非 probe → `range_position_too_low`（test_range_pos_zero_is_rejected_not_coalesced）
- [x] absent range metric → 用统一默认 0.5（test_absent_range_uses_default + test_regime_matches_classify_reason 同默认）
- [x] delegate parity：`_apply_regime_policy` 与直接 `_classify_short_entry_risk` 拒单 reason 一致（test_regime_matches_classify_reason）
- [x] probe 外壳：bullish daily + probe_ok → delegate 后仍 `_route_to_probe`（test_daily_bearish_probe_shell_routes_not_rejects）；probe 失败 → daily_bearish_required（test_daily_bearish_probe_fail_rejects）
- [x] attribution 四字段不回归：caller-owned，既有 attribution 用例随全量回归绿（无 live 路径改动）
- [x] 既有 14 case 保持全绿（该文件现 21 passed：14 + 7 新增）

## 同构与回归（CLAUDE.md 红线）
- [x] `event_backtest.py` 短单 gate（166-167 fillna + 379/398/407 `.get(..., 0.5)`，row 永不 None）已正确处理 0.0 且为单份实现，且不引用 `_classify_short_entry_risk`/`_apply_regime_policy`。结论：P1-02 是 live 向回测对齐、P1-03 是 live 两份合一，**回测决策路径无需改动**，红线满足。
- [x] 全量 `python3 -m pytest -q`：**1073 passed / 4 deselected / 1 warning**（1066 基线 + 7 新增）
- [x] `compileall agents utils` 通过

## 收尾
- [ ] 更新 CLAUDE.md "当前事实" + `docs/to-do-list.md` 关闭 P1-02/P1-03（引用第五次审计报告）
- [ ] delta spec 同步至 master（归档阶段）
