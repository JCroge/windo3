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
    is_tactical_shadow_event,
    iter_new_shadow_events,
    map_shadow_record_to_plan,
)


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


def _reduce_action_for_trigger(trigger: str):
    if trigger in ("tactical_tp1", "partial_tp_1"):
        return 0.5, 1, "sidecar_tactical_tp1"
    if trigger in ("partial_tp_2", "tactical_tp2"):
        return 0.25, 2, "sidecar_tactical_tp2"
    return None


def monitor_sidecar_owned_exposure(paths: SidecarPaths, executor: ContractExecutor) -> dict:
    registry = ShadowTacticalOwnerRegistry(paths.owners)
    data = registry.load()
    summary = {"scanned": 0, "reduced": 0, "closed": 0, "skipped": 0, "failed": 0}
    for shadow_id, row in data.get("owners", {}).items():
        if row.get("status") != "open":
            continue
        summary["scanned"] += 1
        symbol, local = _sidecar_position_for_owner(executor, row)
        if not local:
            summary["skipped"] += 1
            append_audit_event(
                paths.audit,
                "monitor_skipped_unproven",
                {"shadow_id": shadow_id, "symbol": symbol},
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
