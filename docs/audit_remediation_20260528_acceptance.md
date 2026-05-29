# 2026-05-28 系统审计整改验收文档

更新日期：2026-05-28  
关联 PRD：`docs/audit_remediation_20260528_prd.md`  
关联审计报告：`docs/generated_reports/系统性审计报告_20260528.md`

## 1. 验收结论规则

| 结论 | 条件 |
|---|---|
| PASS | P0/P1 全部通过，OKX testnet 保护单生命周期补验通过，文档 Go/No-Go 已同步 |
| CONDITIONAL PASS | P0 全过，P1 有非执行安全项遗留且有 owner、风险说明和豁免 |
| FAIL | 任一 P0 失败；或 testnet/live 状态隔离失败；或文档仍声明无 caveat 扩容 |

当前预期状态：FAIL for live expansion。只有完成本验收 P0 后，才能重新评审扩容。

## 2. 验收前置条件

- 禁止使用 production key 执行 testnet 验收。
- 验收前备份 `data/*.json` 和 `data/*.jsonl`。
- 验收期间不得同时运行 `run_agents.py`、旧 `main.py`、旧 `live_trading.py`。
- OKX testnet 必须确认 `set_sandbox_mode(True)` 生效。
- 所有新增测试不得依赖真实 live 状态文件。

## 3. 自动化命令

基础编译：

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
```

P0 定向回归：

```bash
python3 -m pytest -q \
  test_partial_tp_lifecycle.py \
  test_okx_posmode_executor.py \
  test_execution_result_contract.py \
  test_llm_schema.py
```

本轮必须新增或扩展的测试文件建议：

```bash
python3 -m pytest -q \
  test_protective_sl_owner.py \
  test_judge_close_cause.py \
  test_behavioral_critic_contract.py \
  test_state_namespace.py
```

全量回归：

```bash
python3 -m pytest -q
```

network 分层验收：

```bash
python3 -m pytest -q -m network
```

通过要求：

- `test_kline.py` 不得再无限挂住。
- `network` 测试若缺外部数据，必须 skip 并给出准备说明，不能因临时 cwd 无 `data/klines.db` 失败。

静态扫描：

```bash
rg -n "cancel_order\\(" agents/trading/executor.py
rg -n "pos\\[['\"]stop_loss['\"]\\]|_save_positions\\(" agents/trading/executor.py
rg -n "status == ['\"]force_closed['\"]|_record_sl_hit" agents/trading/judge.py
rg -n "前置阻断已解除|live 扩容：GO|必测项全部通过.*扩容" \
  docs README.md CLAUDE.md \
  -g '!docs/audit_remediation_20260528_acceptance.md'
```

通过要求：

- Agent close path 不再直接撤保护单。
- EarlyReview 不再直接保存本地 SL。
- Judge 的 `force_closed` 分支必须由 close cause 保护。
- 当前文档不得再用过期 GO 结论指导 live 扩容。

## 4. P0 验收项

| ID | 关联需求 | 验收项 | 方法 | 通过标准 |
|---|---|---|---|---|
| AC-P0-001 | FR-001 | EarlyReview 收紧 SL 走 root executor | mock `move_protective_sl/_replace_protective_sl` | 被调用一次，参数含 symbol/new_sl/reason |
| AC-P0-002 | FR-001 | EarlyReview 替换失败不改本地 SL | mock 返回失败 | `stop_loss` 保持旧值，`_save_positions()` 不因新 SL 被调用 |
| AC-P0-003 | FR-001 | EarlyReview 替换成功才保存 | mock 返回成功 | 本地 `stop_loss/sl_algo_id/protection_state` 与 root executor 状态一致 |
| AC-P0-004 | FR-002 | 撤旧 SL 失败不挂新 SL | mock `_cancel_protective_sl=False` | `_place_protective_sl` 不被调用，函数返回 False |
| AC-P0-005 | FR-002 | 撤旧 SL 失败写保护失败状态 | 单测 position dict | `sl_sync_state=failed`，`protection_state=unknown`，`last_protection_error=sl_cancel_failed` |
| AC-P0-006 | FR-002 | live OKX 撤旧失败 halt | `exchange_id=okx,testnet=False` | `_halt_symbol(symbol, reason="sl_cancel_failed")` 被调用 |
| AC-P0-007 | FR-003 | trade_decision close 不直接 `cancel_order` | mock close position | Agent 只调用 `close_position()` |
| AC-P0-008 | FR-003 | risk_alert close 不直接 `cancel_order` | emergency/flash/position_danger 参数化 | Agent 不调用 `cancel_order()`；close result 含 cleanup state |
| AC-P0-009 | FR-003 | close_all 不直接 `cancel_order` | 两个持仓并发 close | 每个 symbol 仅调用 root close path |
| AC-P0-010 | FR-003 | local stop 不直接 `cancel_order` | `stop_loss/take_profit/price_fetch_failed` 参数化 | 由 `close_position()` 统一清理保护单 |
| AC-P0-011 | FR-003 | close 后无本 owner orphan algo | mock OKX open algos | close 返回 `protective_cleanup_state=cleaned` 或失败时 halt |
| AC-P0-012 | FR-004 | execution_result close cause 字段完整 | 所有 close source 参数化 | payload 含 `exit_reason/close_cause/is_strategy_stop/is_risk_forced` |
| AC-P0-013 | FR-004 | Judge 只对策略 SL 记 SL hit | feed `local_stop_loss/exchange_sl` | `_record_sl_hit()` 被调用 |
| AC-P0-014 | FR-004 | Judge 不把风控/全平计 SL | feed `risk_alert/close_all/daily_hard_stop/price_fetch_failed` | `_record_sl_hit()` 不被调用 |
| AC-P0-015 | FR-004 | 下游兼容旧 status | Reviewer/RiskGuard/Telegram 消费新 payload | 不抛 KeyError，旧 status 仍可识别 |

P0 Go 标准：AC-P0-001 至 AC-P0-015 全部通过。

## 5. P1 验收项

| ID | 关联需求 | 验收项 | 方法 | 通过标准 |
|---|---|---|---|---|
| AC-P1-001 | FR-005 | BehavioralCritic schema 字段统一 | schema/prompt/fallback 对照 | 都输出 `counter_recommendation/confidence_in_challenge` |
| AC-P1-002 | FR-005 | PositionAnalyst 兼容旧字段 | 输入旧 payload | 能读取 counter 建议，无 KeyError |
| AC-P1-003 | FR-006 | testnet 报告 caveat | 生成报告 | `7 PASS / 3 SKIP` 不输出无 caveat 扩容结论 |
| AC-P1-004 | FR-006 | T2/T3/T7 标扩容前补验 | 检查报告和 to-do | 明确列为补验项 |
| AC-P1-005 | FR-007 | network 测试有限执行 | 跑 `pytest -q -m network` | 不无限挂住 |
| AC-P1-006 | FR-007 | legacy DB 测试可重复 | 临时 SQLite fixture | 不依赖项目根 `data/klines.db` |
| AC-P1-007 | FR-008 | testnet 状态路径隔离 | `USE_TESTNET=true` dry run | positions/risk/ledger/halt 路径带 testnet namespace |
| AC-P1-008 | FR-008 | paper 状态路径隔离 | paper config dry run | 不写 live ledger |
| AC-P1-009 | FR-008 | 启动 banner 打印状态路径 | capture log | 可见 namespace 和各状态文件路径 |
| AC-P1-010 | FR-008 | live 默认兼容 | 不设置 namespace | 仍读写既有 live 默认路径 |

P1 Go 标准：AC-P1-001 至 AC-P1-010 全部通过，或由项目负责人签字豁免且不涉及执行安全。

## 6. P2 验收项

| ID | 关联需求 | 验收项 | 方法 | 通过标准 |
|---|---|---|---|---|
| AC-P2-001 | FR-009 | Orchestrator health snapshot | mock agent setup/loop/backlog | 输出每个 agent health |
| AC-P2-002 | FR-009 | `/status` 展示 degraded | mock LLM/data degraded | Telegram 状态可见 |
| AC-P2-003 | FR-009 | executor 不自动重启 | kill executor task mock | 只告警，不自动重启交易执行器 |
| AC-P2-004 | FR-010 | 短 ticker 不误报 | `OP/STX/INJ` 文本样例 | 普通英文片段不命中 |
| AC-P2-005 | FR-010 | 高置信格式命中 | `$OP`、`(STX)`、`INJ/USDT` | 正确命中并带 confidence |
| AC-P2-006 | FR-010 | 新闻 provenance | mock news event | 含 `source/freshness_sec/confidence` |

P2 不阻断 P0 修复后的重新灰度，但必须进入后续排期。

## 7. OKX testnet 补验

在原 T0-T9 基础上补充：

| Case | 操作 | 必须记录 | 通过标准 |
|---|---|---|---|
| T10 | EarlyReview 触发 move SL | old algo、new algo、local position | 旧 SL 已撤或失效，新 SL 唯一有效，本地 `sl_algo_id` 指向新单 |
| T11 | cancel old SL failure mock/real fault injection | cancel response、place call count、position state | 不挂新 SL，状态 unknown/failed，live 模式 symbol halt |
| T12 | risk_alert close | close order raw、cancel algo raw、post algos | 平仓后无本 owner 残留 algo |
| T13 | local stop_loss close | execution_result、Judge state | `exit_reason=local_stop_loss`，Judge 记录一次 SL hit |
| T14 | close_all/daily hard stop | execution_result、Judge state | `exit_reason=system_close_all`，Judge 不记录 SL hit |
| T15 | external exchange SL | ledger/position removed/execution_result | 能归因为 `exchange_sl` 时记 SL；无法归因时 `external_unknown` 不记 SL |

OKX 扩容 Go 标准：

- T0/T1/T4/T5/T6/T8/T9 继续 PASS。
- T10 至 T15 PASS。
- T2/T3 net_mode 若仍 SKIP，必须在扩容结论中保留 caveat。
- T7 若仍 mock_only，必须说明 live 风险和人工复核路径。

**2026-05-28 状态**：long_short_mode 子账户跑 `--case all` 13 PASS / 3 SKIP（报告 `docs/generated_reports/OKX执行语义testnet验收报告_20260528_080900.md`），其中 T0/T1/T4-T6/T8-T15 PASS、T2/T3 SKIP、T7 SKIP。切到 net_mode 子账户后单独跑 `--case T0,T2,T3` 3 PASS（报告 `docs/generated_reports/OKX执行语义testnet验收报告_20260528_080723.md`）；T2/T3 net_mode caveat 解除。配套修复：`verify_okx_testnet_real.py` 的 `case_t2` / `case_t3` 改为 self-contained——case 内自己 `open_position_with_plan('long', plan)` 建仓再做 partial reduce / full close，避免 main loop 每个 case 之前的 `_safe_close_remaining` 把账户拉回 flat 导致前置仓位丢失。T7 仍保留 `mock_only`（OKX testnet 不会自然产出 51169/51205，real_attempt 模式需要手工通过 OKX UI 干预触发；已在 `verify_okx_testnet_semantics.py` mock 矩阵覆盖），按本节末尾 caveat 处理。

## 8. 接口回参验收

### 8.1 ProtectiveSLResult

任何保护单移动/替换/取消的结构化结果必须包含：

| 字段 | 要求 |
|---|---|
| `ok` | bool |
| `symbol` | 内部 symbol |
| `operation` | `move_protective_sl/replace_protective_sl/cancel_protective_sl` |
| `reason` | 机器可读原因 |
| `old_sl_algo_id` | 旧 algo id，可空 |
| `new_sl_algo_id` | 新 algo id，可空 |
| `cancel_ok` | bool |
| `place_ok` | bool |
| `sl_sync_state` | `active/pending/failed/unknown` |
| `protection_state` | `protected/local_fallback/unprotected/unknown/halted` |
| `halt_required` | bool |
| `timestamp` | float |

验收标准：

- cancel failed 时 `place_ok=false`。
- place failed 时 `cancel_ok=true/place_ok=false`，并进入保护失败流程。
- 成功时 `protection_state=protected` 且 `new_sl_algo_id` 非空。

### 8.2 execution_result.v2 close cause

close 类 payload 必须包含：

| 字段 | 要求 |
|---|---|
| `exit_reason` | 机器可读归因 |
| `close_cause` | 更细原因，保留原 reason 语义 |
| `is_strategy_stop` | bool |
| `is_risk_forced` | bool |
| `result.exit_reason` | 与顶层一致 |
| `result.close_cause` | 与顶层一致 |
| `result.protective_cleanup_state` | `cleaned/none/failed/unknown` |

验收标准：

- 不删除现有 `schema_version/status/action/source/request_id/correlation_id/reason/result/timestamp`。
- 没有 request_id 时仍生成 correlation_id。
- 下游 consumer 对缺少新字段的历史消息有默认兼容。

## 9. 文档验收

必须同步检查：

- `docs/to-do-list.md`
- `docs/generated_reports/OKX执行语义testnet验收报告_20260527_150518.md` 或新的 testnet 复核报告
- `README.md`
- `docs/runbook.md`
- `docs/handoff.md`
- `CLAUDE.md`

验收命令：

```bash
rg -n "前置阻断已解除|live 扩容：GO|必测项全部通过.*扩容" \
  docs README.md CLAUDE.md \
  -g '!docs/audit_remediation_20260528_acceptance.md'
```

通过标准：

- 当前文档明确：P0 未完成前 live 扩容 NO-GO。
- 历史报告若保留旧结论，必须有新文档或注释说明已被 2026-05-28 审计 supersede。
- `docs/to-do-list.md` 的当前 Go/No-Go 与本验收一致。

## 10. 最终 Go/No-Go 表

| 条件 | Go 标准 |
|---|---|
| P0 | AC-P0-001 至 AC-P0-015 全过 |
| P1 | AC-P1-001 至 AC-P1-010 全过，或非执行安全项有豁免 |
| 全量测试 | `python3 -m pytest -q` 无失败 |
| OKX testnet | T10 至 T15 保护单/close cause 补验通过 |
| 静态扫描 | Agent close path 不直接 `cancel_order()`；EarlyReview 不直接存本地 SL |
| Judge | 风控强平、全平、价格失败不再污染 SL cooldown |
| 文档 | 不再声明无 caveat live 扩容 |

满足全部条件后，结论可从 live 扩容 NO-GO 调整为 CONDITIONAL GO，并先执行 24 小时小额扩容灰度。
