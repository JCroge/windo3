# Comet Design Handoff

- Change: audit-fourth-pass-blockers
- Phase: design
- Mode: compact
- Context hash: 5c5bf5d5c7843ef770cd996866359a2b22d17b2701488254e1edabf0f84768b0

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/audit-fourth-pass-blockers/proposal.md

- Source: openspec/changes/audit-fourth-pass-blockers/proposal.md
- Lines: 1-50
- SHA256: f883ff61b1d738d19b965c1533124bef2ecff104e9c27526ce70580f430fe84c

```md
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
```

## openspec/changes/audit-fourth-pass-blockers/design.md

- Source: openspec/changes/audit-fourth-pass-blockers/design.md
- Lines: 1-168
- SHA256: 8d0f1ff293f3b1944e287f8ed967204dc0d578595f3a1d6179c87dc9baa28132

[TRUNCATED]

```md
## Context

第三次审计整改后系统在底层做对了三件事：`reduce_position` 返回结构化 dict、resolver 输出 `final_close_cause/close_evidence`、引入 `_make_owner_tag_clord_id`。但 Agent 层、Reconciler 发布路径、真实下单点都没切到新接口，导致 1 P0 + 2 P1 阻断（详见 `docs/generated_reports/系统性审计报告_20260528_第四次.md`）。本次设计的核心是：底层语义已经正确，只需把"调用方"统一切到正确的字段集和正确的工厂函数，不引入新的概念。

参考：
- `executor.py:reduce_position()` (root) 返回契约：见 `docs/audit_remediation_third_pass_20260528_acceptance.md`
- `utils/realized_pnl_resolver.py:_classify_close_evidence()` close_evidence 输出契约
- 第三次整改 owner tag 工厂：`executor.py:_make_owner_tag_clord_id` / `_resolve_owner_tag` / `_is_owner_clord_id`

## Goals / Non-Goals

**Goals:**
- Agent 层 reduce 三路径（PositionAnalyst 部分平、portfolio_exposure/correlation_risk、partial_tp_1/2）按 `reduce_ok / ok / protective_update_state` 分支处理，下游 RiskGuard / Telegram / Reviewer 看到的语义与 root executor 一致
- `pnl_resolved / pnl_mismatch` 三个发布点（`_resolve_external_close_async` / `_run_reconciliation` / `Reconciler.auto_resolve_pending` summary）携带同一字段集，Judge 能稳定看到 `final_close_cause` + `is_strategy_stop`，下游可以用 `resolution_id` 幂等去重
- OKX 真实新 SL 三个挂单点（attach / replace / legacy）使用 owner-tag clord_id；live 缺 `BOT_INSTANCE_ID` 时启动 banner 显式告警
- 不破坏现有 807 default + 4 network 测试基线，新增定向测试覆盖三个阻断点

**Non-Goals:**
- 不改 root `reduce_position()` 契约（第三次审计已落地）
- 不改 resolver `_classify_close_evidence` 输出（已是事实标准）
- 不改 owner tag 生成算法（不调整 prefix 长度或字符规则）
- 不引入新的 schema_version（仍是 v1，新增字段对老消费者可向后兼容）
- 不在本次扩到 Binance / 非 OKX 交易所（owner tag 仅 OKX 路径需要）
- 不重写 PortfolioRiskGuard 的本地 `_positions` 模型（仅修补 `_on_execution_result` 的 reduce 分支）

## Decisions

### Decision 1：F4-001 用 `_classify_reduce_outcome(result)` helper 收敛分流逻辑

**问题**：reduce 三路径目前各自 `if result:` → `risk_reduced`，分布在 `agents/trading/executor.py:225-233 / 442-454 / 1004-1020`，要在三处分别加分支非常容易漂移。

**选择**：在 `agents/trading/executor.py` 内新增私有静态方法 `_classify_reduce_outcome(result, requested_pct) -> dict`，返回：

```python
{
    "status": "rejected" | "reduce_failed" | "risk_reduced",  # execution_result.v2 终态
    "reason": str,                                             # 来自 result['reason'] 或派生
    "actual_reduce_pct": float | None,                         # ok=True 时用于 PortfolioRiskGuard
    "protection_failed": bool,                                 # 仅用于 risk_reduced+ok=False
    "protection_state": "protected" | "unknown" | "closed" | "no_op",
    "protective_update_state": str,                            # 透传 result['protective_update_state']
    "warnings": list[str],
}
```

三路径调用：

```python
classification = self._classify_reduce_outcome(result, requested_pct=size_pct)
payload = self._build_execution_result(
    status=classification["status"], action="close" if size_pct < 1.0 else "reduce", ...
)
payload["reduce_pct"] = classification["actual_reduce_pct"] or size_pct
payload["protection_state"] = classification["protection_state"]
if classification["protection_failed"]:
    payload["protection_failed"] = True
    payload["reduce_pct"] = classification["actual_reduce_pct"]
```

**为什么**：单一函数，三处复用，未来再加 add/close 分流也是同样模式；helper 不依赖 self 状态便于单测；不破坏现有 `execution_result.v2` schema。

**Alternatives**：
- 在三处分别 inline 写分支：拒绝，违反第三次整改的 single-source-of-truth 原则（参考 `Judge._select_rr_floor` 教训）
- 把分流推到 `_build_execution_result`：拒绝，`_build_execution_result` 是通用 builder，不应耦合 reduce 业务规则

### Decision 2：F4-001 PortfolioRiskGuard 用 `actual_reduce_amount` 缩敞口

**问题**：`portfolio_risk_guard.py:144-147` 直接 `_positions[symbol]['amount_usdt'] *= (1 - reduce_pct)`，请求 50% 但实际 reject（reduce_ok=False）也会被缩。

**选择**：
1. Agent 层 publish `risk_reduced` 时已带 `actual_reduce_pct`（见 Decision 1），优先用它
2. RiskGuard 监听增加 `if status in ("rejected", "reduce_failed"): return`
3. `risk_reduced` 但 `payload.get("protection_failed")` → 仍按 actual 缩敞口（成交了），但额外发 `risk_alert{type="protection_failed", symbol}`

```python
elif status == 'risk_reduced':
    if symbol in self._positions:
        actual_pct = payload.get('reduce_pct', 0.5)
        self._positions[symbol]['amount_usdt'] *= (1 - actual_pct)
    if payload.get('protection_failed'):
```

Full source: openspec/changes/audit-fourth-pass-blockers/design.md

## openspec/changes/audit-fourth-pass-blockers/tasks.md

- Source: openspec/changes/audit-fourth-pass-blockers/tasks.md
- Lines: 1-39
- SHA256: bcf1822bcdd5ca3fabec973956a4a3bee438f6f6abfc8f9026338b25535e26ef

```md
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
```

## openspec/changes/audit-fourth-pass-blockers/specs/pnl-resolution-bus-events/spec.md

- Source: openspec/changes/audit-fourth-pass-blockers/specs/pnl-resolution-bus-events/spec.md
- Lines: 1-68
- SHA256: 9c36a5401eb1186d429f79550a9abb5d5823491e026bc2fc439d14dd42be8d54

```md
## ADDED Requirements

### Requirement: 所有 pnl_resolved/pnl_mismatch 发布点必须透传 final close cause 与证据

`pnl_resolved` 与 `pnl_mismatch` 总线事件由三个生产者发布：`agents/trading/executor.py:_resolve_external_close_async`、`agents/trading/executor.py:_run_reconciliation`（消费 `Reconciler.auto_resolve_pending` summary）、`utils/reconciliation.py:Reconciler.auto_resolve_pending` 自身。三者发布时 MUST 携带同一字段集，使 Judge / Reviewer / 其他订阅者 SHALL 可以稳定判定 final close cause 与证据。

#### Scenario: _resolve_external_close_async 透传 final_close_cause + close_evidence
- **WHEN** Resolver 返回 `{close_cause: "exchange_sl", final_close_cause: "exchange_sl", is_strategy_stop: True, close_evidence: {match_rule: "sl_algo_id_exact", confidence: 1.0, ...}}`
- **AND** `_resolve_external_close_async` 发布 `pnl_resolved`
- **THEN** payload 必须含 `final_close_cause` / `close_evidence` 两个键，值与 resolution 一致
- **AND** payload `is_strategy_stop` 必须等于 resolution 的 `is_strategy_stop`

#### Scenario: Reconciler.auto_resolve_pending summary 携带四字段集
- **WHEN** `Reconciler.auto_resolve_pending()` 处理 pending 升级
- **THEN** 返回的 summary dict 必须包含 `close_cause` / `final_close_cause` / `is_strategy_stop` / `close_evidence` 四个字段（值取自 resolution）

#### Scenario: _run_reconciliation 发布 pnl_resolved 透传四字段集
- **WHEN** `_run_reconciliation` 收到 summary 并发布 `pnl_resolved` / `pnl_mismatch`
- **THEN** 发布的 payload 必须包含 `final_close_cause` / `close_evidence`（额外字段，与已有 `close_cause` / `is_strategy_stop` 共存）

#### Scenario: 异常路径无 correction 时跳过发布并告警
- **WHEN** Resolver 抛异常或返回 `pending` / `pending_fx` 等 non-final/non-mismatch 状态，且 `correction is None`
- **THEN** 发布点 MUST NOT 发布 `pnl_resolved` / `pnl_mismatch`（避免发出无 `correction_event_id` 的脏事件）
- **AND** MUST 调用 `logger.warning` 记录跳过原因（含 symbol / position_id / status）

### Requirement: pnl_resolved/pnl_mismatch 必须携带 resolution_id 幂等键

每条 `pnl_resolved` / `pnl_mismatch` 事件 MUST 含 `resolution_id` 字段，由唯一函数 `make_resolution_id(resolution, correction)` 生成，SHALL 用于下游订阅者去重。

#### Scenario: resolution_id 优先使用 correction_event_id
- **WHEN** correction 字典含 `event_id`（写 ledger correction 成功时）
- **THEN** `resolution_id` 必须以 `corr:` 前缀且包含 `correction.event_id`

#### Scenario: 没有 correction.event_id 时回退到 supersedes_event_id
- **WHEN** correction 含 `supersedes_event_id` 但无 `event_id`
- **THEN** `resolution_id` 必须以 `sup:` 前缀且包含 `supersedes_event_id`

#### Scenario: 都无时回退 close_match_key
- **WHEN** correction 缺失或不含上述两项，但 resolution 含 `close_match_key`
- **THEN** `resolution_id` 必须以 `key:` 前缀

#### Scenario: 兜底使用 position_id + order_ids
- **WHEN** 上述三者均缺失
- **THEN** `resolution_id` 必须以 `pos:` 前缀，包含 `position_id` 与排序后的 `order_ids` 拼接

#### Scenario: 同一 resolution 多次发布产出相同 resolution_id
- **WHEN** 同一 resolution 经 `_resolve_external_close_async` 与 `_run_reconciliation` 各发布一次
- **THEN** 两次的 `resolution_id` 字段必须相等（基于相同 correction 输入时）

### Requirement: 账本类下游订阅者必须按 resolution_id 幂等去重

账本类 `pnl_resolved` 订阅者（Judge / Reviewer 等会写入 trade_history、计 SL hit、修改 archetype cooldown 的消费者）MUST 在收到事件时按 `resolution_id` 去重，避免同一对账结果被升级两次（例如 `_resolve_external_close_async` 与 `_run_reconciliation` 都触发了同一 pending 的升级）。当 payload 缺失 `resolution_id` 时订阅者 SHALL fail-safe 回退到现有 `correction_event_id` / `supersedes_event_id` 去重逻辑。

通知类订阅者（如 TelegramNotifier）MAY 保留独立的时间窗去重（如现有 `_close_notify_cache` 60s window），不强制接入 `resolution_id` 去重，以避免缓存交叉污染。

#### Scenario: 账本类同一 resolution_id 第二次到达被忽略
- **WHEN** Judge 或 Reviewer 已处理过 `resolution_id="corr:E-123"` 的 `pnl_resolved`
- **AND** 再次收到含同一 `resolution_id` 的 `pnl_resolved`
- **THEN** MUST NOT 重复升级 trade_history / MUST NOT 重复计 SL hit / MUST NOT 重复 record archetype cooldown

#### Scenario: 缺失 resolution_id 时按现有逻辑处理
- **WHEN** 订阅者收到 `pnl_resolved` 但 payload 不含 `resolution_id`（向后兼容）
- **THEN** 订阅者按现有 `correction_event_id` / `supersedes_event_id` 去重逻辑处理，MUST NOT 抛错

#### Scenario: Telegram 不强制 resolution_id 去重
- **WHEN** TelegramNotifier 收到 `pnl_resolved`
- **THEN** 现有 `_close_notify_cache` 60s window 仍然生效
- **AND** MAY 不维护 `_seen_resolution_ids` 集合（不被本 spec 强制要求）
```

## openspec/changes/audit-fourth-pass-blockers/specs/protective-sl-owner-tag/spec.md

- Source: openspec/changes/audit-fourth-pass-blockers/specs/protective-sl-owner-tag/spec.md
- Lines: 1-40
- SHA256: 678415fe3bfbc81593f17446f4d3181c1c3ce0ebff841ac3a56b02c726e30af3

```md
## ADDED Requirements

### Requirement: OKX 真实新挂保护单必须使用 owner-tag clOrdId

`executor.py` 中所有真实下发到 OKX 的新保护单（attached SL、独立 replace SL、legacy 独立 SL）SHALL 使用 `_make_owner_tag_clord_id()` 生成 `attachAlgoClOrdId` / `algoClOrdId`，使本地状态丢失或多 bot 同账户场景下可以按 owner prefix 证明归属。`_make_sl_clord_id()` MUST 仅保留作为历史兼容标识器（cleanup 路径仍然支持），并且 MUST NOT 再被新挂单调用。

#### Scenario: _replace_protective_sl 使用 owner-tag prefix
- **WHEN** `executor._replace_protective_sl(symbol, position, new_sl)` 在 OKX 上挂新 SL
- **THEN** 传给 `_place_protective_sl` 的 `clord_id` 必须满足 `_is_owner_clord_id(clord_id) == True`
- **AND** `clord_id` 不得以 `sl` 前缀开头（除非 `sl` 是 owner prefix `ca<ns><bot>` 后的偶然字符，仍需 `_is_owner_clord_id` 通过）

#### Scenario: open_position_with_plan 的 attached SL 使用 owner-tag
- **WHEN** `open_position_with_plan` 构造 `tp_sl_params`（含 `attachAlgoClOrdId`）下单
- **AND** `stop_loss` 参数为有效价格
- **THEN** `attachAlgoClOrdId` 必须使 `_is_owner_clord_id` 返回 True

#### Scenario: legacy _open_position 独立 SL 使用 owner-tag 并写入 position
- **WHEN** legacy `_open_position()` 调用 `_place_protective_sl`
- **THEN** 必须传入 owner-tag `clord_id`
- **AND** 挂单成功后 `position['sl_algo_clord_id']` 必须等于该 owner-tag clord_id（不再是 None）

#### Scenario: 非 OKX 交易所路径不受影响
- **WHEN** `exchange_id != 'okx'`
- **THEN** `_replace_protective_sl` / `open_position_with_plan` / `_open_position` 不强制生成 owner-tag clOrdId（保持现有行为）

### Requirement: 缺 BOT_INSTANCE_ID 时启动告警

live 多 bot 同账户场景需要 `BOT_INSTANCE_ID` 环境变量来区分不同 bot 的 owner prefix。当 `STATE_NAMESPACE='live'`（或推断为 live）且 `BOT_INSTANCE_ID` 未配置或为空字符串时，启动 banner MUST 打印 WARNING，使运维知晓 cross-bot 归属无法通过 clOrdId 证明。testnet/paper 模式下 SHALL NOT 触发该告警。

#### Scenario: live 模式缺 BOT_INSTANCE_ID 时 banner 打 WARNING
- **WHEN** `STATE_NAMESPACE='live'` 且 `BOT_INSTANCE_ID` 为空
- **THEN** 启动 banner 必须包含 `WARNING` 字样和提示 `BOT_INSTANCE_ID not configured`

#### Scenario: live 模式有 BOT_INSTANCE_ID 时 banner 不打 WARNING
- **WHEN** `STATE_NAMESPACE='live'` 且 `BOT_INSTANCE_ID="bot-A"`
- **THEN** 启动 banner 不得包含 `BOT_INSTANCE_ID not configured` WARNING

#### Scenario: testnet/paper 模式不打 BOT_INSTANCE_ID WARNING
- **WHEN** `STATE_NAMESPACE='testnet'` 或 `STATE_NAMESPACE='paper'`，`BOT_INSTANCE_ID` 为空
- **THEN** banner 不得包含 `BOT_INSTANCE_ID not configured` WARNING（避免 testnet 误报）
```

## openspec/changes/audit-fourth-pass-blockers/specs/reduce-result-propagation/spec.md

- Source: openspec/changes/audit-fourth-pass-blockers/specs/reduce-result-propagation/spec.md
- Lines: 1-85
- SHA256: 796d4f6c9bf63fa5d29cf9b855373f2c0887c2f5f829a16bc82e4cc7fcda35f6

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: Agent reduce 路径必须按 reduce_ok / ok 分支处理终态

执行层 Agent 在调用 `executor.reduce_position()` 后，MUST 基于返回 dict 中的 `reduce_ok` / `ok` / `protective_update_state` 字段决定 `execution_result.v2` 的 `status`，并且 MUST NOT 用 truthy 判断把失败结果广播为 `risk_reduced`。本要求 SHALL 覆盖三条路径：PositionAnalyst 的部分平仓 (`source='position_analyst' && action='close' && size_pct<1.0`)、风控减仓 (`portfolio_exposure` / `correlation_risk`)、partial TP 锁利 (`partial_tp_1` / `partial_tp_2`)。

#### Scenario: pre-trade 失败 (sl_cancel_failed / sl_restore_failed) 必须广播 rejected
- **WHEN** `reduce_position()` 返回 `{reduce_ok: False, reason: "sl_cancel_failed"}` 或 `{reduce_ok: False, reason: "sl_restore_failed"}`（reduce 单还没下到交易所）
- **THEN** Agent MUST publish `status="rejected"`，MUST NOT 发 `risk_reduced` 或 `reduce_failed`
- **AND** payload MUST 含 `reason`（来自 `result.reason`）
- **AND** payload MUST NOT 含 `reduce_pct` 或 `reduce_pct=0`

#### Scenario: 交易所 reject (reduce_rejected) 必须广播 reduce_failed
- **WHEN** `reduce_position()` 返回 `{reduce_ok: False, reason: "reduce_rejected"}`（reduce 单已尝试下交易所但被拒）
- **THEN** Agent MUST publish `status="reduce_failed"`，MUST NOT 发 `risk_reduced` 或 `rejected`
- **AND** payload MUST 含 `reason="reduce_rejected"`
- **AND** payload MUST NOT 含 `reduce_pct` 或 `reduce_pct=0`

#### Scenario: dust_closed 视为平仓终态而非减仓
- **WHEN** `reduce_position()` 返回 `{reduce_ok: True, protective_update_state: "dust_closed", protection_state: "closed"}`（剩余仓位过小被 root executor 删除）
- **THEN** Agent MUST publish `status="executed"` 且 `action="close"`（不是 risk_reduced，不是 reduce）
- **AND** payload MUST 含 `protection_state="closed"`
- **AND** 走 close 文案路径而非 reduce 文案路径

#### Scenario: reduce_ok=True 但 ok=False 必须标记 protection_failed
- **WHEN** `reduce_position()` 返回 `{reduce_ok: True, ok: False, protective_update_state: "replace_failed"}`（reduce 已成交但 residual SL 重挂失败）
- **THEN** Agent MUST publish `status="risk_reduced"` 且 payload 含 `protection_failed=True`
- **AND** payload MUST 含 `protection_state="unknown"`
- **AND** payload `reduce_pct` MUST 使用 `result.actual_reduce_amount` 折算的 actual pct，MUST NOT 用请求 pct

#### Scenario: ok=True 走干净 risk_reduced
- **WHEN** `reduce_position()` 返回 `{reduce_ok: True, ok: True, protective_update_state: "protected"}`
- **THEN** Agent publish `status="risk_reduced"`，`protection_failed` 必须不存在或 False
- **AND** `protection_state="protected"`

#### Scenario: 三路径必须共用同一分流函数
- **WHEN** PositionAnalyst 部分平、portfolio_exposure 风控减仓、partial_tp_1 任一路径调用 `reduce_position()`
- **THEN** MUST 经由同一个 `_classify_reduce_outcome(result, requested_pct)` helper 派生 `status` / `reason` / `actual_reduce_pct` / `protection_failed` / `protection_state` / `action_override`，MUST NOT 在三处各自写 if/else 分支

#### Scenario: result is None 必须广播 rejected
- **WHEN** `reduce_position()` 抛异常被 catch 或返回 `None`
- **THEN** Agent MUST publish `status="rejected"` 且 `reason="executor_returned_none"`

### Requirement: PortfolioRiskGuard 必须按实际成交结果调整本地敞口

PortfolioRiskGuard 监听 `execution_result.v2` 时，对 reduce 类终态的本地 `_positions[symbol]['amount_usdt']` 调整 MUST 基于 Agent 透传的实际百分比，而不是请求百分比；失败/拒绝终态 MUST NOT 缩敞口；保护单失败 SHALL 额外发 risk_alert。

#### Scenario: rejected / reduce_failed 不缩敞口
- **WHEN** 收到 `status ∈ {"rejected", "reduce_failed"}` 的 execution_result（含 reduce 类 action）
- **THEN** RiskGuard MUST NOT 修改 `_positions[symbol]['amount_usdt']`

#### Scenario: dust_closed 移除 symbol 而非缩敞口
- **WHEN** 收到 `status="executed"` 且 `action="close"` 且来源是 reduce 路径（dust_closed）
- **THEN** RiskGuard MUST 走现有 close 分支，从 `_positions` 移除 symbol（同 force_closed 处理）
- **AND** MUST NOT 在已移除 symbol 之后再尝试缩 `amount_usdt`

#### Scenario: risk_reduced 按 actual_reduce_pct 缩敞口
- **WHEN** 收到 `status="risk_reduced"`，payload `reduce_pct=R`
- **THEN** `_positions[symbol]['amount_usdt'] *= (1 - R)`，R MUST 取自 payload 的 `reduce_pct`（已是 actual pct）

#### Scenario: protection_failed 触发 protection_failed risk_alert
- **WHEN** 收到 `status="risk_reduced"` 且 `protection_failed=True`
- **THEN** RiskGuard 仍按 actual pct 缩敞口（reduce 已成交）
- **AND** 必须 publish `risk_alert` 且 `type="protection_failed"`，含 `symbol` / `protective_update_state` / `request_id`

### Requirement: TelegramNotifier 必须按 protective_update_state 分流减仓文案

Telegram 推送对 reduce 类终态 MUST 区分干净减仓与保护单异常，并且 MUST NOT 在保护单异常时仅发"✂️ 减仓"。

#### Scenario: 干净减仓走简短文案
- **WHEN** 收到 `status="risk_reduced"` 且 `protection_failed` 为 False/缺失
- **THEN** 发送形如 `✂️ 减仓 <symbol> <pct>%` 的简短消息

#### Scenario: protection_failed 走故障告警文案
- **WHEN** 收到 `status="risk_reduced"` 且 `protection_failed=True`
- **THEN** 发送故障文案，必须包含 `protective_update_state` 字段值（如 `replace_failed` / `restore_failed` / `cancel_failed`）和 `protection_state="unknown"` 字样

#### Scenario: dust_closed 走平仓文案而非减仓文案
- **WHEN** 收到 `status="executed"` 且 `action="close"` 且 payload 标识来源是 reduce 路径（如携带 `protective_update_state="dust_closed"` 或 `reduce_origin=True`）
- **THEN** 走现有平仓文案分支（含 PnL）
```

Full source: openspec/changes/audit-fourth-pass-blockers/specs/reduce-result-propagation/spec.md

