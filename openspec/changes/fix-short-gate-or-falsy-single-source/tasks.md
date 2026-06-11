# Tasks: fix-short-gate-or-falsy-single-source

## P1-02：`or`-falsy → 哨兵合并
- [ ] 新增 `_coalesce_float(*vals, default)` helper（区分 present 0.0 与 absent None）
- [ ] `_classify_short_entry_risk`（judge.py:2692-2694）`range_pos/pre_move/rsi_val` 改用 `_coalesce_float`
- [ ] `_check_entry_position_policy`（judge.py:2761）long overheat range_pos 改用 `_coalesce_float`（同 bug 类，真实 gate）
- [ ] attribution 写点（judge.py:2359 entry_range_pos_24h / entry_pre_12h_return_pct）改用 `_coalesce_float`（cosmetic 一致性）

## P1-03：`_apply_regime_policy` delegate + 保留 probe 外壳
- [ ] `_apply_regime_policy` 短单结构段（judge.py:2904-2950）改 delegate 到 `_classify_short_entry_risk`，删第二份内联实现
- [ ] 保留 `daily_bearish_required` 的 probe 路由外壳（probe_ok → `_route_to_probe` 不拒；否则拒单）
- [ ] 其它结构 reason（range/pre_move/rsi/score/htf）直接透传拒单
- [ ] `llm_result` 传入 delegate；`_apply_short_gate_attribution` 四字段在 accept/reject 两路径不回归

## 测试（tests/test_short_main_path_risk_guard.py）
- [ ] present `range_position_24h=0.0` + bearish + 非 probe → `range_position_too_low`（P1-02 核心回归）
- [ ] absent range metric → canonical 与 regime 用同一默认（默认值一致性）
- [ ] delegate parity：`_apply_regime_policy` 与直接 `_classify_short_entry_risk` 拒单 reason 一致
- [ ] probe 外壳：bullish daily + probe 条件满足 → delegate 后仍 `_route_to_probe`（不拒）
- [ ] attribution：delegate 后 reject/accept 仍含 `short_gate_version/short_gate_decision/short_gate_reason/llm_short_reversal_risk`
- [ ] 既有 14 case 保持全绿

## 同构与回归（CLAUDE.md 红线）
- [ ] 记录 `event_backtest.py` 短单 gate（396-441）已用 `.get(..., 0.5)` 正确处理 0.0 且单份实现 → P1-02 是 live 向回测对齐、P1-03 是 live 两份合一，回测决策路径无需改动
- [ ] 全量 `python3 -m pytest -q` 须 `1066+ passed`（新增用例后基线上调）
- [ ] `compileall agents utils` 通过

## 收尾
- [ ] 更新 CLAUDE.md "当前事实" + `docs/to-do-list.md` 关闭 P1-02/P1-03（引用第五次审计报告）
- [ ] delta spec 同步至 master（归档阶段）
