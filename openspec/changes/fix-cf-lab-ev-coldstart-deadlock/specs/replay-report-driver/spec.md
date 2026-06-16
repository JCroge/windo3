## ADDED Requirements

### Requirement: 可回放记录过滤按内容而非 stale 标志
报表/方向驱动加载决策磁带时 SHALL 按记录内容判定可回放（`schema_version` 为当前版本 AND `tech_analysis` 非空），SHALL NOT 盲信写入时固化的 `replayable` 标志——旧版本空记录写入时即被标 `replayable=true`，修复不回改磁盘旧记录。

#### Scenario: 驱动过滤旧空记录
- **WHEN** `cf_direction_recommendation` 等驱动 `load_records` 加载磁带
- **THEN** 系统 SHALL 只收 `schema_version=='decision_replay_record.v2' AND tech_analysis 非空` 的记录，过滤掉 v1 旧空记录

#### Scenario: 不盲信 stale replayable
- **WHEN** 某记录 `replayable=true` 但 `schema_version` 为旧版本或 `tech_analysis` 为空
- **THEN** 系统 SHALL 排除该记录，不喂入回放/扫描（避免短路 hold 稀释结论）
