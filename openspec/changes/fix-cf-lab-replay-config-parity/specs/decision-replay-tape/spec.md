## ADDED Requirements

### Requirement: 决策磁带录制 resolved config 快照
决策磁带 SHALL 在录制每条决策时附带该决策实际运行的 config 快照（`config_snapshot`），覆盖回放 harness 消费的 config key 白名单（`_install_config_flags` 读取的 ~57 旋钮 + 四个 Phase-2 flag），使回放能用与 live 决策时一致的 config，防 config 漂移后回放发散。

#### Scenario: build_bundle 录 config_snapshot
- **WHEN** Judge 在 accept/reject chokepoint 录决策磁带
- **THEN** `build_bundle` SHALL 写入 `config_snapshot` = 决策时 Judge resolved config 的白名单子集，`SCHEMA_VERSION` 升至 v3

#### Scenario: config_snapshot 是 write-only observability
- **WHEN** Judge 写 `config_snapshot`
- **THEN** 其 SHALL 只读 Judge 自身 config 写入磁带（与 `state_snapshot` 同性质），SHALL NOT 引入任何决策路径对回放产物的读取

#### Scenario: 旧记录无 config_snapshot 向后兼容
- **WHEN** 回放遇到无 `config_snapshot` 字段的旧版本记录
- **THEN** 系统 SHALL fallback 到生产基线 config，不得报错或丢弃该记录
