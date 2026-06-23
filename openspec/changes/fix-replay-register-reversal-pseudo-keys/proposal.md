## Why

`tests/test_decision_replay.py::test_no_unclassified_missing_snapshot_keys` 预存失败:reversal-veto / pseudo-resonance-downweight 两个旧 change 新增的 4 个 config 键(`llm_rsi_reversal_veto_enabled` / `reversal_veto_min_llm_confidence` / `pseudo_resonance_downweight_enabled` / `ma_bloc_cap`)未登记进 `utils/decision_replay.py::_EPOCH_FALLBACK`。当 live 磁带累积出 ≥50 条 v3 记录(其中老记录缺这些键)时守卫触发。与前例 `521dad5`(已为 regime-aware 键登记)同类维护遗漏。

## What Changes

- 在 `utils/decision_replay.py::_EPOCH_FALLBACK` dict 登记 4 键的纪元回退默认值(纪元前 = 功能不存在 = OFF/默认):`llm_rsi_reversal_veto_enabled:False`、`reversal_veto_min_llm_confidence:0`、`pseudo_resonance_downweight_enabled:False`、`ma_bloc_cap:50`。

## Capabilities

### New Capabilities
<!-- 无 -->

### Modified Capabilities
<!-- 无 spec 级行为变更:纯纪元回退白名单登记,使守卫测试正确分类历史缺键。 -->

## Impact

- 改 `utils/decision_replay.py`(1 处 dict 加 4 项)。
- 修复 `test_no_unclassified_missing_snapshot_keys`;不改任何 live 决策/回放判定行为(enabled=False 在旧纪元本就不影响判定,与 521dad5 同性质防御性 no-op)。
