# Tasks: fix-replay-register-reversal-pseudo-keys

- [x] 1. `utils/decision_replay.py::_EPOCH_FALLBACK` 加 4 键:`llm_rsi_reversal_veto_enabled:False` / `reversal_veto_min_llm_confidence:0` / `pseudo_resonance_downweight_enabled:False` / `ma_bloc_cap:50`(带注释:真翻转 vs 防御性 no-op)
- [x] 2. 跑 `tests/test_decision_replay.py::test_no_unclassified_missing_snapshot_keys` → PASS
- [x] 3. 全量 pytest **1416 passed / 0 failed**(预存 fail 收掉、零新回归)
