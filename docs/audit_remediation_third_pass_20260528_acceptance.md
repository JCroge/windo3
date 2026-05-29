# 2026-05-28 第三次审计整改验收文档

更新日期：2026-05-28  
关联 PRD：`docs/audit_remediation_third_pass_20260528_prd.md`  
关联历史验收：`docs/audit_remediation_20260528_acceptance.md`、`docs/exchange_realized_pnl_ledger_acceptance.md`  
当前预期结论：FAIL for live expansion，直到本文件 P0 全部通过。

## 1. 验收结论规则

| 结论 | 条件 |
|---|---|
| PASS | P0/P1/P2 全部通过，全量测试通过，文档 Go/No-Go 同步 |
| CONDITIONAL PASS | P0 全部通过；P1/P2 有 owner、风险说明和补验排期 |
| FAIL | 任一 P0 失败；或文档仍声明 live 扩容前置阻断已全部解除；或缩仓/cleanup 任一路径无法证明保护单 owner 安全 |

live 扩容只能在 PASS 或项目负责人书面接受 CONDITIONAL PASS 后重新评审。P0 未过时不得扩容。

## 2. 验收前置条件

- 验收前备份 `data/*.json`、`data/*.jsonl`、`data/testnet_*`。
- 验收期间不得同时运行 `run_agents.py`、旧 `main.py` 或旧 `live_trading.py`。
- OKX testnet 验收必须确认 `set_sandbox_mode(True)` 生效，禁止使用 production key。
- 所有新增测试必须使用临时 state path，不得写真实 live ledger。
- 不允许用“默认全量 pytest 通过”替代本文件 P0 定向验收。

## 3. 自动化命令

基础编译：

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
```

本轮必须新增或扩展的定向测试：

```bash
python3 -m pytest -q \
  test_reduce_protective_sl_lifecycle.py \
  test_protective_cleanup_owner.py \
  test_external_close_final_cause.py \
  test_symbol_mentions.py
```

兼容回归：

```bash
python3 -m pytest -q \
  test_partial_tp_lifecycle.py \
  test_protective_sl_owner.py \
  test_judge_close_cause.py \
  test_exchange_realized_pnl_resolver.py \
  test_live_ledger.py
```

全量回归：

```bash
python3 -m pytest -q
```

network 分层验收：

```bash
python3 -m pytest -q -m network
```

## 4. 静态扫描

### 4.1 reduce_position 不得忽略撤单结果

```bash
rg -n "_cancel_protective_sl\\(symbol, position\\)" executor.py
```

通过标准：

- `reduce_position()` 中不得出现裸调用后直接清空 `sl_order_id/sl_algo_id` 的代码。
- 必须能看到 `cancel_ok` 检查、失败返回、旧 ID 保留、保护状态标记。

### 4.2 cleanup 不得 sweep 全部 pending algo

```bash
rg -n "_cleanup_protective_orders_on_close|cancel_orders\\(" executor.py
```

通过标准：

- `_cleanup_protective_orders_on_close()` 中每次 `cancel_orders()` 前都有 owner 判断。
- owner 判断至少覆盖 exact `sl_algo_id`、exact `sl_algo_clord_id`、lifecycle known id、新 owner prefix。
- foreign/unknown algo 不调用 cancel。

### 4.3 pending 外部平仓不得回退到旧 SL 语义

```bash
rg -n "reason=[\"']exchange_sl_tp_triggered[\"']|reason=\"exchange_sl_tp_triggered\"|reason='exchange_sl_tp_triggered'" agents/trading/executor.py
```

通过标准：

- `_notify_removed_positions()` pending payload 不得使用 `exchange_sl_tp_triggered`。
- 只允许兼容层把历史 `exchange_sl_tp_triggered` 映射为 `external_pending/exchange_unknown_pending`，且 `is_strategy_stop=false`。

### 4.4 ticker substring 匹配清零

```bash
rg -n "if .* in text|base in text|symbol in text" \
  agents/research/news_researcher.py agents/trading/multi_data_collector.py
```

通过标准：

- 不得再用裸 substring 判定 ticker mention。
- 两处调用同一个 helper 或同一组严格边界规则。

## 5. P0 验收项

### 5.1 reduce_position 保护单生命周期

| ID | 验收项 | 方法 | 通过标准 |
|---|---|---|---|
| AC3-P0-001 | 撤旧 SL 失败不缩仓 | mock `_cancel_protective_sl=False`，调用 `reduce_position()` | `exchange.create_order` 不被调用；返回 `reason=sl_cancel_failed`；旧 `sl_algo_id/sl_order_id/sl_algo_clord_id` 保留 |
| AC3-P0-002 | 撤旧失败写失败状态 | 单测 position dict | `sl_sync_state=failed`，`protection_state=unknown`，`last_protection_error=sl_cancel_failed` |
| AC3-P0-003 | live OKX 撤旧失败 halt | `exchange_id=okx,testnet=False` | `_halt_symbol(symbol, reason="sl_cancel_failed")` 被调用；payload `halt_required=true` |
| AC3-P0-004 | 撤旧成功但 reduce reject 有恢复策略 | mock cancel success + create_order reject | 尝试 restore 原 SL；restore 失败时 `protection_state=unknown/halted`，不推进 `tp_filled` |
| AC3-P0-005 | 普通 risk reduce 后 residual 有保护 | `tp_advance=None` 且 reduce 成功 | 调用 residual SL replace/resize；返回 `protective_update_state=protected` |
| AC3-P0-006 | partial TP reduce 后保护失败不误报安全 | reduce 成功 + SL replace fail | `tp_filled` 可反映真实成交，但 `protection_state!=protected`，后续 add/open/reduce 被拒，告警发出 |
| AC3-P0-007 | dust 全平不重挂 SL | reduce 后剩余小于最小量 | 本地仓位删除；不调用 replace；cleanup state 可解释 |
| AC3-P0-008 | exit lock 覆盖 protection update | 并发 close/reduce mock | 同 symbol 同时只有一个动作进入临界区 |

P0 通过要求：AC3-P0-001 至 AC3-P0-008 全部通过。

### 5.2 close cleanup owner-bound

| ID | 验收项 | 方法 | 通过标准 |
|---|---|---|---|
| AC3-P0-009 | 只取消本地 known algo | pending algos 含 known + foreign | 只对 known algo 调 `cancel_orders()` |
| AC3-P0-010 | exact clOrdId owner 可取消 | algoId 不同但 `algoClOrdId == sl_algo_clord_id` | 可取消并记录 `owned_algo_ids` |
| AC3-P0-011 | 新 owner prefix 可取消 | `algoClOrdId` 含当前 namespace + bot_instance | 可取消，返回 `state=cleaned` |
| AC3-P0-012 | 历史 `sl...` 前缀不能泛化 sweep | foreign algo 使用 `slBTC...` 但不等于本地 clOrdId | 不取消，进入 `foreign_algo_ids` 或 unknown |
| AC3-P0-013 | unknown/foreign algo 阻断新开仓 | close 后发现 foreign algo | `protective_cleanup_state=foreign_algos_present/unknown`，同 symbol open 被拒或需要人工确认 |
| AC3-P0-014 | cleanup 回参透传到 execution_result | close_position mock | `result.protective_cleanup` 含 cancelled/foreign/warnings |

P0 通过要求：AC3-P0-009 至 AC3-P0-014 全部通过。

## 6. P1 验收项

### 6.1 外部平仓 final close cause 与幂等

| ID | 验收项 | 方法 | 通过标准 |
|---|---|---|---|
| AC3-P1-001 | pending payload 不计 SL | feed `closed_externally` pending | `pnl_is_final=false`，`close_cause=exchange_unknown_pending`，`is_strategy_stop=false`，Judge 不调 `_record_sl_hit()` |
| AC3-P1-002 | final exchange_sl 有证据 | resolver mock 返回 matched SL algo | `pnl_resolved.close_evidence.match_rule=sl_algo_id_exact`，`final_close_cause=exchange_sl` |
| AC3-P1-003 | final exchange_sl 只计一次 | 重放同一 `correction_event_id` 两次 | Judge `_record_sl_hit()` 只调用一次 |
| AC3-P1-004 | final external_unknown 不计 SL | final PnL 为负但无 close evidence | Judge 不调 `_record_sl_hit()`；probe_short 不递增 |
| AC3-P1-005 | final manual close 不计 SL | close fill 不匹配系统 algo/order | `close_cause=manual_close`，不污染 strategy cooldown |
| AC3-P1-006 | legacy payload 兼容 fail-safe | 缺少新字段的历史消息 | 默认不计 SL，不抛异常 |
| AC3-P1-007 | Reviewer 幂等 upsert | 重放同一 `pnl_resolved` | trade_history 不重复 |

P1 通过要求：AC3-P1-001 至 AC3-P1-007 全部通过，或对非执行安全项有明确豁免。

## 7. P2 验收项

### 7.1 新闻 ticker mention

| ID | 验收项 | 方法 | 通过标准 |
|---|---|---|---|
| AC3-P2-001 | 短 ticker 不误报 | 文本含 `options`、`stack`、`injection` | `OP/STX/INJ` 均不命中 |
| AC3-P2-002 | cashtag 命中 | `$OP rallies` | 命中 OP，`match_rule=cashtag`，confidence 高 |
| AC3-P2-003 | 括号格式命中 | `Stacks (STX) upgrade` | 命中 STX，`match_rule=paren` |
| AC3-P2-004 | pair 格式命中 | `INJ/USDT volume spikes`、`INJ-USDT` | 命中 INJ，`match_rule=pair` |
| AC3-P2-005 | helper 被两处复用 | monkeypatch helper | NewsResearcher 与 MultiDataCollector 都调用 helper |
| AC3-P2-006 | provenance 完整 | mock news_snapshot | 每条 mention 含 `source/freshness_sec/confidence/match_rule` |

P2 不阻断 P0 后的小额灰度，但不得作为 live 扩容后的长期遗留。

## 8. OKX testnet 补验

新增 T16-T19，建议写入 `verify_okx_testnet_real.py` 或独立 verifier。

| Case | 操作 | 必须记录 | 通过标准 |
|---|---|---|---|
| T16 | fault injection：reduce 前 cancel old SL 失败 | cancel response、create_order call count、local position | reduce order 未发出；旧 SL ID 保留；状态 failed/unknown |
| T17 | cancel success + reduce reject | restore SL response、local position | 若 restore 成功则 protected；若失败则 halted/unknown，不推进 TP |
| T18 | close cleanup 遇到 foreign algo | pending algos、cleanup result | foreign algo 不被撤；返回 foreign/unknown 并阻断同 symbol 新开仓 |
| T19 | external SL final evidence | matched algo/order/fill、pnl_resolved | 只有 exact SL evidence 才 `exchange_sl`；重复事件幂等 |

testnet 不能自然制造的故障允许 mock/fault injection，但报告必须明确哪些是真实 OKX、哪些是 fault injection。

## 9. 文档验收

必须同步检查：

- `docs/to-do-list.md`
- `docs/handoff.md`
- `docs/architecture.md`
- `CLAUDE.md`
- `README.md`

扫描命令：

```bash
rg -n "前置阻断已全部解除|live 扩容：GO|live 扩容 CONDITIONAL GO|live 扩容前置阻断全部解除" \
  docs README.md CLAUDE.md \
  -g '!docs/audit_remediation_20260528_acceptance.md' \
  -g '!docs/audit_remediation_third_pass_20260528_acceptance.md'
```

通过标准：

- 当前状态必须明确：第三次审计 P0 未闭环前 live 扩容 NO-GO。
- 历史文档可以保留旧验收记录，但必须写明已被第三次审计结论 supersede。
- `docs/to-do-list.md` 必须列出本轮新增 P0/P1/P2 和对应验收文档。

## 10. 最终 Go/No-Go 表

| 条件 | Go 标准 |
|---|---|
| P0 reduce lifecycle | AC3-P0-001 至 AC3-P0-008 全过 |
| P0 cleanup owner | AC3-P0-009 至 AC3-P0-014 全过 |
| P1 final cause | AC3-P1-001 至 AC3-P1-007 全过，或非扩容项有书面豁免 |
| P2 ticker mention | AC3-P2-001 至 AC3-P2-006 有排期；扩容后不得长期遗留 |
| 全量测试 | `python3 -m pytest -q` 无失败 |
| network | `python3 -m pytest -q -m network` 不挂死；缺外部依赖时 clean skip |
| OKX testnet | T16-T19 报告明确 PASS 或 fault injection PASS |
| 文档 | 不再声明第三次审计前置阻断已解除 |

满足全部 P0 且文档同步后，结论可从 live 扩容 NO-GO 调整为 CONDITIONAL GO；继续扩容前必须完成 P1 或明确豁免。
