"""Atomic Tactical V2 operational read model and safe Telegram formatting."""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .governor import (
    LOSS_STREAK_COUNT,
    MAX_CONCURRENT,
    ROLLING_LOSS_LIMIT_USDT,
)
from .models import SCHEMA_VERSION, TACTICAL_V2_MARGIN_USDT


STATUS_REFRESH_SECONDS = 30
STATUS_STALE_SECONDS = 90
ENGINE_VERSION = "tactical_v2"

_PENDING_STATES = frozenset({
    "ready_for_quote",
    "submitting_entry",
    "pending_entry",
    "reconciling_entry",
    "canceling_entry",
    "partial_fill",
    "filled_unverified",
})
_ACTIVE_STATES = frozenset({"protected", "closing", "integrity_required"})


def _status_path(paths_or_path: Any) -> Path:
    raw = getattr(paths_or_path, "tactical_v2_status", paths_or_path)
    if not isinstance(raw, (str, os.PathLike)):
        raise TypeError("Tactical V2 status path is required")
    return Path(raw)


def write_status(paths_or_path: Any, snapshot: Mapping[str, Any]) -> None:
    """Atomically replace the read model; durable ledgers remain authoritative."""
    if not isinstance(snapshot, Mapping):
        raise TypeError("Tactical V2 status snapshot must be a mapping")
    path = _status_path(paths_or_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        dict(snapshot),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    temp_path = path.with_name(
        f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def read_status(paths_or_path: Any) -> Optional[dict]:
    """Read without side effects; malformed or missing data is unknown."""
    try:
        raw = json.loads(_status_path(paths_or_path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def build_status_snapshot(
    *,
    mode: str,
    requested_mode: Optional[str] = None,
    cutover_allowed: bool = True,
    cutover_reason: str = "cutover_not_required",
    namespace: str,
    intents: Sequence[Mapping[str, Any]],
    rolling_pnl: float,
    loss_streak: int,
    pause_until: float,
    integrity_halt: Optional[Mapping[str, Any]],
    episode_outcomes: Mapping[str, int],
    updated_at: float,
    margin_usdt: float = TACTICAL_V2_MARGIN_USDT,
    max_concurrent: int = MAX_CONCURRENT,
    rolling_loss_limit_usdt: float = ROLLING_LOSS_LIMIT_USDT,
    loss_streak_limit: int = LOSS_STREAK_COUNT,
    parity_mismatches: int = 0,
    parity_summary: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Project controller and governor truth into one non-authoritative snapshot."""
    rows = [dict(row) for row in intents if isinstance(row, Mapping)]
    active_rows = [row for row in rows if row.get("state") in _ACTIVE_STATES]
    pending_rows = [row for row in rows if row.get("state") in _PENDING_STATES]
    active_symbols = sorted({str(row.get("symbol")) for row in active_rows if row.get("symbol")})
    pending_symbols = sorted({str(row.get("symbol")) for row in pending_rows if row.get("symbol")})
    occupied = len(active_rows) + len(pending_rows)

    unverified_rows = [
        row for row in rows if row.get("state") in {"filled_unverified", "integrity_required"}
    ]
    protected_rows = [row for row in rows if row.get("state") == "protected"]
    pending_pnl = sum(1 for row in rows if row.get("state") == "exchange_closed_pending_pnl")
    integrity = dict(integrity_halt) if isinstance(integrity_halt, Mapping) else None
    protection_state = "degraded" if unverified_rows else "verified"
    if integrity and "protect" in str(integrity.get("reason") or "").lower():
        protection_state = "halted"
    reconciliation_state = "unknown" if unverified_rows else "verified"
    if pending_pnl and not unverified_rows:
        reconciliation_state = "pending_pnl"

    parity = dict(parity_summary) if isinstance(parity_summary, Mapping) else {}
    categories = parity.get("categories")
    if not isinstance(categories, Mapping):
        categories = {}
    parity_read_model = {
        "compared_intents": int(parity.get("compared_intents", 0) or 0),
        "mismatch_count": int(parity.get("mismatch_count", parity_mismatches) or 0),
        "categories": {
            str(key): int(value) for key, value in sorted(categories.items())
        },
        "shadow_filled": int(parity.get("shadow_filled", 0) or 0),
        "shadow_nonfilled": int(parity.get("shadow_nonfilled", 0) or 0),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "updated_at": float(updated_at),
        "namespace": str(namespace),
        "mode": str(mode),
        "requested_mode": str(requested_mode or mode),
        "cutover": {
            "allowed": bool(cutover_allowed),
            "reason": str(cutover_reason or "unknown"),
        },
        "margin_usdt": float(margin_usdt),
        "max_concurrent": int(max_concurrent),
        "slots": {
            "active": len(active_rows),
            "pending": len(pending_rows),
            "free": max(0, int(max_concurrent) - occupied),
        },
        "symbols": {"active": active_symbols, "pending": pending_symbols},
        "rolling_pnl_24h_usdt": float(rolling_pnl),
        "rolling_loss_limit_usdt": float(rolling_loss_limit_usdt),
        "loss_streak": int(loss_streak),
        "loss_streak_limit": int(loss_streak_limit),
        "timed_pause_until": float(pause_until),
        "integrity_halt": integrity,
        "episode_outcomes": {
            str(key): int(value) for key, value in sorted(episode_outcomes.items())
        },
        "protection": {
            "state": protection_state,
            "protected_count": len(protected_rows),
            "unverified_count": len(unverified_rows),
            "symbols": sorted({
                str(row.get("symbol")) for row in unverified_rows if row.get("symbol")
            }),
        },
        "reconciliation": {
            "state": reconciliation_state,
            "unknown_count": len(unverified_rows),
            "pending_pnl_count": pending_pnl,
        },
        "parity": parity_read_model,
    }


def format_tactical_v2_status(
    snapshot: Optional[Mapping[str, Any]],
    *,
    stale_seconds: int = STATUS_STALE_SECONDS,
    now: Optional[float] = None,
) -> str:
    """Render compact status without ever treating bad data as healthy."""
    if not isinstance(snapshot, Mapping):
        return "Tactical V2: STALE (snapshot missing or malformed)"
    mode = str(snapshot.get("mode") or "unknown").upper()
    requested_mode = str(snapshot.get("requested_mode") or "").lower()
    cutover = snapshot.get("cutover")
    if (
        snapshot.get("schema_version") != SCHEMA_VERSION
        or snapshot.get("engine_version") != ENGINE_VERSION
        or str(snapshot.get("mode") or "").lower() not in {"off", "shadow", "live"}
        or requested_mode not in {"off", "shadow", "live"}
        or not isinstance(cutover, Mapping)
        or not isinstance(cutover.get("allowed"), bool)
        or not str(cutover.get("reason") or "").strip()
        or not str(snapshot.get("namespace") or "").strip()
    ):
        return f"Tactical V2 {mode}: STALE (operational snapshot schema mismatch)"
    current = time.time() if now is None else now
    updated_at = _finite(snapshot.get("updated_at"))
    current_value = _finite(current)
    stale_value = _finite(stale_seconds)
    if (
        updated_at is None
        or current_value is None
        or stale_value is None
        or stale_value <= 0
        or updated_at > current_value + 5
        or current_value - updated_at > stale_value
    ):
        return f"Tactical V2 {mode}: STALE (operational snapshot not current)"

    margin = _finite(snapshot.get("margin_usdt"))
    maximum = _integer(snapshot.get("max_concurrent"))
    slots = snapshot.get("slots") if isinstance(snapshot.get("slots"), Mapping) else {}
    active = _integer(slots.get("active"))
    pending = _integer(slots.get("pending"))
    free = _integer(slots.get("free"))
    config_text = (
        f"{margin:g}U x {maximum}"
        if margin is not None and maximum is not None
        else "?U x ?"
    )
    slot_text = (
        f"{active} active / {pending} pending / {free} free"
        if None not in (active, pending, free)
        else "? active / ? pending / ? free"
    )

    symbols = snapshot.get("symbols") if isinstance(snapshot.get("symbols"), Mapping) else {}
    active_symbols = _compact_symbols(symbols.get("active"))
    pending_symbols = _compact_symbols(symbols.get("pending"))
    pnl = _finite(snapshot.get("rolling_pnl_24h_usdt"))
    limit = _finite(snapshot.get("rolling_loss_limit_usdt"))
    pnl_text = f"{pnl:+.2f}U / 24h" if pnl is not None else "?"
    limit_text = f"{limit:+.2f}U" if limit is not None else "?"
    streak = _integer(snapshot.get("loss_streak"))
    streak_limit = _integer(snapshot.get("loss_streak_limit"))
    streak_text = f"{streak}/{streak_limit}" if None not in (streak, streak_limit) else "?/?"

    integrity = snapshot.get("integrity_halt")
    pause_until = _finite(snapshot.get("timed_pause_until"))
    if isinstance(integrity, Mapping):
        reason = str(integrity.get("reason") or "unresolved integrity")
        circuit = f"integrity HALT ({reason}); existing positions managed"
    elif pnl is None or limit is None or pause_until is None:
        circuit = "circuit unknown (invalid risk data)"
    elif pnl <= limit:
        circuit = "new admission PAUSED (rolling loss); existing positions managed"
    elif pause_until > current_value:
        try:
            until = time.strftime("%H:%M", time.localtime(pause_until))
        except (OverflowError, OSError, ValueError):
            circuit = "circuit unknown (invalid pause deadline)"
        else:
            circuit = (
                f"new admission PAUSED (loss streak until {until}); "
                "existing positions managed"
            )
    else:
        circuit = "circuit clear"

    outcomes = snapshot.get("episode_outcomes")
    outcome_text = _format_outcomes(outcomes)
    if outcome_text is None:
        return f"Tactical V2 {mode}: STALE (operational snapshot malformed)"
    protection = snapshot.get("protection") if isinstance(snapshot.get("protection"), Mapping) else {}
    reconciliation = (
        snapshot.get("reconciliation")
        if isinstance(snapshot.get("reconciliation"), Mapping)
        else {}
    )
    parity = snapshot.get("parity") if isinstance(snapshot.get("parity"), Mapping) else {}
    protection_state = str(protection.get("state") or "unknown")
    reconciliation_state = str(reconciliation.get("state") or "unknown")
    mismatches = _integer(parity.get("mismatch_count"))
    parity_text = f"{mismatches} mismatch" if mismatches is not None else "? mismatch"
    categories = parity.get("categories")
    if isinstance(categories, Mapping) and categories:
        category_rows = []
        for key, value in sorted(categories.items()):
            count = _integer(value)
            category_rows.append(f"{key}:{count if count is not None else '?'}")
        parity_text += f" ({', '.join(category_rows)})"
    shadow_filled = _integer(parity.get("shadow_filled"))
    shadow_nonfilled = _integer(parity.get("shadow_nonfilled"))
    if shadow_filled is not None and shadow_nonfilled is not None:
        parity_text += (
            f"; shadow {shadow_filled} filled / {shadow_nonfilled} nonfilled"
        )
    age = max(0, int(current_value - updated_at))
    requested_text = requested_mode.upper()
    mode_detail = f" | requested {requested_text}" if requested_text != mode else ""
    cutover_reason = str(cutover.get("reason"))
    if requested_mode == "live":
        cutover_text = (
            f"READY ({cutover_reason})"
            if cutover.get("allowed") is True
            else f"BLOCKED ({cutover_reason})"
        )
    else:
        cutover_text = f"not required ({cutover_reason})"

    return "\n".join([
        f"Tactical V2 {mode}{mode_detail} | {config_text}",
        f"Cutover: {cutover_text}",
        f"Slots: {slot_text}",
        f"Symbols: active {active_symbols} | pending {pending_symbols}",
        f"PnL: {pnl_text} (limit {limit_text}) | streak {streak_text}",
        f"Circuit: {circuit}",
        f"Episodes: {outcome_text}",
        (
            f"Protection {protection_state} | reconciliation {reconciliation_state} | "
            f"parity {parity_text}"
        ),
        f"Updated: {age}s ago",
    ])


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any) -> Optional[int]:
    parsed = _finite(value)
    if parsed is None or not parsed.is_integer() or parsed < 0:
        return None
    return int(parsed)


def _compact_symbols(value: Any, limit: int = 5) -> str:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return "?"
    symbols = [str(symbol).split("-")[0] for symbol in value if symbol]
    suffix = f" +{len(symbols) - limit}" if len(symbols) > limit else ""
    return (",".join(symbols[:limit]) or "-") + suffix


def _format_outcomes(value: Any, limit: int = 4) -> Optional[str]:
    if not isinstance(value, Mapping):
        return None
    rows = []
    normalized = []
    for key, count in value.items():
        parsed = _integer(count)
        if parsed is None:
            return None
        normalized.append((str(key), parsed))
    for key, count in sorted(normalized, key=lambda item: (-item[1], item[0])):
        rows.append(f"{key}={count}")
    suffix = f" +{len(rows) - limit}" if len(rows) > limit else ""
    return (", ".join(rows[:limit]) or "none") + suffix


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
