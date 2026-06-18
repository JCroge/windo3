# Tasks: 剔除开仓门的胜率因子

- [x] 1. `judge.py` 构造函数新增 `_ev_winrate_gate_enabled`(默认 True) / `_ev_neutral_p_win`(默认 0.55)
- [x] 2. `judge.py` `_get_p_win()` 顶部短路：关闭时返回 `(ev_neutral_p_win, "fixed")`
- [x] 3. `judge.py` `_check_expected_value()`：分桶块 + 胜率<40%硬阈值前置开关条件，关闭时跳过；EV 阈值门不动
- [x] 4. `utils/config_loader.py`：RISK_DEFAULTS / HARD_LIMITS / env_map / `_load_yaml` / banner 五处接入两个新键
- [x] 5. `config.yaml` risk 节点新增 `ev_winrate_gate_enabled: false` + `ev_neutral_p_win: 0.55`
- [x] 6. `test_ev_gate.py` 新增用例：关闭开关时 (a) `_get_p_win()` 返回 `(0.55,"fixed")`；(b) 胜率25%+score<70+合理R:R 的 plan `_check_expected_value` 返回 True；(c) R:R 极差(EV显著负)的 plan 仍返回 False
- [x] 7. 回归：`pytest test_ev_gate.py test_phase2_bucketed_ev.py test_phase2_confidence_split.py` 全过（32 passed）；`load_config()` 读到 `ev_winrate_gate_enabled=False`

> 备注：三处开关引用用 `getattr(self,'_ev_winrate_gate_enabled',True)` 容错 `MultiJudge.__new__` 构造的测试。`tests/test_decision_replay.py::test_production_baseline_restores_fidelity` 为 base-ref 既有失败，与本 change 无关。
