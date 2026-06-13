## ADDED Requirements

### Requirement: 决策点跨决策状态白名单快照
系统 SHALL 在 Judge 每次开仓决策点（accept + reject）随决策磁带记录一份 `state_snapshot_before_decision`，白名单显式序列化决策依赖的跨决策可变状态，禁止 pickle 整个对象。

#### Scenario: 快照含全部白名单字段
- **WHEN** Judge 决策落磁带
- **THEN** `state_snapshot_before_decision` SHALL 含 `_open_positions`、`_pending_open_symbols`、`_position_slots`、`_pending_open_slots`、archetype cooldown（history + cooldown_until）、`_recent_wins`、`_total_completed_trades`、`_recent_win_rate`、`_probe_short_active`、`_probe_short_sl_count`、`_probe_short_cooldown_until`、`_symbol_state[symbol]`、`_available_balance`、`_regime_manager` 完整 snapshot

#### Scenario: set 可 JSON 序列化
- **WHEN** 快照含 set 类型状态（如 `_open_positions`）
- **THEN** 系统 SHALL 转为 list 落盘，保证磁带 JSON 可序列化

#### Scenario: 不 pickle 实现细节
- **WHEN** 序列化状态快照
- **THEN** 系统 SHALL 只取白名单字段，SHALL NOT pickle/dump 整个 Judge `__dict__`

#### Scenario: 快照落点职责分离
- **WHEN** 采集状态快照
- **THEN** 字段收集 SHALL 由 Judge `_capture_state_snapshot()`（知道自身字段）完成，JSON 化（set→list 等）SHALL 由 `decision_tape` 纯 helper 完成

### Requirement: 快照 forward-only 且向后兼容
系统 SHALL 仅对启用后产生的 record 写状态快照；缺快照的旧 record（L1）回放时 SHALL fail-safe 标记不可复现，不报错。

#### Scenario: 旧 record 标不可复现
- **WHEN** 回放读到无 `state_snapshot_before_decision` 的 record
- **THEN** 系统 SHALL 标 `replayable=false` 并跳过 golden-master 复现，不抛异常

#### Scenario: flag 关停不采集
- **WHEN** 决策磁带 feature flag 关闭
- **THEN** 系统 SHALL NOT 采集状态快照，回到 L1 行为

### Requirement: 状态快照 observability-only write-only
系统 SHALL 保证状态快照为纯观测写入，任何 gate/veto/halt/rank/daily-stop SHALL NOT 读取快照做交易决策。

#### Scenario: 快照不进决策路径
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其代码路径 SHALL NOT 读取 `state_snapshot_before_decision`
