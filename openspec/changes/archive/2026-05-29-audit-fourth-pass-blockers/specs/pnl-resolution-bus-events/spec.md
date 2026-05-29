## ADDED Requirements

### Requirement: 所有 pnl_resolved/pnl_mismatch 发布点必须透传 final close cause 与证据

`pnl_resolved` 与 `pnl_mismatch` 总线事件由三个生产者发布：`agents/trading/executor.py:_resolve_external_close_async`、`agents/trading/executor.py:_run_reconciliation`（消费 `Reconciler.auto_resolve_pending` summary）、`utils/reconciliation.py:Reconciler.auto_resolve_pending` 自身。三者发布时 MUST 携带同一字段集，使 Judge / Reviewer / 其他订阅者 SHALL 可以稳定判定 final close cause 与证据。

#### Scenario: _resolve_external_close_async 透传 final_close_cause + close_evidence
- **WHEN** Resolver 返回 `{close_cause: "exchange_sl", final_close_cause: "exchange_sl", is_strategy_stop: True, close_evidence: {match_rule: "sl_algo_id_exact", confidence: 1.0, ...}}`
- **AND** `_resolve_external_close_async` 发布 `pnl_resolved`
- **THEN** payload 必须含 `final_close_cause` / `close_evidence` 两个键，值与 resolution 一致
- **AND** payload `is_strategy_stop` 必须等于 resolution 的 `is_strategy_stop`

#### Scenario: Reconciler.auto_resolve_pending summary 携带四字段集
- **WHEN** `Reconciler.auto_resolve_pending()` 处理 pending 升级
- **THEN** 返回的 summary dict 必须包含 `close_cause` / `final_close_cause` / `is_strategy_stop` / `close_evidence` 四个字段（值取自 resolution）

#### Scenario: _run_reconciliation 发布 pnl_resolved 透传四字段集
- **WHEN** `_run_reconciliation` 收到 summary 并发布 `pnl_resolved` / `pnl_mismatch`
- **THEN** 发布的 payload 必须包含 `final_close_cause` / `close_evidence`（额外字段，与已有 `close_cause` / `is_strategy_stop` 共存）

#### Scenario: 异常路径无 correction 时跳过发布并告警
- **WHEN** Resolver 抛异常或返回 `pending` / `pending_fx` 等 non-final/non-mismatch 状态，且 `correction is None`
- **THEN** 发布点 MUST NOT 发布 `pnl_resolved` / `pnl_mismatch`（避免发出无 `correction_event_id` 的脏事件）
- **AND** MUST 调用 `logger.warning` 记录跳过原因（含 symbol / position_id / status）

### Requirement: pnl_resolved/pnl_mismatch 必须携带 resolution_id 幂等键

每条 `pnl_resolved` / `pnl_mismatch` 事件 MUST 含 `resolution_id` 字段，由唯一函数 `make_resolution_id(resolution, correction)` 生成，SHALL 用于下游订阅者去重。

#### Scenario: resolution_id 优先使用 correction_event_id
- **WHEN** correction 字典含 `event_id`（写 ledger correction 成功时）
- **THEN** `resolution_id` 必须以 `corr:` 前缀且包含 `correction.event_id`

#### Scenario: 没有 correction.event_id 时回退到 supersedes_event_id
- **WHEN** correction 含 `supersedes_event_id` 但无 `event_id`
- **THEN** `resolution_id` 必须以 `sup:` 前缀且包含 `supersedes_event_id`

#### Scenario: 都无时回退 close_match_key
- **WHEN** correction 缺失或不含上述两项，但 resolution 含 `close_match_key`
- **THEN** `resolution_id` 必须以 `key:` 前缀

#### Scenario: 兜底使用 position_id + order_ids
- **WHEN** 上述三者均缺失
- **THEN** `resolution_id` 必须以 `pos:` 前缀，包含 `position_id` 与排序后的 `order_ids` 拼接

#### Scenario: 同一 resolution 多次发布产出相同 resolution_id
- **WHEN** 同一 resolution 经 `_resolve_external_close_async` 与 `_run_reconciliation` 各发布一次
- **THEN** 两次的 `resolution_id` 字段必须相等（基于相同 correction 输入时）

### Requirement: 账本类下游订阅者必须按 resolution_id 幂等去重

账本类 `pnl_resolved` 订阅者（Judge / Reviewer 等会写入 trade_history、计 SL hit、修改 archetype cooldown 的消费者）MUST 在收到事件时按 `resolution_id` 去重，避免同一对账结果被升级两次（例如 `_resolve_external_close_async` 与 `_run_reconciliation` 都触发了同一 pending 的升级）。当 payload 缺失 `resolution_id` 时订阅者 SHALL fail-safe 回退到现有 `correction_event_id` / `supersedes_event_id` 去重逻辑。

通知类订阅者（如 TelegramNotifier）MAY 保留独立的时间窗去重（如现有 `_close_notify_cache` 60s window），不强制接入 `resolution_id` 去重，以避免缓存交叉污染。

#### Scenario: 账本类同一 resolution_id 第二次到达被忽略
- **WHEN** Judge 或 Reviewer 已处理过 `resolution_id="corr:E-123"` 的 `pnl_resolved`
- **AND** 再次收到含同一 `resolution_id` 的 `pnl_resolved`
- **THEN** MUST NOT 重复升级 trade_history / MUST NOT 重复计 SL hit / MUST NOT 重复 record archetype cooldown

#### Scenario: 缺失 resolution_id 时按现有逻辑处理
- **WHEN** 订阅者收到 `pnl_resolved` 但 payload 不含 `resolution_id`（向后兼容）
- **THEN** 订阅者按现有 `correction_event_id` / `supersedes_event_id` 去重逻辑处理，MUST NOT 抛错

#### Scenario: Telegram 不强制 resolution_id 去重
- **WHEN** TelegramNotifier 收到 `pnl_resolved`
- **THEN** 现有 `_close_notify_cache` 60s window 仍然生效
- **AND** MAY 不维护 `_seen_resolution_ids` 集合（不被本 spec 强制要求）
