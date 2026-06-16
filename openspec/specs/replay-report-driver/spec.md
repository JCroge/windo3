## ADDED Requirements

### Requirement: 端到端被拒单反事实报表 driver
系统 SHALL 提供 driver 读取被拒影子单 `rejected_signal_events.jsonl`，对每条按其存续时段取价格 bars，经 `resolve_counterfactual` 解析后由 `build_cf_report` 汇成分桶报表。

#### Scenario: 端到端可运行
- **WHEN** driver 在有被拒单历史 + klines 数据时运行
- **THEN** 系统 SHALL 输出按 reject_reason×regime×side 分桶、经诚实 gate 的反事实报表，无需手工拼装 rows

#### Scenario: 取数窗口对齐 shadow 过期
- **WHEN** driver 解析某被拒单
- **THEN** 其 SHALL 取 `[created_at, created_at+24h]` 窗口的 bars（对齐 CounterfactualLedger shadow 24h 过期）

#### Scenario: 价格源双轨
- **WHEN** 某被拒单存续时段有 1s bar（`klines_1s.db`）
- **THEN** driver SHALL 优先用 1s bar 取价精度；缺则退化 `klines.db` 1m

#### Scenario: 缺数据降级
- **WHEN** 某被拒单时段无任何 klines 覆盖
- **THEN** driver SHALL 跳过该条并计数，不中断整体报表

### Requirement: driver observability-only
系统 SHALL 保证 driver 为离线分析工具，输出严禁被任何 gate/veto/halt/rank/daily-stop 读取做交易决策。

#### Scenario: driver 输出不进决策
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT 读取 driver 报表产物

### Requirement: 可回放记录过滤按内容而非 stale 标志
报表/方向驱动加载决策磁带时 SHALL 按记录内容判定可回放（`schema_version` 为当前版本 AND `tech_analysis` 非空），SHALL NOT 盲信写入时固化的 `replayable` 标志——旧版本空记录写入时即被标 `replayable=true`，修复不回改磁盘旧记录。

#### Scenario: 驱动过滤旧空记录
- **WHEN** `cf_direction_recommendation` 等驱动 `load_records` 加载磁带
- **THEN** 系统 SHALL 只收 `schema_version=='decision_replay_record.v2' AND tech_analysis 非空` 的记录，过滤掉 v1 旧空记录

#### Scenario: 不盲信 stale replayable
- **WHEN** 某记录 `replayable=true` 但 `schema_version` 为旧版本或 `tech_analysis` 为空
- **THEN** 系统 SHALL 排除该记录，不喂入回放/扫描（避免短路 hold 稀释结论）
