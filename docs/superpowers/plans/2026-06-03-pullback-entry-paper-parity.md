---
change: pullback-entry-paper-parity
design-doc: docs/superpowers/specs/2026-06-03-pullback-entry-paper-parity-design.md
base-ref: f512d1a4c13ec3954fb5c8aed6c2d86acb3ba2a1
archived-with: 2026-06-03-pullback-entry-paper-parity
---

# Pullback Entry Paper Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Paper Executor's limit fill behavior match live executor's pullback policy (limit + timeout + no_fallback), and route `pullback_unfilled` / `paper_unfilled` alerts to Telegram.

**Architecture:** Tick-driven `_wait_paper_limit_fill` + 30s `tick()` cleanup loop, with in-memory `_pending_limits` dict. Paper writes `entry_method` to position records. TG `critical_types` extended; alerts distinguished by `source` field.

**Tech Stack:** Python 3.9 / asyncio / pytest / freezegun (new dev dep) / OKX SDK (untouched)

**Source of truth:** OpenSpec specs at `openspec/changes/pullback-entry-paper-parity/specs/{paper-executor,risk-alert-routing}/spec.md`. Do not redefine requirements; tasks below cite Requirement / Scenario IDs.

archived-with: 2026-06-03-pullback-entry-paper-parity
---

## Phase Map

| Phase | Title | Depends on | Parallel? | Files | Validation |
|---|---|---|---|---|---|
| P1 | Dependency + config | — | — | requirements.txt, requirements.lock, utils/config_loader.py, .env.example | `pip install -r requirements.lock`; `python3 -c "from utils.config_loader import load_config; print(load_config().get('paper_limit_tick_staleness_sec'))"` |
| P2 | Paper executor limit skeleton + entry_method | P1 | — | agents/trading/paper_executor.py | `python3 -m compileall -q agents/trading/paper_executor.py` |
| P3 | Cleanup loop + timeout decision tree | P2 | — | agents/trading/paper_executor.py | `python3 -m compileall -q agents/trading/paper_executor.py` |
| P4 | Telegram critical_types + source prefix | — (file-disjoint, can parallel with P2/P3) | yes (with P2/P3) | agents/trading/telegram_notifier.py | `python3 -m compileall -q agents/trading/telegram_notifier.py` |
| P5 | Live alert source field + agent log passthrough | — (file-disjoint, can parallel with P2/P3) | yes | executor.py, agents/trading/executor.py | `python3 -m compileall -q .` |
| P6 | Tests for paper limit fill | P2+P3+P5 | — | tests/test_paper_limit_fill.py | `pytest -q tests/test_paper_limit_fill.py` |
| P7 | Tests for TG alert routing | P4+P5 | yes (with P6) | tests/test_telegram_pullback_alerts.py | `pytest -q tests/test_telegram_pullback_alerts.py` |
| P8 | Full regression + docs sync | all above | — | docs/to-do-list.md, CLAUDE.md | `pytest -q`; baseline 980+ |

archived-with: 2026-06-03-pullback-entry-paper-parity
---

## Phase 1 — Dependency + Config Plumbing

**Files:**
- Modify: `requirements.txt` (append freezegun line)
- Modify: `requirements.lock` (append freezegun line — same version)
- Modify: `utils/config_loader.py:71-120` (`DEFAULTS` dict), `utils/config_loader.py:200-230` (`_apply_env_overrides` ENV map), `utils/config_loader.py:24-35` (`VALID_RANGES` dict)
- Modify: `.env.example` (add new env var section)

**Spec coverage:** Req7 (staleness gating) Scenario 3 — custom threshold honored.

- [ ] **Step 1.1: Add freezegun to requirements**

Append to `requirements.txt`:

```
freezegun==1.5.1
```

Append to `requirements.lock`:

```
freezegun==1.5.1
```

- [ ] **Step 1.2: Verify freezegun installs**

Run: `pip install freezegun==1.5.1`
Expected: success or "already satisfied"; followed by `python3 -c "from freezegun import freeze_time; print('ok')"` printing `ok`.

- [ ] **Step 1.3: Add DEFAULTS entry in `utils/config_loader.py`**

Locate the `DEFAULTS` dict (starts at line 71). Add this line in the section near `min_confidence` (around line 89):

```python
    # Paper limit fill: max tick staleness before fallback gates to no_tick rejection
    "paper_limit_tick_staleness_sec": 60,
```

- [ ] **Step 1.4: Add ENV mapping**

Locate `_apply_env_overrides` ENV map (around line 200-230). Add:

```python
        "PAPER_LIMIT_TICK_STALENESS_SEC": ("paper_limit_tick_staleness_sec", float),
```

- [ ] **Step 1.5: Add VALID_RANGES entry**

Locate `VALID_RANGES` dict (around line 24). Add:

```python
    "paper_limit_tick_staleness_sec": (1.0, 600.0),
```

- [ ] **Step 1.6: Add `.env.example` documentation**

Append to `.env.example`:

```
# Paper Executor — limit 撮合 tick staleness 阈值 (秒)
# 默认 60s。回测后可按行情源稳定性调整 (建议范围 30-180)
PAPER_LIMIT_TICK_STALENESS_SEC=60
```

- [ ] **Step 1.7: Verify config loads**

Run:
```
python3 -c "from utils.config_loader import load_config; c = load_config(); print('staleness=', c.get('paper_limit_tick_staleness_sec'))"
```
Expected: `staleness= 60` (or `60.0`)

- [ ] **Step 1.8: Commit**

```bash
git add requirements.txt requirements.lock utils/config_loader.py .env.example
git commit -m "feat(config): add paper_limit_tick_staleness_sec + freezegun dev dep

- DEFAULTS = 60s, VALID_RANGES = (1, 600)
- ENV override: PAPER_LIMIT_TICK_STALENESS_SEC
- freezegun 1.5.1 for time-controlled tests in P6/P7

Refs: docs/superpowers/specs/2026-06-03-pullback-entry-paper-parity-design.md TD-2/TD-3

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

archived-with: 2026-06-03-pullback-entry-paper-parity
---

## Phase 2 — Paper Executor Limit Skeleton + entry_method Field

**Files:**
- Modify: `agents/trading/paper_executor.py`

**Spec coverage:** Req1 (order_type respected), Req2 (tick hit → fill), Req4 (entry_method field), Req5 (duplicate-pending guard), Req6 (no persistence — write side).

- [ ] **Step 2.1: Add module constant + imports**

Edit `agents/trading/paper_executor.py:14-26` — at the import block, ensure `Dict` is imported from typing:

```python
from typing import Optional, Dict
```

After `PAPER_EQUITY_FILE = "data/paper_equity.json"` (around line 24), add:

```python
DEFAULT_PAPER_LIMIT_TICK_STALENESS_SEC = 60
```

- [ ] **Step 2.2: Initialize `_pending_limits` and `_tick_staleness_sec` in `__init__`**

Edit `paper_executor.py:35-49` — inside `__init__`, after `self._rejected_log: list = []`, add:

```python
        self._pending_limits: Dict[str, dict] = {}
        self._tick_staleness_sec = float(
            (config or {}).get('paper_limit_tick_staleness_sec',
                               DEFAULT_PAPER_LIMIT_TICK_STALENESS_SEC)
        )
        self._latest_tick_ts: Dict[str, float] = {}
```

- [ ] **Step 2.3: Update `on_message[price_tick]` to track tick timestamp + drive limit fill**

Edit `paper_executor.py:78-85` — replace the `price_tick` branch:

```python
        if mtype == 'price_tick':
            payload = msg.get('payload', {})
            symbol = msg.get('symbol') or payload.get('symbol')
            price = payload.get('price')
            if symbol and price:
                price_f = float(price)
                self._latest_price[symbol] = price_f
                self._latest_tick_ts[symbol] = time.time()
                if symbol in self._pending_limits:
                    await self._wait_paper_limit_fill(symbol, price_f)
                await self._check_sl_tp(symbol, price_f)
            return
```

- [ ] **Step 2.4: Modify `_open_paper` to gate on order_type and dispatch to limit path**

Replace `paper_executor.py:152-159` (the `price = self._latest_price.get(...)` lines and onwards through `if not price:`) with logic that branches by `order_type`. Locate `_open_paper` (line 152) and after extracting `side`, before the price-fetch logic, insert the limit branch. The full new function head should be:

```python
    async def _open_paper(self, symbol: str, action: str, plan: Optional[dict], decision: dict):
        side = 'long' if action == 'open_long' else 'short'

        # Duplicate-pending guard (Req5 Scenario 1)
        if symbol in self._pending_limits:
            self.logger.info(
                f"[PAPER] {symbol} {action} 跳过：已有 pending limit"
            )
            return

        order_type = (plan or {}).get('order_type', 'market')
        entry_zone = (plan or {}).get('entry_zone') or []
        if (order_type == 'limit'
                and isinstance(entry_zone, (list, tuple))
                and len(entry_zone) >= 2
                and float(entry_zone[0] or 0) > 0
                and float(entry_zone[1] or 0) > 0):
            self._enqueue_pending_limit(symbol, side, action, plan, decision)
            return

        price = self._latest_price.get(symbol)
        if not price:
            price = (plan or {}).get('entry_zone', [0])[0] if plan else 0
            if not price:
                self.logger.warning(f"[PAPER] {symbol} 无价格快照，跳过开仓")
                return
```

- [ ] **Step 2.5: Add `entry_method='market'` to immediate-fill position dict**

Inside `_open_paper`, locate the `pos = { ... }` dict construction (around `paper_executor.py:191-207`). Add `'entry_method': 'market',` line:

```python
        pos = {
            'symbol': symbol,
            'side': side,
            'entry_price': price,
            'sl': sl,
            'tp': tp,
            'margin': margin,
            'leverage': leverage,
            'notional': notional,
            'opened_at': time.time(),
            'entry_fee': entry_fee,
            'atr_pct': atr_pct,
            'source': decision.get('source', 'judge'),
            'confidence': decision.get('confidence', 0),
            'request_id': decision.get('request_id', ''),
            'attribution': decision.get('attribution') or (plan or {}).get('attribution') or {},
            'entry_method': 'market',
        }
```

- [ ] **Step 2.6: Add `_enqueue_pending_limit` helper method**

After the `_open_paper` method ends (around `paper_executor.py:225`), insert before `_close_paper`:

```python
    def _enqueue_pending_limit(self, symbol: str, side: str, action: str,
                               plan: dict, decision: dict) -> None:
        """Queue a limit-order paper open until tick hit or timeout."""
        entry_zone = list(plan.get('entry_zone', []))
        low = min(entry_zone[0], entry_zone[1])
        high = max(entry_zone[0], entry_zone[1])
        timeout_sec = float(plan.get('limit_timeout_sec', 30))
        no_fallback = bool(plan.get('limit_no_fallback', False))
        now = time.time()
        self._pending_limits[symbol] = {
            'symbol': symbol,
            'side': side,
            'action': action,
            'plan': dict(plan),
            'decision': dict(decision),
            'entry_zone': [low, high],
            'created_at': now,
            'deadline': now + timeout_sec,
            'limit_no_fallback': no_fallback,
        }
        self.logger.info(
            f"[PAPER] {symbol} {side} 限价挂出 zone=[{low:.6g},{high:.6g}] "
            f"timeout={timeout_sec:.0f}s no_fallback={no_fallback}"
        )
```

- [ ] **Step 2.7: Implement `_wait_paper_limit_fill`**

Insert immediately after `_enqueue_pending_limit`:

```python
    async def _wait_paper_limit_fill(self, symbol: str, tick_price: float) -> None:
        """Tick-driven limit fill check. Hit → midpoint fill + entry_method='limit_filled'."""
        pending = self._pending_limits.get(symbol)
        if not pending:
            return
        low, high = pending['entry_zone']
        if not (low <= tick_price <= high):
            return
        fill_price = (low + high) / 2.0
        plan = pending['plan']
        decision = pending['decision']
        await self._open_paper_at_price(
            symbol=symbol, side=pending['side'], action=pending['action'],
            plan=plan, decision=decision,
            fill_price=fill_price, entry_method='limit_filled',
        )
        self._pending_limits.pop(symbol, None)
```

- [ ] **Step 2.8: Refactor — extract `_open_paper_at_price` helper**

The current `_open_paper` has fill-price-fetching logic intermingled with position construction. Extract a helper called by both immediate fill and limit fill paths. Edit the body of `_open_paper` from the price-fetch onward (`paper_executor.py:154-225`) to delegate to the new helper. The new structure:

Replace lines 154 onward (after the early returns) with:

```python
        price = self._latest_price.get(symbol)
        if not price:
            price = (plan or {}).get('entry_zone', [0])[0] if plan else 0
            if not price:
                self.logger.warning(f"[PAPER] {symbol} 无价格快照，跳过开仓")
                return
        await self._open_paper_at_price(
            symbol=symbol, side=side, action=action,
            plan=plan, decision=decision,
            fill_price=price, entry_method='market',
        )

    async def _open_paper_at_price(self, symbol: str, side: str, action: str,
                                   plan: Optional[dict], decision: dict,
                                   fill_price: float, entry_method: str) -> None:
        """Shared open path: builds position dict, persists, publishes event."""
        if plan:
            margin = float(plan.get('size_usdt', 0))
            leverage = int(plan.get('leverage', 3))
            sl = float(plan.get('stop_loss', 0))
            tp_levels = plan.get('tp_levels') or []
            if tp_levels:
                tp = float(tp_levels[0])
            else:
                tp_raw = plan.get('take_profit', 0)
                if isinstance(tp_raw, (list, tuple)):
                    tp = float(tp_raw[0]) if tp_raw else 0.0
                else:
                    tp = float(tp_raw or 0)
            atr_pct = plan.get('atr_pct', 0.02)
        else:
            margin = self._max_trade_amount * decision.get('size_pct', 0.5)
            leverage = int(self.config.get('leverage', 3))
            sl_dist = 0.025
            sl = fill_price * (1 - sl_dist) if side == 'long' else fill_price * (1 + sl_dist)
            tp = fill_price * (1 + sl_dist * 1.5) if side == 'long' else fill_price * (1 - sl_dist * 1.5)
            atr_pct = 0.02

        if margin <= 0 or (self._equity - self._locked_margin()) < margin:
            self.logger.warning(
                f"[PAPER] {symbol} 跳过：可用保证金不足 (margin={margin}, "
                f"free_equity={self._equity - self._locked_margin():.2f})"
            )
            return

        notional = margin * leverage
        entry_fee = self._fee(notional)
        pos = {
            'symbol': symbol, 'side': side,
            'entry_price': fill_price,
            'sl': sl, 'tp': tp,
            'margin': margin, 'leverage': leverage, 'notional': notional,
            'opened_at': time.time(), 'entry_fee': entry_fee,
            'atr_pct': atr_pct,
            'source': decision.get('source', 'judge'),
            'confidence': decision.get('confidence', 0),
            'request_id': decision.get('request_id', ''),
            'attribution': decision.get('attribution') or (plan or {}).get('attribution') or {},
            'entry_method': entry_method,
        }
        self._positions[symbol] = pos
        self._equity -= entry_fee
        self._persist_state()
        self.logger.info(
            f"[PAPER] OPEN {side.upper()} {symbol} @ {fill_price:.6f} "
            f"margin={margin:.2f} lev={leverage}x SL={sl:.6f} TP={tp:.6f} "
            f"entry_method={entry_method}"
        )
        await self.publish("paper_execution_result", {
            "status": "executed", "action": action, "symbol": symbol,
            "request_id": pos.get('request_id', ''), "result": dict(pos),
            "paper_equity": round(self._equity, 4),
            "locked_margin": round(self._locked_margin(), 4),
            "free_equity": round(self._equity - self._locked_margin(), 4),
        }, symbol=symbol)
```

- [ ] **Step 2.9: Handle `close` action when pending limit exists (Req5 Scenario 2)**

Edit `_execute_decision` at `paper_executor.py:124-135` — before the existing branches that touch `position`, add a pending-limit cancellation path. The full updated section:

```python
        if action in ('open_long', 'open_short') and position is None:
            if symbol in self._pending_limits:
                # Already waiting on a limit fill — guard handled inside _open_paper
                self.logger.info(
                    f"[PAPER] {norm_symbol} {action} 跳过：已有 pending limit"
                )
                return
            if source == 'position_analyst':
                return
            await self._open_paper(norm_symbol, action, plan, decision)
        elif action in ('open_long', 'open_short') and position is not None:
            if source == 'position_analyst':
                await self._add_paper(norm_symbol, action, size_pct, position)
        elif action == 'close':
            if norm_symbol in self._pending_limits:
                self._pending_limits.pop(norm_symbol, None)
                self.logger.info(f"[PAPER] {norm_symbol} close 取消 pending limit")
                return
            if position is None:
                return
            if size_pct < 1.0 and source == 'position_analyst':
                await self._reduce_paper(norm_symbol, size_pct, position)
            else:
                await self._close_paper(norm_symbol, position, reason='signal_close')
```

- [ ] **Step 2.10: Forward `entry_method` into close trade record**

Edit `_close_paper` (`paper_executor.py:227-274`). After the `trade_record = { ... }` dict construction (around `paper_executor.py:251-261`), add `entry_method` propagation. Add this line right after `trade_record['paper_equity_after'] = ...`:

```python
        trade_record['entry_method'] = position.get('entry_method', 'market')
```

The fail-safe default `'market'` covers legacy positions loaded from disk without the field (Req4 Scenario 4).

- [ ] **Step 2.11: Compile check**

Run: `python3 -m compileall -q agents/trading/paper_executor.py`
Expected: no output (success).

- [ ] **Step 2.12: Commit Phase 2**

```bash
git add agents/trading/paper_executor.py
git commit -m "feat(paper): limit-order pending state + entry_method field

- _pending_limits in-memory dict; tick-driven _wait_paper_limit_fill
- _enqueue_pending_limit / _open_paper_at_price helpers (single-funnel)
- entry_method written to position dict + close trade record
- Duplicate-pending guard + close-cancels-pending semantics

Spec: openspec/changes/pullback-entry-paper-parity/specs/paper-executor/spec.md
Req1/2/4/5/6 (write side); timeout/staleness handled in P3.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

archived-with: 2026-06-03-pullback-entry-paper-parity
---

## Phase 3 — Cleanup Loop + Timeout Decision Tree

**Files:**
- Modify: `agents/trading/paper_executor.py` (add `_scan_pending_limits` + `_resolve_pending_timeout`; extend `tick()`)

**Spec coverage:** Req3 (timeout dispatch), Req7 (staleness gating), Req8 (cleanup loop period).

- [ ] **Step 3.1: Implement `_scan_pending_limits`**

Insert after `_wait_paper_limit_fill`:

```python
    async def _scan_pending_limits(self) -> None:
        """Cleanup loop: resolve any pending limit whose deadline has elapsed."""
        if not self._pending_limits:
            return
        now = time.time()
        # Snapshot to allow mutation during iteration
        expired = [(sym, p) for sym, p in self._pending_limits.items()
                   if now >= p['deadline']]
        for symbol, pending in expired:
            await self._resolve_pending_timeout(symbol, pending, now)
```

- [ ] **Step 3.2: Implement `_resolve_pending_timeout` decision tree**

Insert after `_scan_pending_limits`:

```python
    async def _resolve_pending_timeout(self, symbol: str, pending: dict, now: float) -> None:
        """Decision tree: no_fallback → reject; else fresh tick → market; stale → reject."""
        side = pending['side']
        plan = pending['plan']
        decision = pending['decision']
        no_fallback = pending['limit_no_fallback']
        request_id = decision.get('request_id', '')
        entry_zone = pending['entry_zone']

        self._pending_limits.pop(symbol, None)

        if no_fallback:
            self._record_paper_unfilled(symbol, side, request_id, entry_zone,
                                        reason='paper_unfilled')
            await self._publish_paper_unfilled(symbol, side, request_id,
                                               entry_zone, plan, subtype=None)
            return

        last_tick_ts = self._latest_tick_ts.get(symbol)
        latest_price = self._latest_price.get(symbol)
        stale = (last_tick_ts is None
                 or latest_price is None
                 or (now - last_tick_ts) > self._tick_staleness_sec)
        if stale:
            self._record_paper_unfilled(symbol, side, request_id, entry_zone,
                                        reason='paper_unfilled_no_tick')
            await self._publish_paper_unfilled(symbol, side, request_id,
                                               entry_zone, plan, subtype='no_tick')
            return

        # Fresh tick → market fallback
        await self._open_paper_at_price(
            symbol=symbol, side=side, action=pending['action'],
            plan=plan, decision=decision,
            fill_price=float(latest_price), entry_method='market',
        )
        self.logger.info(
            f"[PAPER] {symbol} {side} 限价超时 fallback market @ {latest_price:.6f}"
        )

    def _record_paper_unfilled(self, symbol: str, side: str, request_id: str,
                               entry_zone: list, reason: str) -> None:
        record = {
            "ts": time.time(),
            "symbol": symbol,
            "side": side,
            "action": f"open_{side}",
            "reason": reason,
            "entry_method": "limit_unfilled",
            "entry_zone": list(entry_zone),
            "request_id": request_id,
            "halt_reason": self._halt_state.reason,
        }
        self._rejected_log.append(record)
        if len(self._rejected_log) > 100:
            self._rejected_log = self._rejected_log[-50:]
        self.logger.info(f"[PAPER] {symbol} {side} {reason} entry_zone={entry_zone}")

    async def _publish_paper_unfilled(self, symbol: str, side: str, request_id: str,
                                      entry_zone: list, plan: dict,
                                      subtype: Optional[str]) -> None:
        payload = {
            "type": "paper_unfilled",
            "source": "paper_executor",
            "symbol": symbol,
            "side": side,
            "entry_zone": list(entry_zone),
            "request_id": request_id,
            "timeout_sec": float(plan.get('limit_timeout_sec', 0)),
            "limit_no_fallback": bool(plan.get('limit_no_fallback', False)),
        }
        if subtype:
            payload["subtype"] = subtype
            payload["reason"] = "paper_unfilled_no_tick"
        try:
            await self.publish("risk_alert", payload, symbol=symbol)
        except Exception as e:
            self.logger.warning(f"[PAPER] {symbol} paper_unfilled publish failed: {e}")
```

- [ ] **Step 3.3: Hook `_scan_pending_limits` into `tick()`**

Edit `paper_executor.py:449-457` — replace `tick()` body:

```python
    async def tick(self):
        import asyncio
        await asyncio.sleep(30)
        await self._scan_pending_limits()
        if int(time.time()) % 300 < 30:
            self.logger.info(
                f"[PaperExecutor] equity={self._equity:.2f} 持仓={len(self._positions)} "
                f"unrealized_pnl≈{self._unrealized_pnl():+.2f} "
                f"pending_limits={len(self._pending_limits)}"
            )
```

- [ ] **Step 3.4: Compile check**

Run: `python3 -m compileall -q agents/trading/paper_executor.py`

- [ ] **Step 3.5: Commit Phase 3**

```bash
git add agents/trading/paper_executor.py
git commit -m "feat(paper): timeout decision tree + cleanup loop

- _scan_pending_limits called from tick() (30s period)
- _resolve_pending_timeout: no_fallback → reject;
  fresh tick → market; stale tick → no_tick reject
- _record_paper_unfilled / _publish_paper_unfilled helpers
- Tick staleness gated by configurable _tick_staleness_sec

Spec: Req3, Req7, Req8

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

archived-with: 2026-06-03-pullback-entry-paper-parity
---

## Phase 4 — Telegram critical_types + Source Prefix

**Files:**
- Modify: `agents/trading/telegram_notifier.py:201-260`

**Spec coverage:** risk-alert-routing/spec.md Req1 (critical_types), Req2 (source-based prefix).

- [ ] **Step 4.1: Extend critical_types tuple**

Edit `agents/trading/telegram_notifier.py:207-219`. Add two entries to the `critical_types` tuple:

```python
        critical_types = (
            'flash_move', 'max_drawdown', 'emergency_close', 'llm_degraded',
            'protection_failed',
            'symbol_halt_cleared',
            'symbol_halt_not_found',
            'force_resume_cleared_symbol_halts',
            # entry-drift-hybrid-policy
            'entry_drift_abandoned',
            'entry_drift_rr_fail',
            'plan_missing_entry_ref',
            'tp_invariant_breach',
            'sl_invariant_breach',
            # pullback-entry-paper-parity
            'pullback_unfilled',
            'paper_unfilled',
        )
```

- [ ] **Step 4.2: Add unfilled handler branch with source-based prefix**

After the `force_resume_cleared_symbol_halts` early-return block (around `paper_executor.py:241-242`, NOT paper_executor — telegram_notifier.py:241-242), insert before the existing `type_names` dict (around line 246):

```python
        if alert_type in ('pullback_unfilled', 'paper_unfilled'):
            source = payload.get('source', '')
            if alert_type == 'paper_unfilled' and source != 'paper_executor':
                self.logger.warning(
                    f"[TG] paper_unfilled with unexpected source={source!r}"
                )
            if alert_type == 'pullback_unfilled' and not source:
                self.logger.warning(
                    "[TG] pullback_unfilled missing source field — defaulting to live prefix"
                )
            prefix = '[模拟]' if source == 'paper_executor' else '[实盘]'
            side = payload.get('side', '')
            entry_zone = payload.get('entry_zone') or []
            request_id = payload.get('request_id', '')
            timeout_sec = payload.get('timeout_sec', 0)
            subtype = payload.get('subtype', '')
            kind = '⏱️ 限价未成交'
            if subtype == 'no_tick':
                kind = '⏱️ 限价超时(行情失联)'
            text = (
                f"{prefix} {kind} {symbol} {side}\n"
                f"区间: {entry_zone}\n"
                f"timeout: {timeout_sec:.0f}s\n"
                f"req: {request_id}"
            )
            await self._send_message(text)
            return
```

- [ ] **Step 4.3: Compile check**

Run: `python3 -m compileall -q agents/trading/telegram_notifier.py`

- [ ] **Step 4.4: Commit Phase 4**

```bash
git add agents/trading/telegram_notifier.py
git commit -m "feat(tg): route pullback_unfilled / paper_unfilled to critical_types

- critical_types tuple extended with both alert types
- Branch handler with source-based [实盘]/[模拟] prefix
- subtype='no_tick' surfaces as 行情失联 variant
- Fail-safe: missing/unknown source defaults to live + warning

Spec: openspec/changes/pullback-entry-paper-parity/specs/risk-alert-routing/spec.md Req1/Req2

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

archived-with: 2026-06-03-pullback-entry-paper-parity
---

## Phase 5 — Live Alert source Field + Agent Log Passthrough

**Files:**
- Modify: `executor.py:1235-1241` (`_enqueue_drift_alert` — inject `source='executor'`)
- Modify: `agents/trading/executor.py:423-435` (`_drain_drift_alerts` — log pullback_unfilled at agent layer)

**Spec coverage:** risk-alert-routing/spec.md Req2 Scenario 2 (live source); design TD-7 (agent log passthrough).

- [ ] **Step 5.1: Inject `source='executor'` default in root drift alert helper**

Edit `executor.py:1235-1241`:

```python
    def _enqueue_drift_alert(self, alert_type: str, **fields) -> None:
        """Buffer a drift-related risk alert for the agent layer to drain & publish."""
        alert = {
            'type': alert_type,
            'timestamp': time.time(),
            'source': fields.pop('source', 'executor'),
            **fields,
        }
        self._pending_drift_alerts.append(alert)
```

- [ ] **Step 5.2: Log pullback_unfilled at agent layer**

Edit `agents/trading/executor.py:423-435`. Replace `_drain_drift_alerts`:

```python
    async def _drain_drift_alerts(self) -> None:
        """Drain root executor's pending drift alerts and publish them as risk_alert events."""
        ex = getattr(self, 'executor', None)
        pending = getattr(ex, '_pending_drift_alerts', None) if ex else None
        if not pending:
            return
        alerts = list(pending)
        pending.clear()
        for alert in alerts:
            atype = alert.get('type', '')
            if atype == 'pullback_unfilled':
                self.logger.info(
                    f"[Pullback] {alert.get('symbol')} {alert.get('side')} "
                    f"limit @ {alert.get('limit_price')} "
                    f"timeout={alert.get('timeout_sec')}s 未成交（live）"
                )
            try:
                await self.publish('risk_alert', alert)
            except Exception as e:
                self.logger.warning(f"[Drift Alert] publish failed: {e}")
```

- [ ] **Step 5.3: Compile check**

Run: `python3 -m compileall -q .`
Expected: no errors.

- [ ] **Step 5.4: Commit Phase 5**

```bash
git add executor.py agents/trading/executor.py
git commit -m "feat(executor): tag drift alerts with source + agent-layer pullback log

- _enqueue_drift_alert injects source='executor' (caller can override)
- _drain_drift_alerts logs pullback_unfilled at agent layer
  so agent_executor_*.log shows the event (root logger remains source of truth)

Spec: risk-alert-routing Req2 Scenario 2; design TD-7

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

archived-with: 2026-06-03-pullback-entry-paper-parity
---

## Phase 6 — Tests for Paper Limit Fill

**Files:**
- Create: `tests/test_paper_limit_fill.py`

**Spec coverage:** Req1/2/3/4/5/6/7/8 — all scenarios under paper-executor/spec.md.

**Test infrastructure pattern (use freezegun + asyncio):**

```python
import asyncio
import pytest
from freezegun import freeze_time
from datetime import timedelta
from agents.trading.paper_executor import PaperExecutor

class _MockBus:
    def __init__(self):
        self.published = []
    async def publish(self, sender, msg_type, payload, to='broadcast', symbol=None):
        self.published.append({'type': msg_type, 'payload': payload, 'symbol': symbol})

@pytest.fixture
def pe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir()
    pe = PaperExecutor({'effective_balance_cap': 1000.0,
                        'min_confidence': 60,
                        'max_trade_amount': 30,
                        'paper_limit_tick_staleness_sec': 60})
    pe.bus = _MockBus()
    asyncio.get_event_loop().run_until_complete(pe.setup())
    return pe

def _decision(action='open_short', confidence=70, request_id='REQ-1', symbol='WLD-USDT', plan=None):
    return {
        'type': 'trade_decision',
        'symbol': symbol,
        'payload': {
            'action': action, 'confidence': confidence, 'symbol': symbol,
            'plan': plan, 'request_id': request_id,
            'attribution': {'entry_type': 'ma_aligned'},
        },
    }

def _limit_plan(side='short', entry_low=0.4043, entry_high=0.4047,
                timeout_sec=1800, no_fallback=True):
    sl = 0.4362 if side == 'short' else 0.3700
    tp = 0.3411 if side == 'short' else 0.4500
    return {
        'side': side,
        'entry_zone': [entry_low, entry_high],
        'order_type': 'limit',
        'limit_timeout_sec': timeout_sec,
        'limit_no_fallback': no_fallback,
        'size_usdt': 30, 'leverage': 10,
        'stop_loss': sl, 'take_profit': [tp],
        'atr_pct': 0.0392,
    }

async def _send(pe, msg):
    await pe.on_message(msg)
```

The test cases below build on this infrastructure.

- [ ] **Step 6.1: Test — limit plan enters _pending_limits without immediate fill**

```python
@pytest.mark.asyncio
async def test_limit_plan_enters_pending_not_position(pe):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    assert 'WLD-USDT' in pe._pending_limits
    assert 'WLD-USDT' not in pe._positions
    assert pe._pending_limits['WLD-USDT']['limit_no_fallback'] is True
```

- [ ] **Step 6.2: Test — market plan keeps immediate fill, entry_method='market'**

```python
@pytest.mark.asyncio
async def test_market_plan_immediate_fill(pe):
    plan = _limit_plan()
    plan['order_type'] = 'market'
    pe._latest_price['WLD-USDT'] = 0.40
    await _send(pe, _decision(plan=plan))
    assert 'WLD-USDT' in pe._positions
    assert pe._positions['WLD-USDT']['entry_method'] == 'market'
```

- [ ] **Step 6.3: Test — limit plan with empty entry_zone fails over to market**

```python
@pytest.mark.asyncio
async def test_limit_plan_missing_entry_zone_falls_back_to_market(pe):
    plan = _limit_plan()
    plan['entry_zone'] = []
    pe._latest_price['WLD-USDT'] = 0.40
    await _send(pe, _decision(plan=plan))
    assert 'WLD-USDT' in pe._positions
    assert pe._positions['WLD-USDT']['entry_method'] == 'market'
    assert 'WLD-USDT' not in pe._pending_limits
```

- [ ] **Step 6.4: Test — tick inside zone fills at midpoint**

```python
@pytest.mark.asyncio
async def test_tick_inside_zone_fills_at_midpoint(pe):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    await _send(pe, {'type': 'price_tick', 'symbol': 'WLD-USDT',
                     'payload': {'symbol': 'WLD-USDT', 'price': 0.4044}})
    assert 'WLD-USDT' in pe._positions
    pos = pe._positions['WLD-USDT']
    assert abs(pos['entry_price'] - 0.4045) < 1e-9
    assert pos['entry_method'] == 'limit_filled'
    assert 'WLD-USDT' not in pe._pending_limits
```

- [ ] **Step 6.5: Test — instantaneous tick crossing zone still fills**

```python
@pytest.mark.asyncio
async def test_tick_crossing_zone_fills(pe):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    for p in (0.4042, 0.4046, 0.4060):
        await _send(pe, {'type': 'price_tick', 'symbol': 'WLD-USDT',
                         'payload': {'symbol': 'WLD-USDT', 'price': p}})
    assert pe._positions['WLD-USDT']['entry_method'] == 'limit_filled'
```

- [ ] **Step 6.6: Test — timeout no_fallback=True publishes paper_unfilled**

```python
@pytest.mark.asyncio
async def test_timeout_no_fallback_paper_unfilled(pe):
    plan = _limit_plan(no_fallback=True)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await _send(pe, _decision(plan=plan))
        frozen.tick(delta=timedelta(seconds=1801))
        await pe._scan_pending_limits()
    assert 'WLD-USDT' not in pe._positions
    assert 'WLD-USDT' not in pe._pending_limits
    alerts = [m for m in pe.bus.published if m['type'] == 'risk_alert']
    assert len(alerts) == 1
    a = alerts[0]['payload']
    assert a['type'] == 'paper_unfilled'
    assert a['source'] == 'paper_executor'
    assert 'subtype' not in a
    rejected = [r for r in pe._rejected_log if r['reason'] == 'paper_unfilled']
    assert len(rejected) == 1
    assert rejected[0]['entry_method'] == 'limit_unfilled'
```

- [ ] **Step 6.7: Test — timeout no_fallback=False with fresh tick → market**

```python
@pytest.mark.asyncio
async def test_timeout_fallback_market_with_fresh_tick(pe):
    plan = _limit_plan(no_fallback=False)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await _send(pe, _decision(plan=plan))
        # tick comes 100s before deadline (within 60s staleness window of timeout)
        frozen.tick(delta=timedelta(seconds=1750))
        await _send(pe, {'type': 'price_tick', 'symbol': 'WLD-USDT',
                         'payload': {'symbol': 'WLD-USDT', 'price': 0.4100}})
        frozen.tick(delta=timedelta(seconds=51))
        await pe._scan_pending_limits()
    assert 'WLD-USDT' in pe._positions
    assert pe._positions['WLD-USDT']['entry_method'] == 'market'
    assert abs(pe._positions['WLD-USDT']['entry_price'] - 0.4100) < 1e-9
    alerts = [m for m in pe.bus.published if m['type'] == 'risk_alert']
    assert not alerts  # success path, no alert
```

- [ ] **Step 6.8: Test — timeout no_fallback=False without tick → no_tick rejection**

```python
@pytest.mark.asyncio
async def test_timeout_fallback_no_tick(pe):
    plan = _limit_plan(no_fallback=False)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await _send(pe, _decision(plan=plan))
        frozen.tick(delta=timedelta(seconds=1801))
        await pe._scan_pending_limits()
    assert 'WLD-USDT' not in pe._positions
    alerts = [m for m in pe.bus.published if m['type'] == 'risk_alert']
    assert alerts and alerts[0]['payload'].get('subtype') == 'no_tick'
```

- [ ] **Step 6.9: Test — timeout no_fallback=False with stale tick → no_tick rejection**

```python
@pytest.mark.asyncio
async def test_timeout_fallback_stale_tick(pe):
    plan = _limit_plan(no_fallback=False)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await _send(pe, _decision(plan=plan))
        # tick at t=0, freeze for 1801s (last tick is 1801s old, way > 60s staleness)
        await _send(pe, {'type': 'price_tick', 'symbol': 'WLD-USDT',
                         'payload': {'symbol': 'WLD-USDT', 'price': 0.4100}})
        frozen.tick(delta=timedelta(seconds=1801))
        await pe._scan_pending_limits()
    assert 'WLD-USDT' not in pe._positions
    alerts = [m for m in pe.bus.published if m['type'] == 'risk_alert']
    paper = [a for a in alerts if a['payload']['type'] == 'paper_unfilled']
    assert paper and paper[0]['payload'].get('subtype') == 'no_tick'
```

- [ ] **Step 6.10: Test — custom staleness threshold honored**

```python
@pytest.mark.asyncio
async def test_custom_staleness_threshold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir()
    pe = PaperExecutor({'effective_balance_cap': 1000.0, 'min_confidence': 60,
                        'max_trade_amount': 30, 'paper_limit_tick_staleness_sec': 120})
    pe.bus = _MockBus()
    await pe.setup()
    assert pe._tick_staleness_sec == 120.0

    plan = _limit_plan(no_fallback=False, timeout_sec=600)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await pe.on_message(_decision(plan=plan))
        await pe.on_message({'type': 'price_tick', 'symbol': 'WLD-USDT',
                             'payload': {'symbol': 'WLD-USDT', 'price': 0.4100}})
        # Advance past timeout (600s) but tick is only 100s old (< 120s staleness)
        frozen.tick(delta=timedelta(seconds=601))
        await pe._scan_pending_limits()
    # 100s < 120s threshold → fresh → market fallback succeeds
    assert pe._positions['WLD-USDT']['entry_method'] == 'market'
```

- [ ] **Step 6.11: Test — duplicate open_short during pending is skipped**

```python
@pytest.mark.asyncio
async def test_duplicate_open_during_pending_skipped(pe):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan, request_id='REQ-1'))
    await _send(pe, _decision(plan=plan, request_id='REQ-2'))
    assert pe._pending_limits['WLD-USDT']['decision']['request_id'] == 'REQ-1'
```

- [ ] **Step 6.12: Test — close cancels pending without opening a position**

```python
@pytest.mark.asyncio
async def test_close_cancels_pending(pe):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    assert 'WLD-USDT' in pe._pending_limits
    close_decision = {'type': 'trade_decision', 'symbol': 'WLD-USDT',
                      'payload': {'action': 'close', 'confidence': 0,
                                  'symbol': 'WLD-USDT'}}
    await _send(pe, close_decision)
    assert 'WLD-USDT' not in pe._pending_limits
    assert 'WLD-USDT' not in pe._positions
```

- [ ] **Step 6.13: Test — restart drops _pending_limits**

```python
@pytest.mark.asyncio
async def test_restart_drops_pending_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir()
    cfg = {'effective_balance_cap': 1000.0, 'min_confidence': 60,
           'max_trade_amount': 30}
    pe1 = PaperExecutor(cfg); pe1.bus = _MockBus(); await pe1.setup()
    plan = _limit_plan()
    await pe1.on_message(_decision(plan=plan))
    assert 'WLD-USDT' in pe1._pending_limits
    pe1._persist_state()

    pe2 = PaperExecutor(cfg); pe2.bus = _MockBus(); await pe2.setup()
    assert pe2._pending_limits == {}
```

- [ ] **Step 6.14: Test — _persist_state does not include _pending_limits in any file**

```python
@pytest.mark.asyncio
async def test_save_state_does_not_serialize_pending(pe, tmp_path):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    pe._persist_state()
    import json
    with open('data/paper_positions.json') as f:
        data = json.load(f)
    assert 'pending_limits' not in data
    # paper_positions.json should be empty dict (no positions yet)
    assert data == {}
```

- [ ] **Step 6.15: Test — close trade record carries entry_method (and legacy fail-safe)**

```python
@pytest.mark.asyncio
async def test_close_record_carries_entry_method_and_legacy_default(pe):
    # Path A: limit_filled propagates
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    await _send(pe, {'type': 'price_tick', 'symbol': 'WLD-USDT',
                     'payload': {'symbol': 'WLD-USDT', 'price': 0.4044}})
    assert pe._positions['WLD-USDT']['entry_method'] == 'limit_filled'
    await pe._close_paper('WLD-USDT', pe._positions['WLD-USDT'], reason='manual')
    import json
    with open('data/paper_trades.jsonl') as f:
        rec = json.loads(f.readlines()[-1])
    assert rec['entry_method'] == 'limit_filled'

    # Path B: legacy position with no entry_method falls back to 'market' on close
    pe._positions['LEGACY-USDT'] = {
        'symbol': 'LEGACY-USDT', 'side': 'long', 'entry_price': 1.0,
        'sl': 0.95, 'tp': 1.1, 'margin': 30, 'leverage': 10, 'notional': 300,
        'opened_at': 1.0, 'entry_fee': 0.3, 'atr_pct': 0.02,
        # NO entry_method field — simulating pre-change record
    }
    pe._latest_price['LEGACY-USDT'] = 1.05
    await pe._close_paper('LEGACY-USDT', pe._positions['LEGACY-USDT'], reason='manual')
    with open('data/paper_trades.jsonl') as f:
        rec = json.loads(f.readlines()[-1])
    assert rec['entry_method'] == 'market'  # fail-safe default
```

- [ ] **Step 6.16: Test — cleanup loop runs in tick(), processes deadline-elapsed entries**

```python
@pytest.mark.asyncio
async def test_cleanup_loop_runs_each_tick(pe, monkeypatch):
    plan = _limit_plan(timeout_sec=10)
    # Patch tick() to skip 30s sleep
    import asyncio as _asyncio
    monkeypatch.setattr(_asyncio, 'sleep', lambda *_a, **_k: _asyncio.sleep(0))
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await _send(pe, _decision(plan=plan))
        assert 'WLD-USDT' in pe._pending_limits
        frozen.tick(delta=timedelta(seconds=11))
        await pe.tick()
    assert 'WLD-USDT' not in pe._pending_limits
```

- [ ] **Step 6.17: Run all tests and commit**

```bash
pytest -q tests/test_paper_limit_fill.py
```
Expected: 16+ passed, 0 failed.

```bash
git add tests/test_paper_limit_fill.py
git commit -m "test(paper): cover limit fill paths + tick staleness gating

16 cases over Req1-Req8 / paper-executor spec.
Uses freezegun to control time; _MockBus to capture risk_alert publishes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

archived-with: 2026-06-03-pullback-entry-paper-parity
---

## Phase 7 — Tests for Telegram Alert Routing

**Files:**
- Create: `tests/test_telegram_pullback_alerts.py`

**Spec coverage:** risk-alert-routing/spec.md Req1 (critical_types) + Req2 (source-based prefix), all 6 scenarios.

- [ ] **Step 7.1: Test — pullback_unfilled (live) sends [实盘] message**

```python
import asyncio, pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agents.trading.telegram_notifier import TelegramNotifier

def _tg(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'x')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '1')
    tg = TelegramNotifier({})
    tg._send_message = AsyncMock()
    return tg

@pytest.mark.asyncio
async def test_pullback_unfilled_live_prefix(monkeypatch):
    tg = _tg(monkeypatch)
    msg = {'payload': {'type': 'pullback_unfilled', 'source': 'executor',
                       'symbol': 'WLD-USDT', 'side': 'short',
                       'entry_zone': [0.4043, 0.4047], 'request_id': 'R1',
                       'timeout_sec': 1800}}
    await tg._handle_risk_alert(msg)
    tg._send_message.assert_called_once()
    text = tg._send_message.call_args[0][0]
    assert '[实盘]' in text
    assert 'WLD-USDT' in text
    assert 'R1' in text
```

- [ ] **Step 7.2: Test — paper_unfilled (paper) sends [模拟] message**

```python
@pytest.mark.asyncio
async def test_paper_unfilled_paper_prefix(monkeypatch):
    tg = _tg(monkeypatch)
    msg = {'payload': {'type': 'paper_unfilled', 'source': 'paper_executor',
                       'symbol': 'WLD-USDT', 'side': 'short',
                       'entry_zone': [0.4043, 0.4047], 'request_id': 'R1',
                       'timeout_sec': 1800}}
    await tg._handle_risk_alert(msg)
    text = tg._send_message.call_args[0][0]
    assert '[模拟]' in text
```

- [ ] **Step 7.3: Test — paper_unfilled with subtype=no_tick uses 行情失联 variant**

```python
@pytest.mark.asyncio
async def test_paper_unfilled_no_tick_variant(monkeypatch):
    tg = _tg(monkeypatch)
    msg = {'payload': {'type': 'paper_unfilled', 'source': 'paper_executor',
                       'symbol': 'WLD-USDT', 'side': 'short',
                       'entry_zone': [0.4043, 0.4047], 'request_id': 'R1',
                       'timeout_sec': 1800, 'subtype': 'no_tick'}}
    await tg._handle_risk_alert(msg)
    text = tg._send_message.call_args[0][0]
    assert '行情失联' in text
```

- [ ] **Step 7.4: Test — pullback_unfilled missing source defaults to live + warning**

```python
@pytest.mark.asyncio
async def test_pullback_unfilled_missing_source_fail_safe(monkeypatch):
    tg = _tg(monkeypatch)
    tg.logger = MagicMock()
    msg = {'payload': {'type': 'pullback_unfilled',
                       'symbol': 'WLD-USDT', 'side': 'short',
                       'entry_zone': [0.4043, 0.4047], 'request_id': 'R1',
                       'timeout_sec': 1800}}
    await tg._handle_risk_alert(msg)
    text = tg._send_message.call_args[0][0]
    assert '[实盘]' in text
    tg.logger.warning.assert_called()
```

- [ ] **Step 7.5: Test — non-critical alert types unaffected (regression guard)**

```python
@pytest.mark.asyncio
async def test_unknown_type_does_not_send(monkeypatch):
    tg = _tg(monkeypatch)
    msg = {'payload': {'type': 'some_random_type', 'symbol': 'X'}}
    await tg._handle_risk_alert(msg)
    tg._send_message.assert_not_called()
```

- [ ] **Step 7.6: Test — both alerts in sequence yield two distinguishable messages**

```python
@pytest.mark.asyncio
async def test_paper_and_live_unfilled_distinguished(monkeypatch):
    tg = _tg(monkeypatch)
    base = {'symbol': 'WLD-USDT', 'side': 'short',
            'entry_zone': [0.4043, 0.4047], 'request_id': 'R1', 'timeout_sec': 1800}
    await tg._handle_risk_alert({'payload': {'type': 'pullback_unfilled',
                                              'source': 'executor', **base}})
    await tg._handle_risk_alert({'payload': {'type': 'paper_unfilled',
                                              'source': 'paper_executor', **base}})
    assert tg._send_message.call_count == 2
    text_live = tg._send_message.call_args_list[0][0][0]
    text_paper = tg._send_message.call_args_list[1][0][0]
    assert '[实盘]' in text_live
    assert '[模拟]' in text_paper
    assert text_live != text_paper
```

- [ ] **Step 7.7: Run tests and commit**

```bash
pytest -q tests/test_telegram_pullback_alerts.py
```
Expected: 6 passed.

```bash
git add tests/test_telegram_pullback_alerts.py
git commit -m "test(tg): cover pullback_unfilled / paper_unfilled routing

6 cases over risk-alert-routing spec Req1+Req2 (all scenarios).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

archived-with: 2026-06-03-pullback-entry-paper-parity
---

## Phase 8 — Full Regression + Docs Sync

**Files:**
- Modify: `docs/to-do-list.md`, `CLAUDE.md`
- Verify: All existing tests still green

- [ ] **Step 8.1: Run targeted regression on adjacent suites**

```bash
python3 -m pytest -q tests/test_pullback_atr_policy.py tests/test_limit_no_fallback.py
```
Expected: existing pullback tests all pass (no regression in Judge layer).

- [ ] **Step 8.2: Run paper_executor existing test suite**

```bash
python3 -m pytest -q -k paper
```
Expected: existing paper tests still pass.

- [ ] **Step 8.3: Run full pytest suite**

```bash
python3 -m pytest -q
```
Expected: baseline grows from 954 → ~980 (+~26 new). 0 failed, deselect/warning unchanged.

- [ ] **Step 8.4: Run compile check**

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
```
Expected: no errors.

- [ ] **Step 8.5: Update `docs/to-do-list.md`**

Locate the "P2 后续优化" table. Update the line `| OPEN | Paper 结果独立复盘 | ...` to reflect that `entry_method` field has been added (foundational for future reviewer). Add a new line under it:

```
| DONE 2026-06-03 | Paper limit 撮合契约对齐 (pullback-entry-paper-parity) | _wait_paper_limit_fill 单一入口 + entry_method 字段 + tick staleness gating + TG critical_types 路由 | tests/test_paper_limit_fill.py 16 case + tests/test_telegram_pullback_alerts.py 6 case PASS;详见 docs/audit_remediation_pullback_entry_paper_parity_acceptance.md (verify 阶段产出) |
```

Add follow-up open items if not yet present:

```
| OPEN | ma_aligned 触发面收窄 (issue #2) | 评估 PULLBACK_ATR_ENTRY_TYPES 是否应排除 ma_aligned，让该 entry_type 走 deferred_15m_confirmation | 数据回测后决策 |
| OPEN | PULLBACK_LIMIT_TIMEOUT_SEC 数值调参 (issue #4) | 1800s 是否合理，是否应根据 atr/regime 动态化 | 数据回测后决策 |
| OPEN | paper_limit_tick_staleness_sec 阈值调参 | 60s 默认值是否合适，从 paper_unfilled / paper_unfilled_no_tick 比例评估 | 数据回测后决策 |
```

- [ ] **Step 8.6: Update `CLAUDE.md` 当前事实段**

Locate "## 当前事实" section. Add a new bullet (chronologically ordered):

```
- 2026-06-03 Pullback Entry Paper Parity 上线后基线：`980+ passed / 4 deselected / 1 warning`（新增 `test_paper_limit_fill.py` 16 case + `test_telegram_pullback_alerts.py` 6 case）。Paper Executor `_wait_paper_limit_fill` 单一入口与 `_scan_pending_limits` cleanup loop 双驱动；超时分流 `paper_unfilled` (no_fallback=True) / market fallback (fresh tick) / `paper_unfilled_no_tick` (stale/missing tick)；TG `critical_types` 加入 `pullback_unfilled` (live) 与 `paper_unfilled` (paper)，按 `payload.source` 加 `[实盘]`/`[模拟]` 前缀；`entry_method ∈ {market, limit_filled, limit_unfilled}` 写入 paper position 与 close trade record；`paper_limit_tick_staleness_sec` 默认 60 可经 `.env` 覆盖。详见 `docs/superpowers/specs/2026-06-03-pullback-entry-paper-parity-design.md`。
```

Replace the actual baseline number after running pytest in Step 8.3.

- [ ] **Step 8.7: Mark all tasks.md checkboxes done**

Edit `openspec/changes/pullback-entry-paper-parity/tasks.md` — flip every `- [ ]` to `- [x]` for tasks 0.1 through 10.4 that were addressed by P1-P8 above. (Tasks 10.3 / 10.4 acceptance doc are produced in `/comet-verify`, leave those last two unchecked.)

- [ ] **Step 8.8: Final regression sanity check**

```bash
python3 -m pytest -q
```
Expected: same green count as Step 8.3.

- [ ] **Step 8.9: Commit Phase 8**

```bash
git add docs/to-do-list.md CLAUDE.md openspec/changes/pullback-entry-paper-parity/tasks.md
git commit -m "docs: sync to-do-list, CLAUDE.md baseline, tasks.md after pullback-entry-paper-parity build

- New baseline (replace with actual pytest count)
- Mark P1-P8 tasks complete
- Open follow-ups: ma_aligned trigger surface, timeout tuning, staleness tuning

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

archived-with: 2026-06-03-pullback-entry-paper-parity
---

## Self-Review Checklist (run before dispatching subagents)

- [x] **Spec coverage** — every Requirement / Scenario in OpenSpec spec is touched by P1-P7 (cross-referenced inline above)
- [x] **No placeholders** — all "TBD" / vague comments removed; every step has concrete code or command
- [x] **Type consistency** — `_pending_limits` / `_open_paper_at_price` / `_scan_pending_limits` / `_wait_paper_limit_fill` names consistent everywhere
- [x] **Phase dependencies** — P1 unblocks all; P2 → P3; P4/P5 parallel-safe with P2/P3; P6 needs P2+P3+P5; P7 needs P4+P5; P8 last
- [x] **No drift from design doc** — TD-1..TD-7 implemented as specified; no extra abstractions introduced
- [x] **No drift from spec** — entry_method values fixed at 3, staleness configurable as designed, critical_types extended exactly per spec

## Execution Handoff

Plan complete. Saved to `docs/superpowers/plans/2026-06-03-pullback-entry-paper-parity.md`.

Ready for `subagent-driven-development` (already chosen). Each Phase = one subagent dispatch with the relevant Spec Requirement IDs in the brief.

Suggested dispatch order:
1. **P1** (single subagent — config plumbing)
2. **P2 + P3** (single subagent — paper executor implementation, sequential within)
3. **P4 + P5 in parallel** (two subagents — disjoint files)
4. **P6 + P7 in parallel** (two subagents — disjoint test files; both depend on prior phases done)
5. **P8** (main agent — docs sync + final regression)

