#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from executor import ContractExecutor
from utils.shadow_tactical_live import (
    ShadowTacticalOwnerRegistry,
    SidecarPaths,
    SidecarStateStore,
    append_audit_event,
    blocks_same_symbol_account_exposure,
    canonical_sidecar_symbols,
    is_tactical_shadow_event,
    iter_new_shadow_events,
    map_shadow_record_to_plan,
)

SIDECAR_FLAT_CLEAR_HALT_REASONS = {
    "migrate_missing_sl",
    "sidecar_sl_unverified",
    "sl_algo_unresolved",
    "sl_cancel_failed",
    "sl_replace_failed",
    "sl_restore_failed",
}


def _paths(args) -> SidecarPaths:
    defaults = SidecarPaths()
    return SidecarPaths(
        events=args.events or defaults.events,
        state=args.state or defaults.state,
        audit=args.audit or defaults.audit,
        owners=args.owners or defaults.owners,
        positions=defaults.positions,
        risk_state=defaults.risk_state,
        halt_state=defaults.halt_state,
        live_order_events=defaults.live_order_events,
        live_position_lifecycle=defaults.live_position_lifecycle,
    )


def _active_owner_count(registry: ShadowTacticalOwnerRegistry) -> int:
    return sum(
        1
        for row in registry.load().get("owners", {}).values()
        if row.get("status") == "open"
    )


def cmd_status(args) -> int:
    paths = _paths(args)
    state = SidecarStateStore(paths.state).load()
    owners = ShadowTacticalOwnerRegistry(paths.owners).load().get("owners", {})
    seen = state.get("seen_shadow_ids", {})
    opened = sum(1 for status in seen.values() if status == "opened")
    rejected = sum(1 for status in seen.values() if status == "rejected")
    active = sum(1 for row in owners.values() if row.get("status") == "open")
    print(
        f"last_offset={state.get('last_offset', 0)} "
        f"opened={opened} rejected={rejected} active={active}"
    )
    return 0


def _build_executor(paths: SidecarPaths) -> ContractExecutor:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    import utils.halt_state as halt_state_mod

    halt_state_mod.HALT_STATE_FILE = paths.halt_state
    os.environ.setdefault("BOT_INSTANCE_ID", "stlive")
    return ContractExecutor(
        exchange_id="okx",
        api_key=os.getenv("OKX_API_KEY"),
        secret=os.getenv("OKX_SECRET"),
        password=os.getenv("OKX_PASSWORD") or os.getenv("OKX_PASSPHRASE"),
        testnet=os.getenv("USE_TESTNET", "false").lower() == "true",
        leverage=int(os.getenv("DEFAULT_LEVERAGE", "20")),
        positions_file=paths.positions,
        risk_state_file=paths.risk_state,
        ledger_events_file=paths.live_order_events,
        ledger_lifecycle_file=paths.live_position_lifecycle,
    )


def _fetch_exchange_positions(executor: ContractExecutor) -> list:
    try:
        return executor._fetch_positions_with_retry()
    except Exception as exc:
        executor.logger.warning(f"[Sidecar] fetch positions failed for guard: {exc}")
        return []


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _canonical_exchange_symbol(symbol: str) -> str:
    return canonical_sidecar_symbols(symbol or "").get("exchange_symbol") or symbol


def _exchange_position_matches_symbol(position: dict, symbol: str) -> bool:
    wanted = _canonical_exchange_symbol(symbol)
    candidates = [
        position.get("symbol"),
        position.get("inst_id"),
        position.get("instId"),
        (position.get("info") or {}).get("instId"),
    ]
    for candidate in candidates:
        if candidate and _canonical_exchange_symbol(candidate) == wanted:
            return True
    return False


def _position_contracts(position: dict) -> float:
    return abs(
        _safe_float(
            position.get("contracts")
            or position.get("available_contracts")
            or position.get("amount")
            or (position.get("info") or {}).get("pos")
        )
    )


def _sidecar_exchange_position_state(
    executor: ContractExecutor,
    symbol: str,
) -> tuple[str, dict | None]:
    """Return present/flat/unknown/unsupported for the exchange-side position."""
    if getattr(executor, "exchange_id", None) != "okx":
        return "unsupported", None

    fetch_positions = getattr(executor, "_fetch_positions_with_retry", None)
    normalize = getattr(executor, "_normalize_okx_position", None)
    if not callable(fetch_positions) or not callable(normalize):
        return "unknown", None

    try:
        exchange_positions = fetch_positions()
    except Exception as exc:
        logger = getattr(executor, "logger", None)
        if logger:
            logger.warning(f"[Sidecar] exchange position check failed: {exc}")
        return "unknown", None

    for raw in exchange_positions or []:
        try:
            normalized = normalize(raw)
        except Exception:
            normalized = raw
        if not normalized or not _exchange_position_matches_symbol(normalized, symbol):
            continue
        if _position_contracts(normalized) > 0:
            return "present", normalized
    return "flat", None


def _remove_local_sidecar_position(executor: ContractExecutor, symbol: str) -> bool:
    marker = getattr(executor, "_mark_external_closed", None)
    if callable(marker):
        try:
            marker(symbol, reason="sidecar_monitor_exchange_flat")
            return True
        except Exception as exc:
            logger = getattr(executor, "logger", None)
            if logger:
                logger.warning(f"[Sidecar] mark external flat failed: {exc}")

    positions = getattr(executor, "positions", None)
    if isinstance(positions, dict) and symbol in positions:
        positions.pop(symbol, None)
    for attr in ("_sl_check_failures", "_last_protection_alert"):
        store = getattr(executor, attr, None)
        if isinstance(store, dict):
            store.pop(symbol, None)
    saver = getattr(executor, "_save_positions", None)
    if callable(saver):
        saver()
    return True


def _record_exchange_flat_close(
    executor: ContractExecutor,
    symbol: str,
    local: dict,
    row: dict,
    *,
    closed_at: float,
) -> dict:
    ledger = getattr(executor, "ledger", None)
    recorder = getattr(ledger, "record_pending_external_close", None)
    if not callable(recorder):
        return {"ledger_close_recorded": False}

    try:
        event = recorder(
            symbol=symbol,
            side=local.get("side") or row.get("side"),
            entry_price=_safe_float(local.get("entry_price") or row.get("entry_price")),
            amount_usdt=_safe_float(local.get("amount_usdt") or row.get("amount_usdt")),
            leverage=int(_safe_float(local.get("leverage") or row.get("leverage"), 1.0) or 1),
            estimated_pnl=None,
            position_id=local.get("position_id"),
            entry_request_id=local.get("shadow_id") or row.get("shadow_id"),
            opened_at=local.get("open_time") or row.get("opened_at"),
            closed_at=closed_at,
            sl_algo_id=local.get("sl_algo_id") or row.get("sl_algo_id"),
            sl_algo_clord_id=local.get("sl_algo_clord_id") or row.get("sl_algo_clord_id"),
            entry_attribution=local.get("gate_metadata")
            or local.get("entry_attribution")
            or {},
        )
    except Exception as exc:
        logger = getattr(executor, "logger", None)
        if logger:
            logger.warning(f"[Sidecar] pending external close ledger failed: {exc}")
        return {
            "ledger_close_recorded": False,
            "ledger_close_error": str(exc),
        }

    return {
        "ledger_close_recorded": True,
        "ledger_close_event_id": (event or {}).get("event_id", ""),
        "ledger_close_pnl_status": (event or {}).get("pnl_status", ""),
    }


def _halt_reason_from_global_state(reason: str, symbol: str) -> str:
    prefix = "okx_"
    suffix = f":{symbol}"
    if reason.startswith(prefix) and reason.endswith(suffix):
        return reason[len(prefix) : -len(suffix)]
    return ""


def _flat_halt_reason_candidates(
    executor: ContractExecutor,
    symbol: str,
    halt_state=None,
) -> list[str]:
    reasons = []
    halted = getattr(executor, "_halted_symbols", {}) or {}
    if isinstance(halted, dict):
        local_reason = (halted.get(symbol) or {}).get("reason")
        if local_reason:
            reasons.append(local_reason)
    global_reason = getattr(halt_state, "reason", "") if halt_state else ""
    parsed_global_reason = _halt_reason_from_global_state(global_reason, symbol)
    if parsed_global_reason:
        reasons.append(parsed_global_reason)

    result = []
    seen = set()
    for reason in reasons:
        if reason in SIDECAR_FLAT_CLEAR_HALT_REASONS and reason not in seen:
            result.append(reason)
            seen.add(reason)
    return result


def _clear_halt_after_exchange_flat(
    paths: SidecarPaths,
    executor: ContractExecutor,
    symbol: str,
) -> dict:
    cleared_symbol = False
    cleared_global = False
    global_reason = ""

    halt_state = None
    try:
        import utils.halt_state as halt_state_mod

        previous_path = halt_state_mod.HALT_STATE_FILE
        previous_instance = halt_state_mod._instance
        switched = previous_path != paths.halt_state
        if switched:
            halt_state_mod.HALT_STATE_FILE = paths.halt_state
            halt_state_mod._instance = None
        try:
            halt_state = halt_state_mod.get_halt_state()
            global_reason = getattr(halt_state, "reason", "") or ""
            for reason in _flat_halt_reason_candidates(executor, symbol, halt_state):
                expected = f"okx_{reason}:{symbol}"
                if halt_state.auto_clear_if_reason(
                    expected,
                    cleared_by="sidecar_monitor_exchange_flat",
                ):
                    cleared_global = True
                    break
        finally:
            if switched:
                halt_state_mod.HALT_STATE_FILE = previous_path
                halt_state_mod._instance = previous_instance
    except Exception as exc:
        logger = getattr(executor, "logger", None)
        if logger:
            logger.warning(f"[Sidecar] global halt clear after flat failed: {exc}")

    local_reasons = _flat_halt_reason_candidates(executor, symbol, halt_state)
    if local_reasons:
        clear_symbol_halt = getattr(executor, "clear_symbol_halt", None)
        if callable(clear_symbol_halt):
            try:
                cleared_symbol = bool(
                    clear_symbol_halt(
                        symbol,
                        source="sidecar_monitor_exchange_flat",
                    )
                )
            except Exception as exc:
                logger = getattr(executor, "logger", None)
                if logger:
                    logger.warning(f"[Sidecar] symbol halt clear after flat failed: {exc}")
        else:
            halted = getattr(executor, "_halted_symbols", None)
            if isinstance(halted, dict) and symbol in halted:
                halted.pop(symbol, None)
                cleared_symbol = True

    return {
        "cleared_symbol_halt": cleared_symbol,
        "cleared_global_halt": cleared_global,
        "global_halt_reason": global_reason,
    }


def _record_owner_if_open(
    registry: ShadowTacticalOwnerRegistry,
    shadow_id: str,
    position: dict,
) -> None:
    registry.record_open(
        shadow_id=shadow_id,
        symbol=position.get("symbol"),
        side=position.get("side"),
        amount_usdt=float(position.get("amount_usdt") or 0),
        order_id=position.get("entry_order_id") or "",
        entry_clord_id=position.get("entry_clord_id") or "",
        sl_algo_id=position.get("sl_algo_id") or "",
        sl_algo_clord_id=position.get("sl_algo_clord_id") or "",
        internal_symbol=position.get("internal_symbol") or "",
        exchange_symbol=position.get("exchange_symbol") or position.get("symbol") or "",
    )


def _drain_sidecar_entry_drift_alerts(paths: SidecarPaths, executor, shadow_id: str) -> int:
    alerts = getattr(executor, "_pending_drift_alerts", [])
    if not isinstance(alerts, list):
        return 0

    kept = []
    persisted = 0
    for alert in alerts:
        alert_type = (alert or {}).get("type") if isinstance(alert, dict) else None
        if not alert_type or not alert_type.startswith("sidecar_entry_drift_"):
            kept.append(alert)
            continue
        alert_shadow_id = (alert or {}).get("shadow_id")
        if alert_shadow_id and alert_shadow_id != shadow_id:
            kept.append(alert)
            continue
        payload = {key: value for key, value in alert.items() if key != "type"}
        payload["shadow_id"] = alert_shadow_id or shadow_id
        append_audit_event(paths.audit, alert_type, payload)
        persisted += 1

    executor._pending_drift_alerts = kept
    return persisted


def _sidecar_position_for_owner(executor: ContractExecutor, row: dict):
    positions = getattr(executor, "positions", {}) or {}
    exchange_symbol = row.get("exchange_symbol") or row.get("symbol")
    internal_symbol = row.get("internal_symbol")
    shadow_id = row.get("shadow_id")
    local = positions.get(exchange_symbol)
    if not local:
        for key, candidate in positions.items():
            if (
                candidate.get("shadow_id") == shadow_id
                and (
                    candidate.get("internal_symbol") == internal_symbol
                    or candidate.get("exchange_symbol") == exchange_symbol
                    or candidate.get("symbol") == exchange_symbol
                    or key == exchange_symbol
                )
            ):
                exchange_symbol = key
                local = candidate
                break
    if not local:
        return exchange_symbol, None
    source = local.get("sidecar_source")
    source_ok = source in (None, "", "shadow_tactical_live")
    proven = (
        local.get("shadow_id") == shadow_id
        and local.get("side") == row.get("side")
        and source_ok
    )
    return exchange_symbol, local if proven else None


def _owner_row_as_close_metadata(row: dict, symbol: str) -> dict:
    """Fallback metadata for an owner row whose local position is already gone."""
    return {
        "symbol": symbol,
        "side": row.get("side"),
        "entry_price": row.get("entry_price"),
        "amount_usdt": row.get("amount_usdt"),
        "leverage": row.get("leverage"),
        "position_id": row.get("position_id"),
        "shadow_id": row.get("shadow_id"),
        "open_time": row.get("opened_at"),
        "sl_algo_id": row.get("sl_algo_id"),
        "sl_algo_clord_id": row.get("sl_algo_clord_id"),
        "gate_metadata": row.get("entry_attribution") or {},
    }


def _owner_group_key(row: dict) -> tuple[str, str]:
    return (
        row.get("exchange_symbol") or row.get("symbol") or "",
        row.get("side") or "",
    )


def _open_owner_group_counts(owners: dict) -> dict[tuple[str, str], int]:
    counts = {}
    for row in owners.values():
        if row.get("status") != "open":
            continue
        key = _owner_group_key(row)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _pending_protection_state(executor: ContractExecutor, symbol: str) -> str:
    lister = getattr(executor, "_list_pending_algos", None)
    if not callable(lister):
        return "unknown"
    try:
        algos = lister(symbol)
    except Exception:
        return "unknown"
    for algo in algos or []:
        has_sl = algo.get("sl_trigger") not in (None, "", "0")
        has_tp = algo.get("tp_trigger") not in (None, "", "0")
        if has_sl or has_tp:
            return "present"
    return "absent"


def _reduce_action_for_trigger(trigger: str):
    if trigger in ("tactical_tp1", "partial_tp_1"):
        return 0.5, 1, "sidecar_tactical_tp1"
    if trigger in ("partial_tp_2", "tactical_tp2"):
        return 0.25, 2, "sidecar_tactical_tp2"
    return None


def monitor_sidecar_owned_exposure(paths: SidecarPaths, executor: ContractExecutor) -> dict:
    registry = ShadowTacticalOwnerRegistry(paths.owners)
    data = registry.load()
    summary = {
        "scanned": 0,
        "reduced": 0,
        "closed": 0,
        "exchange_flat": 0,
        "skipped": 0,
        "failed": 0,
        "ghost_exposure": 0,
        "ambiguous_stacks": 0,
    }
    owner_group_counts = _open_owner_group_counts(data.get("owners", {}))
    for shadow_id, row in data.get("owners", {}).items():
        if row.get("status") != "open":
            continue
        summary["scanned"] += 1
        symbol, local = _sidecar_position_for_owner(executor, row)
        if not local:
            exchange_state, _exchange_position = _sidecar_exchange_position_state(
                executor,
                symbol,
            )
            if exchange_state == "flat":
                now = time.time()
                ledger_close = _record_exchange_flat_close(
                    executor,
                    symbol,
                    _owner_row_as_close_metadata(row, symbol),
                    row,
                    closed_at=now,
                )
                row["status"] = "closed"
                row["closed_at"] = now
                row["last_monitor_at"] = now
                row["close_reason"] = "exchange_flat_reconciled"
                if ledger_close.get("ledger_close_event_id"):
                    row["close_ledger_event_id"] = ledger_close["ledger_close_event_id"]
                if ledger_close.get("ledger_close_pnl_status"):
                    row["close_pnl_status"] = ledger_close["ledger_close_pnl_status"]
                halt_clear = _clear_halt_after_exchange_flat(paths, executor, symbol)
                summary["closed"] += 1
                summary["exchange_flat"] += 1
                append_audit_event(
                    paths.audit,
                    "monitor_reconciled_flat",
                    {
                        "shadow_id": shadow_id,
                        "symbol": symbol,
                        "unproven_owner": True,
                        **ledger_close,
                        **halt_clear,
                    },
                )
                continue

            protection_state = _pending_protection_state(executor, symbol)
            operator_action_required = protection_state in ("absent", "unknown")
            if exchange_state in ("present", "unknown"):
                summary["ghost_exposure"] += 1
                summary["skipped"] += 1
                halt = getattr(executor, "_halt_symbol", None)
                if callable(halt):
                    halt(symbol, reason="sidecar_ghost_exposure")
                append_audit_event(
                    paths.audit,
                    "monitor_ghost_exposure",
                    {
                        "shadow_id": shadow_id,
                        "symbol": symbol,
                        "exchange_state": exchange_state,
                        "unproven_owner": True,
                        "pending_protection_state": protection_state,
                        "operator_action_required": operator_action_required,
                    },
                )
                continue

            summary["skipped"] += 1
            append_audit_event(
                paths.audit,
                "monitor_skipped_exchange_unsupported"
                if exchange_state == "unsupported"
                else "monitor_skipped_unproven",
                {
                    "shadow_id": shadow_id,
                    "symbol": symbol,
                    "exchange_state": exchange_state,
                    "unproven_owner": True,
                },
            )
            continue

        exchange_state, _exchange_position = _sidecar_exchange_position_state(
            executor,
            symbol,
        )
        if exchange_state == "unknown":
            row["last_monitor_at"] = time.time()
            summary["skipped"] += 1
            append_audit_event(
                paths.audit,
                "monitor_skipped_exchange_unknown",
                {"shadow_id": shadow_id, "symbol": symbol},
            )
            continue
        if exchange_state == "flat":
            now = time.time()
            ledger_close = _record_exchange_flat_close(
                executor,
                symbol,
                local,
                row,
                closed_at=now,
            )
            row["status"] = "closed"
            row["closed_at"] = now
            row["last_monitor_at"] = now
            row["close_reason"] = "exchange_flat_reconciled"
            if ledger_close.get("ledger_close_event_id"):
                row["close_ledger_event_id"] = ledger_close["ledger_close_event_id"]
            if ledger_close.get("ledger_close_pnl_status"):
                row["close_pnl_status"] = ledger_close["ledger_close_pnl_status"]
            _remove_local_sidecar_position(executor, symbol)
            halt_clear = _clear_halt_after_exchange_flat(paths, executor, symbol)
            summary["closed"] += 1
            summary["exchange_flat"] += 1
            append_audit_event(
                paths.audit,
                "monitor_reconciled_flat",
                {
                    "shadow_id": shadow_id,
                    "symbol": symbol,
                    **ledger_close,
                    **halt_clear,
                },
            )
            continue

        group_count = owner_group_counts.get(_owner_group_key(row), 0)
        if group_count > 1:
            summary["ambiguous_stacks"] += 1
            summary["skipped"] += 1
            append_audit_event(
                paths.audit,
                "monitor_ambiguous_net_mode_stack",
                {
                    "shadow_id": shadow_id,
                    "symbol": symbol,
                    "owner_group_count": group_count,
                    "exchange_state": exchange_state,
                },
            )
            continue

        trigger = executor.check_stop_loss_take_profit(symbol)
        if not trigger:
            row["last_monitor_at"] = time.time()
            continue

        row["last_monitor_at"] = time.time()
        row["last_exit_trigger"] = trigger
        reduce_action = _reduce_action_for_trigger(trigger)
        if reduce_action:
            pct, tp_advance, action_kind = reduce_action
            result = executor.reduce_position(
                symbol,
                pct,
                tp_advance=tp_advance,
                action_kind=action_kind,
            )
            row["last_exit_ok"] = bool(result)
            row["last_exit_action_kind"] = action_kind
            if result:
                summary["reduced"] += 1
                append_audit_event(
                    paths.audit,
                    "monitor_reduced",
                    {
                        "shadow_id": shadow_id,
                        "symbol": symbol,
                        "trigger": trigger,
                        "result": True,
                    },
                )
            else:
                summary["failed"] += 1
                append_audit_event(
                    paths.audit,
                    "monitor_reduce_failed",
                    {"shadow_id": shadow_id, "symbol": symbol, "trigger": trigger},
                )
            continue

        action_kind = f"sidecar_{trigger}"
        result = executor.close_position(symbol, action_kind=action_kind)
        row["last_exit_ok"] = bool(result)
        row["last_exit_action_kind"] = action_kind
        if result:
            row["status"] = "closed"
            row["closed_at"] = time.time()
            summary["closed"] += 1
            append_audit_event(
                paths.audit,
                "monitor_closed",
                {
                    "shadow_id": shadow_id,
                    "symbol": symbol,
                    "trigger": trigger,
                    "result": True,
                },
            )
        else:
            summary["failed"] += 1
            append_audit_event(
                paths.audit,
                "monitor_close_failed",
                {"shadow_id": shadow_id, "symbol": symbol, "trigger": trigger},
            )
    registry.save(data)
    return summary


def _process_event(args, paths, state, registry, executor, event) -> None:
    if not is_tactical_shadow_event(event):
        return
    record = event.get("record") or {}
    shadow_id = record.get("id") or ""
    if shadow_id in state["seen_shadow_ids"]:
        append_audit_event(paths.audit, "duplicate_skipped", {"shadow_id": shadow_id})
        return

    plan, reason = map_shadow_record_to_plan(record, return_error=True)
    if reason:
        state["seen_shadow_ids"][shadow_id] = "rejected"
        append_audit_event(paths.audit, "rejected", {"shadow_id": shadow_id, "reason": reason})
        return

    if args.dry_run:
        state["seen_shadow_ids"][shadow_id] = "opened"
        append_audit_event(paths.audit, "dry_run_plan", {"shadow_id": shadow_id, "plan": plan})
        return

    max_active = int(args.max_active)
    if _active_owner_count(registry) >= max_active:
        state["seen_shadow_ids"][shadow_id] = "rejected"
        append_audit_event(
            paths.audit,
            "rejected",
            {"shadow_id": shadow_id, "reason": "sidecar_active_cap"},
        )
        return

    blocked, guard_reason = blocks_same_symbol_account_exposure(
        _fetch_exchange_positions(executor),
        plan["symbol"],
        plan["side"],
        registry,
    )
    if blocked:
        state["seen_shadow_ids"][shadow_id] = "rejected"
        append_audit_event(
            paths.audit,
            "rejected",
            {"shadow_id": shadow_id, "reason": guard_reason},
        )
        return

    position = executor.open_sidecar_plan(plan, size_usdt=float(args.size_usdt))
    if position:
        state["seen_shadow_ids"][shadow_id] = "opened"
        _record_owner_if_open(registry, shadow_id, position)
        append_audit_event(
            paths.audit,
            "opened",
            {"shadow_id": shadow_id, "symbol": position.get("symbol")},
        )
    else:
        state["seen_shadow_ids"][shadow_id] = "rejected"
        _drain_sidecar_entry_drift_alerts(paths, executor, shadow_id)
        append_audit_event(
            paths.audit,
            "rejected",
            {"shadow_id": shadow_id, "reason": "executor_rejected"},
        )


def cmd_run(args) -> int:
    paths = _paths(args)
    state_exists = os.path.exists(paths.state)
    store = SidecarStateStore(paths.state)
    state = store.load()
    now = time.time()
    state.setdefault("started_at", now)
    state["stop_at"] = state.get("stop_at") or now + float(args.duration_hours) * 3600
    state.setdefault("seen_shadow_ids", {})
    if not state_exists:
        if args.backfill_from_start:
            state["last_offset"] = 0
        elif os.path.exists(paths.events):
            state["last_offset"] = os.path.getsize(paths.events)

    registry = ShadowTacticalOwnerRegistry(paths.owners)
    executor = None if args.dry_run else _build_executor(paths)

    while time.time() < state["stop_at"]:
        for row in iter_new_shadow_events(paths.events, state.get("last_offset", 0)):
            state["last_offset"] = row.next_offset
            _process_event(args, paths, state, registry, executor, row.event)
            store.save(state)
        if executor is not None:
            monitor_sidecar_owned_exposure(paths, executor)
        if args.once:
            break
        time.sleep(float(args.poll_seconds))

    if time.time() >= state["stop_at"]:
        append_audit_event(
            paths.audit,
            "window_expired",
            {"processed": len(state.get("seen_shadow_ids", {}))},
        )
    store.save(state)
    return 0


def stop_sidecar_owned_exposure(paths: SidecarPaths, executor: ContractExecutor) -> dict:
    registry = ShadowTacticalOwnerRegistry(paths.owners)
    data = registry.load()
    closed = 0
    skipped = 0
    for shadow_id, row in data.get("owners", {}).items():
        if row.get("status") != "open":
            continue
        symbol, local = _sidecar_position_for_owner(executor, row)
        proven = bool(symbol and local)
        if not proven:
            skipped += 1
            append_audit_event(
                paths.audit,
                "stop_skipped_unproven",
                {"shadow_id": shadow_id, "symbol": symbol},
            )
            continue
        sl_algo_id = row.get("sl_algo_id")
        if sl_algo_id:
            executor._cancel_algo_by_id(symbol, sl_algo_id)
        result = executor.close_position(symbol, action_kind="sidecar_stop")
        row["status"] = "closed" if result else "close_attempted"
        row["closed_at"] = time.time()
        append_audit_event(
            paths.audit,
            "stop_closed",
            {"shadow_id": shadow_id, "symbol": symbol, "result": bool(result)},
        )
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
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
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
    run.add_argument("--max-active", default=os.getenv("MAX_CONCURRENT_POSITIONS", "3"))
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--once", action="store_true")
    run.add_argument("--from-end", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--backfill-from-start", action="store_true")

    args = parser.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status, "stop": cmd_stop}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
