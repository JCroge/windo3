## Why

`scripts/track_marginal60.py` 8 个边缘单"未结算"，诊断（2026-06-20）出三层根因，主因是 **reviewer 的 symbol 格式不一致违反内部约定**：

- `agents/trading/reviewer.py:112/151/216` 取 `symbol = msg.get('symbol') or payload.get('symbol')`，**不经 `utils/symbol.py::to_internal()` 归一**——而该 helper 文档明确"所有 agent state dict 的 key 都应该用这个函数处理"，CLAUDE.md 红线也规定"跨 Agent symbol 用内部格式 `BASE-USDT`"。上游某 close 路径 leak `BASE-USDT-SWAP`，被原样落入 `trade_record['symbol']` 与 `[复盘] 记录交易` 日志。
- 后果：`记录交易` 日志格式混乱（`ETH-USDT-SWAP`/`UNI-USDT-SWAP`/`XRP-USDT-SWAP` 与 `XLM-USDT`/`XRP-USDT` 并存），`track_marginal60.py` 按精确字符串配对 fills（judge `开仓成功` 全 `BASE-USDT`）↔ PnL 失败 → ETH +0.86 / UNI −1.97 / XRP −0.58 **实际有 PnL 却被格式挡住**未结算；也是 XLM −7.76(跟踪器) vs −10.09(lifecycle) 对不上的根源。
- 次因：reviewer 漏记部分 close 的"记录交易"（external_close pending 未 finalize）；跟踪器选错数据源（grep 有损日志而非权威 `live_position_lifecycle.json`）。

## What Changes

- **① reviewer 入口 symbol 归一（根治 live 数据 bug，单点收口）**：`agents/trading/reviewer.py` 的 3 处 `symbol = msg.get(...)` 套 `to_internal(symbol)`，使 `trade_record['symbol']` 与 `记录交易` 日志恒为内部 `BASE-USDT`。对上游任何格式鲁棒，契合既有约定。
- **② `track_marginal60.py` 结算源改读权威 lifecycle**：从 grep `agent_reviewer_*.log` 改为读 `data/live_position_lifecycle.json`（`total_realized_pnl` 权威 + 统一键 + reconcile 状态）；fill 与 lifecycle 都经 `to_internal` 归一后按 symbol + `opened_at≈fill_ts` join。多 settle external_close 漏记的 close，并用 reconcile 后权威 PnL。
- **非目标**：不回填历史 `trade_history.json`（红线不改 data/ 用户数据，① 仅前向）；不逐个修上游 leak 的 publisher（reviewer 入口收口已对上游鲁棒）。

## Capabilities

### New Capabilities

- `reviewer-canonical-symbol`: reviewer trade record 与日志的 symbol 必须经 `to_internal` 归一为内部 `BASE-USDT`；边缘单 PnL 跟踪从权威 lifecycle 结算。

### Modified Capabilities

（无）

## Impact

- `agents/trading/reviewer.py`（3 处 symbol 取值套 `to_internal`，live 路径需回归）。
- `scripts/track_marginal60.py`（结算源改读 lifecycle.json，observability）。
- 测试：reviewer symbol 归一（混合格式入 → trade_record/日志恒 BASE-USDT）、tracker 从 lifecycle 正确 settle（含原未结算的 ETH/UNI/XRP）。
- 不动 data/ 历史数据；不改 close path / executor / realized_pnl_resolver。
