# 交易所真实已实现 PnL 账本验收文档

更新日期：2026-05-28  
关联需求：`docs/exchange_realized_pnl_ledger_prd.md`  
状态：OPEN

## 1. 验收目标

证明系统在外部平仓、交易所 SL/TP、partial reduce、手工平仓、账单延迟、API 失败等场景下：

- 不把估算 PnL 当作真实 PnL。
- 能从 OKX fills/bills 解析最终净已实现 PnL。
- 能把 pending 账本异步修正为 final。
- Reviewer/Judge/RiskGuard/Telegram 只在合适的 PnL 状态下消费数据。
- 同一笔交易重复对账不会重复计入。

## 2. 前置条件

| 条件 | 标准 |
|---|---|
| API 权限 | OKX Read 权限可访问 fills-history、bills、orders-history；testnet 需 demo trading key |
| 本地状态 | 测试前备份 `data/live_order_events.jsonl`、`data/live_position_lifecycle.json`、`data/trade_history.json` |
| 状态隔离 | live/testnet/paper 使用独立 namespace 或独立数据目录 |
| 时间同步 | 本机时间与 OKX server time 偏差不超过 5s |
| 测试标的 | 优先使用低成本高流动性 USDT-SWAP |
| 证据保存 | 每个 testnet case 保存 raw request、raw response、normalized resolution、ledger diff |

## 3. 自动化验收

### AC-A1 PnL 状态合同

构造 `execution_result`：

| 输入 | 期望 |
|---|---|
| final PnL | `result.pnl_is_final=true`，`result.pnl` 等于 `realized_pnl_net_usdt` |
| pending external close | `result.pnl_is_final=false`，`result.pnl` 为 null/缺失，`estimated_pnl` 单独存在 |
| mismatch | `pnl_status=mismatch`，不写 final trade history |

通过标准：

- Reviewer/Judge 的测试覆盖 `pnl_is_final=false` 时不记录策略学习结果。
- Telegram pending 文案不显示为“真实 PnL”。
- 旧版没有 `pnl_is_final` 的历史事件按兼容策略处理，但新事件必须带字段。

### AC-A2 Resolver 通过 fills-history 解析 final PnL

Mock OKX `private_get_trade_fills_history` 返回 close fills：

```json
[
  {
    "ordId": "close_1",
    "billId": "bill_1",
    "instId": "JTO-USDT-SWAP",
    "subType": "5",
    "posSide": "long",
    "fillPnl": "-1.5800",
    "fee": "-0.1500",
    "feeCcy": "USDT",
    "fillPx": "0.5438",
    "fillSz": "543",
    "fillTime": "1779922722000"
  }
]
```

通过标准：

- resolver 输出 `pnl_status=final`。
- `realized_pnl_net_usdt=-1.73`。
- `order_ids=["close_1"]`，`bill_ids=["bill_1"]`。
- 费用为负数时直接相加，不二次取反。

### AC-A3 bills 校验

Mock fills 与 bills 一致：

- fills: `fillPnl=-1.58`，`fee=-0.15`
- bills: 同 `billId` 或同 `ordId`，`pnl=-1.58`，`fee=-0.15`

通过标准：

- `pnl_source=okx_fills_history+okx_bills`。
- `match_confidence >= 0.95`。

Mock bills 与 fills 超过阈值不一致：

| local/fills | bills | threshold |
|---:|---:|---:|
| -1.73 | -2.30 | max(0.1, abs(exchange) * 0.05) |

通过标准：

- 输出 `pnl_status=mismatch`。
- 不覆盖已有 final PnL。
- 发布/返回 mismatch 明细。

### AC-A4 外部平仓 pending 不污染

模拟 OKX 查询失败：

- `private_get_trade_fills_history` 抛异常。
- `private_get_account_bills` 抛异常。
- 本地 position 有 `unrealized_pnl=-0.543` 或 `stop_loss` 可估算。

通过标准：

- `LiveLedger` 写入 `event_type=external_close`，`pnl_status=pending` 或 `estimated`。
- `estimated_pnl=-0.543` 可以记录，但 `realized_pnl_net_usdt` 为空。
- `ReviewerAgent.trade_history` 不新增 final 记录。
- `Judge` 不调用 `_archetype_cooldown.record_result()`。

### AC-A5 pending 后补账 upsert

步骤：

1. 先写一条 pending external close。
2. 后续 resolver 找到 final PnL。
3. 调用 `apply_pnl_resolution()`。
4. 重复调用一次相同 resolution。

通过标准：

- 第一次生成 correction event。
- lifecycle `total_realized_pnl` 更新为 final。
- `trade_history.json` 按 `entry_request_id` 或 `position_id` upsert，仅一条 close record。
- 第二次调用不改变累计 PnL，不新增重复 trade record。

### AC-A5a final resolution 严格幂等

构造一条 pending external close，final resolution 为：

```json
{
  "position_id": "JTO-USDT-SWAP-7e57abc2-long",
  "close_match_key": "match-1",
  "order_ids": ["close_1"],
  "bill_ids": ["bill_1"],
  "realized_pnl_net_usdt": -1.73,
  "pnl_status": "final"
}
```

连续调用两次 `LiveLedger.apply_pnl_resolution()`。

通过标准：

- 只存在一条 final correction，第二次返回 `status=existing` 或等价幂等结果。
- 不新增 standalone correction。
- lifecycle `total_realized_pnl=-1.73`，不得变为 `-3.46`。
- daily realized PnL 只计入 `-1.73` 一次。
- `pnl_resolved` 下游事件只导致 Reviewer/Judge 消费一次。

### AC-A5b pending resolution 保持可重试

步骤：

1. 先写一条 pending external close。
2. resolver 因 OKX API 延迟/失败返回 `pnl_status=pending`，原因 `exchange_data_not_ready`。
3. Reconciler 处理该结果。
4. 调用 `find_pending_external_closes()`。
5. 第二次 resolver 返回 final，再由 Reconciler 处理。

通过标准：

- 第 3 步不得调用 final correction 语义，不得 supersede 原 pending event。
- pending event 的 `attempt_count/last_attempt_at/next_retry_at/last_pending_reason` 被更新。
- 第 4 步仍能查到同一 `position_id` 的 pending close。
- 第 5 步能成功生成 final correction，并清理 retry 待办状态。
- retry schedule 符合 `10s -> 30s -> 2m -> 10m -> 30m`，24 小时未 final 时进入 `needs_manual_reconcile`。

### AC-A6 partial reduce + full close

构造生命周期：

- open 100 USDT margin。
- reduce 50% final PnL `+1.20`。
- external full close pending。
- resolver 后补 final PnL `-0.70`。

通过标准：

- lifecycle `total_realized_pnl=+0.50`。
- reduce 和 close 两个事件都有独立 `order_id/bill_id`。
- full close 修正不覆盖 reduce 事件。
- resolver fallback 候选必须排除已归属 partial reduce 的 `order_id/bill_id`。
- 若 fallback 只靠 `side + time window` 会同时命中 reduce 和 full close，本 case 必须失败并返回 `ambiguous_close_match`，不得写 final。

### AC-A7 多候选不猜测

在同一 symbol 同一时间窗口内构造两组 close fills，且都满足 side/posSide/window。

通过标准：

- resolver 返回 `pnl_status=pending` 或 `mismatch`。
- `warnings` 包含 `ambiguous_close_match`。
- 不选择任意一组 fills 写 final。

### AC-A8 funding 归属

构造 lifecycle 跨 funding 时间：

- close fill net before funding `+0.80`。
- account bills 中 symbol/posSide/lifecycle window funding `-0.05`。

通过标准：

- `funding_usdt=-0.05`。
- `realized_pnl_net_usdt=+0.75`。
- funding bill id 被记录。

无法唯一归属 funding：

- 通过标准：funding 进入 `funding_unattributed`，单笔 PnL 不强行 final，或 final 中明确 `funding_included=false`。

### AC-A9 fee currency 非 USDT

Mock close fill `feeCcy=JTO`。

通过标准：

- 如果没有可靠换算源，`pnl_status=pending_fx`。
- 不写 final trade history。
- 告警包含 fee currency。

### AC-A10 Backfill dry-run

执行：

```bash
python3 scripts/backfill_realized_pnl.py \
  --since 2026-05-28T00:00:00+08:00 \
  --until 2026-05-28T08:00:00+08:00 \
  --symbol JTO-USDT-SWAP \
  --dry-run
```

通过标准：

- 输出每个 pending/estimated close 的旧 PnL、新候选 PnL、delta、匹配来源。
- dry-run 不修改任何 data 文件。
- 对 JTO 三笔样例必须能展示目标实际净结果：`+0.16`、`+0.28`、`-1.73`，若本地没有交易所 raw 数据则标记 `needs_exchange_data`。

### AC-A11 Backfill apply

执行同参数去掉 `--dry-run`。

通过标准：

- 只追加 correction event 或 upsert trade history，不删除旧 JSONL。
- 重复运行 apply 不改变最终累计值。
- 输出 summary：`resolved/pending/mismatch/skipped`。

### AC-A12 Reconciler 自动修正

Mock `Reconciler.check_recent_bills()` 找到 pending 对应 bills。

通过标准：

- 生成 `pnl_resolved` 事件。
- `run_and_report()` 对自动修正输出 summary。
- mismatch 仍告警，不自动覆盖 final。
- resolver 返回 `pending` 时，Reconciler 只更新 retry metadata，不调用 `apply_pnl_resolution()`。
- Reconciler 不允许用 `opened_at=0` 重建 pending close；缺失 lifecycle 时间窗时输出 `missing_lifecycle_window` 并保持 pending。
- final/mismatch 事件必须包含 `resolution_id` 或等价稳定幂等键。

### AC-A13 position identity 与入场归因传播

构造从 open 到 external close pending，再到 final resolution 的完整事件链。

通过标准：

- `LiveLedger.record_open()` 返回的 `position_id` 被保存到持仓对象，并出现在后续 `execution_result.result.position_id`。
- pending external close 持久化 `position_id/opened_at/closed_at/sl_algo_id/sl_algo_clord_id/tp_algo_id/tp_algo_clord_id`。
- pending 和 final 事件都携带原始 `entry_attribution`，至少包含 archetype/entry_bucket 或本项目等价字段。
- `pnl_resolved` 不通过当前行情重新推断入场归因。
- Reviewer/Judge 能按 `position_id` 或 `entry_request_id` upsert 同一 lifecycle，不生成孤儿记录。

### AC-A14 Resolver 外部平仓匹配合同

构造同一 symbol、同一时间窗口内的三类成交：

- 当前 lifecycle 的保护性 SL/TP close fill，带 `algoId/algoClOrdId`。
- 同 symbol 的 partial reduce fill，方向相同但 `order_id/bill_id` 已归属 reduce event。
- 另一笔 position 的 close fill，时间相近但 `position_id/entry_request_id` 不同。

通过标准：

- resolver 优先使用 `order_id/ordId/algoId/algoClOrdId/clOrdId` 精确匹配当前 lifecycle。
- 时间窗口 fallback 必须同时校验 `symbol/side/posSide/fillTime/remaining_size`。
- 已归属 reduce event 的 `order_id/bill_id` 出现在 `excluded_order_ids/excluded_bill_ids`，不得进入 full close PnL。
- 多候选无法唯一归属时返回 `pending` 或 `mismatch`，不得写 final。
- resolution 输出包含 `match_rule/matched_order_ids/matched_bill_ids/close_match_key`，可用于幂等。

## 4. 下游消费验收

### AC-D1 ReviewerAgent

输入事件：

| 事件 | 期望 |
|---|---|
| `closed_externally` + `pnl_is_final=false` | 不追加 final trade record，可追加 pending index |
| `pnl_resolved` + final | upsert 一条 final trade record |
| 重复 `pnl_resolved` | 不重复 |
| mismatch | 不改 final，记录告警 |

通过标准：

- `trade_history.json` 中 final 交易每个 `entry_request_id/position_id` 只有一条。
- `_calculate_daily_pnl()` 只统计 final PnL。

### AC-D2 Judge

输入 pending close：

- 通过标准：清理 open position 状态，但不更新 archetype cooldown/EV bucket，不增加连续 SL 次数。

输入 final resolution：

- 通过标准：只记录一次 archetype result。
- 如果 pending 时已清理仓位，final resolution 不重新触发开/平仓状态变更。
- final TP 或 `exchange_tp` 不得计入 SL cooldown。
- final loss 只有在 close cause 或订单证据明确为 `exchange_sl` 时才计入 SL hit，且重复 `pnl_resolved` 只计一次。
- pending close cause 必须是 `external_pending` 或 `exchange_unknown_pending`，不得使用 `exchange_sl_tp_triggered` 直接触发 SL 分支。

### AC-D3 RiskGuard

通过标准：

- pending 估算亏损可用于保守减风险或禁止扩仓。
- daily hard stop 的 final realized PnL 只使用 final events。
- 若 pending 超过 30 分钟且 estimated loss 触及 hard stop，触发 `pnl_unconfirmed_risk_halt`，文案必须说明“未确认”。

### AC-D4 TelegramNotifier

通过标准：

- Telegram 订阅并处理 `pnl_resolved`、`pnl_mismatch`。
- pending close 中 `result.pnl=null` 时不得发生 `pnl > 0` 类型错误。
- pending close 文案示例：`平仓 JTO-USDT-SWAP，PnL 待交易所账单确认，估算 -0.54 USDT`。
- final correction 文案示例：`PnL 已校正 JTO-USDT-SWAP: -0.54 -> -1.73 USDT`。
- mismatch 文案包含本地/交易所差异和人工处理提示。
- daily summary 只统计 final PnL；pending 估算可单独显示为“未确认估算”，不得混入日盈亏。

## 5. OKX Testnet 验收矩阵

### T0 API 可用性

调用：

- `GET /api/v5/trade/fills-history?instType=SWAP`
- `GET /api/v5/account/bills?instType=SWAP`
- `GET /api/v5/trade/orders-history?instType=SWAP`

通过标准：

- 三个接口均返回 `code=0` 或明确可解释的空数据。
- 日志记录 raw request params，不打印密钥。

### T1 普通开仓 + 主动平仓

操作：

1. 开最小 size long。
2. 主动 market close。
3. resolver 按 close `ordId` 查询 fills/bills。

通过标准：

- final PnL 与 OKX 页面/账单一致，误差 <= 0.01 USDT 或 <= 1 tick fee rounding。
- ledger event `source` 不是 `estimated`。

### T2 交易所 SL 外部平仓

操作：

1. 开最小 size long。
2. 挂独立 SL algo。
3. 等待或人工触发 SL，使本地只通过 `sync_positions()` 发现仓位消失。

通过标准：

- 第一条 close 可以是 pending，但 10 分钟内必须 resolved。
- resolver 能通过 `algoId/algoClOrdId` 或时间窗口匹配 close fills。
- final PnL 不使用 `_estimate_close_pnl()`。

### T3 交易所 TP 外部平仓

同 T2，但触发 TP。

通过标准：

- `exit_reason` 可归因为 exchange TP 或 external close。
- final PnL 为正/负都按账单。
- 不把 TP 当作 SL hit 计入策略冷却。

### T4 partial reduce 后外部 close

操作：

1. 开仓。
2. 本地 partial reduce 50%。
3. 剩余仓位由交易所 SL/TP 外部关闭。

通过标准：

- reduce final PnL 和 external close final PnL 分别记录。
- lifecycle 总 PnL 正确。
- trade history 只生成一条完整 lifecycle 结果或明确两条 reduce/close 事件，但汇总不重复。

### T5 API 延迟/失败

通过 mock 或临时网络隔离让 resolver 首次失败，随后恢复。

通过标准：

- 首次发布 pending。
- 后续 retry/backfill resolved。
- pending 期间不污染 Reviewer/Judge。

### T6 多 symbol 并发

同时对两个 symbol 触发 external close。

通过标准：

- position_id 不串。
- JSONL/lifecycle 没有交叉覆盖。
- Reconciler 按 symbol/window 聚合正确。

## 6. 回归命令

必须通过：

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
python3 -m pytest -q test_live_ledger.py test_reconciliation.py test_execution_result_contract.py test_judge_close_cause.py
python3 -m pytest -q test_paper_live_isolation.py test_lifecycle_pnl.py
```

新增或更新测试建议：

```bash
python3 -m pytest -q test_exchange_realized_pnl_resolver.py
python3 -m pytest -q test_external_close_pnl_contract.py
python3 -m pytest -q test_realized_pnl_backfill.py
python3 -m pytest -q test_live_ledger_idempotency.py
python3 -m pytest -q test_external_close_retry_pending.py
python3 -m pytest -q test_telegram_pnl_pending.py
```

本轮实现审查对应的最小回归必须覆盖：

- 同一 `final` resolution 连续 apply 两次，lifecycle/daily PnL 不重复计数。
- 首次 resolver `pending` 后，原 pending close 仍能被 `find_pending_external_closes()` 查到并在第二次 final 时修正。
- partial reduce 的 `order_id/bill_id` 不会被 full close fallback 重复归属。
- resolver 精确匹配优先于时间窗口 fallback，多候选时不猜测。
- pending external close 不触发 Judge SL cooldown，final TP 不触发 SL cooldown。
- Telegram 对 `result.pnl=null` 不崩溃，且 final correction 能补发。

全量通过标准：

```bash
python3 -m pytest -q
```

## 7. 生产观察期

上线后 24 小时 shadow 运行：

| 指标 | 通过标准 |
|---|---|
| external close resolved rate | >= 95% 在 10 分钟内 resolved |
| pending > 30m | 0，或全部有告警 |
| final PnL 与 OKX 页面差异 | <= 0.01 USDT 或有费用/资金费解释 |
| duplicate final records | 0 |
| Reviewer/Judge 使用 estimated PnL | 0 |
| Reconciler query failure | 连续失败不超过 3 次；超过自动告警 |

## 8. 完成定义

本需求可关闭必须满足：

- P0/P1 自动化验收全 PASS。
- 至少 T0、T1、T2、T5 testnet PASS。
- JTO 事件完成 dry-run 回放，文档记录旧 PnL、新 PnL、delta 和数据来源。
- `data/trade_history.json` 不再新增 `pnl_status != final` 的最终记录。
- `live_order_events.jsonl` 中所有 external close 都有 `pnl_status` 和 `pnl_source`。
- 运行文档明确说明如何处理 pending/mismatch。
