# 2026-05-28 系统审计整改产品需求文档

更新日期：2026-05-28  
关联审计报告：`docs/generated_reports/系统性审计报告_20260528.md`  
关联验收：`docs/audit_remediation_20260528_acceptance.md`  
当前结论：默认回归为绿，但 P1 执行语义修复前不得扩大 live。

## 1. 背景

2026-05-28 系统性审计覆盖多 Agent flow、OKX 条件单语义、执行接口回参、LLM 契约、测试体系和文档事实一致性。审计确认当前系统可继续小额 live 灰度，但不具备扩大 live 的条件。

核心原因不是策略信号，而是订单生命周期和回参语义仍有混合 owner：

- Agent 层仍在多个 close 路径直接撤 `sl_order_id`，绕开 OKX trigger algo 的撤单语义。
- EarlyReview 会直接写本地 `stop_loss`，没有同步替换交易所保护单。
- `_replace_protective_sl()` 在旧 SL 撤单失败时仍会继续挂新 SL，可能制造双保护单。
- Judge 把所有 `force_closed` 当成 SL hit，导致风控强平、系统全平、价格获取失败污染策略冷却。
- 部分文档和 testnet Go/No-Go 把 `7 PASS / 3 SKIP` 表述成无 caveat 扩容。

本 PRD 目标是把审计项 F-001 至 F-010 转换为可开发、可回归、可验收的整改路径。

## 2. 产品目标

1. 保护单生命周期由根 `ContractExecutor` 单一 owner 管理。
2. 所有 SL 移动、替换、取消都走 OKX-aware trigger algo 语义。
3. close/force close 的接口回参能区分真实 SL、风控强平、系统全平、价格异常和外部平仓。
4. Judge cooldown 只被策略止损或明确亏损 SL 污染，不被运维/风控动作污染。
5. LLM schema、prompt、fallback、consumer 字段一致，避免靠额外字段透传工作。
6. testnet/live/paper 状态文件有命名空间，测试体系能重复执行。
7. 文档 Go/No-Go 与代码事实一致，禁止用过期 PASS 结论指导扩容。

## 3. 非目标

- 不调整策略参数、R:R floor、入场过滤、收益目标或仓位规模。
- 不重写多 Agent 架构，不引入 LangGraph/Hummingbot/Freqtrade 作为运行时依赖。
- 不新增交易所。
- 不把 mock、paper 或 long_short_mode testnet 结果等同于全量 OKX live 语义。
- 不在本需求中实现自动切换 OKX posMode。

## 4. 优先级与 Go/No-Go

| 优先级 | 范围 | Go/No-Go |
|---|---|---|
| P0 | F-001 至 F-004：保护单 owner、SL 替换失败、close path、close cause | 未完成不得扩大 live；仅允许现有小额灰度和人工盯盘 |
| P1 | F-005 至 F-008：LLM 契约、testnet caveat、network 测试、状态命名空间 | 扩容前必须完成或有书面豁免 |
| P2 | F-009 至 F-010：Agent health、新闻 symbol matching | 不阻断小额灰度，但必须纳入下一阶段 |

当前状态：live 扩容 NO-GO。小额 live 灰度只能在维持现有额度、人工可接管、每日复核 algo 残留的前提下继续。

## 5. 问题地图

| ID | 审计项 | 直接风险 | 修复归属 |
|---|---|---|---|
| RQ-001 | EarlyReview 本地改 SL | 本地显示已收紧，交易所仍旧 SL | `agents/trading/executor.py` 调根 executor |
| RQ-002 | `_replace_protective_sl()` 忽略撤旧失败 | 旧 SL 和新 SL 同时存在 | `executor.py` |
| RQ-003 | Agent close 路径直接 `cancel_order()` | OKX algo 撤不掉，close 后 orphan SL | `agents/trading/executor.py` + `executor.py` |
| RQ-004 | `force_closed` 被 Judge 全计为 SL | cooldown 和表现评估被污染 | `agents/trading/judge.py` |
| RQ-005 | BehavioralCritic 字段不一致 | PA 读取依赖额外字段透传 | `agents/llm_client.py`、critic、PA |
| RQ-006 | testnet GO 无 caveat | 误以为可扩容 | docs + `verify_okx_testnet_real.py` |
| RQ-007 | network 测试不可重复 | 全量验收口径失真 | pytest/network legacy tests |
| RQ-008 | testnet/live 共用状态路径 | testnet/paper 污染 live ledger | state path config |
| RQ-009 | 无统一 health supervisor | agent 停止或退化不可见 | orchestrator/status |
| RQ-010 | 新闻 substring 匹配 | 短 ticker 误报 | news/data collector |

## 6. 需求与开发路径

### FR-001 保护单移动必须走根执行器单一入口

关联：RQ-001  
优先级：P0

需求：

- `_early_review()` 不得直接写 `pos["stop_loss"]` 后 `_save_positions()`。
- EarlyReview 收紧 SL 时必须调用 `ContractExecutor._move_sl()` 或公开包装方法 `move_protective_sl()`。
- 只有交易所保护单替换成功后，才能保存本地 `stop_loss`。
- 替换失败时保留旧本地 `stop_loss`，并把 `sl_sync_state/protection_state` 置为失败或 unknown。

推荐实现：

1. 在 `ContractExecutor` 增加公开方法：

   ```python
   move_protective_sl(symbol, new_sl, reason, action_id=None) -> ProtectiveSLResult
   ```

2. 内部复用 `_move_sl()` / `_replace_protective_sl()`，不要让 Agent 直接碰 `positions`。
3. `_early_review()` 只计算目标 SL 和 reason，例如 `early_review_tighten`。
4. 返回成功后由 root executor 统一更新：
   - `stop_loss`
   - `sl_algo_id`
   - `sl_algo_clord_id`
   - `sl_sync_state`
   - `protection_state`
   - `last_protection_update_reason`

避免并发症：

| 并发症 | 缓解 |
|---|---|
| 本地 SL 已收紧但交易所仍旧 SL | 本地状态只能由 root executor 在替换成功后保存 |
| EarlyReview 与 partial TP 同时移动 SL | 复用 symbol exit/protection lock |
| 频繁 EarlyReview 打爆 OKX algo cancel/place | 保留 120s review throttle，但无保护单时不节流补挂 |

### FR-002 SL 替换必须 fail closed

关联：RQ-002  
优先级：P0

需求：

- `_replace_protective_sl()` 必须检查 `_cancel_protective_sl()` 返回值。
- 撤旧 SL 失败时不得继续 `_place_protective_sl()`。
- live OKX 下撤旧失败必须 symbol halt，并发出高优先级告警。

推荐实现：

```python
cancel_ok = self._cancel_protective_sl(symbol, position)
if not cancel_ok:
    position["sl_sync_state"] = "failed"
    position["protection_state"] = "unknown"
    position["last_protection_error"] = "sl_cancel_failed"
    self._save_positions()
    if self.exchange_id == "okx" and not self.testnet:
        self._halt_symbol(symbol, reason="sl_cancel_failed")
    return False
```

同时建议引入结构化回参，短期可兼容 bool：

```json
{
  "ok": false,
  "symbol": "BTC-USDT",
  "operation": "replace_protective_sl",
  "reason": "sl_cancel_failed",
  "old_sl_algo_id": "360...",
  "new_sl_algo_id": null,
  "cancel_ok": false,
  "place_ok": false,
  "sl_sync_state": "failed",
  "protection_state": "unknown",
  "halt_required": true,
  "timestamp": 1770000000.0
}
```

避免并发症：

| 并发症 | 缓解 |
|---|---|
| 撤旧失败后又挂新 SL，交易所双 SL | cancel failed 立即返回，不下新单 |
| 本地 `sl_algo_id` 指向新单但旧单还活着 | 失败时不覆盖旧 `sl_algo_id`，记录 unknown 并 halt |
| 撤单接口异常导致裸仓 | 撤旧失败本质仍有旧保护，不能盲目删除本地保护字段 |

### FR-003 Agent close path 不得直接撤保护单

关联：RQ-003  
优先级：P0

需求：

- `agents/trading/executor.py` 所有 close/force close/local stop/close_all 路径不得调用 `self.executor.cancel_order(symbol, sl_order_id)`。
- 全平统一调用 `ContractExecutor.close_position()`。
- 若业务确实需要显式取消保护单，只能调用 root executor 公开方法：

  ```python
  cancel_protective_sl(symbol, reason, action_id=None) -> ProtectiveSLResult
  ```

- `close_position()` 内部负责 OKX `cancel_orders([algo_id], symbol, params={"trigger": True})`。
- close 成功后必须复核该 symbol 没有本系统 owner 的残留 algo。

涉及路径：

- trade_decision close
- risk_alert emergency_close / flash_move / position_danger / trailing_stop
- close_all / daily hard stop
- local stop_loss / take_profit / price_fetch_failed

避免并发症：

| 并发症 | 缓解 |
|---|---|
| Agent 用普通 `cancel_order()` 撤 OKX algo 失败 | Agent 不再知道保护单撤单细节 |
| close 成功但 orphan SL 之后反向开仓 | close_position 结束前查询/取消本 owner algos |
| 多个 close 并发互相撤保护单 | 复用 root executor exit lock |
| execution_result 先发布成功但保护单未清 | close result 必须包含 `protective_cleanup_state` |

### FR-004 execution_result.v2 增加 close cause 语义

关联：RQ-004  
优先级：P0

需求：

- 保留 `status="force_closed"` 以兼容下游，但新增明确字段：
  - `exit_reason`
  - `close_cause`
  - `is_strategy_stop`
  - `is_risk_forced`
- Judge 不能再仅凭 `status == "force_closed"` 记录 SL hit。

推荐字段契约：

```json
{
  "schema_version": "execution_result.v2",
  "status": "force_closed",
  "action": "close",
  "symbol": "BTC-USDT",
  "source": "risk_alert",
  "request_id": "req-123",
  "correlation_id": "",
  "reason": "daily_hard_stop",
  "exit_reason": "risk_alert",
  "close_cause": "daily_hard_stop",
  "is_strategy_stop": false,
  "is_risk_forced": true,
  "result": {
    "pnl": -5.0,
    "entry_request_id": "req-123",
    "exit_reason": "risk_alert",
    "close_cause": "daily_hard_stop",
    "protective_cleanup_state": "cleaned"
  },
  "timestamp": 1770000000.0
}
```

close cause 分类：

| source | reason/trigger | exit_reason | is_strategy_stop | Judge 是否记 SL |
|---|---|---|---|---|
| `local_stop` | `stop_loss` | `local_stop_loss` | true | 是 |
| `external_close` | `exchange_sl_tp_triggered` 且 ledger/价格证明为 SL | `exchange_sl` | true | 是 |
| `local_stop` | `take_profit` | `take_profit` | false | 否 |
| `partial_tp` | `partial_tp_1/2` | `partial_take_profit` | false | 否 |
| `risk_alert` | 任意 | `risk_alert` | false | 否 |
| `close_all` | 任意 | `system_close_all` | false | 否 |
| `local_stop` | `price_fetch_failed` | `price_fetch_failed` | false | 否 |
| `external_close` | 无法归因 | `external_unknown` | false | 否，除非 PnL/触发价证明为 SL |

Judge 修改：

1. 读取 `payload.exit_reason`，兼容 `payload.result.exit_reason`。
2. 只有 `exit_reason in {"stop_loss", "local_stop_loss", "exchange_sl"}` 或 `is_strategy_stop is true` 时调用 `_record_sl_hit()`。
3. `risk_alert/close_all/price_fetch_failed` 只清理 open position 和 slot，不增加 escalating SL cooldown。

避免并发症：

| 并发症 | 缓解 |
|---|---|
| 修改 status 导致 Reviewer/RiskGuard 破坏 | 不改现有 status，新增字段 |
| 外部平仓原因不明却误判为 SL | 默认 `external_unknown` 不记 SL，只有证据充分才记 |
| 风控强平后立刻重新开仓 | 可使用独立 risk cooldown，不复用 SL cooldown |

### FR-005 BehavioralCritic 字段契约统一

关联：RQ-005  
优先级：P1

需求：

- schema、prompt、fallback、PositionAnalyst consumer 使用同一套字段。
- 推荐保留业务语义更清晰的字段：
  - `counter_recommendation`
  - `confidence_in_challenge`
- 兼容旧字段一个版本窗口：
  - `counter_action` -> `counter_recommendation`
  - `confidence` -> `confidence_in_challenge`

实现路径：

1. 修改 `BEHAVIORAL_CRITIC_SCHEMA`。
2. `BehavioralCritic._rule_fallback()` 输出 schema 字段。
3. `PositionAnalyst._arbitrate()` 双读，优先新字段。
4. 增加 schema validation tests：
   - prompt 字段完整
   - fallback 字段完整
   - 旧字段兼容
   - 缺字段时默认值可用

避免并发症：

| 并发症 | 缓解 |
|---|---|
| 一次性删旧字段导致历史 payload 失败 | PA 双读一个版本窗口 |
| schema 不透传额外字段后逻辑失效 | 测试必须覆盖 LLM 严格 schema 输出 |

### FR-006 testnet Go/No-Go 文案收敛

关联：RQ-006  
优先级：P1

需求：

- `7 PASS / 3 SKIP` 只能支持“小额 live 灰度”，不能写成无 caveat 扩容。
- T2/T3/T7 标为扩容前建议补验项。
- T5 `algo_count=2` 只能证明 standalone SL 能挂成功，不证明单一保护单 owner 始终成立。

实现路径：

1. 修改 `verify_okx_testnet_real.py` 报告生成逻辑。
2. 更新 `docs/generated_reports/OKX执行语义testnet验收报告_20260527_150518.md` caveat，或生成新的复核报告。
3. `docs/to-do-list.md` 当前 Go/No-Go 改为：
   - 小额 live 灰度：CONDITIONAL GO
   - live 扩容：NO-GO until RQ-001 至 RQ-004 通过

### FR-007 network 测试可重复

关联：RQ-007  
优先级：P1

需求：

- 默认 `pytest` 继续不依赖外网。
- `network` 标记下也不能包含无限流测试。
- 依赖 `data/klines.db` 的 legacy 测试必须使用 fixture 造临时 SQLite，或显式 skip 并说明准备方式。

实现路径：

1. 把 `test_kline.py` 改为 `scripts/manual_kline_stream.py`，或给测试加 bounded timeout。
2. `test_indicators.py`、`test_backtest.py`、`test_strategy.py` 使用 fixture 建表和样本数据。
3. README/runbook 不再把这组 Binance Kline legacy 用例当 OKX/Telegram 冒烟验收。

### FR-008 状态文件命名空间

关联：RQ-008  
优先级：P1

需求：

- `run_agents.py` 在 `USE_TESTNET=true` 时不得默认读写 live 状态文件。
- 所有状态路径由配置生成，不允许各 Agent 硬编码 `data/positions.json`。
- 支持 `STATE_NAMESPACE=live|testnet|paper`，默认：
  - live -> `data/positions.json`
  - testnet -> `data/testnet_positions.json`
  - paper -> `data/paper_positions.json`

覆盖文件：

- positions
- risk_state
- live_order_events.jsonl
- live_position_lifecycle.json
- halt_state
- riskguard_state

迁移要求：

- 不自动搬迁 live 文件。
- 首次启用 testnet/paper 命名空间时创建空状态。
- 启动 banner 打印所有状态路径。

避免并发症：

| 并发症 | 缓解 |
|---|---|
| testnet 污染 live ledger | namespace 强制分流 |
| 老脚本找不到状态文件 | 保留 live 默认路径，testnet/paper 通过配置切换 |
| 自动迁移误删 live 状态 | 不做自动迁移，只创建新 namespace |

### FR-009 Agent health supervisor

关联：RQ-009  
优先级：P2

需求：

- Orchestrator 记录每个 Agent 的：
  - setup status
  - task alive
  - last_tick
  - last_message_at
  - queue backlog
  - DLQ count
  - degraded flags
- Telegram `/status` 展示 health summary。
- 关键 Agent setup 失败或 loop 停止时发 `telegram_alert`。

分阶段策略：

1. 阶段 1 只观测和告警，不自动重启。
2. 阶段 2 对无状态 research agent 支持自动重启。
3. 阶段 3 再评估 trading executor 是否允许自动重启；默认不自动重启执行器。

### FR-010 新闻 symbol mention 精准匹配

关联：RQ-010  
优先级：P2

需求：

- 禁止用裸 substring 判定 ticker mention。
- 支持以下高置信格式：
  - `$SYMBOL`
  - `(SYMBOL)`
  - `SYMBOL/USDT`
  - `SYMBOL token`
  - 英文/数字边界包围的独立 token
- 新闻信号增加：
  - `source`
  - `freshness_sec`
  - `confidence`

避免并发症：

| 并发症 | 缓解 |
|---|---|
| `OP` 命中普通英文片段 | token boundary + whitelist |
| 过严匹配漏掉真实新闻 | `$SYMBOL` 和 `(SYMBOL)` 始终高置信 |
| 弱新闻被 Judge 当强事实 | 下游按 `confidence/freshness_sec` 降权 |

## 7. 推荐开发顺序

1. 建立 P0 回归测试骨架，先让失败显性化。
2. 修复 `_replace_protective_sl()` cancel failure fail-closed。
3. 改 EarlyReview，通过 root executor 移动 SL。
4. 收敛 Agent close path，移除 `agents/trading/executor.py` 的保护单 `cancel_order()`。
5. 扩展 `execution_result.v2` close cause，并修正 Judge SL hit 逻辑。
6. 跑 P0 定向回归和默认全量 pytest。
7. 处理 BehavioralCritic schema。
8. 修正文档和 testnet Go/No-Go 口径。
9. 整理 network tests 和 state namespace。
10. 做 health supervisor 和新闻 mention 匹配。

## 8. 扩容准入

live 扩容必须同时满足：

- P0 自动化验收全部 PASS。
- `python3 -m pytest -q` 无失败。
- OKX testnet 新增保护单生命周期用例 PASS。
- `rg -n "cancel_order\\(" agents/trading/executor.py` 不再命中保护单取消路径。
- Judge 对 `risk_alert`、`close_all`、`price_fetch_failed` 不再记录 SL hit。
- `docs/to-do-list.md` 和 testnet 报告不再宣称无 caveat 扩容。
