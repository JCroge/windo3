import logging
import json
import time
from unittest.mock import MagicMock

from executor import ContractExecutor
from scripts.shadow_tactical_live_sidecar import monitor_sidecar_owned_exposure
from utils.shadow_tactical_live import SidecarPaths


SYMBOL = "ONDO-USDT-SWAP"


def _executor_with_position(
    *,
    price=1.26,
    tp_filled=0,
    open_age_minutes=0,
    extra_position=None,
):
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger("test_shadow_tactical_exit_monitoring")
    ex.positions = {}
    ex._config = {
        "tactical_max_hold_minutes": 90,
        "tactical_min_progress_r": 0.15,
        "tactical_weakened_no_progress_min_minutes": 30,
    }
    ex._sl_check_failures = {}
    ex._sl_max_failures = 3
    ex._fetch_price_robust = MagicMock(return_value=price)
    ex._halt_symbol = MagicMock()
    ex._enqueue_drift_alert = MagicMock()
    ex._move_sl = MagicMock()

    position = {
        "symbol": SYMBOL,
        "internal_symbol": "ONDO-USDT",
        "side": "long",
        "entry_price": 1.25,
        "stop_loss": 1.20,
        "original_sl": 1.20,
        "take_profit": 1.32,
        "take_profit_levels": [1.32, 1.38],
        "tp_filled": tp_filled,
        "highest_price": 1.25,
        "lowest_price": 1.25,
        "atr_pct": 0.02,
        "track": "tactical",
        "open_time": time.time() - open_age_minutes * 60,
    }
    if extra_position:
        position.update(extra_position)
    ex.positions[SYMBOL] = position
    return ex


def test_tactical_tp1_returns_reduce_trigger():
    ex = _executor_with_position(price=1.32, tp_filled=0)

    assert ex.check_stop_loss_take_profit(SYMBOL) == "tactical_tp1"


def test_tactical_tp2_returns_second_reduce_trigger():
    ex = _executor_with_position(price=1.38, tp_filled=1)

    assert ex.check_stop_loss_take_profit(SYMBOL) == "partial_tp_2"


def test_tactical_invalidated_returns_close_trigger():
    ex = _executor_with_position(
        price=1.26,
        extra_position={
            "tactical_thesis_state": "invalidated",
            "tactical_thesis_reason": "15m_opposing_block",
        },
    )

    assert ex.check_stop_loss_take_profit(SYMBOL) == "tactical_invalidated"
    assert ex.positions[SYMBOL]["tactical_close_detail"] == "15m_opposing_block"


def test_tactical_weakened_without_progress_returns_close_trigger():
    ex = _executor_with_position(
        price=1.251,
        extra_position={
            "tactical_thesis_state": "weakened",
            "tactical_last_progress_time": time.time() - 31 * 60,
            "tactical_weakened_no_progress_minutes": 30,
        },
    )

    assert ex.check_stop_loss_take_profit(SYMBOL) == "tactical_weakened_no_progress"


def test_tactical_max_hold_returns_close_trigger():
    ex = _executor_with_position(price=1.26, open_age_minutes=91)

    assert ex.check_stop_loss_take_profit(SYMBOL) == "tactical_max_hold"


def _sidecar_paths(tmp_path):
    return SidecarPaths(
        events=str(tmp_path / "events.jsonl"),
        state=str(tmp_path / "state.json"),
        audit=str(tmp_path / "audit.jsonl"),
        owners=str(tmp_path / "owners.json"),
        positions=str(tmp_path / "positions.json"),
        risk_state=str(tmp_path / "risk.json"),
        halt_state=str(tmp_path / "halt.json"),
        live_order_events=str(tmp_path / "orders.jsonl"),
        live_position_lifecycle=str(tmp_path / "lifecycle.json"),
    )


def _write_open_owner(paths, *, shadow_id="stale-owner"):
    try:
        with open(paths.owners) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {
            "schema_version": "shadow_tactical_owners.v1",
            "owners": {},
        }
    data["owners"][shadow_id] = {
        "shadow_id": shadow_id,
        "symbol": SYMBOL,
        "internal_symbol": "ONDO-USDT",
        "exchange_symbol": SYMBOL,
        "side": "long",
        "amount_usdt": 30.0,
        "order_id": "entry-order",
        "entry_clord_id": "",
        "sl_algo_id": "sl-1",
        "sl_algo_clord_id": "sl-client-1",
        "status": "open",
        "opened_at": 1234.0,
    }
    with open(paths.owners, "w") as fh:
        json.dump(data, fh)


def _executor_for_monitor(exchange_positions):
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger("test_shadow_tactical_exit_monitoring")
    ex.exchange_id = "okx"
    ex.positions = {}
    ex.ledger = MagicMock()
    ex.ledger.record_pending_external_close.return_value = {
        "event_id": "close-event-1",
        "pnl_status": "pending",
    }
    ex._fetch_positions_with_retry = MagicMock(return_value=exchange_positions)
    ex._normalize_okx_position = MagicMock(side_effect=lambda raw: raw)
    ex.check_stop_loss_take_profit = MagicMock()
    ex.close_position = MagicMock()
    ex.reduce_position = MagicMock()
    ex._save_positions = MagicMock()
    return ex


def test_unproven_owner_reconciles_closed_when_exchange_is_flat(tmp_path):
    paths = _sidecar_paths(tmp_path)
    _write_open_owner(paths)
    ex = _executor_for_monitor([])

    summary = monitor_sidecar_owned_exposure(paths, ex)

    owners = json.loads(open(paths.owners).read())["owners"]
    assert owners["stale-owner"]["status"] == "closed"
    assert owners["stale-owner"]["close_reason"] == "exchange_flat_reconciled"
    assert owners["stale-owner"]["close_ledger_event_id"] == "close-event-1"
    assert summary["closed"] == 1
    assert summary["exchange_flat"] == 1
    ex.ledger.record_pending_external_close.assert_called_once()
    ex.close_position.assert_not_called()
    ex.reduce_position.assert_not_called()


def test_unproven_owner_with_exchange_position_is_ghost_skipped_without_exit_action(tmp_path):
    paths = _sidecar_paths(tmp_path)
    _write_open_owner(paths)
    ex = _executor_for_monitor(
        [{"symbol": SYMBOL, "side": "long", "contracts": 1.0}]
    )
    ex._halt_symbol = MagicMock()
    ex._list_pending_algos = MagicMock(return_value=[])

    summary = monitor_sidecar_owned_exposure(paths, ex)

    owners = json.loads(open(paths.owners).read())["owners"]
    assert owners["stale-owner"]["status"] == "open"
    assert summary["skipped"] == 1
    assert summary["ghost_exposure"] == 1
    ex.ledger.record_pending_external_close.assert_not_called()
    ex.close_position.assert_not_called()
    ex.reduce_position.assert_not_called()


def test_unproven_owner_with_exchange_position_records_ghost_exposure(tmp_path):
    paths = _sidecar_paths(tmp_path)
    _write_open_owner(paths)
    ex = _executor_for_monitor(
        [{"symbol": SYMBOL, "side": "long", "contracts": 1.0}]
    )
    ex._halt_symbol = MagicMock()
    ex._list_pending_algos = MagicMock(return_value=[])

    summary = monitor_sidecar_owned_exposure(paths, ex)

    audit_events = [
        json.loads(line)
        for line in open(paths.audit).read().splitlines()
        if line.strip()
    ]
    assert summary["ghost_exposure"] == 1
    assert audit_events[-1]["event_type"] == "monitor_ghost_exposure"
    assert audit_events[-1]["exchange_state"] == "present"
    assert audit_events[-1]["operator_action_required"] is True
    ex._halt_symbol.assert_called_once_with(SYMBOL, reason="sidecar_ghost_exposure")
    ex.close_position.assert_not_called()
    ex.reduce_position.assert_not_called()


def test_unproven_owner_with_unknown_exchange_state_records_ghost_exposure(tmp_path):
    paths = _sidecar_paths(tmp_path)
    _write_open_owner(paths)
    ex = _executor_for_monitor([])
    ex._fetch_positions_with_retry.side_effect = RuntimeError("okx unavailable")
    ex._halt_symbol = MagicMock()
    ex._list_pending_algos = MagicMock(return_value=[])

    summary = monitor_sidecar_owned_exposure(paths, ex)

    audit_events = [
        json.loads(line)
        for line in open(paths.audit).read().splitlines()
        if line.strip()
    ]
    assert summary["ghost_exposure"] == 1
    assert audit_events[-1]["event_type"] == "monitor_ghost_exposure"
    assert audit_events[-1]["exchange_state"] == "unknown"
    ex._halt_symbol.assert_called_once_with(SYMBOL, reason="sidecar_ghost_exposure")
    ex.close_position.assert_not_called()
    ex.reduce_position.assert_not_called()


def test_unproven_owner_with_unsupported_exchange_state_is_skipped_without_exit_action(tmp_path):
    paths = _sidecar_paths(tmp_path)
    _write_open_owner(paths)
    ex = _executor_for_monitor([])
    ex.exchange_id = "binance"
    ex.check_stop_loss_take_profit.return_value = "tactical_max_hold"

    summary = monitor_sidecar_owned_exposure(paths, ex)

    owners = json.loads(open(paths.owners).read())["owners"]
    audit_events = [
        json.loads(line)
        for line in open(paths.audit).read().splitlines()
        if line.strip()
    ]
    assert owners["stale-owner"]["status"] == "open"
    assert summary["skipped"] == 1
    assert audit_events[-1]["event_type"] == "monitor_skipped_exchange_unsupported"
    assert audit_events[-1]["exchange_state"] == "unsupported"
    ex.check_stop_loss_take_profit.assert_not_called()
    ex.close_position.assert_not_called()
    ex.reduce_position.assert_not_called()


def test_monitor_does_not_close_one_row_from_ambiguous_net_mode_stack(tmp_path):
    paths = _sidecar_paths(tmp_path)
    _write_open_owner(paths, shadow_id="owner-1")
    _write_open_owner(paths, shadow_id="owner-2")
    data = json.loads(open(paths.owners).read())
    data["owners"]["owner-1"]["shadow_id"] = "owner-1"
    data["owners"]["owner-2"]["shadow_id"] = "owner-2"
    open(paths.owners, "w").write(json.dumps(data))

    ex = _executor_for_monitor(
        [{"symbol": SYMBOL, "side": "long", "contracts": 2.0}]
    )
    ex.positions[SYMBOL] = {
        "symbol": SYMBOL,
        "internal_symbol": "ONDO-USDT",
        "exchange_symbol": SYMBOL,
        "shadow_id": "owner-2",
        "side": "long",
        "sidecar_source": "shadow_tactical_live",
        "entry_price": 1.25,
        "stop_loss": 1.20,
        "take_profit": 1.32,
    }
    ex.check_stop_loss_take_profit.return_value = "tactical_max_hold"
    ex._list_pending_algos = MagicMock(return_value=[])
    ex._halt_symbol = MagicMock()

    summary = monitor_sidecar_owned_exposure(paths, ex)

    owners = json.loads(open(paths.owners).read())["owners"]
    assert owners["owner-1"]["status"] == "open"
    assert owners["owner-2"]["status"] == "open"
    assert summary["ambiguous_stacks"] == 1
    ex.close_position.assert_not_called()
    ex.reduce_position.assert_not_called()
