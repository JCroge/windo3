---
comet_change: audit-fourth-pass-blockers
role: technical-design
canonical_spec: openspec
archived-with: 2026-05-29-audit-fourth-pass-blockers
status: final
---

# Audit Fourth Pass Blockers — Technical Design

## 1. 设计目标与范围

闭环第四次审计（`docs/generated_reports/系统性审计报告_20260528_第四次.md`）的三个阻断：F4-001（reduce 失败回参 Agent 误广播为 risk_reduced）、F4-002（pnl_resolved 总线事件未透传 final close cause / 证据 / 幂等键）、F4-003（OKX 真实新 SL 未使用 owner-tag clOrdId）。三个阻断是 live 扩容 NO-GO 的最后前置。

**Canonical spec**：本次需求与验收契约以 OpenSpec change `audit-fourth-pass-blockers` 为准（见 `openspec/changes/audit-fourth-pass-blockers/specs/{reduce-result-propagation,pnl-resolution-bus-events,protective-sl-owner-tag}/spec.md`）。本设计文档只描述实现方案、技术选型、风险与测试策略，不重复定义需求。

**关键技术决策**（brainstorm 已确认）：
- F4-001：单一 helper `_classify_reduce_outcome()` 输出 6 分支分类，三路径调用方零分支逻辑
- F4-001：`status` 切分为 `rejected`（pre-trade）/ `reduce_failed`（exchange reject），保留语义信息给下游 Reviewer
- F4-002：`make_resolution_id()` 单一入口，4 级优先级链；异常路径无 correction 时跳过发布
- F4-002：账本类（Judge/Reviewer）强制 resolution_id 去重，Telegram 保留 60s window 不动
- F4-003：三处挂单切到 `_make_owner_tag_clord_id()`，legacy `_make_sl_clord_id` 保留并标 DEPRECATED
- F4-003：缺 `BOT_INSTANCE_ID` 仅 banner WARNING（不引入 hard-fail 开关，符合报告原文）

## 2. F4-001 reduce 失败回参分流实现方案

### 2.1 helper `_classify_reduce_outcome` 单点契约

新增静态方法 `agents/trading/executor.py:MultiExecutor._classify_reduce_outcome(result, requested_pct) -> dict`：

```python
@staticmethod
def _classify_reduce_outcome(result, requested_pct):
    if result is None:
        return {
            "status": "rejected", "reason": "executor_returned_none",
            "actual_reduce_pct": 0.0, "protection_failed": False,
            "protection_state": "unknown",
            "protective_update_state": "no_op",
            "action_override": None, "warnings": [],
        }

    reduce_ok = bool(result.get("reduce_ok", False))
    ok = bool(result.get("ok", False))
    reason = result.get("reason", "")
    pus = result.get("protective_update_state", "")
    actual_amt = result.get("actual_reduce_amount") or 0.0
    requested_amt = result.get("requested_reduce_amount") or 0.0
    actual_pct = (actual_amt / requested_amt * requested_pct) \
        if requested_amt > 0 else requested_pct
    warnings = list(result.get("warnings") or [])

    # 分支 1: pre-trade 失败 (reduce 单还没下交易所)
    if not reduce_ok and reason in ("sl_cancel_failed", "sl_restore_failed"):
        return {
            "status": "rejected", "reason": reason,
            "actual_reduce_pct": 0.0, "protection_failed": False,
            "protection_state": result.get("protection_state", "unknown"),
            "protective_update_state": pus,
            "action_override": None, "warnings": warnings,
        }

    # 分支 2: 交易所 reject
    if not reduce_ok:
        return {
            "status": "reduce_failed", "reason": reason or "reduce_rejected",
            "actual_reduce_pct": 0.0, "protection_failed": False,
            "protection_state": result.get("protection_state", "unknown"),
            "protective_update_state": pus,
            "action_override": None, "warnings": warnings,
        }

    # 分支 3: dust_closed → 视为平仓
    if pus == "dust_closed":
        return {
            "status": "executed", "reason": reason or "dust_closed",
            "actual_reduce_pct": actual_pct,
            "protection_failed": False,
            "protection_state": "closed",
            "protective_update_state": pus,
            "action_override": "close", "warnings": warnings,
        }

    # 分支 4: reduce 成交但 SL 重挂失败
    if reduce_ok and not ok:
        return {
            "status": "risk_reduced", "reason": reason or "protection_failed",
            "actual_reduce_pct": actual_pct,
            "protection_failed": True,
            "protection_state": "unknown",
            "protective_update_state": pus,
            "action_override": None, "warnings": warnings,
        }

    # 分支 5: 干净 risk_reduced
    return {
        "status": "risk_reduced", "reason": "ok",
        "actual_reduce_pct": actual_pct,
        "protection_failed": False,
        "protection_state": result.get("protection_state", "protected"),
        "protective_update_state": pus,
        "action_override": None, "warnings": warnings,
    }
```

**为什么 helper 内部统一处理 None**：避免三处调用方各自写 `if result is None`，符合 brainstorm Q2 结论 A。

**为什么单独切出 dust_closed 分支**：root executor 在 dust 时已经 `del self.positions[symbol]` 并把 `protection_state='closed'`，这是平仓终态而非减仓，下游 RiskGuard / Telegram / Reviewer 必须走 close 分支（移除 symbol、走平仓文案、计入 trade_history）。

### 2.2 三路径调用方改造

PositionAnalyst 部分平 (`agents/trading/executor.py:225-284`)、portfolio_exposure / correlation_risk 风控减仓 (L438-454)、partial_tp_1/2 (L1004-1020) 的统一改造模式：

```python
result = await asyncio.to_thread(self.executor.reduce_position, ...)
classification = self._classify_reduce_outcome(result, requested_pct=size_pct)

action = classification["action_override"] or original_action  # close 路径走 close
status = classification["status"]
payload = self._build_execution_result(
    status=status, action=action, symbol=symbol,
    source=exec_source, request_id=request_id, result=result or {},
    reason=classification["reason"],
)
if status == "risk_reduced":
    payload["reduce_pct"] = classification["actual_reduce_pct"]
    payload["protection_state"] = classification["protection_state"]
    if classification["protection_failed"]:
        payload["protection_failed"] = True
        payload["protective_update_state"] = classification["protective_update_state"]
elif status == "executed" and action == "close":
    # dust_closed 路径，沿用现有 close payload 字段
    payload["protection_state"] = "closed"
    payload["protective_update_state"] = "dust_closed"
    payload["reduce_origin"] = True  # Telegram 用此字段路由

await self.publish("execution_result", payload, symbol=symbol)
```

`_build_execution_result` 不需要改造（已经是通用 builder）。

### 2.3 PortfolioRiskGuard 与 TelegramNotifier 改造

`agents/trading/portfolio_risk_guard.py:_on_execution_result` 增加分支：

```python
elif status in ("rejected", "reduce_failed"):
    return  # 不缩敞口
elif status == "executed" and action == "close" and payload.get("reduce_origin"):
    # dust_closed: 走 close 分支移除 symbol（与 force_closed 同处理）
    self._positions.pop(symbol, None)
elif status == "risk_reduced":
    if symbol in self._positions:
        actual_pct = payload.get("reduce_pct", 0.5)
        self._positions[symbol]["amount_usdt"] *= (1 - actual_pct)
    if payload.get("protection_failed"):
        await self.publish("risk_alert", {
            "type": "protection_failed", "symbol": symbol,
            "protective_update_state": payload.get("protective_update_state", ""),
            "request_id": payload.get("request_id", ""),
        })
```

`agents/trading/telegram_notifier.py:risk_reduced` 分支：

```python
elif status == "risk_reduced":
    reduce_pct = payload.get("reduce_pct", 0.5)
    if payload.get("protection_failed"):
        pus = payload.get("protective_update_state", "unknown")
        text = (
            f"⚠️ 减仓 {symbol} {int(reduce_pct*100)}% 已成交\n"
            f"但保护单异常: {pus}\n"
            f"protection_state=unknown,需人工核查"
        )
    else:
        text = f"✂️ 减仓 {symbol} {int(reduce_pct*100)}%"
    await self._send_message(text)
```

dust_closed 由现有 `executed && action='close'` 分支自然处理（已有 PnL 文案）。

## 3. F4-002 pnl_resolved 总线事件契约实现方案

### 3.1 `make_resolution_id` 单一入口

新增 `utils/realized_pnl_resolver.py:make_resolution_id(resolution, correction)`：

```python
def make_resolution_id(resolution, correction=None):
    if correction:
        if correction.get("event_id"):
            return f"corr:{correction['event_id']}"
        if correction.get("supersedes_event_id"):
            return f"sup:{correction['supersedes_event_id']}"
    if resolution.get("close_match_key"):
        return f"key:{resolution['close_match_key']}"
    pos_id = resolution.get("position_id", "") or ""
    order_ids = sorted(resolution.get("order_ids") or [])
    return f"pos:{pos_id}|orders:{','.join(order_ids)}"
```

**为什么 corr 优先 sup**：写 ledger correction 成功时 `event_id` 是 ledger 内全局唯一的；`supersedes_event_id` 仅在写失败兜底。

### 3.2 三个发布点透传

`agents/trading/executor.py:_resolve_external_close_async` (L880-921)：在现有 publish payload 增加：

```python
"final_close_cause": resolution.get("final_close_cause", close_cause),
"close_evidence": resolution.get("close_evidence", {}),
"resolution_id": make_resolution_id(resolution, correction),
```

但发布前先检查防御条件：

```python
if correction is None and resolution.get("pnl_status") not in (PNL_STATUS_FINAL, "mismatch"):
    self.logger.warning(
        f"[Resolver] {symbol} 跳过发布：correction=None 且 status={resolution.get('pnl_status')} "
        f"(position_id={resolution.get('position_id', '')})"
    )
    return
```

`utils/reconciliation.py:Reconciler.auto_resolve_pending` 的 summary 字段集补齐（line 255-285）：

```python
results.append({
    ...
    "close_cause": resolution.get("close_cause", ""),
    "final_close_cause": resolution.get("final_close_cause", ""),
    "is_strategy_stop": bool(resolution.get("is_strategy_stop", False)),
    "close_evidence": resolution.get("close_evidence", {}),
    "resolution_id": make_resolution_id(resolution, correction),
    ...
})
```

`agents/trading/executor.py:_run_reconciliation` (L698-731) 透传同字段集 + 同样的 `correction is None` 防御。

### 3.3 账本类下游订阅者去重

Judge / Reviewer 增加 LRU 集合：

```python
class Judge:
    def __init__(self, ...):
        self._seen_resolution_ids = collections.OrderedDict()
        self._seen_resolution_ids_max = 1024

    def _is_duplicate_resolution(self, payload):
        rid = payload.get("resolution_id")
        if not rid:
            return False  # 缺失时 fail-safe，回退到现有 correction_event_id 逻辑
        if rid in self._seen_resolution_ids:
            self._seen_resolution_ids.move_to_end(rid)  # LRU
            return True
        self._seen_resolution_ids[rid] = True
        if len(self._seen_resolution_ids) > self._seen_resolution_ids_max:
            self._seen_resolution_ids.popitem(last=False)
        return False

    async def _handle_pnl_resolved(self, payload):
        if self._is_duplicate_resolution(payload):
            return
        # 现有逻辑...
```

Reviewer 同模式接入。Telegram 不接入（保持 60s `_close_notify_cache`）。

## 4. F4-003 owner-tag clOrdId 实现方案

### 4.1 三处挂单切换

`executor.py:_replace_protective_sl` L1464：

```diff
- new_clord = self._make_sl_clord_id(symbol) if self.exchange_id == 'okx' else None
+ new_clord = self._make_owner_tag_clord_id(symbol) if self.exchange_id == 'okx' else None
```

`executor.py:open_position_with_plan` L1950：同上替换。

`executor.py:_open_position` (legacy) L1068-1095：

```diff
+ sl_clord_id = self._make_owner_tag_clord_id(symbol) if self.exchange_id == 'okx' and stop_loss else None
  sl_order_id = self._place_protective_sl(
      symbol=symbol, side=side, stop_price=stop_loss, amount=amount,
+     clord_id=sl_clord_id,
  )
  ...
  position = {
      ...
      'sl_algo_id': sl_order_id if self.exchange_id == 'okx' else None,
-     'sl_algo_clord_id': None,
+     'sl_algo_clord_id': sl_clord_id,
      ...
  }
```

`_make_sl_clord_id` 保留并加注释：

```python
@staticmethod
def _make_sl_clord_id(symbol: str) -> str:
    """[DEPRECATED] 历史兼容标识器,新挂单 MUST 使用 _make_owner_tag_clord_id。

    保留原因: cleanup 路径 _is_owner_clord_id 仍按 sl_algo_clord_id 字段做 exact 匹配,
    存量 positions.json 中的历史 sl... 前缀 algoClOrdId 仍能被识别为本系统所有,
    避免误清扫。预计 1-2 个月后跑全量 positions.json 审计确认无遗留再删除。
    """
    base = symbol.replace('-', '').replace('/', '').replace(':', '').upper()[:8]
    return f"sl{base}{uuid.uuid4().hex[:18]}"
```

### 4.2 启动 banner BOT_INSTANCE_ID 告警

`utils/state_paths.py:format_banner` 末尾追加：

```python
def format_banner(...):
    lines = [...]  # 现有 banner
    namespace = ...  # 现有 namespace 推断
    bot_id = (os.getenv("BOT_INSTANCE_ID") or "").strip()
    lines.append(f"BOT_INSTANCE_ID: {bot_id or '<empty>'}")
    if namespace == "live" and not bot_id:
        lines.append(
            "WARNING: BOT_INSTANCE_ID not configured; "
            "cross-bot SL ownership cannot be proven by clOrdId."
        )
    return "\n".join(lines)
```

testnet/paper namespace 不打 WARNING（避免噪音）。

## 5. 测试策略

### 5.1 测试矩阵

| 文件 | 关键 case | 覆盖 spec scenario |
|---|---|---|
| `test_owner_tag_clord_id_callsites.py` | replace SL clOrdId 通过 `_is_owner_clord_id`；attached SL `attachAlgoClOrdId` 通过；legacy open SL 写 `sl_algo_clord_id`；live 缺 BOT_INSTANCE_ID banner 含 WARNING；testnet 不含 WARNING；legacy `_make_sl_clord_id` 仍可调用（用于历史 cleanup） | protective-sl-owner-tag 全部 |
| `test_pnl_resolved_event_contract.py` | `make_resolution_id` 4 级优先级；`_resolve_external_close_async` 透传 final_close_cause + close_evidence + resolution_id；`_run_reconciliation` 透传同字段集；`auto_resolve_pending` summary 字段；correction=None && status=pending 跳过发布 + warning；Judge 同 resolution_id 第二次被忽略；Reviewer 同上；Telegram 不接入 resolution_id；老 payload 缺 resolution_id fail-safe | pnl-resolution-bus-events 全部 |
| `test_reduce_failure_propagation.py` | `_classify_reduce_outcome` 6 分支单测；result=None → rejected；`sl_cancel_failed` → rejected；`sl_restore_failed` → rejected；`reduce_rejected` → reduce_failed；`dust_closed` → executed+close；`replace_failed` (reduce_ok=True, ok=False) → risk_reduced+protection_failed；ok=True → risk_reduced；三路径都通过 helper（mock 注入）；RiskGuard rejected/reduce_failed 不缩；RiskGuard dust_closed 移除 symbol；RiskGuard protection_failed 缩 + 发 alert；Telegram 三种文案分流 | reduce-result-propagation 全部 |

预期新增 case ≥ 25（基线 807 → 至少 832）。

### 5.2 回归基线

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit5_pycache python3 -m compileall -q .
python3 -m pytest -q  # 期望 ≥ 832 passed
python3 -m pytest -q -m network  # 期望仍 4 passed
python3 verify_okx_testnet_semantics.py  # 期望 reduce + external close 场景 PASS,owner-tag clOrdId 在真实 OKX 下发与撤单成功
```

## 6. 风险与回滚

- **F4-001 RiskGuard 敞口数学微变**：`reduce_pct` 字段语义从"请求 pct"变为"actual pct"。Mitigation：测试覆盖三种 reduce_ok 状态；老 payload 缺 `reduce_pct` 时 fallback 到 0.5（现有默认值）
- **F4-002 resolution_id 长度**：兜底 `pos:xxx|orders:a,b,c` 可能很长。Mitigation：仅总线层使用，不进 Telegram 文案；LRU 容量 1024 足以覆盖单日 pending 升级数
- **F4-002 异常路径跳过发布**：可能丢失 pending 状态对账信息。Mitigation：`logger.warning` 记录 + ledger `update_pending_resolution_attempt` 仍正常更新 retry metadata；下次 tick 重试
- **F4-003 owner-tag 切换后历史 SL cleanup**：`_is_owner_clord_id` 双判定（owner prefix + legacy exact）已支持。Mitigation：测试 `test_legacy_make_sl_clord_id_still_callable_for_cleanup` 兜底
- **F4-003 缺 BOT_INSTANCE_ID 仅告警不阻断**：多 bot 场景仍存在 cross-bot orphan 风险。Mitigation：当前生产单 bot 部署，多 bot 真出现时再做单独 change 引入 hard-fail 开关
- **回滚策略**：每个 FR 独立 commit，回滚单个不影响其余；新字段（`protection_failed` / `resolution_id` / `final_close_cause`）对老消费者向后兼容（缺失时 fail-safe）

## 7. 实施顺序与里程碑

按风险递增顺序：

1. **F4-003 owner-tag**（风险最低）：仅影响新挂单，不动事件总线。完成后跑 `test_owner_tag_clord_id_callsites.py` + `test_protective_cleanup_owner.py`
2. **F4-002 resolution_id + 透传**（中等）：总线字段增加，老消费者向后兼容。完成后跑 `test_pnl_resolved_event_contract.py` + `test_external_close_final_cause.py` + `test_exchange_realized_pnl_resolver.py`
3. **F4-001 Agent reduce 分流**（风险最高）：同步改 RiskGuard / Telegram，需要全量回归。完成后跑 `test_reduce_failure_propagation.py` + `test_reduce_protective_sl_lifecycle.py`
4. **全量回归 + OKX testnet 验收**：807 → ≥ 832 passed；OKX testnet reduce + external close 场景冒烟
5. **文档同步**：更新 `CLAUDE.md` 当前事实段、`docs/to-do-list.md` 标 F4-001/002/003 闭环、撰写验收报告 `docs/audit_remediation_fourth_pass_20260528_acceptance.md`

## 8. Open Questions（已闭合）

- 是否在 `protection_failed` 时强制 Agent 层再 `_halt_symbol`？→ 否，root executor 已 halt（live OKX），Agent 层只广播 risk_alert
- `resolution_id` 是否写入 ledger correction？→ 否，仅总线层使用；ledger 已有 `event_id` / `supersedes_event_id`
- 是否引入 `MULTI_BOT_MODE` 开关？→ 否，超出报告原文范围；多 bot 真出现时再单独 change
