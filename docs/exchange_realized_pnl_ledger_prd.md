# 交易所真实已实现 PnL 账本需求文档

更新日期：2026-05-28  
状态：OPEN  
关联验收：`docs/exchange_realized_pnl_ledger_acceptance.md`  
触发事件：2026-05-28 JTO-USDT-SWAP 三笔外部平仓，本地 `trade_history` 记录 `+1.3255/+2.7175/-0.543`，实际账户净结果为 `+0.16/+0.28/-1.73`，本地估算污染复盘和 EV。

## 1. 背景

当前外部平仓链路如下：

1. `agents/trading/executor.py::_notify_removed_positions()` 发现 `sync_positions()` 后本地仓位已从交易所消失。
2. `_get_external_close_pnl()` 调用 `LiveLedger.record_external_close()` 尝试查最近平仓订单。
3. `utils/live_ledger.py::_fetch_recent_close_order()` 使用 `exchange.fetch_orders()` 查询，OKX/CCXT 当前日志显示 `fetchOrders() is not supported`。
4. 查询失败后 `record_external_close()` 写入 `source=estimated`、`reconcile_status=pending`。
5. `_get_external_close_pnl()` 继续退回 `_estimate_close_pnl()`，用最后一次 `unrealized_pnl` 或 `stop_loss` 价格估算。
6. `execution_result` 把估算值放入 `result.pnl`，Reviewer/Judge/Telegram/Risk 继续当作真实 PnL 使用。

直接后果：

- `data/trade_history.json` 记录非真实 PnL。
- `ReviewerAgent` 的 daily PnL、连续亏损、策略衰减判断失真。
- `Judge` 的 archetype cooldown 和 EV bucket 可能用错误盈亏训练。
- Telegram 展示的平仓盈亏和交易所账户不一致，降低人工复盘可信度。
- 后续 Reconciler 只告警不修账，无法把 `pending` 事件升级为真实结果。

## 2. 目标

1. 外部平仓、交易所 SL/TP、条件单触发、手工平仓、清算/ADL 等场景的已实现 PnL，必须以交易所成交/账单为权威。
2. 任何估算 PnL 都不得进入策略学习、EV、胜率、archetype cooldown、daily hard stop 的最终账本。
3. 支持先发布“仓位已关闭、PnL 待确认”，再异步补账并回传最终 PnL。
4. `trade_history.json`、`live_order_events.jsonl`、`live_position_lifecycle.json` 必须能幂等修正，不重复计入同一笔平仓。
5. 对账结果不能只告警；当交易所账单可验证时，应自动生成修正事件并回写下游。
6. 兼容现有 `execution_result.v2` 消费者，逐步引入 PnL 质量字段，不要求一次性重写消息总线。

## 3. 非目标

- 不重写全部执行器。
- 不把 PaperExecutor 的模拟 PnL 与实盘账本合并。
- 不用本地 K 线回推真实成交价。
- 不自动修改 OKX 账户持仓模式。
- 不承诺所有历史旧账一次性修复；本需求提供可重复执行的 backfill 工具和验收样例。

## 4. 权威来源

OKX 官方接口能力参考：

- `GET /api/v5/trade/fills-history`：最近 3 个月成交明细，支持 `instType`、`instId`、`ordId`、`begin`、`end`、`limit`；返回 `ordId`、`clOrdId`、`billId`、`fillPx`、`fillSz`、`fillPnl`、`fee`、`feeCcy`、`posSide`、`subType`、`fillTime`。
- `GET /api/v5/account/bills`：最近 7 天账户账单，支持 `instType`、`instId`、`ccy`、`begin`、`end`；返回 `billId`、`ordId`、`pnl`、`fee`、`balChg`、`fillTime`、`subType`。
- `GET /api/v5/trade/orders-history`：最近 7 天订单历史，可作为 `avgPx`、`accFillSz`、`algoId`、`algoClOrdId`、`state=filled` 的辅助来源。
- WebSocket `orders` channel：推送 `fillPnl`、`fillFee`、`ordId`、`algoId`、`algoClOrdId`、`fillTime`，可作为低延迟来源，但 REST 账单仍是最终校验来源。

本项目优先级：

| 优先级 | 来源 | 是否可作为最终 PnL | 用途 |
|---|---|---:|---|
| P0 | OKX bills + fills matched | 是 | 最终净 PnL、费用、资金费、审计 |
| P1 | OKX fills-history only | 是，需费用币种为 USDT 或可换算 | 成交级平仓 PnL |
| P2 | OKX orders-history / WS orders | 否，除非含完整 fillPnl/fee 且后续 bills 校验通过 | 快速定位 ordId、algoId |
| P3 | 本地 `unrealized_pnl` / stop_loss 估算 | 否 | Telegram 临时展示、风险保守参考 |

## 5. 数据口径

### 5.1 净已实现 PnL

统一字段：`realized_pnl_net_usdt`

计算口径：

```text
realized_pnl_net_usdt =
  sum(close_fill.fillPnl)
  + sum(all_lifecycle_trade_fee_in_usdt)
  + sum(funding_fee_in_usdt_during_lifecycle)
  + sum(realized_reduce_pnl_net_usdt)
```

说明：

- OKX `fillPnl` 用于平仓成交收益；费用字段通常为负数，净值直接相加。
- 如果 `feeCcy != USDT` 且无法可靠换算，事件保持 `pnl_status=pending_fx`，不得标记 final。
- funding 账单按 `symbol + posSide + opened_at <= ts <= closed_at` 归属。无法归属时单独记录 `funding_unattributed`，不强行摊入单笔交易。
- partial reduce 先产生部分已实现 PnL，full close 时 lifecycle 总 PnL 为所有 reduce/close/funding/fee 的和。

### 5.2 PnL 状态

| 字段 | 取值 | 说明 |
|---|---|---|
| `pnl_status` | `final` | 已由交易所成交/账单确认，可进入学习和风控最终账本 |
| `pnl_status` | `pending` | 仓位已关闭，但尚未拿到交易所 PnL |
| `pnl_status` | `estimated` | 只有本地估算，不能进入最终账本 |
| `pnl_status` | `mismatch` | 本地成交与账单不一致，需人工或下一轮修复 |
| `pnl_status` | `pending_fx` | 费用币种无法换算，不能 final |

硬规则：

- `pnl_status != final` 时，`ReviewerAgent` 不追加最终 trade record。
- `pnl_status != final` 时，`Judge` 不更新 archetype cooldown、EV bucket、连续 SL 亏损统计。
- `pnl_status != final` 时，`Telegram` 必须展示“待确认/估算”，不能写成真实 PnL。
- `pnl_status != final` 时，`RiskGuard` 可读取 `estimated_pnl` 做保守风险参考，但不得写入 final daily realized PnL。

## 6. 技术路线

### 6.1 新增 PnL Resolver

新增模块建议：

```text
utils/realized_pnl_resolver.py
```

核心类：

```python
class RealizedPnlResolver:
    def resolve_external_close(self, position_snapshot: dict, close_window: dict) -> dict:
        ...

    def resolve_by_order_id(self, symbol: str, order_id: str, position_id: str = "") -> dict:
        ...

    def backfill_pending(self, since_ts: float, until_ts: float = None) -> list[dict]:
        ...
```

返回结构：

```json
{
  "pnl_status": "final",
  "pnl_source": "okx_fills_history+okx_bills",
  "position_id": "JTO-USDT-SWAP-7e57abc2-long",
  "entry_request_id": "20260527-JTO-36c8890c",
  "entry_attribution": {
    "archetype": "breakout_pullback",
    "entry_bucket": "high_conviction",
    "strategy_version": "judge.v2"
  },
  "symbol": "JTO-USDT-SWAP",
  "side": "long",
  "pos_side": "long",
  "opened_at": 1779922606.49,
  "closed_at": 1779922722.27,
  "order_ids": ["360442..."],
  "algo_ids": ["3604423910009618432"],
  "bill_ids": ["..."],
  "gross_close_pnl_usdt": -1.58,
  "fee_usdt": -0.15,
  "funding_usdt": 0.0,
  "realized_pnl_net_usdt": -1.73,
  "avg_exit_price": 0.5438,
  "closed_size_contracts": 543.0,
  "match_confidence": 0.98,
  "warnings": []
}
```

匹配合同：

1. 精确匹配优先级高于时间窗口匹配：`order_id/ordId`、`algoId`、`algoClOrdId`、`clOrdId` 任一能稳定关联当前 `position_id` 时，必须只使用该组 close fills/bills。
2. 时间窗口 fallback 必须同时满足 `symbol`、`side`、`posSide`、`fillTime in [opened_at, closed_at + grace]`、平仓方向、剩余可平 size 约束。
3. fallback 不能只靠 `side + time window`。若同窗口存在多组候选，必须返回 `pending` 或 `mismatch`，并带 `ambiguous_close_match`，不得猜测。
4. 已归属到 partial reduce 或其他 lifecycle event 的 `order_id/bill_id` 必须从 full close 候选中排除。
5. resolver 输出必须带 `matched_order_ids`、`matched_bill_ids`、`excluded_order_ids`、`excluded_bill_ids`、`match_rule`，用于审计和幂等键。

### 6.2 外部平仓发布改造

修改 `agents/trading/executor.py::_notify_removed_positions()`：

1. 拿到 `removed position_snapshot` 后先调用 resolver。
2. 如果 resolver 返回 `final`：
   - `execution_result.result.pnl = realized_pnl_net_usdt`
   - `result.pnl_status = final`
   - `result.pnl_source = okx_fills_history+okx_bills`
3. 如果 resolver 返回 `pending/estimated/mismatch`：
   - 仍发布 `closed_externally`，让持仓状态及时释放。
   - 不把估算值写入 `result.pnl`。
   - 写入 `result.estimated_pnl`、`result.pnl_status`、`result.pnl_source`、`result.pnl_pending_reason`。
   - 追加 pending ledger event，等待 backfill。
4. 发布独立 `pnl_resolution_pending` 事件，供 Telegram/RiskGuard 告警。
5. pending 阶段不得把 `exchange_sl_tp_triggered` 直接等同于 `exchange_sl`。close cause 应为 `external_pending` 或 `exchange_unknown_pending`，只有 final resolution 或明确 SL 订单证据能落到 `exchange_sl`。

兼容字段建议：

```json
{
  "schema_version": "execution_result.v2",
  "status": "closed_externally",
  "action": "close",
  "source": "external_close",
  "result": {
    "symbol": "JTO-USDT-SWAP",
    "entry_request_id": "20260527-JTO-36c8890c",
    "pnl_status": "pending",
    "pnl_source": "estimated_local_stop_loss",
    "estimated_pnl": -0.543,
    "pnl": null,
    "pnl_is_final": false,
    "position_id": "JTO-USDT-SWAP-7e57abc2-long",
    "opened_at": 1779922606.49,
    "closed_at": 1779922722.27,
    "sl_algo_id": "3604423910009618432",
    "sl_algo_clord_id": "sl-36c8890c-long",
    "tp_algo_id": "",
    "tp_algo_clord_id": "",
    "close_cause": "exchange_unknown_pending",
    "entry_attribution": {
      "archetype": "breakout_pullback",
      "entry_bucket": "high_conviction"
    }
  }
}
```

### 6.3 异步补账事件

新增消息类型：

```text
pnl_resolved
```

payload：

```json
{
  "schema_version": "pnl_resolution.v1",
  "event_type": "pnl_resolved",
  "position_id": "JTO-USDT-SWAP-7e57abc2-long",
  "symbol": "JTO-USDT-SWAP",
  "entry_request_id": "20260527-JTO-36c8890c",
  "close_request_id": "",
  "pnl_status": "final",
  "pnl_source": "okx_fills_history+okx_bills",
  "realized_pnl_net_usdt": -1.73,
  "previous_estimated_pnl": -0.543,
  "pnl_delta": -1.187,
  "order_ids": ["..."],
  "bill_ids": ["..."],
  "close_match_key": "sha256:...",
  "close_cause": "exchange_sl",
  "entry_attribution": {
    "archetype": "breakout_pullback",
    "entry_bucket": "high_conviction"
  },
  "resolved_at": 1779922800.0
}
```

消费者行为：

| 消费者 | 行为 |
|---|---|
| `ReviewerAgent` | 按 `entry_request_id` 或 `position_id` upsert trade record；已有 pending record 时替换，不追加重复记录 |
| `Judge` | 只在 `pnl_status=final` 时更新 archetype cooldown、EV、probe SL 统计；必须使用 `entry_attribution` 还原原入场桶 |
| `TelegramNotifier` | pending 时显示待确认；resolved 时补发“PnL 已校正”；订阅 `pnl_resolved` 和 `pnl_mismatch` |
| `RiskGuard` | final daily PnL 使用 resolved 值；pending 期间可用 conservative estimate 触发只减风险的保护，不触发策略学习 |
| `Reconciler` | mismatch 时生成 `pnl_mismatch` 告警，不直接覆盖 final |

### 6.4 LiveLedger 改造

`utils/live_ledger.py` 需要从“写事件”升级为“事件 + 幂等修正”：

新增字段：

| 字段 | 说明 |
|---|---|
| `position_id` | 生命周期主键 |
| `entry_request_id` | 策略请求主键 |
| `close_match_key` | `symbol/side/opened_at/closed_at/algo_id` 的稳定哈希 |
| `pnl_status` | final/pending/estimated/mismatch |
| `pnl_source` | okx_fills_history/okx_bills/ws_order/estimated |
| `realized_pnl_net_usdt` | final 净 PnL |
| `estimated_pnl` | 临时估算 |
| `supersedes_event_id` | 修正事件指向旧 pending/estimated 事件 |
| `correction_seq` | 同一 position 的修正序号 |
| `resolution_id` | final resolution 幂等键 |
| `attempt_count` | pending 对账尝试次数 |
| `last_attempt_at` | 最近一次尝试时间 |
| `next_retry_at` | 下次允许重试时间 |
| `last_pending_reason` | 最近一次 pending 原因 |
| `opened_at` / `closed_at` | 持仓生命周期窗口，不允许重启后丢失为 0 |
| `sl_algo_id` / `sl_algo_clord_id` | 保护性 SL 订单归属 |
| `tp_algo_id` / `tp_algo_clord_id` | 保护性 TP 订单归属 |
| `entry_attribution` | Judge/Reviewer 后续学习所需的原入场归因 |

新增方法：

```python
def record_pending_external_close(position_snapshot, estimated_pnl, reason) -> dict
def apply_pnl_resolution(position_id, resolution) -> dict
def find_pending_external_closes(since_ts) -> list[dict]
def update_pending_resolution_attempt(position_id, reason, next_retry_at) -> dict
```

幂等要求：

- final correction 幂等键必须包含 `position_id + close_match_key + sorted(order_ids) + sorted(bill_ids) + realized_pnl_net_usdt`，或使用等价的稳定 `resolution_id`。
- 同一 final resolution 重复写入时不得重复加 PnL、不得新增 standalone correction、不得改变 lifecycle `total_realized_pnl` 或 daily realized PnL。
- `apply_pnl_resolution()` 收到 `pnl_status=pending` 时不得 `supersede` 原 pending event；只能更新 retry metadata，且 `find_pending_external_closes()` 仍必须能找到该 close。
- `pending_fx` 和 `mismatch` 不得默认移除 retryability；只有明确 terminal/manual 状态才能停止自动重试。
- 已 final 的 position 再收到不同 final/mismatch 不得覆盖原 final，必须产生告警或人工复核事件。
- `mismatch` 不能覆盖 `final`，必须产生告警等待人工确认。

### 6.5 Reconciler 改造

现有 `utils/reconciliation.py` 只比较 `ordId -> pnl`，且 `type=5` 过滤可能漏掉费用、资金费或不同 bill subtype。

改造要求：

1. 查询 `GET /api/v5/account/bills` 时优先按 `instType=SWAP, instId, begin, end`，不要在主路径硬编码单一 `type`。
2. 按 `position_id` 生命周期窗口聚合，而不是只按 `ordId`。
3. 对 pending external close 执行自动 resolution：
   - 先用 stored `algoId/algoClOrdId/order_id` 精确匹配。
   - 再用 `symbol + close_side + posSide + fillTime in [opened_at, closed_at + grace] + remaining_size` 匹配。
   - fallback 必须排除 lifecycle 已使用的 reduce `order_id/bill_id`。
   - 多候选时保持 pending 并告警，不猜测。
4. 输出 `pnl_resolved` 或 `pnl_mismatch` 事件。
5. 每次对账记录 raw query params、返回条数、匹配规则、阈值。
6. 只在 resolver 返回 `pnl_status=final` 时调用 `apply_pnl_resolution()`。resolver 返回 `pending` 时只更新 `attempt_count/last_attempt_at/next_retry_at/last_pending_reason`，原 pending event 必须继续可查询。
7. retry schedule 固定为 `10s -> 30s -> 2m -> 10m -> 30m`，超过 24 小时仍未 final 时标记 `needs_manual_reconcile` 并保留审计链。
8. Reconciler 不允许用 `opened_at=0` 重建 pending close；缺失 lifecycle 时间窗时必须返回 `pending` 并告警 `missing_lifecycle_window`。

### 6.6 保护单关联增强

为避免外部 SL/TP 触发后找不到真实订单：

- 下独立 SL/TP algo 时生成 `algoClOrdId`，尽量包含 `entry_request_id` 短码和 side。
- 本地 `positions.json` 记录：
  - `position_id`
  - `entry_request_id`
  - `opened_at`
  - `sl_algo_id`
  - `sl_algo_clord_id`
  - `tp_algo_id`
  - `tp_algo_clord_id`
  - `close_owner`
  - `entry_attribution`
- `LiveLedger.record_open()` 返回的 `position_id` 必须回写到持仓对象，后续 close/reduce/external close 全链路携带。
- `execution_result.result.position_id` 必须来自同一个 lifecycle 主键，不允许 close 时重新生成。
- `pnl_resolved` 必须透传 pending/lifecycle 中的 `entry_attribution`，Reviewer/Judge 不得通过当前行情重新推断历史入场归因。

### 6.7 Backfill 工具

新增脚本建议：

```text
scripts/backfill_realized_pnl.py
```

功能：

```bash
python3 scripts/backfill_realized_pnl.py \
  --since 2026-05-28T00:00:00+08:00 \
  --until 2026-05-28T08:00:00+08:00 \
  --symbol JTO-USDT-SWAP \
  --dry-run
```

要求：

- dry-run 输出候选 fills、bills、匹配分数、旧 PnL、新 PnL、delta。
- apply 模式只写 correction event，不直接删除旧 JSONL。
- 对 `trade_history.json` 使用 upsert，不 append 重复交易。
- 运行结束输出 summary：resolved / pending / mismatch / skipped。

### 6.8 2026-05-28 实现审查追加硬要求

以下条目来自代码实现审查，作为 P0/P1 关闭条件，不满足则本需求不得 Go：

| 优先级 | 硬要求 | 失败后果 |
|---|---|---|
| P0 | `apply_pnl_resolution()` 对同一 final resolution 必须严格幂等 | 重复对账会把同一笔亏损/盈利计入两次，daily PnL 和 lifecycle 失真 |
| P0 | pending resolution 不得 supersede 原 pending close | OKX API 延迟/失败后，后续 retry/backfill 找不到待修账事件 |
| P0 | Reconciler 只对 final 调用 `apply_pnl_resolution()` | pending 被误当修正写入，破坏 retry 队列 |
| P1 | resolver 不能只用 `side + time window` 匹配外部 close | partial reduce fill 可能被误归入 full close |
| P1 | pending/final 事件必须携带 `position_id/opened_at/closed_at/algoId/algoClOrdId/entry_attribution` | 重启后无法精确匹配，Judge/Reviewer 无法归因 |
| P1 | Telegram 必须兼容 `result.pnl=null` 并订阅 resolution 事件 | pending close 会报错或继续展示错误日盈亏 |
| P1 | pending external close 不得立即归类为 SL hit | TP 或未知外部平仓会误触发 SL cooldown |

## 7. 参数与回传清单

### 7.1 OKX fills-history

调用：

```python
exchange.private_get_trade_fills_history({
    "instType": "SWAP",
    "instId": "JTO-USDT-SWAP",
    "begin": str(begin_ms),
    "end": str(end_ms),
    "limit": "100"
})
```

可选精确查询：

```python
exchange.private_get_trade_fills_history({
    "instType": "SWAP",
    "instId": symbol,
    "ordId": order_id
})
```

读取字段：

| 字段 | 用途 |
|---|---|
| `ordId` | close/reduce/order 归属 |
| `clOrdId` | 本地 request/owner 归属 |
| `billId` | 与 account bills 对齐 |
| `subType` | 区分 open/close long/short、liquidation、ADL |
| `side` | buy/sell |
| `posSide` | long/short/net |
| `fillPx` | 成交价 |
| `fillSz` | 成交数量 |
| `fillPnl` | 平仓成交收益 |
| `fee` / `feeCcy` | 手续费/返佣 |
| `fillTime` | 成交时间 |

### 7.2 OKX account bills

调用：

```python
exchange.private_get_account_bills({
    "instType": "SWAP",
    "instId": "JTO-USDT-SWAP",
    "begin": str(begin_ms),
    "end": str(end_ms),
    "limit": "100"
})
```

读取字段：

| 字段 | 用途 |
|---|---|
| `billId` | 与 fills 对齐 |
| `ordId` | 与订单对齐 |
| `pnl` | 账单 PnL |
| `fee` | 费用 |
| `balChg` | 余额变化，用于审计 |
| `ccy` | 币种 |
| `subType` | 账单子类型 |
| `fillTime` / `ts` | 时间窗口匹配 |

### 7.3 本地事件回传

`execution_result.result` 新增：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `pnl_status` | 是 | final/pending/estimated/mismatch |
| `pnl_is_final` | 是 | bool |
| `pnl_source` | 是 | 数据来源 |
| `pnl` | 条件 | 仅 final 时填净 PnL；pending 时为 null 或不出现 |
| `estimated_pnl` | 条件 | pending/estimated 时填 |
| `realized_pnl_net_usdt` | 条件 | final 时等于 `pnl` |
| `position_id` | 是 | lifecycle 主键 |
| `entry_request_id` | 是 | 策略主键 |
| `opened_at` | 是 | lifecycle 开仓时间 |
| `closed_at` | 是 | 外部平仓发现/确认时间 |
| `close_cause` | 是 | pending 时为 external_pending/exchange_unknown_pending；final 后可为 exchange_sl/exchange_tp/manual/liquidation |
| `sl_algo_id` / `sl_algo_clord_id` | 条件 | 有保护性 SL 时必须带 |
| `tp_algo_id` / `tp_algo_clord_id` | 条件 | 有保护性 TP 时必须带 |
| `entry_attribution` | 是 | 原入场归因，供 EV/archetype 复盘 |
| `order_ids` | 否 | 匹配到的订单 |
| `bill_ids` | 否 | 匹配到的账单 |
| `pnl_pending_reason` | 条件 | pending 时说明 |
| `resolution_id` | 条件 | final/mismatch 时提供稳定幂等键 |

## 8. 并发症与防护

| 风险 | 影响 | 防护 |
|---|---|---|
| OKX 成交/账单延迟 | 刚平仓时查不到 final PnL | 发布 pending，延迟重试 10s/30s/2m/10m/30m |
| CCXT 不支持 `fetch_orders` | 查不到外部 close order | 使用 OKX implicit REST 方法，不依赖统一 `fetch_orders` |
| 多笔同 symbol 连续开平 | 时间窗口误匹配 | 必须携带 `position_id`、`entry_request_id`、algo id；多候选不猜 |
| partial reduce + full close | PnL 重复计入 | lifecycle 聚合，按 `event_id/order_id/bill_id` 幂等 |
| 重复 final resolution | 同一 PnL 被计入两次 | 使用稳定 `resolution_id`，重复 apply 只返回 existing |
| pending 被 resolution 覆盖 | 后续 retry/backfill 找不到待修账 close | pending 只更新 retry metadata，不写 supersede correction |
| 重启后 lifecycle 上下文丢失 | Reconciler 用 `opened_at=0` 误匹配大窗口账单 | pending event 持久化 opened/closed/algo/attribution，缺失则告警不 final |
| 费用币种非 USDT | 净 PnL 无法直接相加 | 保持 `pending_fx`，后续引入换算源 |
| funding 难归属 | 单笔净值偏差 | 按 symbol/posSide/lifecycle 时间窗口聚合，无法唯一归属则单独告警 |
| Reconciler 自动修正历史 | EV/胜率突然变化 | 只通过 correction event upsert，保留旧估算和 delta |
| Telegram 读取 `pnl=None` | 通知线程异常或日盈亏错误 | pending 分支使用 `estimated_pnl` 文案，daily summary 只统计 final |
| pending 误判为 SL | TP/未知平仓触发错误 cooldown | pending cause 保持 unknown，final 后按订单证据分类 |
| pending 太久 | 复盘缺数据 | 30 分钟未 resolved 发告警；24 小时仍 pending 标记 `needs_manual_reconcile` |
| final PnL 后又收到 mismatch | 数据竞争或交易所修订 | 不自动覆盖 final，生成 mismatch 告警 |
| testnet/live 状态混写 | 污染实盘账本 | 遵循 `STATE_NAMESPACE`，testnet/paper 不写 live ledger |
| JSON 并发写 | 事件丢失或重复 | 继续使用 atomic write，JSONL append 需加文件锁或单 writer |
| 下游未升级 | 仍读取 `result.pnl` 污染 | 在代码验收中强制 Reviewer/Judge 检查 `pnl_is_final` |

## 9. 迁移计划

### Phase 1: 合同与消费者防污染

- `execution_result` 增加 PnL 质量字段。
- Reviewer/Judge/RiskGuard/Telegram 只把 `pnl_is_final=true` 当作最终 PnL。
- pending 事件不进入 EV、胜率、archetype cooldown。

### Phase 2: Resolver 与 Ledger 修正

- 实现 `RealizedPnlResolver`。
- `LiveLedger` 支持 pending external close 和 final correction。
- `Reconciler` 对 pending 自动补账。

### Phase 3: 保护单关联与 backfill

- algoClOrdId 带入 entry_request_id 短码。
- backfill 工具支持按日期/symbol 回补。
- 回补 2026-05-28 JTO 三笔，验收真实结果 `+0.16/+0.28/-1.73`。

### Phase 4: testnet 与 live shadow

- testnet 生成真实外部 SL/TP，验证 resolver。
- live 初期只 shadow 运行 24 小时：同时记录旧估算和新 final，不让旧估算进入学习。

## 10. Go / No-Go

Go 条件：

- `exchange_realized_pnl_ledger_acceptance.md` 中 P0/P1 验收通过。
- JTO 回放样例能把本地错误 PnL 修正为实际账户口径。
- 所有 `pnl_status != final` 的事件不会进入 Reviewer trade history 和 Judge 学习统计。
- Reconciler 能生成 correction event，并且重复运行不重复计数。
- Telegram 对 pending/final/mismatch 有不同文案。

No-Go 条件：

- 仍存在 `result.get("pnl", 0)` 未检查 `pnl_is_final` 的学习/复盘路径。
- 外部平仓无法解析时仍把估算 PnL 写入 final trade history。
- backfill 会删除旧 JSONL 或破坏审计链。
- testnet/live/paper 状态路径未隔离。
