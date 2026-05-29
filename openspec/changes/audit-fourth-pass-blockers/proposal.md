## Why

第四次审计 (`docs/generated_reports/系统性审计报告_20260528_第四次.md`) 在第三次整改之后又识别出 1 个 P0 + 2 个 P1 阻断，这些缺口会让 live 风控视图低估真实敞口、让 Judge 看不到外部 SL 触发、并让多 bot 同账户场景下的 owner 归属无法证明。三个阻断未闭环之前 live 扩容保持 NO-GO。

## What Changes

- **F4-001 (P0)**：`agents/trading/executor.py` 的 reduce 路径（PositionAnalyst 部分平、`portfolio_exposure/correlation_risk` 风控减仓、`partial_tp_1/2`）必须按 `result.reduce_ok` / `result.ok` / `result.protective_update_state` 分支处理：
  - `reduce_ok=False` → 发布 `status="rejected"` 或 `status="reduce_failed"`，禁止任何 `risk_reduced` 终态
  - `reduce_ok=True && ok=False` → 发布 `status="risk_reduced"` 但显式带 `protection_state="unknown"` 与 `protection_failed=true`，并发风控告警
  - `ok=True` → 干净的 `risk_reduced`
- **F4-001 配套**：`PortfolioRiskGuard._on_execution_result` 改用 `result.actual_reduce_amount`/`actual_reduced_pct` 缩本地敞口；reject/protection_failed 不缩；`TelegramNotifier` 区分干净减仓与 `protection_failed/restore_failed/cancel_failed/replace_failed` 故障文案
- **F4-002 (P1)**：所有 `pnl_resolved/pnl_mismatch` 发布点统一携带字段集 `{close_cause, final_close_cause, is_strategy_stop, close_evidence, resolution_id}`：
  - `Reconciler.auto_resolve_pending()` 的 summary 字段集补齐
  - `MultiExecutor._run_reconciliation()` 透传同字段集
  - `_resolve_external_close_async()` 透传 `final_close_cause` + `close_evidence`
  - 新增 `resolution_id`，优先级 `correction_event_id → supersedes_event_id → close_match_key → position_id+order_ids`
- **F4-003 (P1)**：OKX 真实新挂保护单全部走 `_make_owner_tag_clord_id()`：
  - `_replace_protective_sl()` 的 `new_clord`
  - `open_position_with_plan()` 的 attached SL `attachAlgoClOrdId`
  - legacy `_open_position()` 的独立 SL `clord_id`，并把返回 algoId 写入 `position['sl_algo_clord_id']`
  - live 启动 banner / 日志在缺 `BOT_INSTANCE_ID` 时打告警

## Capabilities

### New Capabilities

（无，本次全部是修改现有能力）

### Modified Capabilities

- `reduce-result-propagation`: Agent 层与下游订阅者按 reduce 结果结构字段（`ok / reduce_ok / replace_ok / actual_reduce_amount / protective_update_state`）分支处理，禁止失败结果被广播为干净 `risk_reduced`
- `pnl-resolution-bus-events`: `pnl_resolved/pnl_mismatch` 全部发布路径透传 `final_close_cause / close_evidence / is_strategy_stop` 并新增 `resolution_id` 幂等键
- `protective-sl-owner-tag`: 真实 OKX 新 SL（attach / replace / legacy）统一通过 owner-tag clOrdId 下发，缺 `BOT_INSTANCE_ID` 时启动告警

## Impact

- **代码**：
  - `agents/trading/executor.py` (reduce 三路径分支、reconciler publish、external close publish)
  - `agents/trading/portfolio_risk_guard.py` (`_on_execution_result` reduce 处理)
  - `agents/trading/telegram_notifier.py` (`risk_reduced` 文案分流 + 故障告警)
  - `executor.py` (`_replace_protective_sl` / `open_position_with_plan` / legacy `_open_position` 三处 clord_id；启动 banner BOT_INSTANCE_ID 告警)
  - `utils/realized_pnl_resolver.py` (`resolution_id` 生成)
  - `utils/reconciliation.py` (`auto_resolve_pending` summary 字段)
- **测试**：
  - 新增 `test_reduce_failure_propagation.py` (F4-001)
  - 新增 `test_pnl_resolved_event_contract.py` (F4-002)
  - 新增 `test_owner_tag_clord_id_callsites.py` (F4-003)
  - 现有 `test_reduce_protective_sl_lifecycle.py` / `test_external_close_final_cause.py` / `test_protective_cleanup_owner.py` 不应回归
- **风控/运行时**：解除 live 扩容 NO-GO 的最后一个前置；不影响现有 paper / mock / testnet 验收语义
- **依赖**：无新依赖
