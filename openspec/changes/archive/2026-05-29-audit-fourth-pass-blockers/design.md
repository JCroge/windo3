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
        await self.publish('risk_alert', {
            'type': 'protection_failed', 'symbol': symbol, ...
        })
```

**Alternatives**：用 `result.actual_reduce_amount`（USDT 数量）→ 拒绝，已在 Agent 层折成百分比，不让 RiskGuard 重新算敞口数学。

### Decision 3：F4-001 Telegram 文案按 `protective_update_state` 分流

**问题**：`telegram_notifier.py:129-132` 一句 "✂️ 减仓 X%" 把 `cancel_failed/restore_failed/replace_failed` 全部吞掉。

**选择**：
- `status=rejected/reduce_failed` 不发减仓文案（保留默认 rejected 文案）
- `status=risk_reduced && protection_failed=False` → 干净 "✂️ 减仓"
- `status=risk_reduced && protection_failed=True` → "⚠️ 减仓已成交但保护单异常: <protective_update_state>"
- 单独订阅 `risk_alert.type=protection_failed` → 推送 critical 告警

### Decision 4：F4-002 `resolution_id` 优先级链与生成入口

**问题**：报告要求新增 `resolution_id`，但生成点必须唯一，否则三条发布路径各自算 → 不幂等。

**选择**：在 `utils/realized_pnl_resolver.py` 新增模块级函数 `make_resolution_id(resolution, correction)`，被三个发布点共用：

```python
def make_resolution_id(resolution: dict, correction: Optional[dict]) -> str:
    if correction and correction.get("event_id"):
        return f"corr:{correction['event_id']}"
    if correction and correction.get("supersedes_event_id"):
        return f"sup:{correction['supersedes_event_id']}"
    if resolution.get("close_match_key"):
        return f"key:{resolution['close_match_key']}"
    pos_id = resolution.get("position_id", "")
    order_ids = ",".join(sorted(resolution.get("order_ids") or []))
    return f"pos:{pos_id}|orders:{order_ids}"
```

三个发布点：
1. `agents/trading/executor.py:_resolve_external_close_async` → 已有 `correction`，直接调用
2. `agents/trading/executor.py:_run_reconciliation` → 从 summary 拿 `correction_event_id` / `supersedes_event_id` / `close_match_key` / `position_id` / `order_ids`
3. `utils/reconciliation.py:auto_resolve_pending` → summary 内同步增加 `close_cause / final_close_cause / is_strategy_stop / close_evidence / resolution_id`

下游（Judge / Reviewer / Telegram）增加 `seen_resolution_ids` set，重复 id 直接 ignore（默认 LRU 1024）。

**为什么**：`correction_event_id` 已经是写 ledger correction 时唯一的；`supersedes_event_id` 是 pending → final 链；`close_match_key` 是 resolver 内部 key；`position_id+order_ids` 兜底。这条链最强不依赖 wallclock。

### Decision 5：F4-003 三处挂单切换到 owner tag

**问题**：`_make_owner_tag_clord_id()` 已存在但没接入真实下单。

**选择**：
1. `executor.py:1464` `_replace_protective_sl` → `new_clord = self._make_owner_tag_clord_id(symbol) if self.exchange_id == 'okx' else None`
2. `executor.py:1950` `open_position_with_plan` → 同上替换
3. `executor.py:1069` legacy `_open_position` → 调用前生成 clord_id，传给 `_place_protective_sl(... clord_id=clord_id)`，挂成功后写 `position['sl_algo_clord_id'] = clord_id`
4. 启动 banner（`utils/state_paths.py:format_banner` 或 `run_agents.py` 入口）：env `BOT_INSTANCE_ID` 为空且 `STATE_NAMESPACE in ('live',)` 时输出 `WARNING: BOT_INSTANCE_ID not configured; cross-bot SL ownership cannot be proven by clOrdId.`

`_make_sl_clord_id` 保留但仅作为历史兼容标识器（cleanup 路径 `_is_owner_clord_id` 已支持），不再用于新挂单。

### Decision 6：测试策略

| 阻断 | 新增测试文件 | 关键 case |
|---|---|---|
| F4-001 | `test_reduce_failure_propagation.py` | (a) `cancel_failed` → executor 不发 risk_reduced；(b) `reduce_rejected` 且 restore_ok → 不缩敞口；(c) `replace_failed` → publish `risk_reduced` + protection_failed=True + RiskGuard 发 protection_failed alert；(d) Telegram 文案分流 |
| F4-002 | `test_pnl_resolved_event_contract.py` | (a) `_resolve_external_close_async` 透传 final_close_cause + close_evidence + resolution_id；(b) `_run_reconciliation` 同字段集；(c) `Reconciler.auto_resolve_pending` summary 字段；(d) 下游接收同一 resolution_id 第二次时被 dedupe |
| F4-003 | `test_owner_tag_clord_id_callsites.py` | (a) replace SL 的 algoClOrdId 以 owner prefix 开头；(b) attached SL 的 attachAlgoClOrdId 以 owner prefix 开头；(c) legacy open SL 写入 `position['sl_algo_clord_id']`；(d) 缺 BOT_INSTANCE_ID 时 banner 含 WARNING 字串 |

回归基线：`pytest -q` 必须从 807 → 至少 822（+15 case），network tier `pytest -q -m network` 仍 4 通过。

## Risks / Trade-offs

- **PortfolioRiskGuard 敞口数学微变** → Mitigation：保留旧分支 `if 'reduce_pct' in payload`，新字段缺失走旧逻辑（向后兼容）；通过 `test_reduce_failure_propagation.py` 覆盖三种情况
- **resolution_id 长度** → 测试 dump 时可能很长（`pos:xxx|orders:a,b,c,d`），不上限会让 Telegram 文案丑；不在文案里展示 resolution_id，仅用于事件总线幂等
- **owner tag 切换后历史 SL 的撤单** → 已有 `_is_owner_clord_id` 支持 owner prefix + legacy `sl` prefix（仅 exact 匹配），切换不影响存量 SL 的 cleanup（第三次整改已闭环）
- **缺 BOT_INSTANCE_ID 仅告警不阻断** → live 多 bot 场景仍存在跨 bot orphan 风险；Mitigation：banner 同时打印 namespace + bot，运维 SOP 强制配置；后续可加 startup hard-fail 选项（non-goal）
- **Agent 层 helper 拒绝引入新 schema_version** → `protection_failed` 字段对老消费者不可见；Mitigation：默认 False，老消费者按现有路径走，不会误判

## Migration Plan

1. F4-003 (owner tag) 风险最低，先落 — 仅影响新挂 SL，不动事件总线
2. F4-002 (resolution_id + 透传) 中等 — 总线字段增加，老消费者可向后兼容
3. F4-001 (Agent reduce 分流) 风险最高 — 同步改 RiskGuard / Telegram，需要全量回归
4. 全部完成后跑 `python3 -m pytest -q` + `python3 -m pytest -q -m network`，跑 OKX testnet 验收冒烟（reduce + external close 各一次），再解除 NO-GO

回滚：每个 FR 一个独立 commit，回滚单个不影响其余；`protection_failed` / `resolution_id` 字段缺失时下游 fail-safe 即可。

## Open Questions

- 是否在 `protection_failed` 时强制 `_halt_symbol`？root executor 已 halt（live OKX），Agent 层不再重复 halt，仅广播 risk_alert。**结论：保持现状**。
- `resolution_id` 是否写入 ledger correction？暂不写，ledger 已有 `event_id` / `supersedes_event_id`，resolution_id 只是事件总线层幂等键。**结论：仅总线层使用**。
