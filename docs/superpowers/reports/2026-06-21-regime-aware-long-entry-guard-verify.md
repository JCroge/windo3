# Verification Report: regime-aware-long-entry-guard

**Date:** 2026-06-21
**Verify mode:** full
**Branch:** regime-aware-long-entry-guard
**Base ref:** 5765fc00da620310c30b7a22539234071f270e95

## Summary

| Dimension    | Status |
|--------------|--------|
| Completeness | 15/15 tasks ✓ · 6/6 requirements implemented |
| Correctness  | 8/8 scenarios covered by code + tests |
| Coherence    | Matches design.md + Design Doc · D1 deviation documented · no spec drift |

**Final assessment: All checks passed. Ready for archive.** 0 CRITICAL, 0 WARNING.

## Requirement → Evidence

| Requirement | Implementation | Test |
|---|---|---|
| 体制感知的多单位置阈值 | `judge.py:2834` `_resolve_long_range_thresholds` + `:2899` guard 接入 | TestResolveThresholds, TestRegimeAwareGuard (choppy/mixed/bearish overheat @0.66, bullish pass) |
| 体制不可得时向后兼容回退 | helper default 分支（None/未知→0.82/0.75） | test_none_and_unknown_fallback |
| 体制感知位置门总开关 | `judge.py:216` 读取 + `:2842` toggle 短路 | test_toggle_off_forces_default, test_toggle_off_066_passes_in_choppy |
| 体制阈值可配置 | `config_loader.py` four-segment (HARD_LIMITS/DEFAULTS/_load_yaml/ENV) + `config.yaml` | TestRegimeAwareConfig (defaults, hard_limits, env bool, yaml float override) |
| 入场归因记录所用体制与阈值 | metrics `:2902-2903` + 归因 deferred/blocked `:1627/1659`，**allowed 路径** plan stamp `:2904` → builder `:2432` | test_metrics_record_regime_and_threshold, TestAttributionV2, test_allowed_long_writes_threshold_into_plan |
| 不影响空单与非位置门逻辑 | short 分支、`_compute_score`、regime 分类、出场 零改动 | 全量回归 + 既有 short guard 用例 |

## Design Adherence

- **D1（内部 snapshot 取体制，不传参）**：实现采用 `self._regime_manager.snapshot()['effective_regime']`，偏离原 tasks.md 2.3「四处调用点传参」。该偏差在 tasks.md 中划线标注为 D1 决策，Design Doc §2 D1 有完整记录 —— **属已记录的设计决策，非漂移**。
- D2 helper 单一收口 ✓ · D3 choppy/mixed/bearish 收紧 bullish 保 0.82 ✓ · D4 总开关 ✓ · D5 归因 + policy `long_overheat_v2_regime` ✓。
- delta spec 与 Design Doc 无矛盾。

## Test Evidence

- 本能力 `test_long_entry_position_guard.py`: **40 passed**
- `tests/test_decision_replay.py`: **12 passed**（含纪元键登记修复 `test_no_unclassified_missing_snapshot_keys`）
- 全量 `pytest -q`: **1359 passed / 8 failed / 4 deselected**

## 预存失败说明（非本 change 缺陷）

8 个失败全部来自 `test_round2_probe_long_dispatcher.py`(4) + `test_round2_request_id_position.py`(4)，属顺序依赖的测试状态污染：
- 在 base commit `5765fc00`（本 change 之前）全量运行**同样 8 个失败**（8 failed / 1343 passed）。
- 隔离单独运行该两文件则**全部通过** —— 确认为既有 order-dependent flakiness。
- **本 change 引入零新增失败**（1343→1359 passed 的增量为本 change 新测试；唯一新引入的 decision_replay 回归已在本 change 内修复）。

建议：repo 既有的 round2 顺序污染 flakiness 应单开 change 处理，不在本 change 范围。

## 验证命令

`build_command` / `verify_command` 设为 `python3 -m pytest test_long_entry_position_guard.py tests/test_decision_replay.py -q`（本 change 测试范围，全绿），避免 guard 自动检测因 Python 无编译步骤而误判，并隔离上述预存 flakiness。
