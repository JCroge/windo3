# 2026-05-28 第三次审计整改产品需求文档

更新日期：2026-05-28  
关联审计报告：`docs/generated_reports/系统性审计报告_20260528.md`  
关联历史整改：`docs/audit_remediation_20260528_prd.md`、`docs/exchange_realized_pnl_ledger_prd.md`  
关联验收：`docs/audit_remediation_third_pass_20260528_acceptance.md`  
当前结论：live 扩容 NO-GO。小额 live 灰度只能维持现有额度，并要求人工可接管。

## 1. 背景

前两轮整改已经把 EarlyReview、root executor 保护单替换、Agent close path、close cause 基础字段、真实已实现 PnL 账本和状态命名空间推进到可测试状态。但第三次代码审计发现：部分修复在主路径上成立，边界路径仍可能产生新的交易并发症。

本轮不重复实现上一轮 F-001 至 F-010，而是把代码更新后的残余风险转成下一轮开发需求：

- `reduce_position()` 在缩仓前取消旧 SL，但没有检查 `_cancel_protective_sl()` 返回值，随后清空旧 `sl_order_id/sl_algo_id` 并继续下 reduce order。
- `_cleanup_protective_orders_on_close()` 注释声明只 sweep 本 owner algo，但实际对该 symbol pending algos 全部撤单，可能误撤手工或其他系统保护单。
- 外部平仓 pending 阶段已经走 `external_pending/exchange_unknown_pending`，但 final `pnl_resolved` 的 close cause 证据、Judge 幂等和 probe_short 计数仍需要补强。
- 新闻 ticker mention 仍使用 substring 匹配，`OP/STX/INJ` 等短 symbol 会被普通英文片段误报。

## 2. 产品目标

1. 缩仓、分批止盈、风险减仓后，剩余仓位必须始终有可解释的保护状态。
2. 任一保护单撤单失败不得继续制造新交易风险：不能双 SL，不能清空旧保护 ID，不能误报 protected。
3. close cleanup 只能处理本系统 owner 的 algo，不能扫掉人工单或其他 bot 的保护单。
4. 外部平仓必须两阶段归因：pending 阶段不计 SL；final 阶段只有证据明确时才计 `exchange_sl`，且重复事件幂等。
5. 新闻 mention 必须有边界匹配和 provenance，弱新闻信号不能被当作强事实。
6. 所有整改都要形成可回归验收，不依赖“默认 pytest 绿”推断 live 扩容安全。

## 3. 非目标

- 不调整交易策略、仓位规模、R:R floor 或入场过滤。
- 不引入新的交易所适配。
- 不把 testnet/mock 结果等同于 live 扩容许可。
- 不要求立即实现 OKX algo amend API；若未验证 amend 能力，本轮使用 fail-closed 的 cancel/reduce/replace 流程。
- 不为了新闻匹配引入新的外部数据供应商。

## 4. Go/No-Go

| 范围 | 当前状态 | Go/No-Go |
|---|---|---|
| 本地开发 | 可继续 | GO |
| paper/mock | 可继续 | GO |
| 小额 live 灰度 | 可维持现有额度 | CONDITIONAL GO |
| live 扩容 | 存在新增 P0/P1 风险 | NO-GO |

live 扩容重新评审前，必须完成本 PRD 的 P0 项，并通过 `docs/audit_remediation_third_pass_20260528_acceptance.md` 中的 P0 验收。P1 可在明确风险豁免后进入小额灰度，但不得忽略最终归因/幂等测试。

## 5. 问题地图

| ID | 优先级 | 位置 | 风险 |
|---|---|---|---|
| R3-001 | P0 | `executor.py` `reduce_position()` | 撤旧 SL 失败仍继续缩仓并清空本地旧 ID，可能同时存在旧 SL + 新 SL，或本地误以为无旧保护 |
| R3-002 | P0 | `executor.py` `_cleanup_protective_orders_on_close()` | OKX sweep 未做 owner 判断，可能撤掉手工保护单或其他 bot 的 algo |
| R3-003 | P1 | `agents/trading/executor.py`、`agents/trading/judge.py`、`utils/realized_pnl_resolver.py` | pending 阶段已 fail-safe，但 final `pnl_resolved` 缺少证据字段和幂等门控，可能重复计 SL 或 probe_short 误计 |
| R3-004 | P2 | `agents/research/news_researcher.py`、`agents/trading/multi_data_collector.py` | substring 匹配导致短 ticker 新闻误报 |

## 6. 功能需求与技术路径

### FR-3A 缩仓保护单生命周期必须 fail-closed

关联：R3-001  
优先级：P0

#### 需求

- `reduce_position()` 不得忽略 `_cancel_protective_sl()` 的失败。
- 撤旧 SL 失败时必须立即返回，不得调用 `exchange.create_order()` 发起缩仓。
- 撤旧 SL 失败时不得清空旧 `sl_order_id/sl_algo_id/sl_algo_clord_id`，因为旧保护单可能仍在交易所有效。
- 任意成功缩仓后，只要剩余仓位未 dust 全平，就必须完成保护单 resize/replace，不能只在 `tp_advance` 场景更新保护单。
- 如果 reduce 成功但保护单重挂失败，仓位必须进入 `protection_state=unknown` 或 `local_fallback`，并阻断后续 add/open/reduce，直到人工或 reconciliation 修复。

#### 推荐流程

```text
acquire exit/protection lock
  -> snapshot position + old protective ids
  -> fetch exchange position state
  -> if old SL exists:
       cancel old SL
       if cancel failed:
          keep old ids
          mark sl_sync_state=failed, protection_state=unknown
          live OKX halt + alert
          return ReducePositionResult(ok=false, reason=sl_cancel_failed)
  -> submit reduce order
       if reduce failed after cancel success:
          attempt restore protective SL with original amount/stop_loss
          if restore failed: protection_state=unknown/halted
          return ok=false
  -> update local amount and PnL
  -> if residual position exists:
       replace protective SL for residual amount
       if replace failed: mark unsafe + halt/alert
  -> save position once with final state
  -> return structured ReducePositionResult
release lock
```

如果后续验证 OKX/ccxt 能安全 amend algo amount/trigger price，可把 cancel/reduce/replace 升级为 amend-first；在验证前不以未证明的 amend 能力作为 live 扩容依据。

#### 接口回参

`reduce_position()` 应从裸 order dict 升级为结构化结果，短期可兼容原字段。

```json
{
  "ok": false,
  "symbol": "BTC-USDT",
  "operation": "reduce_position",
  "action_id": "tp1-BTC-USDT-1770000000",
  "requested_pct": 0.5,
  "requested_reduce_amount": 0.5,
  "actual_reduce_amount": 0.0,
  "order": null,
  "reduce_ok": false,
  "cancel_ok": false,
  "replace_ok": false,
  "protective_update_state": "cancel_failed",
  "old_sl_algo_id": "123",
  "old_sl_algo_clord_id": "slBTCUSDT...",
  "new_sl_algo_id": null,
  "sl_sync_state": "failed",
  "protection_state": "unknown",
  "halt_required": true,
  "reason": "sl_cancel_failed",
  "warnings": ["old_sl_may_still_be_live"],
  "entry_request_id": "req-123",
  "timestamp": 1770000000.0
}
```

`execution_result.v2` 中涉及 partial TP 或 risk reduce 的 payload，应把该结构挂到 `result.reduce_result`，并透传：

- `result.protective_update_state`
- `result.protection_state`
- `result.halt_required`
- `result.reduce_ok`
- `result.replace_ok`

#### 并发症与缓解

| 并发症 | 缓解 |
|---|---|
| 撤旧失败后继续缩仓，旧 SL 数量大于剩余仓位 | cancel failed 立即返回，不发 reduce order，不清旧 ID |
| 撤旧成功但 reduce reject，仓位变成无保护 | reduce reject 后尝试按原始仓位 restore old SL；失败则 halt |
| reduce 成功但新 SL 挂不上 | 标记 unsafe，阻断 add/open/reduce，live OKX halt + Telegram 告警 |
| partial TP 已成交但保护失败 | `tp_filled` 可以反映真实成交，但必须同时标 `partial_tp_state=protection_failed`，后续动作被保护状态阻断 |
| close 与 reduce 同时触发 | 继续复用 symbol exit lock，并把 protection update 纳入同一临界区 |
| 进程在 cancel 与 replace 中间崩溃 | 启动 reconciliation 必须识别 residual position + no owner SL，进入 halt/补挂流程 |

### FR-3B close cleanup 必须 owner-bound

关联：R3-002  
优先级：P0

#### 需求

- `_cleanup_protective_orders_on_close()` 只能取消以下 algo：
  - 本地 position 记录的 `sl_algo_id/sl_order_id`。
  - `algoClOrdId` 精确等于本地 `sl_algo_clord_id`。
  - lifecycle/ledger 中记录为本系统创建的 algo id。
  - 新 owner 前缀明确匹配当前 `STATE_NAMESPACE` + `BOT_INSTANCE_ID` 的 algo。
- 对无法证明 owner 的 pending algo，不得自动取消。
- 如果同 symbol 存在 foreign/unknown algo，返回 `unknown` 或 `foreign_algos_present`，并发告警，让运维判断是否人工处理。
- 注释、实现、测试必须一致；不能再出现“注释写 owner，代码扫全部”的状态。

#### owner 标识

新增统一 owner tag 规则，供未来所有 OKX algo 使用：

```text
algoClOrdId = ca + <namespace> + <bot_instance> + <base> + <random>
```

约束：

- 只使用 OKX 允许的字母数字字符。
- 总长度不超过 OKX/ccxt 限制。
- `namespace` 来自 `STATE_NAMESPACE`。
- `bot_instance` 来自 `BOT_INSTANCE_ID`，未配置时启动期生成并打印，但 live 扩容建议显式配置。
- 历史 `sl...` 前缀只能通过本地 exact `sl_algo_clord_id` 识别 owner，不能仅凭 `sl` 前缀批量 sweep。

#### 接口回参

把 `_cleanup_protective_orders_on_close()` 从字符串升级为结构化结果；短期可保留字符串兼容。

```json
{
  "ok": false,
  "symbol": "BTC-USDT",
  "operation": "cleanup_protective_orders_on_close",
  "state": "foreign_algos_present",
  "known_cancel_ok": true,
  "cancelled_algo_ids": ["123"],
  "owned_algo_ids": ["123"],
  "foreign_algo_ids": ["manual-999"],
  "unknown_algo_count": 1,
  "warnings": ["foreign_algo_not_cancelled"],
  "halt_required": true,
  "timestamp": 1770000000.0
}
```

`close_position()` 和 `execution_result.v2` 应透传：

- `result.protective_cleanup_state`
- `result.protective_cleanup`
- `result.foreign_algo_ids`
- `result.cleanup_warnings`

#### 并发症与缓解

| 并发症 | 缓解 |
|---|---|
| 手工在 OKX UI 挂了同 symbol SL，被系统 close cleanup 撤掉 | unknown/foreign algo 不自动撤，只告警 |
| 同一账户跑两个 bot，互相扫保护单 | owner tag 包含 namespace + bot_instance，sweep 只认本实例 |
| 历史仓位没有 clOrdId | 只取消本地已知 algo id；未知 algo 进入人工复核 |
| close 后 foreign algo 留存导致后续反向开仓风险 | `foreign_algos_present` 阻断同 symbol 新开仓，直到人工确认 |

### FR-3C 外部平仓 final close cause 证据与幂等

关联：R3-003  
优先级：P1；pending 误计 SL 的回归保护按 P0 验收

#### 需求

- pending `closed_externally` 必须继续使用 `reason=external_pending`，`close_cause=exchange_unknown_pending`，`pnl_is_final=false`，`is_strategy_stop=false`。
- `RealizedPnlResolver.resolve_external_close()` 的 final 结果必须给出 `final_close_cause` 或 `close_cause`，以及可审计 `close_evidence`。
- 只有证据明确匹配本系统 SL algo 时，才允许 `close_cause=exchange_sl` 与 `is_strategy_stop=true`。
- `Judge` 消费 `pnl_resolved` 必须幂等：同一 `correction_event_id/supersedes_event_id/close_match_key/position_id` 重复到达时，只能记一次 SL hit。
- `probe_short` 的 SL 计数也必须受 final close cause 约束，不能仅凭 final PnL 为负就计 SL。

#### close cause 分类

| final close cause | 必要证据 | Judge SL hit |
|---|---|---|
| `exchange_sl` | matched algo id/clOrdId 等于本地 SL；或 OKX fill/order 明确关联该 SL algo | 是，且幂等 |
| `exchange_tp` | matched TP algo/order 证据 | 否 |
| `manual_close` | close fill 存在但不属于系统 order/algo | 否 |
| `liquidation_or_adl` | OKX bills/fills subtype 能证明 | 否，进入风险事件 |
| `external_unknown` | PnL final 但原因证据不足 | 否 |

价格接近 stop_loss 只能作为 weak evidence，不能单独把 final 归因为 `exchange_sl`。

#### 接口回参

`pnl_resolved` payload 增加或规范以下字段：

```json
{
  "schema_version": "pnl_resolution.v1",
  "event_type": "pnl_resolved",
  "symbol": "BTC-USDT",
  "position_id": "pos-123",
  "entry_request_id": "req-123",
  "pnl_status": "final",
  "pnl_is_final": true,
  "realized_pnl_net_usdt": -12.34,
  "close_cause": "exchange_sl",
  "final_close_cause": "exchange_sl",
  "is_strategy_stop": true,
  "close_evidence": {
    "matched_algo_id": "123",
    "matched_algo_clord_id": "caLiveBotBTC...",
    "matched_order_ids": ["ord-1"],
    "match_rule": "sl_algo_id_exact",
    "confidence": 1.0
  },
  "supersedes_event_id": "pending-evt-1",
  "correction_event_id": "corr-evt-1",
  "resolution_id": "corr-evt-1"
}
```

#### 并发症与缓解

| 并发症 | 缓解 |
|---|---|
| Resolver 延迟，多轮 pending 重试 | pending 不广播 final；只更新 retry metadata |
| `pnl_resolved` 重复发布 | Judge/Reviewer 按 resolution id 幂等 upsert |
| 实际 SL 触发但缺证据 | fail-safe 为 `external_unknown`，不计 SL，进入人工复核 |
| final 亏损来自手工平仓 | 不计 strategy SL，不污染 probe_short cooldown |
| 历史 pending 事件字段缺失 | 兼容旧字段，但默认不计 SL |

### FR-3D 新闻 ticker mention 边界匹配与 provenance

关联：R3-004  
优先级：P2

#### 需求

- 新增共享 helper，例如 `utils/symbol_mentions.py`。
- `NewsResearcher._extract_symbol_mentions()` 和 `MultiDataCollector._refresh_news_cache()` 必须使用同一 helper。
- 禁止 `if symbol in text` 作为命中条件。
- 支持高置信格式：
  - `$OP`
  - `(STX)`
  - `INJ/USDT`
  - `INJ-USDT`
  - `OP token`
  - 独立 token 边界内的 symbol
- 对短 symbol 或常见英文词，使用更严格规则，避免 `OP` 命中 `options`、`STX` 命中 `stack`、`INJ` 命中 `injection`。

#### 回参

新闻 mention 输出建议统一为：

```json
{
  "symbol": "OP",
  "count": 2,
  "confidence": 0.82,
  "match_rules": ["cashtag", "pair"],
  "source": "news_rss",
  "freshness_sec": 180,
  "headlines": ["..."]
}
```

`news_snapshot.symbol_news` 中每条新闻应保留：

- `source`
- `published_ts`
- `freshness_sec`
- `confidence`
- `match_rule`

#### 并发症与缓解

| 并发症 | 缓解 |
|---|---|
| 规则过严导致漏报 | cashtag/pair/括号格式保持高召回；普通裸词低置信 |
| 非英文标点边界 | helper 用 Unicode 非字母数字边界或标准 tokenizer |
| 同一标题多 symbol | 返回每个 symbol 的 match_rule，不只返回布尔 |
| 新闻信号被 Judge 当强事实 | 透传 confidence，consumer 按阈值使用 |

## 7. 开发顺序

1. P0-1：先修 `reduce_position()` fail-closed 和 residual protection resize。
2. P0-2：再修 `_cleanup_protective_orders_on_close()` owner-bound sweep 和回参透传。
3. P0 回归：跑第三次验收 P0 + 默认全量 pytest。
4. P1：补 `pnl_resolved` close evidence、Judge/Reviewer 幂等、probe_short close cause 门控。
5. P2：抽 `symbol_mentions` helper，替换两处 substring 匹配。
6. 文档同步：`docs/to-do-list.md`、`docs/handoff.md`、`docs/architecture.md` 的 live 扩容结论统一以本轮验收为准。

## 8. 最终产品判定

完成本 PRD 后，系统应达到：

- 缩仓路径不会因为保护单撤单失败而制造双 SL 或裸仓。
- close cleanup 不会越权撤掉非本系统 algo。
- 外部平仓的 pending/final/SL hit 语义可审计、可幂等、可回放。
- 新闻信号有匹配规则和置信度，不再由 substring 误报驱动交易判断。

上述条件未满足前，live 扩容维持 NO-GO。
