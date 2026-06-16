## ADDED Requirements

### Requirement: 报告/方向驱动以生产 config 为回放基线
报告与方向推荐驱动（`cf_direction_recommendation.py` / `sweep_knob`）SHALL 以 per-record 有效生产 config 为回放基线喂入回放，SHALL NOT 用空 config，避免 baseline_fidelity 因 config 不一致虚低而误判 untrustworthy。

#### Scenario: 驱动用生产基线
- **WHEN** 驱动跑 L2 终验 / L4 扫描
- **THEN** baseline 臂 SHALL 用 per-record 有效生产 config（`config_snapshot` 或 `production_base_config()` fallback），扫描各值在该基线上覆盖目标旋钮

#### Scenario: 修复后可信度恢复
- **WHEN** 修复后重跑 `cf_direction_recommendation.py`
- **THEN** baseline_fidelity SHALL 跨过阈值（untrustworthy 解除），驱动可给出方向或可信的 no_actionable_direction，区别于此前因 config 不一致的 untrustworthy 拒答
