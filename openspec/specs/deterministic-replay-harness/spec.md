## ADDED Requirements

### Requirement: 隔离回放构造真实 Judge
系统 SHALL 用 `MultiJudge.__new__` 绕过 `__init__` 构造 Judge，从 record 的状态快照白名单还原 `self.*`，并复用真实 `_make_decision` 决策逻辑，不重写评分/gate。

#### Scenario: 状态还原
- **WHEN** 给定一条带状态快照的 record
- **THEN** harness SHALL 还原快照内全部白名单 `self.*` 字段（list 还原回 set 等），使 Judge 看到与历史一致的隐藏状态

#### Scenario: 复用真实决策代码
- **WHEN** harness 执行回放
- **THEN** 其 SHALL 调用真实 `MultiJudge._make_decision`，SHALL NOT 另写第二份评分/gate/RR-floor 实现

### Requirement: 回放确定性 mock
系统 SHALL mock 决策路径全部已知非确定性来源，使同一 record 回放结果确定。

#### Scenario: 时间确定
- **WHEN** 回放执行
- **THEN** `time.time()` SHALL 返回 record 的 timestamp，使 cooldown/TTL/deferred timeout 判定确定

#### Scenario: 不触交易所
- **WHEN** 回放需要余额
- **THEN** 系统 SHALL 用快照 `_available_balance` 恢复，余额刷新打桩为 no-op，SHALL NOT 调真实交易所

#### Scenario: LLM 复用内联
- **WHEN** 回放走 LLM 决策路径
- **THEN** 系统 SHALL 注入 record 的 `llm_output_inline`，SHALL NOT 重新调用 LLM

#### Scenario: publish 截获
- **WHEN** 回放中 Judge 调用 `publish`
- **THEN** harness SHALL override 为 capture，收集 payload 而非发真实总线消息

### Requirement: golden-master 决策比对
系统 SHALL 比对回放输出与 record 的 `trade_decision_output`：离散字段严格相等，plan 连续字段允许极小相对容差。

#### Scenario: 严格字节级字段（决定决策）
- **WHEN** 比对回放与历史决策
- **THEN** `action`/`confidence`/`dispatch_path`/`entry_type`/`slot_type`/`is_probe`/`is_low_rr`/`short_gate_decision`/`short_gate_reason`/`rr_policy`/`rr_floor_used`/`entry_position_status`/`entry_position_block_reason`/`blocked_by` SHALL 严格相等，任一不等即判 mismatch

#### Scenario: 连续字段容差
- **WHEN** 比对 plan 的 `size_usdt`/`entry_ref`/`stop_loss`/`take_profit`（逐元素）/`leverage`
- **THEN** 系统 SHALL 允许 <0.5% 相对误差，超出即判 mismatch

#### Scenario: 自由文本仅信息不判负
- **WHEN** 比对 `reasoning`/`key_factors`/`risk_warnings`（LLM 自由文本透传）
- **THEN** 系统 SHALL 记录 diff 但 SHALL NOT 因其不一致判 mismatch（golden-master 钉决策逻辑，不钉自由文本）

#### Scenario: 复现不重算 PnL
- **WHEN** 回放经过 EV gate
- **THEN** 系统 SHALL 用快照 `_recent_wins`/`_total_completed_trades` 还原值，SHALL NOT 重算 realized PnL

### Requirement: harness observability-only write-only
系统 SHALL 保证回放 harness 为离线工具，严禁被任何 gate/veto/halt/rank/daily-stop 读取或进入生产决策链路。

#### Scenario: harness 不进生产链路
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT import 或调用回放 harness
