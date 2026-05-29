## 1. F4-003 Owner Tag clOrdId（先落，风险最低）

- [ ] 1.1 `executor.py:_replace_protective_sl` (L1424-1489) 的 `new_clord` 改为 `_make_owner_tag_clord_id(symbol)`（OKX 路径），保留 non-OKX `None`
- [ ] 1.2 `executor.py:open_position_with_plan` (L1949-1951 附近) 的 `sl_clord_id` 改为 `_make_owner_tag_clord_id(symbol)`
- [ ] 1.3 `executor.py:_open_position` (legacy, L1068-1095 附近) 在调 `_place_protective_sl` 前生成 owner-tag `clord_id`，传入 kwarg，挂单成功后写 `position['sl_algo_clord_id'] = clord_id`
- [ ] 1.4 启动 banner（`utils/state_paths.py:format_banner` 或 `run_agents.py` 入口）：当 `STATE_NAMESPACE='live'` 且 `BOT_INSTANCE_ID` 为空时打印 `WARNING: BOT_INSTANCE_ID not configured; cross-bot SL ownership cannot be proven by clOrdId.`
- [ ] 1.5 新增 `test_owner_tag_clord_id_callsites.py`：(a) replace SL 的 `algoClOrdId` 通过 `_is_owner_clord_id`；(b) attached SL 的 `attachAlgoClOrdId` 通过 `_is_owner_clord_id`；(c) legacy open SL 写入 `position['sl_algo_clord_id']`；(d) live 缺 `BOT_INSTANCE_ID` 时 banner 含 WARNING；(e) testnet 缺 `BOT_INSTANCE_ID` 时 banner 不含 WARNING
- [ ] 1.6 跑 `python3 -m pytest -q test_owner_tag_clord_id_callsites.py test_protective_cleanup_owner.py` 确认无回归

## 2. F4-002 pnl_resolved/pnl_mismatch 总线事件契约

- [ ] 2.1 `utils/realized_pnl_resolver.py` 新增模块级函数 `make_resolution_id(resolution: dict, correction: Optional[dict]) -> str`，按 design Decision 4 的优先级链生成 id
- [ ] 2.2 `utils/reconciliation.py:Reconciler.auto_resolve_pending` 的 summary 字段集补齐 `close_cause` / `final_close_cause` / `is_strategy_stop` / `close_evidence` / `resolution_id`
- [ ] 2.3 `agents/trading/executor.py:_resolve_external_close_async` (L880-921) 发布 `pnl_resolved` / `pnl_mismatch` 时透传 `final_close_cause` / `close_evidence` / `resolution_id`
- [ ] 2.4 `agents/trading/executor.py:_run_reconciliation` (L698-731) 发布 `pnl_resolved` / `pnl_mismatch` 时透传 `final_close_cause` / `close_evidence` / `resolution_id`
- [ ] 2.5 `agents/trading/judge.py` / `agents/trading/reviewer.py`（具体订阅者）增加 `_seen_resolution_ids` LRU set（容量 1024），收到重复 `resolution_id` 时直接 return
- [ ] 2.6 新增 `test_pnl_resolved_event_contract.py`：(a) `make_resolution_id` 四种优先级；(b) `_resolve_external_close_async` 透传字段；(c) `_run_reconciliation` 透传字段；(d) `auto_resolve_pending` summary 含字段；(e) Judge/Reviewer 同 resolution_id 第二次被忽略；(f) 老 payload 缺 resolution_id 时下游 fail-safe
- [ ] 2.7 跑 `python3 -m pytest -q test_pnl_resolved_event_contract.py test_external_close_final_cause.py test_exchange_realized_pnl_resolver.py` 确认无回归

## 3. F4-001 Agent reduce 分流（最后落，风险最高）

- [ ] 3.1 `agents/trading/executor.py` 新增私有静态方法 `_classify_reduce_outcome(result, requested_pct) -> dict`，按 design Decision 1 输出 `{status, reason, actual_reduce_pct, protection_failed, protection_state, protective_update_state, warnings}`
- [ ] 3.2 改写 PositionAnalyst 部分平路径（L225-284）：`if result is None` → rejected unknown_none_result（保留现有）；否则调用 `_classify_reduce_outcome`，按返回的 `status` 走 `rejected/reduce_failed/risk_reduced` 分支
- [ ] 3.3 改写 portfolio_exposure / correlation_risk 路径（L438-454）：调用 `_classify_reduce_outcome`，失败/拒绝必须 publish `rejected/reduce_failed`，禁止再写死 `risk_reduced`
- [ ] 3.4 改写 partial_tp_1 / partial_tp_2 路径（L1004-1020）：同样接入 `_classify_reduce_outcome`
- [ ] 3.5 `agents/trading/portfolio_risk_guard.py:_on_execution_result` (L144-147)：rejected/reduce_failed 不缩敞口；risk_reduced 按 payload `reduce_pct`（已是 actual）缩；`protection_failed=True` 额外 publish `risk_alert{type='protection_failed'}`
- [ ] 3.6 `agents/trading/telegram_notifier.py` 的 `risk_reduced` 分支（L129-132）：按 `protection_failed` 分流；`protection_failed=True` 时输出含 `protective_update_state` 的故障文案
- [ ] 3.7 新增 `test_reduce_failure_propagation.py`：(a) `cancel_failed`（reduce_ok=False）→ status=rejected/reduce_failed，无 risk_reduced；(b) `reduce_rejected` 且 `restore_ok=True` → status=rejected/reduce_failed，敞口不变；(c) `replace_failed`（reduce_ok=True, ok=False）→ risk_reduced + protection_failed，RiskGuard 缩 actual 并发 protection_failed alert；(d) Telegram 三种文案分流；(e) 三路径均通过 `_classify_reduce_outcome`（mock 单元注入验证）
- [ ] 3.8 跑 `python3 -m pytest -q test_reduce_failure_propagation.py test_reduce_protective_sl_lifecycle.py` 确认无回归

## 4. 全量回归与验证收尾

- [ ] 4.1 全量回归：`env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit5_pycache python3 -m compileall -q .`
- [ ] 4.2 默认全量回归：`python3 -m pytest -q` 必须从 807 升至 ≥822（+15 case 新增最小预期）
- [ ] 4.3 network 分层回归：`python3 -m pytest -q -m network` 仍 4 通过
- [ ] 4.4 OKX testnet 冒烟：`python3 verify_okx_testnet_semantics.py`（至少跑 reduce + external close 各一次场景，确认 owner-tag clOrdId 在真实 OKX 上能成功下发与撤单）
- [ ] 4.5 更新 `CLAUDE.md` 当前事实段：第四次审计阻断闭环 + 新基线
- [ ] 4.6 更新 `docs/to-do-list.md`：F4-001/F4-002/F4-003 标为已闭环，移到"已关闭事项"
- [ ] 4.7 撰写本次整改的验收报告 `docs/audit_remediation_fourth_pass_20260528_acceptance.md`，含验收命令与三个 FR 的 AC 列表
