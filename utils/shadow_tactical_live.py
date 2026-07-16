from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Iterator, Optional

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
        data.setdefault("started_at", time.time())
        data.setdefault("stop_at", None)
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
        "gate_metadata": {key: record.get(key) for key in gate_keys if key in record},
    }
    return (plan, None) if return_error else plan
