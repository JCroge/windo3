## ADDED Requirements

### Requirement: Reviewer trade record symbol 归一为内部格式

ReviewerAgent 写入 `trade_record['symbol']` 与 `[复盘] 记录交易` 日志的 symbol SHALL 先经 `utils/symbol.py::to_internal()` 归一为内部 `BASE-USDT` 格式，不得把上游 payload 携带的原始格式（可能为 `BASE-USDT-SWAP` 或 ccxt `BASE/USDT:USDT`）原样落入。归一 MUST 在 reviewer 取 `symbol = msg.get('symbol') or payload.get('symbol')` 的各处入口统一施加（单点收口），契合 CLAUDE.md "跨 Agent symbol 用内部格式 BASE-USDT" 约定。

#### Scenario: 上游 -SWAP 格式被归一

- **WHEN** execution_result / pnl_resolved payload 携带 `XRP-USDT-SWAP`
- **THEN** reviewer 写入的 `trade_record['symbol']` 与 `记录交易` 日志均为 `XRP-USDT`

#### Scenario: 已是内部格式不变

- **WHEN** payload 携带 `XRP-USDT`
- **THEN** 归一后仍为 `XRP-USDT`（幂等）

#### Scenario: pnl_resolution upsert 不受影响

- **WHEN** `_apply_pnl_resolution` 按 entry_request_id/position_id upsert 已有 close 记录
- **THEN** 匹配键不依赖 symbol 格式，归一不破坏 upsert 关联

### Requirement: 边缘单 PnL 跟踪从权威 lifecycle 结算

`scripts/track_marginal60.py` 结算已实现 PnL 的数据源 SHALL 为权威 `data/live_position_lifecycle.json`（`total_realized_pnl` + reconcile 状态），而非 grep `agent_reviewer_*.log`。fill（judge `开仓成功`）与 lifecycle 记录 SHALL 都经 `to_internal` 归一 symbol 后按 symbol + `opened_at≈fill_ts` 时间邻近 join。observability-only write-only，不改 config、不下单。

#### Scenario: 格式不一致的已实现 PnL 正确结算

- **WHEN** 一笔边缘单的 lifecycle 记录 symbol 为 `ETH-USDT-SWAP`、fill 日志为 `ETH-USDT`
- **THEN** 经 `to_internal` 归一后两者 join 成功，正确结算其 `total_realized_pnl`（不再"未结算"）

#### Scenario: external_close 已 reconcile 的 PnL 被纳入

- **WHEN** 某 close 走 external_close、reviewer 未记"记录交易"日志，但 lifecycle 有 `total_realized_pnl` 且 `reconcile_status=matched`
- **THEN** 跟踪器从 lifecycle 结算该笔，不再因日志漏行而"未结算"

#### Scenario: 仍 pending 的不强行结算

- **WHEN** lifecycle 记录 `status` 未平仓或 `total_realized_pnl` 缺失/pending
- **THEN** 标"持仓中/未结算"，不伪造 PnL
