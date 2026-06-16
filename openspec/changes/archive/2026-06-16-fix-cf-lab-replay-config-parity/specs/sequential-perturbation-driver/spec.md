## ADDED Requirements

### Requirement: 两臂以生产 config 基线起步，扰动只覆盖目标旋钮
`build_delta_report`/`run_arm` 的 baseline 臂与 perturbed 臂 SHALL 以 per-record 有效生产 config（`config_snapshot` 或 `production_base_config()` fallback）为基线；perturbed 臂 = 该基线 + 扰动覆盖，扰动 SHALL 只覆盖目标旋钮，SHALL NOT 把其它旋钮重置出生产基线。

#### Scenario: baseline 臂用生产基线
- **WHEN** `run_arm` 以 `config={}`（baseline 臂）运行
- **THEN** 系统 SHALL 把空扰动解释为「生产基线，无覆盖」，即用 per-record 有效生产 config，而非 `_install_config_flags` 的硬默认

#### Scenario: 扰动叠加只覆盖目标旋钮
- **WHEN** perturbed 臂用扰动 `{rr_floor_default: 0.3}` 运行
- **THEN** 其有效 config SHALL 等于生产基线仅把 `rr_floor_default` 覆盖为 0.3，其它旋钮（含 Phase-2 flag）保持生产基线值

#### Scenario: 两臂同基线使 delta 干净
- **WHEN** baseline 与 perturbed 臂跑同一序列
- **THEN** 两臂 SHALL 从同一 per-record 生产基线起步，差异仅来自扰动旋钮，使 delta 不含 config 基线偏差
