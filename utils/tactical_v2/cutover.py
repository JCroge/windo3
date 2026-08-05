"""Sidecar drain evidence and fail-closed Tactical V2 live cutover gate."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from utils.atomic_io import atomic_write_json


RETIREMENT_SCHEMA_VERSION = "sidecar_retirement.v1"


@dataclass(frozen=True)
class CutoverDecision:
    allowed: bool
    reason: str
    report: Optional[dict] = None


def build_drain_report(
    *,
    namespace: str,
    sidecar_bot_owner_id: str,
    admission_state: Mapping[str, Any],
    pending_entries: Sequence[Mapping[str, Any]],
    owners: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
    local_positions: Sequence[Mapping[str, Any]],
    exchange_positions: Sequence[Mapping[str, Any]],
    protection_orders: Sequence[Mapping[str, Any]],
    ownership_proof: Mapping[str, Any],
    exchange_state: str,
    pending_pnl: Sequence[Mapping[str, Any]],
    final_pnl: Sequence[Mapping[str, Any]],
    documented_exceptions: Sequence[Mapping[str, Any]],
    generated_at: Optional[float] = None,
) -> dict:
    """Build complete evidence while treating every unknown as unresolved."""
    owner_rows = list(owners.values()) if isinstance(owners, Mapping) else list(owners)
    pending_entry_rows = _mapping_rows(pending_entries)
    owner_rows = _mapping_rows(owner_rows)
    local_rows = _mapping_rows(local_positions)
    exchange_rows = _mapping_rows(exchange_positions)
    protection_rows = _mapping_rows(protection_orders)
    pending_pnl_rows = _mapping_rows(pending_pnl)
    final_pnl_rows = _mapping_rows(final_pnl)
    exception_rows = _mapping_rows(documented_exceptions)
    proof = dict(ownership_proof or {})
    admission = dict(admission_state or {})

    accepted_pending_pnl = {
        str(row.get("object_id"))
        for row in exception_rows
        if row.get("type") == "pending_pnl"
        and row.get("accepted") is True
        and str(row.get("object_id") or "").strip()
        and str(row.get("reason") or "").strip()
    }
    unresolved_pending_pnl = [
        row
        for row in pending_pnl_rows
        if _pnl_identity(row) not in accepted_pending_pnl
    ]
    open_owners = [
        row
        for row in owner_rows
        if str(row.get("status") or "unknown").lower()
        not in {"closed", "archived", "retired"}
    ]
    relevant_exchange = [
        row for row in exchange_rows if row.get("sidecar_relevant", True) is not False
    ]
    unresolved_protection = [
        row
        for row in protection_rows
        if row.get("sidecar_relevant", True) is not False
        and str(row.get("state") or "present").lower()
        not in {"absent", "canceled", "cancelled", "closed"}
    ]
    missing_proof = [
        key
        for key in ("ownership", "orders", "positions", "protection")
        if proof.get(key) is not True
    ]
    exchange_state_value = str(exchange_state or "unknown").lower()
    unresolved = {
        "admission_enabled": 1 if admission.get("admission_enabled", True) is not False else 0,
        "pending_entries": len(pending_entry_rows),
        "open_owners": len(open_owners),
        "local_positions": len(local_rows),
        "exchange_positions": len(relevant_exchange),
        "protection_ambiguities": len(unresolved_protection),
        "pending_pnl": len(unresolved_pending_pnl),
        "ownership_unknown": len(missing_proof),
        "exchange_state": 0 if exchange_state_value == "flat" else 1,
    }
    complete = all(value == 0 for value in unresolved.values())
    report = {
        "schema_version": RETIREMENT_SCHEMA_VERSION,
        "namespace": str(namespace or "").strip().lower(),
        "sidecar_bot_owner_id": str(sidecar_bot_owner_id or "").strip(),
        "generated_at": float(time.time() if generated_at is None else generated_at),
        "admission_state": admission,
        "pending_entries": pending_entry_rows,
        "owners": owner_rows,
        "local_positions": local_rows,
        "exchange_positions": exchange_rows,
        "protection_orders": protection_rows,
        "ownership_proof": proof,
        "exchange_state": exchange_state_value,
        "pending_pnl": pending_pnl_rows,
        "final_pnl": final_pnl_rows,
        "documented_exceptions": exception_rows,
        "unresolved": unresolved,
        "complete": complete,
        "retired": False,
        "archived_at": None,
    }
    report["content_hash"] = _content_hash(report)
    return report


drain_report = build_drain_report


def write_drain_report(report: Mapping[str, Any], paths_or_path: Any) -> dict:
    """Persist inspectable evidence, including incomplete drain attempts."""
    normalized = copy.deepcopy(dict(report))
    normalized["content_hash"] = _content_hash(normalized)
    atomic_write_json(str(_retirement_path(paths_or_path)), normalized)
    return normalized


def archive_drain_report(
    report: Mapping[str, Any],
    paths_or_path: Any,
    *,
    archived_at: Optional[float] = None,
) -> dict:
    """Archive only a hash-valid complete drain barrier."""
    normalized = copy.deepcopy(dict(report))
    if normalized.get("schema_version") != RETIREMENT_SCHEMA_VERSION:
        raise ValueError("unsupported sidecar retirement schema")
    if not _hash_matches(normalized):
        raise ValueError("sidecar drain report hash mismatch")
    if normalized.get("complete") is not True or any(
        int(value or 0) != 0 for value in (normalized.get("unresolved") or {}).values()
    ):
        raise ValueError("sidecar drain is unresolved")
    normalized["retired"] = True
    normalized["archived_at"] = float(
        time.time() if archived_at is None else archived_at
    )
    normalized["content_hash"] = _content_hash(normalized)
    atomic_write_json(str(_retirement_path(paths_or_path)), normalized)
    return normalized


def validate_live_cutover(
    paths_or_path: Any,
    *,
    namespace: str,
    sidecar_bot_owner_id: str,
) -> CutoverDecision:
    path = _retirement_path(paths_or_path)
    if not path.exists():
        return CutoverDecision(False, "sidecar_retirement_missing")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return CutoverDecision(False, "sidecar_retirement_malformed")
    if not isinstance(report, dict) or report.get("schema_version") != RETIREMENT_SCHEMA_VERSION:
        return CutoverDecision(False, "sidecar_retirement_malformed")
    if not _hash_matches(report):
        return CutoverDecision(False, "sidecar_retirement_hash_mismatch", report)
    if str(report.get("namespace") or "").lower() != str(namespace or "").lower():
        return CutoverDecision(False, "sidecar_retirement_namespace_mismatch", report)
    if str(report.get("sidecar_bot_owner_id") or "") != str(sidecar_bot_owner_id or ""):
        return CutoverDecision(False, "sidecar_retirement_owner_mismatch", report)
    unresolved = report.get("unresolved")
    if (
        report.get("complete") is not True
        or report.get("retired") is not True
        or not isinstance(unresolved, Mapping)
        or any(_nonzero(value) for value in unresolved.values())
        or (report.get("admission_state") or {}).get("admission_enabled") is not False
        or str(report.get("exchange_state") or "").lower() != "flat"
    ):
        return CutoverDecision(False, "sidecar_drain_unresolved", report)
    proof = report.get("ownership_proof") or {}
    if not all(proof.get(key) is True for key in ("ownership", "orders", "positions", "protection")):
        return CutoverDecision(False, "sidecar_drain_unresolved", report)
    return CutoverDecision(True, "sidecar_retirement_verified", report)


def _retirement_path(paths_or_path: Any) -> Path:
    raw = getattr(paths_or_path, "sidecar_retirement", paths_or_path)
    if not isinstance(raw, (str, os.PathLike)):
        raise TypeError("sidecar retirement path is required")
    return Path(raw)


def _mapping_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    return [copy.deepcopy(dict(row)) for row in rows if isinstance(row, Mapping)]


def _pnl_identity(row: Mapping[str, Any]) -> str:
    return str(
        row.get("resolution_id")
        or row.get("event_id")
        or row.get("position_id")
        or row.get("entry_request_id")
        or ""
    )


def _content_hash(report: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in report.items() if key != "content_hash"}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hash_matches(report: Mapping[str, Any]) -> bool:
    observed = str(report.get("content_hash") or "")
    if not observed:
        return False
    try:
        expected = _content_hash(report)
    except (TypeError, ValueError, OverflowError):
        return False
    return hmac.compare_digest(observed, expected)


def _nonzero(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    try:
        return int(value) != 0
    except (TypeError, ValueError, OverflowError):
        return True
