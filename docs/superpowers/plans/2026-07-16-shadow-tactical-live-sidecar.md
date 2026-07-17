---
change: promote-shadow-tactical-live-48h
design-doc: docs/superpowers/specs/2026-07-16-shadow-tactical-live-sidecar-design.md
base-ref: fb7ea653c184c896290f2b793f7488488ec6bd7d
archived-with: 2026-07-17-promote-shadow-tactical-live-48h
---

# Shadow Tactical Live Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a 24-hour live sidecar that mirrors new Shadow Tactical records into live OKX orders while keeping Main strategy state isolated.

**Architecture:** Add a focused sidecar core module for event filtering, mapping, state, audit, and ownership. Add a narrow executor entrypoint that reuses mechanical exchange checks but skips strategy admission gates. Patch Main account-level sync/migration paths to ignore sidecar-owned objects before the live sidecar is started.

**Tech Stack:** Python 3, pytest, existing `ContractExecutor`, `RiskManager`, `LiveLedger`, `CounterfactualLedger`, JSON/JSONL state files, OKX ccxt client.

archived-with: 2026-07-17-promote-shadow-tactical-live-48h
---

## File Structure

- Create `utils/shadow_tactical_live.py`: pure sidecar logic, state persistence, event filtering, plan mapping, ownership registry, same-symbol guard helpers.
- Create `scripts/shadow_tactical_live_sidecar.py`: CLI runner with `run`, `status`, and `stop` commands.
- Modify `executor.py`: optional explicit sidecar state paths, sidecar mechanical open method, owner registry integration in `sync_positions()` and OKX algo migration.
- Modify `utils/halt_state.py` only if tests prove executor-level explicit halt-state injection cannot be kept local to sidecar process.
- Create `tests/test_shadow_tactical_live_core.py`: pure sidecar filtering, mapping, watermark, idempotency, ownership registry tests.
- Create `tests/test_shadow_tactical_live_executor.py`: fake-exchange tests for sidecar mechanical open behavior.
- Create `tests/test_shadow_tactical_owner_isolation.py`: Main sync and algo migration preservation tests.
- Create `tests/test_shadow_tactical_live_cli.py`: CLI dry-run/status/stop tests without live exchange calls.
- Update `openspec/changes/promote-shadow-tactical-live-48h/tasks.md`: check off tasks as implementation completes.

## Task 1: Sidecar Core Parsing, Mapping, State, and Audit

**Files:**
- Create: `utils/shadow_tactical_live.py`
- Create: `tests/test_shadow_tactical_live_core.py`

- [ ] **Step 1: Write failing core tests**

Create `tests/test_shadow_tactical_live_core.py` with these tests:

```python
import json

from utils.shadow_tactical_live import (
    SidecarPaths,
    SidecarStateStore,
    append_audit_event,
    is_tactical_shadow_event,
    iter_new_shadow_events,
    map_shadow_record_to_plan,
)


def _event(record):
    return {"event_type": "rejected_plan_created", "record": record}


def _tactical_record(**overrides):
    rec = {
        "id": "shadow-1",
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "entry_price": 1.25,
        "stop_loss": 1.20,
        "take_profit": [1.32, 1.38],
        "leverage": 20,
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "tactical_source": "shadow_only",
        "tactical_max_hold_minutes": 90,
        "reject_reason": "rr_below_floor",
        "tactical_track_gate": "fail",
    }
    rec.update(overrides)
    return rec


def test_tactical_identity_accepts_track_or_exit_profile():
    assert is_tactical_shadow_event(_event(_tactical_record(track="tactical")))
    assert is_tactical_shadow_event(_event(_tactical_record(track="main", exit_profile="tactical_v1")))
    assert not is_tactical_shadow_event(_event(_tactical_record(track="main", exit_profile="trend_runner")))
    assert not is_tactical_shadow_event({"event_type": "shadow_tp", "record": _tactical_record()})


def test_map_shadow_record_preserves_execution_fields():
    plan = map_shadow_record_to_plan(_tactical_record())
    assert plan["symbol"] == "WLD-USDT-SWAP"
    assert plan["side"] == "long"
    assert plan["entry_ref"] == 1.25
    assert plan["stop_loss"] == 1.20
    assert plan["take_profit"] == [1.32, 1.38]
    assert plan["leverage"] == 20
    assert plan["exit_profile"] == "tactical_v1"
    assert plan["tactical_max_hold_minutes"] == 90
    assert plan["shadow_id"] == "shadow-1"
    assert plan["sidecar_source"] == "shadow_tactical_live"
    assert plan["gate_metadata"]["tactical_track_gate"] == "fail"


def test_missing_required_field_rejects_without_plan():
    ok, reason = map_shadow_record_to_plan(_tactical_record(stop_loss=0), return_error=True)
    assert ok is None
    assert reason == "missing_stop_loss"


def test_state_store_watermark_and_shadow_status(tmp_path):
    state_path = tmp_path / "state.json"
    store = SidecarStateStore(str(state_path))
    state = store.load()
    assert state["last_offset"] == 0
    store.save({**state, "last_offset": 123, "seen_shadow_ids": {"shadow-1": "opened"}})
    loaded = store.load()
    assert loaded["last_offset"] == 123
    assert loaded["seen_shadow_ids"]["shadow-1"] == "opened"


def test_iter_new_shadow_events_starts_after_watermark(tmp_path):
    events_path = tmp_path / "events.jsonl"
    first = json.dumps(_event(_tactical_record(id="old"))) + "\n"
    events_path.write_text(first)
    start_offset = events_path.stat().st_size
    with events_path.open("a") as fh:
        fh.write(json.dumps(_event(_tactical_record(id="new"))) + "\n")
    rows = list(iter_new_shadow_events(str(events_path), start_offset))
    assert len(rows) == 1
    assert rows[0].event["record"]["id"] == "new"
    assert rows[0].next_offset == events_path.stat().st_size


def test_append_audit_event_writes_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_audit_event(str(path), "rejected", {"shadow_id": "s1", "reason": "missing_stop_loss"})
    row = json.loads(path.read_text().strip())
    assert row["event_type"] == "rejected"
    assert row["shadow_id"] == "s1"
    assert row["reason"] == "missing_stop_loss"
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
pytest tests/test_shadow_tactical_live_core.py -q
```

Expected: fails because `utils.shadow_tactical_live` does not exist.

- [ ] **Step 3: Implement core module**

Create `utils/shadow_tactical_live.py` with:

```python
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from utils.atomic_io import atomic_write_json


DEFAULT_EVENTS_PATH = "data/rejected_signal_events.jsonl"


@dataclass(frozen=True)
class SidecarPaths:
    events: str = DEFAULT_EVENTS_PATH
    state: str = "data/shadow_tactical_live_state.json"
    audit: str = "data/shadow_tactical_live_events.jsonl"
    owners: str = "data/shadow_tactical_live_owners.json"
    positions: str = "data/shadow_tactical_live_positions.json"
    risk_state: str = "data/shadow_tactical_live_risk_state.json"
    halt_state: str = "data/shadow_tactical_live_halt_state.json"
    live_order_events: str = "data/shadow_tactical_live_order_events.jsonl"
    live_position_lifecycle: str = "data/shadow_tactical_live_position_lifecycle.json"


@dataclass(frozen=True)
class ShadowEventRow:
    event: dict
    start_offset: int
    next_offset: int


class SidecarStateStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {
                "started_at": time.time(),
                "stop_at": None,
                "last_offset": 0,
                "seen_shadow_ids": {},
            }
        with open(self.path, "r") as fh:
            data = json.load(fh)
        data.setdefault("last_offset", 0)
        data.setdefault("seen_shadow_ids", {})
        return data

    def save(self, state: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        atomic_write_json(self.path, state)


def append_audit_event(path: str, event_type: str, payload: dict) -> dict:
    event = {"ts": time.time(), "event_type": event_type}
    event.update(payload)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def iter_new_shadow_events(path: str, start_offset: int) -> Iterator[ShadowEventRow]:
    if not os.path.exists(path):
        return
    with open(path, "rb") as fh:
        fh.seek(max(0, int(start_offset or 0)))
        while True:
            line_start = fh.tell()
            raw = fh.readline()
            if not raw:
                break
            next_offset = fh.tell()
            if not raw.strip():
                continue
            try:
                event = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                event = {"event_type": "malformed_json", "raw": raw.decode("utf-8", errors="replace")}
            yield ShadowEventRow(event=event, start_offset=line_start, next_offset=next_offset)


def is_tactical_shadow_event(event: dict) -> bool:
    if event.get("event_type") != "rejected_plan_created":
        return False
    rec = event.get("record") or {}
    return rec.get("track") == "tactical" or rec.get("exit_profile") == "tactical_v1"


def _missing_reason(record: dict) -> Optional[str]:
    required = [
        ("symbol", "missing_symbol"),
        ("side", "missing_side"),
        ("entry_price", "missing_entry_price"),
        ("stop_loss", "missing_stop_loss"),
        ("take_profit", "missing_take_profit"),
        ("leverage", "missing_leverage"),
    ]
    for key, reason in required:
        value = record.get(key)
        if value in (None, "", 0, [], {}):
            return reason
    if record.get("side") not in ("long", "short"):
        return "invalid_side"
    return None


def map_shadow_record_to_plan(record: dict, *, return_error: bool = False):
    reason = _missing_reason(record)
    if reason:
        return (None, reason) if return_error else None
    gate_keys = [
        "reject_reason",
        "tactical_track_gate",
        "tactical_gate_failed",
        "tactical_effective_rr",
        "tactical_expected_value",
        "tactical_min_rr_for_track",
        "tactical_min_ev_for_track",
    ]
    plan = {
        "symbol": record["symbol"],
        "side": record["side"],
        "entry_ref": float(record["entry_price"]),
        "entry_price": float(record["entry_price"]),
        "stop_loss": float(record["stop_loss"]),
        "take_profit": list(record["take_profit"]),
        "leverage": int(record["leverage"]),
        "exit_profile": record.get("exit_profile", "tactical_v1"),
        "tactical_source": record.get("tactical_source", ""),
        "tactical_max_hold_minutes": record.get("tactical_max_hold_minutes"),
        "shadow_id": record.get("id"),
        "sidecar_source": "shadow_tactical_live",
        "gate_metadata": {k: record.get(k) for k in gate_keys if k in record},
    }
    return (plan, None) if return_error else plan
```

- [ ] **Step 4: Run core tests and commit**

Run:

```bash
pytest tests/test_shadow_tactical_live_core.py -q
```

Expected: all tests pass.

Commit:

```bash
git add utils/shadow_tactical_live.py tests/test_shadow_tactical_live_core.py
git commit -m "feat: add shadow tactical sidecar core"
```

## Task 2: Ownership Registry and Same-Symbol Guard

**Files:**
- Modify: `utils/shadow_tactical_live.py`
- Modify: `tests/test_shadow_tactical_live_core.py`

- [ ] **Step 1: Add failing ownership tests**

Append to `tests/test_shadow_tactical_live_core.py`:

```python
from utils.shadow_tactical_live import (
    ShadowTacticalOwnerRegistry,
    blocks_same_symbol_account_exposure,
)


def test_owner_registry_records_and_matches_active_symbol_side(tmp_path):
    path = tmp_path / "owners.json"
    reg = ShadowTacticalOwnerRegistry(str(path))
    reg.record_open(
        shadow_id="shadow-1",
        symbol="WLD-USDT-SWAP",
        side="long",
        amount_usdt=30.0,
        order_id="ord-1",
        entry_clord_id="stlWLD1",
        sl_algo_id="algo-1",
        sl_algo_clord_id="castliveWLD1",
    )
    assert reg.active_for("WLD-USDT-SWAP", "long")["shadow_id"] == "shadow-1"
    assert reg.matches_position("WLD-USDT-SWAP", "long")
    assert not reg.matches_position("WLD-USDT-SWAP", "short")


def test_same_symbol_guard_ignores_sidecar_owned_exposure(tmp_path):
    reg = ShadowTacticalOwnerRegistry(str(tmp_path / "owners.json"))
    reg.record_open("shadow-1", "WLD-USDT-SWAP", "long", 30.0, "ord-1", "stl1", "algo-1", "castlive1")
    exchange_positions = [{"symbol": "WLD/USDT:USDT", "side": "long", "contracts": 10}]
    blocked, reason = blocks_same_symbol_account_exposure(exchange_positions, "WLD-USDT-SWAP", "long", reg)
    assert blocked is False
    assert reason == ""


def test_same_symbol_guard_blocks_non_sidecar_exposure(tmp_path):
    reg = ShadowTacticalOwnerRegistry(str(tmp_path / "owners.json"))
    exchange_positions = [{"symbol": "WLD/USDT:USDT", "side": "long", "contracts": 10}]
    blocked, reason = blocks_same_symbol_account_exposure(exchange_positions, "WLD-USDT-SWAP", "long", reg)
    assert blocked is True
    assert reason == "same_symbol_account_exposure"
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
pytest tests/test_shadow_tactical_live_core.py -q
```

Expected: fails because registry and guard helpers are missing.

- [ ] **Step 3: Implement registry and guard**

Add to `utils/shadow_tactical_live.py`:

```python
def normalize_swap_symbol(symbol: str) -> str:
    if not symbol:
        return ""
    if "/" in symbol and ":" in symbol:
        return f"{symbol.split('/')[0]}-USDT-SWAP"
    return symbol


class ShadowTacticalOwnerRegistry:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {"schema_version": "shadow_tactical_owners.v1", "owners": {}}
        with open(self.path, "r") as fh:
            data = json.load(fh)
        data.setdefault("owners", {})
        return data

    def save(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        atomic_write_json(self.path, data)

    def record_open(self, shadow_id: str, symbol: str, side: str, amount_usdt: float,
                    order_id: str, entry_clord_id: str, sl_algo_id: str,
                    sl_algo_clord_id: str) -> dict:
        data = self.load()
        row = {
            "shadow_id": shadow_id,
            "symbol": normalize_swap_symbol(symbol),
            "side": side,
            "amount_usdt": float(amount_usdt),
            "order_id": order_id,
            "entry_clord_id": entry_clord_id,
            "sl_algo_id": sl_algo_id,
            "sl_algo_clord_id": sl_algo_clord_id,
            "status": "open",
            "opened_at": time.time(),
        }
        data["owners"][shadow_id] = row
        self.save(data)
        return row

    def active_for(self, symbol: str, side: str) -> Optional[dict]:
        wanted = normalize_swap_symbol(symbol)
        for row in self.load().get("owners", {}).values():
            if row.get("status") == "open" and row.get("symbol") == wanted and row.get("side") == side:
                return row
        return None

    def matches_position(self, symbol: str, side: str) -> bool:
        return self.active_for(symbol, side) is not None


def blocks_same_symbol_account_exposure(exchange_positions: list, symbol: str, side: str,
                                        owners: ShadowTacticalOwnerRegistry) -> tuple[bool, str]:
    wanted = normalize_swap_symbol(symbol)
    for pos in exchange_positions or []:
        contracts = float(pos.get("contracts") or pos.get("amount") or 0)
        if contracts <= 0:
            continue
        pos_symbol = normalize_swap_symbol(pos.get("symbol", ""))
        pos_side = "long" if pos.get("side") == "long" else "short"
        if pos_symbol == wanted and pos_side == side and not owners.matches_position(wanted, side):
            return True, "same_symbol_account_exposure"
    return False, ""
```

- [ ] **Step 4: Run tests and commit**

Run:

```bash
pytest tests/test_shadow_tactical_live_core.py -q
```

Expected: all tests pass.

Commit:

```bash
git add utils/shadow_tactical_live.py tests/test_shadow_tactical_live_core.py
git commit -m "feat: add shadow tactical ownership registry"
```

## Task 3: Sidecar Mechanical Executor Path

**Files:**
- Modify: `executor.py`
- Create: `tests/test_shadow_tactical_live_executor.py`

- [ ] **Step 1: Add failing executor tests**

Create `tests/test_shadow_tactical_live_executor.py`:

```python
import logging
from unittest.mock import MagicMock

from executor import ContractExecutor


def _executor():
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger("test_shadow_tactical_live_executor")
    ex.exchange_id = "okx"
    ex.testnet = False
    ex.leverage = 20
    ex.positions = {}
    ex.risk_manager = MagicMock()
    ex.risk_manager.max_trade_amount = 30.0
    ex.risk_manager.check_can_trade.return_value = (True, "ok")
    ex.get_balance = MagicMock(return_value=300.0)
    ex.balance_adapter = MagicMock()
    ex.balance_adapter.get_free.return_value = 100.0
    ex.caps = MagicMock()
    ex.caps.precheck_order.return_value = (True, "ok", {})
    ex.idempotency = None
    ex.ledger = None
    ex._okx_pos_mode = "net_mode"
    ex.is_symbol_halted = MagicMock(return_value=False)
    ex._halt_symbol = MagicMock()
    ex._check_slippage = MagicMock(return_value=True)
    ex._verify_attached_sl_after_fill = MagicMock(return_value="algo-1")
    ex._make_owner_tag_clord_id = MagicMock(return_value="castliveWLD1")
    ex._build_tp_sl_params = ContractExecutor._build_tp_sl_params.__get__(ex, ContractExecutor)
    ex._build_attach_algo_from_tp_sl = ContractExecutor._build_attach_algo_from_tp_sl.__get__(ex, ContractExecutor)
    ex._build_open_order_params = ContractExecutor._build_open_order_params.__get__(ex, ContractExecutor)
    ex.exchange = MagicMock()
    ex.exchange.fetch_ticker.return_value = {"last": 1.25}
    ex.exchange.set_leverage.return_value = None
    ex.exchange.market.return_value = {"contractSize": 1, "limits": {"amount": {"min": 1e-8}}}
    ex.exchange.amount_to_precision.side_effect = lambda symbol, amount: str(round(float(amount), 6))
    ex.exchange.create_order.return_value = {"id": "ord-1"}
    return ex


def _plan(**overrides):
    p = {
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "entry_ref": 1.25,
        "stop_loss": 1.20,
        "take_profit": [1.32, 1.38],
        "leverage": 20,
        "shadow_id": "shadow-1",
        "sidecar_source": "shadow_tactical_live",
    }
    p.update(overrides)
    return p


def test_open_sidecar_plan_places_order_without_drift_gate():
    ex = _executor()
    pos = ex.open_sidecar_plan(_plan(), size_usdt=30.0)
    assert pos["symbol"] == "WLD-USDT-SWAP"
    assert pos["amount_usdt"] == 30.0
    assert pos["sidecar_source"] == "shadow_tactical_live"
    assert pos["shadow_id"] == "shadow-1"
    ex.exchange.create_order.assert_called_once()
    assert not hasattr(ex, "_pending_drift_alerts") or ex._pending_drift_alerts == []


def test_open_sidecar_plan_rejects_invalid_long_stop_side():
    ex = _executor()
    assert ex.open_sidecar_plan(_plan(stop_loss=1.30), size_usdt=30.0) is None
    ex.exchange.create_order.assert_not_called()


def test_open_sidecar_plan_enforces_hard_size_cap():
    ex = _executor()
    pos = ex.open_sidecar_plan(_plan(), size_usdt=99.0)
    assert pos["amount_usdt"] == 30.0


def test_open_sidecar_plan_fails_closed_when_sl_unverified():
    ex = _executor()
    ex._verify_attached_sl_after_fill.return_value = None
    assert ex.open_sidecar_plan(_plan(), size_usdt=30.0) is None
    ex._halt_symbol.assert_called_once_with("WLD-USDT-SWAP", reason="sidecar_sl_unverified")
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
pytest tests/test_shadow_tactical_live_executor.py -q
```

Expected: fails because `open_sidecar_plan` is missing.

- [ ] **Step 3: Add explicit sidecar state path injection**

Modify `ContractExecutor.__init__` in `executor.py` so sidecar can pass explicit risk and ledger paths without relying on invalid `STATE_NAMESPACE` values:

```python
def __init__(self, exchange_id: str = 'binance',
             api_key: str = None,
             secret: str = None,
             password: str = None,
             testnet: bool = True,
             leverage: int = 1,
             positions_file: Optional[str] = None,
             risk_state_file: Optional[str] = None,
             ledger_events_file: Optional[str] = None,
             ledger_lifecycle_file: Optional[str] = None):
```

Use:

```python
self.positions_file = positions_file or sp.positions
...
state_file=risk_state_file or sp.risk_state,
...
self.ledger = LiveLedger(
    self.exchange,
    events_path=ledger_events_file or sp.live_order_events,
    lifecycle_path=ledger_lifecycle_file or sp.live_position_lifecycle,
    logger=self.logger,
)
```

- [ ] **Step 4: Add `open_sidecar_plan`**

Add a method near `open_position_with_plan` that copies only the mechanical parts needed for sidecar opens:

```python
def open_sidecar_plan(self, plan: dict, *, size_usdt: Optional[float] = None) -> Optional[Dict]:
    symbol = plan["symbol"]
    side = plan["side"]
    if self.is_symbol_halted(symbol):
        self.logger.warning(f"[Sidecar] {symbol} halted, reject open")
        return None
    balance = self.get_balance()
    can_trade, msg = self.risk_manager.check_can_trade(balance)
    if not can_trade:
        self.logger.warning(f"[Sidecar] risk reject: {msg}")
        return None
    leverage = int(plan.get("leverage") or self.leverage)
    size_usdt = min(float(size_usdt or self.risk_manager.max_trade_amount), self.risk_manager.max_trade_amount)
    free_balance = self.balance_adapter.get_free() if self.balance_adapter else self.exchange.fetch_balance()["USDT"]["free"]
    if free_balance < size_usdt * 1.1:
        self.logger.warning(f"[Sidecar] free balance too low: {free_balance:.2f} < {size_usdt * 1.1:.2f}")
        return None
    ticker = self.exchange.fetch_ticker(symbol)
    current_price = float(ticker["last"])
    stop_loss = float(plan["stop_loss"])
    take_profit = list(plan.get("take_profit") or [])
    if side == "long" and stop_loss >= current_price:
        self.logger.error(f"[Sidecar] invalid long SL {stop_loss} >= {current_price}")
        return None
    if side == "short" and stop_loss <= current_price:
        self.logger.error(f"[Sidecar] invalid short SL {stop_loss} <= {current_price}")
        return None
    if not take_profit:
        self.logger.error("[Sidecar] missing take_profit")
        return None
    self.exchange.set_leverage(leverage, symbol)
    if not self._check_slippage(symbol, size_usdt, current_price):
        return None
    if self.caps:
        ok, reason, _ = self.caps.precheck_order(
            symbol=symbol,
            side="buy" if side == "long" else "sell",
            size_usdt=size_usdt,
            price=current_price,
            leverage=leverage,
        )
        if not ok:
            self.logger.warning(f"[Sidecar] precheck reject: {reason}")
            return None
    market = self.exchange.market(symbol)
    contract_size = float(market.get("contractSize", 1) or 1)
    amount = float(self.exchange.amount_to_precision(symbol, size_usdt * leverage / (current_price * contract_size)))
    min_amount = market.get("limits", {}).get("amount", {}).get("min", 0)
    if min_amount and amount < min_amount:
        return None
    sl_clord_id = self._make_owner_tag_clord_id(symbol) if self.exchange_id == "okx" else None
    tp_sl_params = self._build_tp_sl_params(side, stop_loss, take_profit[0], sl_clord_id=sl_clord_id)
    attach_algo = self._build_attach_algo_from_tp_sl(tp_sl_params)
    params = self._build_open_order_params(side, attach_algo=attach_algo)
    order_side = "buy" if side == "long" else "sell"
    order = self.exchange.create_order(symbol=symbol, type="market", side=order_side, amount=amount, params=params)
    sl_algo_id = None
    if self.exchange_id == "okx" and sl_clord_id:
        sl_algo_id = self._verify_attached_sl_after_fill(symbol, sl_clord_id)
        if not sl_algo_id:
            self._halt_symbol(symbol, reason="sidecar_sl_unverified")
            return None
    position = {
        "symbol": symbol,
        "side": side,
        "entry_price": current_price,
        "amount": amount,
        "amount_usdt": size_usdt,
        "leverage": leverage,
        "stop_loss": stop_loss,
        "take_profit": take_profit[0],
        "take_profit_levels": take_profit,
        "sl_order_id": sl_algo_id,
        "exit_owner": "sidecar_tactical_exchange_sl",
        "sl_algo_id": sl_algo_id,
        "sl_algo_clord_id": sl_clord_id,
        "sl_sync_state": "active" if sl_algo_id else "pending",
        "protection_state": "protected" if sl_algo_id else "unprotected",
        "shadow_id": plan.get("shadow_id"),
        "sidecar_source": plan.get("sidecar_source", "shadow_tactical_live"),
        "open_time": time.time(),
    }
    self.positions[symbol] = position
    self._save_positions()
    if self.ledger and order:
        self.ledger.record_open(order["id"], symbol, side, size_usdt, leverage, current_price)
    return position
```

If review finds duplicated logic too large, extract shared amount/precheck helpers only when it reduces the diff.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
pytest tests/test_shadow_tactical_live_executor.py -q
```

Expected: all tests pass.

Commit:

```bash
git add executor.py tests/test_shadow_tactical_live_executor.py
git commit -m "feat: add sidecar mechanical open path"
```

## Task 4: Main Owner Isolation for Sync and OKX Algo Migration

**Files:**
- Modify: `executor.py`
- Create: `tests/test_shadow_tactical_owner_isolation.py`

- [ ] **Step 1: Add failing owner isolation tests**

Create `tests/test_shadow_tactical_owner_isolation.py`:

```python
from unittest.mock import MagicMock

from executor import ContractExecutor


def _executor():
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.exchange_id = "okx"
    ex.testnet = False
    ex.logger = MagicMock()
    ex.positions = {}
    ex._close_cooldown = {}
    ex._pending_resync = {}
    ex._removed_positions_data = []
    ex._last_removed_symbols = []
    ex._sl_check_failures = {}
    ex._last_protection_alert = {}
    ex._halted_symbols = {}
    ex._config = {"position_resync_confirm_ticks": 1}
    ex._save_positions = MagicMock()
    ex._migrate_all_symbols_algos = MagicMock()
    ex._maybe_auto_clear_protection_halt = MagicMock()
    ex._load_sidecar_owner_registry = MagicMock(return_value=None)
    return ex


def _raw_pos():
    return {
        "symbol": "WLD/USDT:USDT",
        "contracts": 10,
        "side": "long",
        "leverage": 20,
        "notional": 25.0,
        "entryPrice": 1.25,
        "unrealizedPnl": 0.0,
    }


def test_sync_positions_skips_sidecar_owned_backfill():
    ex = _executor()
    owners = MagicMock()
    owners.matches_position.return_value = True
    ex._load_sidecar_owner_registry.return_value = owners
    ex._fetch_positions_with_retry = MagicMock(return_value=[_raw_pos()])
    ex.sync_positions()
    assert "WLD-USDT-SWAP" not in ex.positions
    owners.matches_position.assert_called_once_with("WLD-USDT-SWAP", "long")


def test_sync_positions_still_backfills_non_sidecar_position():
    ex = _executor()
    owners = MagicMock()
    owners.matches_position.return_value = False
    ex._load_sidecar_owner_registry.return_value = owners
    ex._fetch_positions_with_retry = MagicMock(return_value=[_raw_pos()])
    ex.sync_positions()
    assert "WLD-USDT-SWAP" in ex.positions


def test_migration_does_not_cancel_foreign_owner_tag_without_local_position():
    ex = _executor()
    ex.positions = {}
    ex._list_pending_algos = MagicMock(return_value=[{
        "algoId": "algo-sidecar",
        "algoClOrdId": "castliveWLDabc",
        "sl_trigger": "1.20",
        "tp_trigger": "",
        "ordType": "conditional",
    }])
    ex._is_foreign_owner_clord_id = MagicMock(return_value=True)
    ex._cancel_algo_by_id = MagicMock()
    summary = ex._migrate_okx_algos_for_symbol("WLD-USDT-SWAP")
    assert summary["orphan_sl"] == 0
    ex._cancel_algo_by_id.assert_not_called()
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
pytest tests/test_shadow_tactical_owner_isolation.py -q
```

Expected: fails because owner registry loading and foreign-owner filtering are missing.

- [ ] **Step 3: Implement owner registry loading and sync skip**

In `executor.py`, add:

```python
def _load_sidecar_owner_registry(self):
    try:
        from utils.shadow_tactical_live import ShadowTacticalOwnerRegistry, SidecarPaths
        path = os.getenv("SHADOW_TACTICAL_OWNER_REGISTRY") or SidecarPaths().owners
        return ShadowTacticalOwnerRegistry(path)
    except Exception as e:
        self.logger.warning(f"[SidecarOwner] load failed: {e}")
        return None
```

In `sync_positions()`, before pending resync/backfill for a missing local position, add:

```python
owners = self._load_sidecar_owner_registry()
...
if owners and owners.matches_position(sym, ex_pos["side"]):
    self.logger.info(f"仓位同步: {sym} ignored as sidecar-owned")
    self._pending_resync.pop(sym, None)
    continue
```

Keep existing Main-owned local position updates unchanged.

- [ ] **Step 4: Implement foreign owner-tag detection in migration**

In `executor.py`, add:

```python
@classmethod
def _is_foreign_owner_clord_id(cls, clord_id: Optional[str]) -> bool:
    if not clord_id:
        return False
    return str(clord_id).startswith("ca") and not cls._is_owner_clord_id(clord_id)
```

In `_migrate_okx_algos_for_symbol()`, filter foreign owner-tag SL/OCO algos before cancellation or adoption:

```python
foreign_algos = []
owned_or_unknown_algos = []
for algo in sl_algos + oco_algos:
    if self._is_foreign_owner_clord_id(algo.get("algoClOrdId")):
        foreign_algos.append(algo)
    else:
        owned_or_unknown_algos.append(algo)
```

Use `owned_or_unknown_algos` for existing cancel/adopt logic. Do not cancel or adopt `foreign_algos`. Add `foreign_algos` count to summary for tests and audit.

- [ ] **Step 5: Run isolation tests and commit**

Run:

```bash
pytest tests/test_shadow_tactical_owner_isolation.py tests/test_phantom_position_resync.py -q
```

Expected: all tests pass.

Commit:

```bash
git add executor.py tests/test_shadow_tactical_owner_isolation.py
git commit -m "fix: isolate sidecar-owned account objects from main"
```

## Task 5: Sidecar Runner and Stop Command

**Files:**
- Create: `scripts/shadow_tactical_live_sidecar.py`
- Create: `tests/test_shadow_tactical_live_cli.py`
- Modify: `utils/shadow_tactical_live.py`

- [ ] **Step 1: Add failing runner tests**

Create `tests/test_shadow_tactical_live_cli.py`:

```python
import json
import subprocess
import sys


SCRIPT = "scripts/shadow_tactical_live_sidecar.py"


def test_status_prints_state_counts(tmp_path):
    state = tmp_path / "state.json"
    owners = tmp_path / "owners.json"
    state.write_text(json.dumps({"last_offset": 10, "seen_shadow_ids": {"s1": "opened", "s2": "rejected"}}))
    owners.write_text(json.dumps({"owners": {"s1": {"status": "open", "symbol": "WLD-USDT-SWAP", "side": "long"}}}))
    out = subprocess.check_output([
        sys.executable, SCRIPT, "status",
        "--state", str(state),
        "--owners", str(owners),
    ], text=True)
    assert "opened=1" in out
    assert "rejected=1" in out
    assert "active=1" in out


def test_run_dry_run_processes_new_tactical_event(tmp_path):
    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    rec = {
        "id": "s1",
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "entry_price": 1.25,
        "stop_loss": 1.20,
        "take_profit": [1.32],
        "leverage": 20,
        "track": "tactical",
        "exit_profile": "tactical_v1",
    }
    events.write_text(json.dumps({"event_type": "rejected_plan_created", "record": rec}) + "\n")
    subprocess.check_call([
        sys.executable, SCRIPT, "run",
        "--dry-run",
        "--once",
        "--events", str(events),
        "--state", str(state),
        "--audit", str(audit),
        "--duration-hours", "24",
    ])
    row = json.loads(audit.read_text().strip())
    assert row["event_type"] == "dry_run_plan"
    assert row["shadow_id"] == "s1"


def test_stop_closes_only_proven_sidecar_owned_exposure(tmp_path, monkeypatch):
    import importlib.util
    from unittest.mock import MagicMock

    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    owners = tmp_path / "owners.json"
    audit = tmp_path / "audit.jsonl"
    positions = tmp_path / "positions.json"
    owners.write_text(json.dumps({
        "owners": {
            "s1": {
                "shadow_id": "s1",
                "status": "open",
                "symbol": "WLD-USDT-SWAP",
                "side": "long",
                "sl_algo_id": "algo-1",
                "sl_algo_clord_id": "castliveWLD1",
            },
            "s2": {
                "shadow_id": "s2",
                "status": "open",
                "symbol": "ETH-USDT-SWAP",
                "side": "short"
            },
        }
    }))
    fake = MagicMock()
    fake.positions = {
        "WLD-USDT-SWAP": {"symbol": "WLD-USDT-SWAP", "side": "long", "shadow_id": "s1"}
    }
    fake._cancel_algo_by_id.return_value = True
    fake.close_position.return_value = {"id": "close-1"}
    monkeypatch.setattr(mod, "_build_executor", lambda paths: fake)

    code = mod.main(["stop", "--owners", str(owners), "--audit", str(audit), "--state", str(tmp_path / "state.json")])

    assert code == 0
    fake._cancel_algo_by_id.assert_called_once_with("WLD-USDT-SWAP", "algo-1")
    fake.close_position.assert_called_once_with("WLD-USDT-SWAP", action_kind="sidecar_stop")
    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [row["event_type"] for row in rows] == ["stop_closed", "stop_skipped_unproven"]
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
pytest tests/test_shadow_tactical_live_cli.py -q
```

Expected: fails because the script is missing.

- [ ] **Step 3: Implement CLI**

Create `scripts/shadow_tactical_live_sidecar.py` with:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time

from executor import ContractExecutor
from utils.shadow_tactical_live import (
    ShadowTacticalOwnerRegistry,
    SidecarPaths,
    SidecarStateStore,
    append_audit_event,
    is_tactical_shadow_event,
    iter_new_shadow_events,
    map_shadow_record_to_plan,
)


def _paths(args) -> SidecarPaths:
    return SidecarPaths(
        events=args.events or SidecarPaths.events,
        state=args.state or SidecarPaths.state,
        audit=args.audit or SidecarPaths.audit,
        owners=args.owners or SidecarPaths.owners,
    )


def cmd_status(args) -> int:
    paths = _paths(args)
    state = SidecarStateStore(paths.state).load()
    owners = ShadowTacticalOwnerRegistry(paths.owners).load().get("owners", {})
    seen = state.get("seen_shadow_ids", {})
    opened = sum(1 for v in seen.values() if v == "opened")
    rejected = sum(1 for v in seen.values() if v == "rejected")
    active = sum(1 for row in owners.values() if row.get("status") == "open")
    print(f"last_offset={state.get('last_offset', 0)} opened={opened} rejected={rejected} active={active}")
    return 0


def _build_executor(paths: SidecarPaths) -> ContractExecutor:
    import utils.halt_state as halt_state_mod
    halt_state_mod.HALT_STATE_FILE = paths.halt_state
    os.environ.setdefault("BOT_INSTANCE_ID", "stlive")
    return ContractExecutor(
        exchange_id="okx",
        api_key=os.getenv("OKX_API_KEY"),
        secret=os.getenv("OKX_SECRET"),
        password=os.getenv("OKX_PASSWORD"),
        testnet=os.getenv("USE_TESTNET", "false").lower() == "true",
        leverage=int(os.getenv("DEFAULT_LEVERAGE", "20")),
        positions_file=paths.positions,
        risk_state_file=paths.risk_state,
        ledger_events_file=paths.live_order_events,
        ledger_lifecycle_file=paths.live_position_lifecycle,
    )


def cmd_run(args) -> int:
    paths = _paths(args)
    store = SidecarStateStore(paths.state)
    state = store.load()
    now = time.time()
    state.setdefault("started_at", now)
    state["stop_at"] = state.get("stop_at") or now + float(args.duration_hours) * 3600
    state.setdefault("seen_shadow_ids", {})
    if args.from_end and os.path.exists(paths.events):
        state["last_offset"] = os.path.getsize(paths.events)
    executor = None if args.dry_run else _build_executor(paths)
    while time.time() < state["stop_at"]:
        for row in iter_new_shadow_events(paths.events, state.get("last_offset", 0)):
            state["last_offset"] = row.next_offset
            event = row.event
            if not is_tactical_shadow_event(event):
                continue
            record = event.get("record") or {}
            shadow_id = record.get("id")
            if shadow_id in state["seen_shadow_ids"]:
                append_audit_event(paths.audit, "duplicate_skipped", {"shadow_id": shadow_id})
                continue
            plan, reason = map_shadow_record_to_plan(record, return_error=True)
            if reason:
                state["seen_shadow_ids"][shadow_id] = "rejected"
                append_audit_event(paths.audit, "rejected", {"shadow_id": shadow_id, "reason": reason})
                continue
            if args.dry_run:
                state["seen_shadow_ids"][shadow_id] = "opened"
                append_audit_event(paths.audit, "dry_run_plan", {"shadow_id": shadow_id, "plan": plan})
                continue
            pos = executor.open_sidecar_plan(plan, size_usdt=float(args.size_usdt))
            state["seen_shadow_ids"][shadow_id] = "opened" if pos else "rejected"
            append_audit_event(paths.audit, "opened" if pos else "rejected", {"shadow_id": shadow_id, "symbol": record.get("symbol")})
        store.save(state)
        if args.once:
            break
        time.sleep(float(args.poll_seconds))
    append_audit_event(paths.audit, "window_expired", {"processed": len(state.get("seen_shadow_ids", {}))})
    store.save(state)
    return 0


def stop_sidecar_owned_exposure(paths: SidecarPaths, executor: ContractExecutor) -> dict:
    registry = ShadowTacticalOwnerRegistry(paths.owners)
    data = registry.load()
    owners = data.get("owners", {})
    closed = 0
    skipped = 0
    for shadow_id, row in owners.items():
        if row.get("status") != "open":
            continue
        symbol = row.get("symbol")
        sl_algo_id = row.get("sl_algo_id")
        local = getattr(executor, "positions", {}).get(symbol)
        proven = bool(symbol and local and local.get("shadow_id") == shadow_id)
        if not proven:
            skipped += 1
            append_audit_event(paths.audit, "stop_skipped_unproven", {"shadow_id": shadow_id, "symbol": symbol})
            continue
        if sl_algo_id:
            executor._cancel_algo_by_id(symbol, sl_algo_id)
        result = executor.close_position(symbol, action_kind="sidecar_stop")
        row["status"] = "closed" if result else "close_attempted"
        row["closed_at"] = time.time()
        append_audit_event(paths.audit, "stop_closed", {"shadow_id": shadow_id, "symbol": symbol, "result": bool(result)})
        closed += 1
    registry.save(data)
    return {"closed": closed, "skipped": skipped}


def cmd_stop(args) -> int:
    paths = _paths(args)
    executor = _build_executor(paths)
    result = stop_sidecar_owned_exposure(paths, executor)
    append_audit_event(paths.audit, "stop_requested", result)
    print(f"stop_requested closed={result['closed']} skipped={result['skipped']}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "status", "stop"):
        sp = sub.add_parser(name)
        sp.add_argument("--events")
        sp.add_argument("--state")
        sp.add_argument("--audit")
        sp.add_argument("--owners")
    run = sub.choices["run"]
    run.add_argument("--duration-hours", default="24")
    run.add_argument("--poll-seconds", default="2")
    run.add_argument("--size-usdt", default=os.getenv("MAX_TRADE_AMOUNT", "30"))
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--once", action="store_true")
    run.add_argument("--backfill-from-start", action="store_true")
    args = p.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status, "stop": cmd_stop}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
```

The stop path is intentionally narrow: it only closes a symbol when both the sidecar owner registry and the sidecar positions file prove the same shadow id. It skips records without proof and records `stop_skipped_unproven` so the operator can inspect them manually.

- [ ] **Step 4: Run CLI tests and commit**

Run:

```bash
pytest tests/test_shadow_tactical_live_cli.py -q
python scripts/shadow_tactical_live_sidecar.py status
```

Expected: pytest passes; status prints counts without exchange calls.

Commit:

```bash
git add scripts/shadow_tactical_live_sidecar.py tests/test_shadow_tactical_live_cli.py utils/shadow_tactical_live.py
git commit -m "feat: add shadow tactical live sidecar runner"
```

## Task 6: Final Verification, OpenSpec Task Sync, and Cloud Run Command

**Files:**
- Modify: `openspec/changes/promote-shadow-tactical-live-48h/tasks.md`
- Modify: `docs/runbook.md` or create `docs/superpowers/reports/2026-07-16-shadow-tactical-live-sidecar-build.md`

- [ ] **Step 1: Run focused test suite**

Run:

```bash
pytest \
  tests/test_shadow_tactical_live_core.py \
  tests/test_shadow_tactical_live_executor.py \
  tests/test_shadow_tactical_owner_isolation.py \
  tests/test_shadow_tactical_live_cli.py \
  tests/test_phantom_position_resync.py \
  test_okx_posmode_executor.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 2: Run OpenSpec validation**

Run:

```bash
openspec validate promote-shadow-tactical-live-48h --strict
```

Expected: valid.

- [ ] **Step 3: Update `tasks.md` checkboxes**

Check every completed item in `openspec/changes/promote-shadow-tactical-live-48h/tasks.md`. At minimum, tasks 1.1-1.4, 2.1-2.5, 3.1-3.6, 4.1-4.4, and 5.1-5.8 must be checked before build guard.

- [ ] **Step 4: Record cloud command**

Create `docs/superpowers/reports/2026-07-16-shadow-tactical-live-sidecar-build.md` with:

```markdown
# Shadow Tactical Live Sidecar Build Report

## Local Verification

- Focused pytest command: PASS
- OpenSpec strict validation: PASS

## Cloud Start Command

Run only after Main owner-ignore patch is deployed and Main is running the new code:

```bash
cd /opt/crypto-arbitrage
git pull --ff-only
export BOT_INSTANCE_ID=stlive
export SHADOW_TACTICAL_OWNER_REGISTRY=data/shadow_tactical_live_owners.json
nohup python3 scripts/shadow_tactical_live_sidecar.py run \
  --duration-hours 24 \
  --poll-seconds 2 \
  > logs/shadow_tactical_live_sidecar.log 2>&1 &
```

The runner defaults to no backfill on first start; use `--backfill-from-start`
only for an intentional replay test.

## Stop Command

```bash
cd /opt/crypto-arbitrage
python3 scripts/shadow_tactical_live_sidecar.py stop
python3 scripts/shadow_tactical_live_sidecar.py status
```
```

- [ ] **Step 5: Commit build completion docs**

Run:

```bash
git add openspec/changes/promote-shadow-tactical-live-48h/tasks.md docs/superpowers/reports/2026-07-16-shadow-tactical-live-sidecar-build.md
git commit -m "docs: record shadow tactical sidecar verification"
```

- [ ] **Step 6: Run build guard**

Run only after the user has selected isolation and execution mode and all tasks are complete:

```bash
COMET_ENV="${COMET_ENV:-$(find . "$HOME"/.*/skills "$HOME/.config" "$HOME/.gemini" -path '*/comet/scripts/comet-env.sh' -type f -print -quit 2>/dev/null)}"
. "$COMET_ENV"
bash "$COMET_GUARD" promote-shadow-tactical-live-48h build --apply
```

Expected: guard passes and moves the change to `phase=verify`.

## Self-Review

- Spec coverage: The plan covers shadow event filtering, plan mapping, strategy-gate bypass, mechanical fail-closed checks, separated sidecar state, same-account owner isolation, same-symbol guard, 24-hour stop semantics, stop/status CLI, and OpenSpec validation.
- Placeholder scan: The plan contains concrete file paths, commands, expected outcomes, and code sketches for every implementation task.
- Type consistency: The same names are used across tasks: `SidecarPaths`, `SidecarStateStore`, `ShadowTacticalOwnerRegistry`, `is_tactical_shadow_event`, `map_shadow_record_to_plan`, `iter_new_shadow_events`, `append_audit_event`, `blocks_same_symbol_account_exposure`, and `ContractExecutor.open_sidecar_plan`.
