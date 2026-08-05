from __future__ import annotations

import json
import math
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

import fcntl

from utils.atomic_io import atomic_write_json
from utils.symbol import to_internal, to_okx_inst


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

    @contextmanager
    def locked(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(f"{self.path}.lock", "a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {
                "started_at": time.time(),
                "stop_at": None,
                "last_offset": 0,
                "seen_shadow_ids": {},
                "admission_enabled": True,
                "admission_disabled_at": None,
                "admission_disabled_by": None,
            }
        with open(self.path, "r") as fh:
            data = json.load(fh)
        data.setdefault("started_at", time.time())
        data.setdefault("stop_at", None)
        data.setdefault("last_offset", 0)
        data.setdefault("seen_shadow_ids", {})
        data.setdefault("admission_enabled", True)
        data.setdefault("admission_disabled_at", None)
        data.setdefault("admission_disabled_by", None)
        return data

    def save(self, state: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        atomic_write_json(self.path, state)

    def disable_admission(
        self,
        *,
        source: str,
        now: Optional[float] = None,
    ) -> dict:
        with self.locked():
            state = self.load()
            disabled_at = time.time() if now is None else float(now)
            state["admission_enabled"] = False
            state["admission_disabled_at"] = disabled_at
            state["admission_disabled_by"] = str(source or "operator")
            self.save(state)
            return state


def append_audit_event(path: str, event_type: str, payload: dict) -> dict:
    event = {"ts": time.time(), "event_type": event_type}
    event.update(payload)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(event) + "\n")
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
                event = {
                    "event_type": "malformed_json",
                    "raw": raw.decode("utf-8", errors="replace"),
                }
            yield ShadowEventRow(
                event=event,
                start_offset=line_start,
                next_offset=next_offset,
            )


def is_tactical_shadow_event(event: dict) -> bool:
    if event.get("event_type") != "rejected_plan_created":
        return False
    record = event.get("record") or {}
    return (
        record.get("track") == "tactical"
        or record.get("exit_profile") == "tactical_v1"
    )


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
        if record.get(key) in (None, "", 0, [], {}):
            return reason
    if record.get("side") not in ("long", "short"):
        return "invalid_side"
    return None


def _normalize_take_profit_levels(value) -> list[float]:
    if value in (None, "", [], {}):
        return []
    raw_levels = value if isinstance(value, (list, tuple)) else [value]
    levels = []
    for level in raw_levels:
        try:
            normalized = float(level)
        except (TypeError, ValueError):
            return []
        if not math.isfinite(normalized) or normalized <= 0:
            return []
        levels.append(normalized)
    return levels


def map_shadow_record_to_plan(record: dict, *, return_error: bool = False):
    reason = _missing_reason(record)
    if reason:
        return (None, reason) if return_error else None
    take_profit = _normalize_take_profit_levels(record.get("take_profit"))
    if not take_profit:
        return (None, "missing_take_profit") if return_error else None

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
        "take_profit": take_profit,
        "leverage": int(record["leverage"]),
        "exit_profile": record.get("exit_profile", "tactical_v1"),
        "tactical_source": record.get("tactical_source", ""),
        "tactical_max_hold_minutes": record.get("tactical_max_hold_minutes"),
        "shadow_id": record.get("id"),
        "sidecar_source": "shadow_tactical_live",
        "gate_metadata": {key: record.get(key) for key in gate_keys if key in record},
    }
    return (plan, None) if return_error else plan


def canonical_sidecar_symbols(symbol: str) -> dict:
    internal_symbol = to_internal(symbol)
    exchange_symbol = to_okx_inst(internal_symbol)
    return {
        "internal_symbol": internal_symbol,
        "exchange_symbol": exchange_symbol,
    }


def normalize_swap_symbol(symbol: str) -> str:
    return canonical_sidecar_symbols(symbol).get("exchange_symbol", symbol)


def _canonical_owner_row(row: dict) -> dict:
    normalized = dict(row or {})
    seed = (
        normalized.get("exchange_symbol")
        or normalized.get("symbol")
        or normalized.get("internal_symbol")
        or ""
    )
    canonical = canonical_sidecar_symbols(seed)
    if normalized.get("internal_symbol"):
        canonical["internal_symbol"] = to_internal(normalized["internal_symbol"])
    if normalized.get("exchange_symbol"):
        canonical["exchange_symbol"] = normalize_swap_symbol(normalized["exchange_symbol"])
    elif normalized.get("symbol"):
        canonical["exchange_symbol"] = normalize_swap_symbol(normalized["symbol"])
    normalized["internal_symbol"] = canonical["internal_symbol"]
    normalized["exchange_symbol"] = canonical["exchange_symbol"]
    normalized["symbol"] = canonical["exchange_symbol"]
    return normalized


class ShadowTacticalOwnerRegistry:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {"schema_version": "shadow_tactical_owners.v1", "owners": {}}
        with open(self.path, "r") as fh:
            data = json.load(fh)
        data.setdefault("schema_version", "shadow_tactical_owners.v1")
        owners = data.setdefault("owners", {})
        if isinstance(owners, dict):
            for key, row in list(owners.items()):
                if isinstance(row, dict):
                    owners[key] = _canonical_owner_row(row)
        return data

    def save(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        atomic_write_json(self.path, data)

    def record_open(
        self,
        shadow_id: str,
        symbol: str,
        side: str,
        amount_usdt: float,
        order_id: str,
        entry_clord_id: str,
        sl_algo_id: str,
        sl_algo_clord_id: str,
        internal_symbol: Optional[str] = None,
        exchange_symbol: Optional[str] = None,
    ) -> dict:
        data = self.load()
        canonical = canonical_sidecar_symbols(exchange_symbol or symbol or internal_symbol or "")
        if internal_symbol:
            canonical["internal_symbol"] = to_internal(internal_symbol)
        if exchange_symbol:
            canonical["exchange_symbol"] = normalize_swap_symbol(exchange_symbol)
        row = {
            "shadow_id": shadow_id,
            "symbol": canonical["exchange_symbol"],
            "internal_symbol": canonical["internal_symbol"],
            "exchange_symbol": canonical["exchange_symbol"],
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
        wanted = canonical_sidecar_symbols(symbol)["internal_symbol"]
        for row in self.load().get("owners", {}).values():
            normalized = _canonical_owner_row(row) if isinstance(row, dict) else row
            if (
                normalized.get("status") == "open"
                and normalized.get("internal_symbol") == wanted
                and normalized.get("side") == side
            ):
                return normalized
        return None

    def has_active_owner(self, symbol: str, side: str) -> bool:
        return self.active_for(symbol, side) is not None

    def matches_position(self, symbol: str, side: str) -> bool:
        return self.active_for(symbol, side) is not None


def blocks_same_symbol_account_exposure(
    exchange_positions: list,
    symbol: str,
    side: str,
    owners: ShadowTacticalOwnerRegistry,
) -> tuple[bool, str]:
    wanted = canonical_sidecar_symbols(symbol)["internal_symbol"]
    if owners.has_active_owner(symbol, side):
        return True, "same_symbol_sidecar_active"
    for pos in exchange_positions or []:
        contracts = float(pos.get("contracts") or pos.get("amount") or 0)
        if contracts <= 0:
            continue
        pos_symbol = canonical_sidecar_symbols(pos.get("symbol", ""))["internal_symbol"]
        pos_side = "long" if pos.get("side") == "long" else "short"
        if pos_symbol != wanted:
            continue
        if owners.has_active_owner(pos.get("symbol", ""), pos_side):
            return True, "same_symbol_sidecar_active"
        return True, "same_symbol_account_exposure"
    return False, ""
