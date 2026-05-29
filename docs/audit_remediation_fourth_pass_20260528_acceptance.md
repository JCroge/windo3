# 第四次审计整改验收报告

- 整改窗口：2026-05-29
- 整改基线：第三次审计后 `807 passed / 4 deselected / 1 warning`
- 闭环目标：`docs/generated_reports/系统性审计报告_20260528_第四次.md` 中 1 个 P0 + 2 个 P1（F4-001 / F4-002 / F4-003）
- 工作分支：`feat/audit-fourth-pass-20260529`
- OpenSpec change：`audit-fourth-pass-blockers`
- Comet 状态文件：`openspec/changes/audit-fourth-pass-blockers/.comet.yaml`

## 1. 范围

| FR | 等级 | 主题 |
|---|---|---|
| F4-001 | P0 | reduce 失败回参 Agent 误广播为 `risk_reduced` |
| F4-002 | P1 | `pnl_resolved` / `pnl_mismatch` 总线事件未透传 `final_close_cause` / `close_evidence` / 幂等键 |
| F4-003 | P1 | OKX 真实新 SL 未使用 owner-tag clOrdId |

详细需求见 OpenSpec delta spec：

- `openspec/changes/audit-fourth-pass-blockers/specs/reduce-result-propagation/spec.md`
- `openspec/changes/audit-fourth-pass-blockers/specs/pnl-resolution-bus-events/spec.md`
- `openspec/changes/audit-fourth-pass-blockers/specs/protective-sl-owner-tag/spec.md`

## 2. 验收命令

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit5_pycache python3 -m compileall -q .
python3 -m pytest -q
python3 -m pytest -q -m network
env BOT_INSTANCE_ID=audit5b STATE_NAMESPACE=testnet python3 verify_okx_testnet_real.py --case T0,T1,T6
```

## 3. 验收结果

### 3.1 字节码扫描

`compileall` 静默退出，无 SyntaxError。

### 3.2 默认全量回归

```
860 passed, 4 deselected, 1 warning in 150.57s
```

相对第三次审计后 `807 passed` 净增 53 case，覆盖：

- `test_owner_tag_clord_id_callsites.py` 8 case（F4-003）
- `test_pnl_resolved_event_contract.py` 19 case（F4-002）
- `test_reduce_failure_propagation.py` 25 case（F4-001）
- `test_partial_tp_lifecycle.py` clord_id 断言改为 owner-tag（避免回归）
- `test_execution_result_contract.py` 风控减仓 / partial_tp 两个契约 case 同步到 actual reduce_pct 语义

### 3.3 network 分层回归

```
4 passed, 860 deselected, 1 warning in 15.84s
```

### 3.4 OKX 真实 testnet 冒烟

`verify_okx_testnet_real.py --case T0,T1,T6`，env `BOT_INSTANCE_ID=audit5b STATE_NAMESPACE=testnet`：

| Case | 结果 | 关键证据 |
|---|---|---|
| T0 Account Config | PASS | testnet posMode 探测 = `long_short_mode` |
| T1 Market Open + Attached TP/SL | PASS | OKX 回包 `algoClOrdId="catestneaudit5BTCUSD39d0fd1f36c9"` — 含 owner-tag 前缀 `ca + testne + audit5 + BTCUSD + 12 hex`，证明 F4-003 在真实 OKX 链路生效 |
| T6 Move SL | PASS | `_replace_protective_sl` 切换后挂单成功，无回归 |

报告写入 `docs/generated_reports/OKX执行语义testnet验收报告_20260529_112117.md`，原始 JSONL 在 `data/testnet_verify_20260529_112117.jsonl`。

## 4. 验收明细

### 4.1 F4-001 reduce-result-propagation

| AC | 实现 | 测试 |
|---|---|---|
| Agent 三路径必须共用 `_classify_reduce_outcome` 分流 | `agents/trading/executor.py:_classify_reduce_outcome` 6 分支静态方法；PositionAnalyst 部分平 / portfolio_exposure / partial_tp 三处都调用 | `TestClassifyReduceOutcome` 7 / `TestPositionAnalystPartialClose` 3 / `TestPortfolioExposureReduce` 3 / `TestPartialTpReduce` 3 |
| pre-trade 失败 → `rejected` | `sl_cancel_failed` / `sl_restore_failed` 走 rejected 分支 | covered |
| 交易所 reject → `reduce_failed` | `reduce_rejected` 走 reduce_failed 分支 | covered |
| dust_closed → `executed` + `action=close` + `reduce_origin=True` | `_classify_reduce_outcome` 分支 4 + 三个 callsite 的 action_override 处理 | covered |
| `reduce_ok=True && ok=False` → `risk_reduced` + `protection_failed=True` + actual reduce_pct | 分支 5 | covered |
| 干净 ok=True → `risk_reduced` + 实际 pct + 无 protection_failed | 分支 6 | covered |
| RiskGuard rejected/reduce_failed 不缩敞口 | `_handle_execution_result` 显式 early-return | `TestPortfolioRiskGuardReduceHandling` 5 |
| RiskGuard protection_failed 缩 + 发 risk_alert | `risk_reduced` 分支增加 `risk_alert{type='protection_failed'}` publish | covered |
| RiskGuard dust_closed 移除 symbol | `executed/close` 分支已处理；`reduce_origin=True` 仅日志增强 | covered |
| Telegram 干净减仓简短文案 | `_handle_execution` risk_reduced 分流 | `TestTelegramReduceMessages` 4 |
| Telegram protection_failed 故障文案含 `protective_update_state` + `protection_state=unknown` | 故障分支 | covered |
| Telegram rejected/reduce_failed 不发减仓文案 | `_handle_execution` 三分支均不匹配，自然 silent | covered |
| Telegram protection_failed risk_alert 进入 critical 推送 | `critical_types` 加 `protection_failed`，`type_names` 加对应 emoji | covered |

### 4.2 F4-002 pnl-resolution-bus-events

| AC | 实现 | 测试 |
|---|---|---|
| `make_resolution_id` 4 级优先级（corr → sup → key → pos） | `utils/realized_pnl_resolver.py:make_resolution_id` | `TestMakeResolutionId` 8 |
| `Reconciler.auto_resolve_pending` summary 含 `close_cause / final_close_cause / is_strategy_stop / close_evidence / resolution_id` | `utils/reconciliation.py:auto_resolve_pending` 字段补齐 | `TestReconcilerSummaryFields` 1 |
| `_resolve_external_close_async` publish 含同字段集 | `agents/trading/executor.py:_resolve_external_close_async` payload 增加 3 键 | `TestResolveExternalCloseAsyncPublish` 3 |
| `correction is None` && status 非 final/mismatch → 跳过发布 + warning | early-return + `logger.warning` | covered |
| `_run_reconciliation` publish 透传同字段集 | payload 增加 5 键 | `TestRunReconciliationPublish` 1 |
| Judge / Reviewer 优先按 `resolution_id` LRU 去重 | `agents/trading/judge.py` / `reviewer.py` 调换 fall-back 链 | `TestSubscriberDeduplication` 6 |
| 缺 `resolution_id` 时回退 `correction_event_id`（fail-safe） | 三元 `or` 链顺序 | covered |
| Telegram 不强制 `resolution_id` 去重 | 未触碰 `_close_notify_cache` 60s window | covered |

### 4.3 F4-003 protective-sl-owner-tag

| AC | 实现 | 测试 |
|---|---|---|
| `_replace_protective_sl` 使用 owner-tag clOrdId | `executor.py:1464` 切到 `_make_owner_tag_clord_id` | `TestReplaceProtectiveSlOwnerTag` 1 |
| `open_position_with_plan` attached SL 使用 owner-tag | `executor.py:1950` 切换 | `TestAttachedSlOwnerTag` 1 |
| legacy `_open_position` 独立 SL 使用 owner-tag 并写入 `position['sl_algo_clord_id']` | `executor.py:1068-1095` 增加 `sl_clord_id` 变量 + 写字段 | `TestLegacyOpenPositionOwnerTag` 1 |
| `_make_sl_clord_id` 仍可调用（cleanup 兼容） | 保留并标 `[DEPRECATED]` 注释 + 准确说明 | `test_legacy_make_sl_clord_id_still_callable_for_cleanup` |
| 非 OKX 路径不受影响 | 三处条件 `if self.exchange_id == 'okx'` 保留 | covered（mock 单测） |
| live 缺 `BOT_INSTANCE_ID` banner WARNING | `utils/state_paths.py:as_banner_lines` 追加 | `TestBotInstanceIdBanner` 4 |
| testnet/paper 不打 BOT_INSTANCE_ID WARNING | namespace 判定收窄到 live | covered |
| 真实 OKX testnet 新挂 SL 含 owner-tag 前缀 | 见 §3.4 T1 证据 | OKX 回包验证 |

## 5. 实施顺序与提交链

按风险递增顺序：

1. F4-003 (Tasks 1-3): banner WARNING → replace/attached SL → legacy SL + DEPRECATED
2. F4-002 (Tasks 4-8): make_resolution_id → Reconciler summary → external close async → run_reconciliation → Judge/Reviewer dedup
3. F4-001 (Tasks 9-14): _classify_reduce_outcome → 3 callsite 接入 → RiskGuard 分流 → Telegram 文案
4. 收尾 (Tasks 15-19): compileall → pytest → network → testnet 冒烟 → 验收报告 → 文档同步

每个 Task 一次原子 commit，message 带 `[F4-001/002/003]` 前缀；scope creep 在 Task 13 同 commit 顺手加了 `_effective_balance_cap` config（与逻辑账户 cap 300 对齐，已用户确认接纳）。

## 6. Go/No-Go

| 范围 | 第四次整改后 |
|---|---|
| 本地开发 | GO |
| paper / mock | GO |
| 小额 live 灰度 | GO（继续 ~300 USDT cap） |
| live 扩容 | **CONDITIONAL GO** — 解除 NO-GO，前置全部完成；扩容前需确认运维 SOP 把 `BOT_INSTANCE_ID` 写入 systemd / pm2 启动配置，避免单 bot 重启时丢失 owner-tag 区分能力 |

## 7. 附件

- 全量 pytest 输出：见 §3.2
- network 输出：见 §3.3
- OKX testnet 报告：`docs/generated_reports/OKX执行语义testnet验收报告_20260529_112117.md`
- OKX testnet JSONL：`data/testnet_verify_20260529_112117.jsonl`
- Plan：`docs/superpowers/plans/2026-05-29-audit-fourth-pass-blockers.md`
- Design Doc：`docs/superpowers/specs/2026-05-29-audit-fourth-pass-blockers-design.md`
- OpenSpec change：`openspec/changes/audit-fourth-pass-blockers/`
