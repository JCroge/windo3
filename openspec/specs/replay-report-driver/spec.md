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
