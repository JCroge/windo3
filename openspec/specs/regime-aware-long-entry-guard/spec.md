## ADDED Requirements

### Requirement: 体制感知的多单位置阈值

多单"过热"位置门 SHALL 根据当前有效市场体制（`self._regime_manager.snapshot()['effective_regime']`，与相邻 regime policy 同源）选择 `position_in_24h_range` 的过热阈值，而非使用单一固定值。`choppy`、`mixed`、`bearish` 体制 SHALL 使用收紧后的阈值（`long_live_max_range_pos_choppy`，默认 0.55）；仅 `bullish`（确认上涨）体制 SHALL 使用现有默认阈值（`long_live_max_range_pos`，默认 0.82）。同一判定 SHALL 在主路径与 deferred 路径共用，避免漂移。

#### Scenario: choppy 体制收紧阈值拦截中位追突破
- **WHEN** 一个 `open_long` 候选在 `choppy` 体制下，`position_in_24h_range=0.66`，且非 probe
- **THEN** 位置门 SHALL 判定为 `overheated`（0.66 ≥ choppy 阈值 0.55）
- **AND** SHALL 拒绝主动 open 并按现有逻辑转 `deferred_pullback_overheat`

#### Scenario: mixed 与 bearish 体制同样收紧
- **WHEN** 一个 `open_long` 候选在 `mixed` 或 `bearish` 体制下，`position_in_24h_range=0.66`，且非 probe
- **THEN** 位置门 SHALL 判定为 `overheated`（0.66 ≥ 收紧阈值 0.55）并转 `deferred_pullback_overheat`

#### Scenario: bullish 体制维持原阈值放行
- **WHEN** 一个 `open_long` 候选在 `bullish` 体制下，`position_in_24h_range=0.66`，且非 probe
- **THEN** 位置门 SHALL 维持默认阈值 0.82，判定为 `normal` 并放行（0.66 < 0.82）

### Requirement: 体制不可得时向后兼容回退

当有效体制不可得（缺失、未知或非白名单值）时，位置门 SHALL 回退到现有默认阈值 `long_live_max_range_pos`（0.82），使行为与本变更前完全一致。

#### Scenario: 体制缺失回退默认
- **WHEN** 一个 `open_long` 候选其有效体制为 `None` 或未知，`position_in_24h_range=0.70`
- **THEN** 位置门 SHALL 使用默认阈值 0.82，判定为 `normal` 并放行

### Requirement: 体制感知位置门总开关

体制感知逻辑 SHALL 受配置键 `long_live_regime_aware_range_enabled`（默认 `true`）控制。当其为 `false` 时，多单位置门 SHALL 对所有体制使用现有默认阈值（0.82/0.75），行为与本变更前完全一致，提供实盘即时回退能力。

#### Scenario: 总开关关闭回退旧行为
- **WHEN** `risk.long_live_regime_aware_range_enabled=false`，一个 `open_long` 候选在 `choppy` 体制下 `position_in_24h_range=0.66`，且非 probe
- **THEN** 位置门 SHALL 使用默认阈值 0.82，判定为 `normal` 并放行（与变更前一致）

### Requirement: 体制阈值可配置

收紧体制（choppy/mixed/bearish）与默认体制的多单位置阈值（含 `daily_gain_range_pos` 对应键）SHALL 经 `config.yaml` `risk` 段配置键提供，并按既有 four-segment 配置模式接入；未配置时使用规范默认值（收紧 0.55/0.50，默认 0.82/0.75）。

#### Scenario: 配置覆盖 choppy 阈值
- **WHEN** `config.yaml` 设 `risk.long_live_max_range_pos_choppy=0.50`
- **THEN** choppy 体制下位置门 SHALL 以 0.50 作为过热阈值

### Requirement: 入场归因记录所用体制与阈值

位置门 SHALL 在入场 attribution 中记录本次判定所用的有效体制与生效阈值，使后续可按体制切分核对入场位置分布与盈亏。

#### Scenario: 归因含体制与阈值
- **WHEN** 一个 `open_long` 候选经体制感知位置门判定（无论放行或转 defer）
- **THEN** attribution SHALL 包含所用有效体制及该体制下生效的 `range_pos` 阈值字段

### Requirement: 不影响空单与非位置门逻辑

本能力 SHALL 仅作用于多单（long）过热位置门；空单 short-side guard、`_compute_score` 打分、regime 分类本身、出场/SL 逻辑 SHALL 不受影响。

#### Scenario: 空单不受影响
- **WHEN** 一个 `open_short` 候选在任意体制下进入位置门
- **THEN** 其判定 SHALL 完全沿用既有 short-side guard 语义，不应用多单体制阈值
