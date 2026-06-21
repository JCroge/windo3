## 高层架构决策（深度技术设计见 comet-design 的 Superpowers Design Doc）

### 根因

```
上游某 close 路径 leak BASE-USDT-SWAP (违反 "跨 Agent 用 BASE-USDT" 约定)
  → reviewer.py:112/151/216 `symbol = msg.get('symbol') or payload.get('symbol')` 不归一
    → trade_record['symbol'] + "记录交易" 日志格式混乱
      → track_marginal60 grep 精确字符串配对失败 → 8 未结算(ETH/UNI/XRP 实际有 PnL)
```

### 方案：消费侧收口归一 + 跟踪器读权威源

**① reviewer 入口 `to_internal` 收口**：3 处 `symbol = ...` 之后立即 `symbol = to_internal(symbol)`。消费侧防御——对上游任何格式鲁棒，无需逐个排查/修每个 leak 的 publisher。`to_internal` 已是 canonical helper（`SOL-USDT-SWAP`/`SOL/USDT:USDT`/`SOL-USDT` 全 → `SOL-USDT`，幂等）。

**② track_marginal60 读 lifecycle**：
- fill 仍从 judge `开仓成功` 取（symbol+ts），但归一。
- 结算源从 `agent_reviewer_*.log` 的 `记录交易` grep 改为 `data/live_position_lifecycle.json`：遍历 lifecycle 记录（每条有 `symbol`/`opened_at`/`closed_at`/`status`/`total_realized_pnl`/`reconcile_status`），归一 symbol，按 symbol + `opened_at≈fill_ts`（容差窗，如 ±300s）join fill。
- `total_realized_pnl` 是权威 reconcile 后值（解决 −7.76 vs −10.09）；external_close 漏记日志的也能 settle。

### 关键决策

1. **消费侧收口 vs 上游逐个修**：选消费侧（reviewer 入口 + tracker 读时双重归一）。理由：`to_internal` 幂等、契约文档支持"所有 key 都该过它"、对未知/未来 leak 鲁棒；逐个修上游 publisher 是 rabbit hole 且无法保证抓全。仅记录"观察到上游 -SWAP leak"供后续可选根治。
2. **不回填历史 trade_history.json**：红线"不改 data/ 用户数据"；① 前向归一已足够，历史分析读时归一即可。
3. **tracker join 容差**：fill_ts（judge 开仓成功）与 lifecycle.opened_at 可能差几秒（fill 日志 vs lifecycle 落库时点），用时间邻近窗 + 同 symbol + 同 side 匹配最近一条；多 fill 同 symbol 按时序配对。
4. **安全/回归**：reviewer 是 live 路径——`trade_record['symbol']` 被 segmented metrics / 分桶消费，归一为统一格式只会提升一致性；`_apply_pnl_resolution` 按 request_id/position_id upsert 不依赖 symbol，安全。需跑 reviewer 既有测试回归。

### 边界条件

| 情形 | 处理 |
|---|---|
| payload symbol 缺失/None | `to_internal(None)` 须 fail-safe（返回原值或空，不抛）；reviewer 既有 `or` 兜底保留 |
| lifecycle 无对应 opened_at 窗内记录 | 该 fill 标"未结算"（真未平或无 lifecycle） |
| lifecycle total_realized_pnl 为 None/pending | 标"未结算"，不伪造 |
| 同 symbol 多 fill | 按时序配对最近的 lifecycle 记录，避免重复消费 |

### 非目标

- 不改 close path / executor / realized_pnl_resolver（不动 PnL 来源，只改 reviewer 落记格式 + tracker 读源）。
- 不回填历史数据。
- 不逐个根治上游 leak publisher（消费侧收口已覆盖）。
