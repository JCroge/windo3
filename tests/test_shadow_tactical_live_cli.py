import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = str(ROOT / "scripts" / "shadow_tactical_live_sidecar.py")


def test_status_prints_state_counts(tmp_path):
    state = tmp_path / "state.json"
    owners = tmp_path / "owners.json"
    state.write_text(
        json.dumps({"last_offset": 10, "seen_shadow_ids": {"s1": "opened", "s2": "rejected"}})
    )
    owners.write_text(
        json.dumps(
            {
                "owners": {
                    "s1": {
                        "status": "open",
                        "symbol": "WLD-USDT-SWAP",
                        "side": "long",
                    }
                }
            }
        )
    )

    out = subprocess.check_output(
        [
            sys.executable,
            SCRIPT,
            "status",
            "--state",
            str(state),
            "--owners",
            str(owners),
        ],
        text=True,
        cwd=str(ROOT),
    )

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
        "tactical_track_gate": "pass",
    }
    events.write_text(json.dumps({"event_type": "rejected_plan_created", "record": rec}) + "\n")

    subprocess.check_call(
        [
            sys.executable,
            SCRIPT,
            "run",
            "--dry-run",
            "--once",
            "--backfill-from-start",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
            "--duration-hours",
            "24",
        ],
        cwd=str(ROOT),
    )

    row = json.loads(audit.read_text().splitlines()[0])
    assert row["event_type"] == "dry_run_plan"
    assert row["shadow_id"] == "s1"


def test_run_defaults_to_no_backfill_on_first_start(tmp_path):
    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    rec = {
        "id": "old",
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "entry_price": 1.25,
        "stop_loss": 1.20,
        "take_profit": [1.32],
        "leverage": 20,
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "tactical_track_gate": "pass",
    }
    events.write_text(json.dumps({"event_type": "rejected_plan_created", "record": rec}) + "\n")

    subprocess.check_call(
        [
            sys.executable,
            SCRIPT,
            "run",
            "--dry-run",
            "--once",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
        ],
        cwd=str(ROOT),
    )

    loaded = json.loads(state.read_text())
    assert loaded["last_offset"] == events.stat().st_size
    assert loaded["seen_shadow_ids"] == {}
    assert not audit.exists()


def test_run_preserves_existing_watermark_when_no_backfill_default(tmp_path):
    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    old_line = json.dumps(
        {
            "event_type": "rejected_plan_created",
            "record": {
                "id": "old",
                "symbol": "WLD-USDT-SWAP",
                "side": "long",
                "entry_price": 1.25,
                "stop_loss": 1.20,
                "take_profit": [1.32],
                "leverage": 20,
                "track": "tactical",
                "exit_profile": "tactical_v1",
                "tactical_track_gate": "pass",
            },
        }
    ) + "\n"
    events.write_text(old_line)
    old_offset = events.stat().st_size
    state.write_text(
        json.dumps(
            {
                "started_at": 1,
                "stop_at": None,
                "last_offset": old_offset,
                "seen_shadow_ids": {},
            }
        )
    )
    with events.open("a") as fh:
        fh.write(
            json.dumps(
                {
                    "event_type": "rejected_plan_created",
                    "record": {
                        "id": "new",
                        "symbol": "WLD-USDT-SWAP",
                        "side": "long",
                        "entry_price": 1.25,
                        "stop_loss": 1.20,
                        "take_profit": [1.32],
                        "leverage": 20,
                        "track": "tactical",
                        "exit_profile": "tactical_v1",
                        "tactical_track_gate": "pass",
                    },
                }
            )
            + "\n"
        )

    subprocess.check_call(
        [
            sys.executable,
            SCRIPT,
            "run",
            "--dry-run",
            "--once",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
        ],
        cwd=str(ROOT),
    )

    row = json.loads(audit.read_text().splitlines()[0])
    assert row["shadow_id"] == "new"
    loaded = json.loads(state.read_text())
    assert loaded["last_offset"] == events.stat().st_size


def test_stop_closes_only_proven_sidecar_owned_exposure(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    owners = tmp_path / "owners.json"
    audit = tmp_path / "audit.jsonl"
    owners.write_text(
        json.dumps(
            {
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
                        "side": "short",
                    },
                }
            }
        )
    )
    fake = MagicMock()
    fake.positions = {
        "WLD-USDT-SWAP": {
            "symbol": "WLD-USDT-SWAP",
            "side": "long",
            "shadow_id": "s1",
        }
    }
    fake._cancel_algo_by_id.return_value = True
    fake.close_position.return_value = {"id": "close-1"}
    monkeypatch.setattr(mod, "_build_executor", lambda paths: fake)

    code = mod.main(
        [
            "stop",
            "--owners",
            str(owners),
            "--audit",
            str(audit),
            "--state",
            str(tmp_path / "state.json"),
        ]
    )

    assert code == 0
    fake._cancel_algo_by_id.assert_called_once_with("WLD-USDT-SWAP", "algo-1")
    fake.close_position.assert_called_once_with(
        "WLD-USDT-SWAP",
        action_kind="sidecar_stop",
    )
    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [row["event_type"] for row in rows[:2]] == [
        "stop_closed",
        "stop_skipped_unproven",
    ]


def test_stop_matches_legacy_internal_symbol_position(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    owners = tmp_path / "owners.json"
    audit = tmp_path / "audit.jsonl"
    owners.write_text(
        json.dumps(
            {
                "owners": {
                    "s1": {
                        "shadow_id": "s1",
                        "status": "open",
                        "symbol": "ONDO-USDT",
                        "internal_symbol": "ONDO-USDT",
                        "exchange_symbol": "ONDO-USDT-SWAP",
                        "side": "long",
                        "sl_algo_id": "algo-1",
                        "sl_algo_clord_id": "castliveONDO1",
                    }
                }
            }
        )
    )
    fake = MagicMock()
    fake.positions = {
        "ONDO-USDT": {
            "symbol": "ONDO-USDT",
            "internal_symbol": "ONDO-USDT",
            "side": "long",
            "shadow_id": "s1",
        }
    }
    fake._cancel_algo_by_id.return_value = True
    fake.close_position.return_value = {"id": "close-1"}
    monkeypatch.setattr(mod, "_build_executor", lambda paths: fake)

    code = mod.main(
        [
            "stop",
            "--owners",
            str(owners),
            "--audit",
            str(audit),
            "--state",
            str(tmp_path / "state.json"),
        ]
    )

    assert code == 0
    fake._cancel_algo_by_id.assert_called_once_with("ONDO-USDT", "algo-1")
    fake.close_position.assert_called_once_with(
        "ONDO-USDT",
        action_kind="sidecar_stop",
    )


def test_monitor_routes_tactical_tp1_reduce(tmp_path):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
    )
    mod.ShadowTacticalOwnerRegistry(paths.owners).record_open(
        shadow_id="s1",
        symbol="ONDO-USDT-SWAP",
        side="long",
        amount_usdt=30.0,
        order_id="ord-1",
        entry_clord_id="cl-1",
        sl_algo_id="algo-1",
        sl_algo_clord_id="sl-1",
    )
    fake = MagicMock()
    fake.positions = {
        "ONDO-USDT-SWAP": {
            "symbol": "ONDO-USDT-SWAP",
            "internal_symbol": "ONDO-USDT",
            "side": "long",
            "shadow_id": "s1",
            "sidecar_source": "shadow_tactical_live",
            "take_profit_levels": [1.32, 1.38],
            "tp_filled": 0,
            "entry_price": 1.25,
            "stop_loss": 1.20,
            "original_sl": 1.20,
            "highest_price": 1.25,
            "lowest_price": 1.25,
            "atr_pct": 0.02,
            "open_time": 0,
        }
    }
    fake.check_stop_loss_take_profit.return_value = "tactical_tp1"
    fake.reduce_position.return_value = {"ok": True}

    result = mod.monitor_sidecar_owned_exposure(paths, fake)

    fake.reduce_position.assert_called_once_with(
        "ONDO-USDT-SWAP",
        0.5,
        tp_advance=1,
        action_kind="sidecar_tactical_tp1",
    )
    assert result["reduced"] == 1


def test_run_once_monitors_open_sidecar_position_without_new_events(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    owners = tmp_path / "owners.json"
    state.write_text(json.dumps({"last_offset": 0, "seen_shadow_ids": {}}))
    owners.write_text(
        json.dumps(
            {
                "owners": {
                    "s1": {
                        "shadow_id": "s1",
                        "status": "open",
                        "symbol": "ONDO-USDT-SWAP",
                        "internal_symbol": "ONDO-USDT",
                        "exchange_symbol": "ONDO-USDT-SWAP",
                        "side": "long",
                        "sl_algo_id": "algo-1",
                        "sl_algo_clord_id": "sl-1",
                    }
                }
            }
        )
    )
    fake = MagicMock()
    fake.positions = {
        "ONDO-USDT-SWAP": {
            "symbol": "ONDO-USDT-SWAP",
            "internal_symbol": "ONDO-USDT",
            "side": "long",
            "shadow_id": "s1",
            "sidecar_source": "shadow_tactical_live",
            "take_profit_levels": [1.32, 1.38],
            "tp_filled": 0,
            "entry_price": 1.25,
            "stop_loss": 1.20,
            "original_sl": 1.20,
            "highest_price": 1.25,
            "lowest_price": 1.25,
            "atr_pct": 0.02,
            "open_time": 0,
        }
    }
    fake.check_stop_loss_take_profit.return_value = "tactical_tp1"
    fake.reduce_position.return_value = {"ok": True}
    monkeypatch.setattr(mod, "_build_executor", lambda paths: fake)

    code = mod.main(
        [
            "run",
            "--once",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
            "--owners",
            str(owners),
            "--duration-hours",
            "1",
        ]
    )

    assert code == 0
    fake.check_stop_loss_take_profit.assert_called_once_with("ONDO-USDT-SWAP")


def test_monitor_reconciles_sidecar_owner_when_exchange_is_flat(tmp_path):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
        positions=str(tmp_path / "positions.json"),
        risk_state=str(tmp_path / "risk_state.json"),
        halt_state=str(tmp_path / "halt_state.json"),
    )
    mod.ShadowTacticalOwnerRegistry(paths.owners).record_open(
        shadow_id="s1",
        symbol="WLD-USDT-SWAP",
        side="short",
        amount_usdt=30.0,
        order_id="ord-1",
        entry_clord_id="cl-1",
        sl_algo_id="algo-1",
        sl_algo_clord_id="sl-1",
    )
    Path(paths.halt_state).write_text(
        json.dumps(
            {
                "halted": True,
                "reason": "okx_sl_cancel_failed:WLD-USDT-SWAP",
                "triggered_at": 1.0,
                "triggered_by": "executor",
                "resume_at": 0.0,
                "resume_by": "",
                "reconciliation_pending": False,
                "reconciliation_result": None,
            }
        )
    )
    fake = SimpleNamespace(
        exchange_id="okx",
        positions={
            "WLD-USDT-SWAP": {
                "symbol": "WLD-USDT-SWAP",
                "internal_symbol": "WLD-USDT",
                "side": "short",
                "shadow_id": "s1",
                "sidecar_source": "shadow_tactical_live",
            }
        },
        _halted_symbols={
            "WLD-USDT-SWAP": {"reason": "sl_cancel_failed", "halted_at": 1.0}
        },
        _fetch_positions_with_retry=MagicMock(return_value=[]),
        _normalize_okx_position=MagicMock(side_effect=lambda raw: raw),
        _save_positions=MagicMock(),
        check_stop_loss_take_profit=MagicMock(return_value=None),
        clear_symbol_halt=MagicMock(return_value=1),
        logger=MagicMock(),
    )

    result = mod.monitor_sidecar_owned_exposure(paths, fake)

    owners = mod.ShadowTacticalOwnerRegistry(paths.owners).load()["owners"]
    assert owners["s1"]["status"] == "closed"
    assert owners["s1"]["close_reason"] == "exchange_flat_reconciled"
    assert "WLD-USDT-SWAP" not in fake.positions
    assert mod._active_owner_count(mod.ShadowTacticalOwnerRegistry(paths.owners)) == 0
    assert result["closed"] == 1
    assert result["exchange_flat"] == 1
    fake._save_positions.assert_called_once()
    fake.check_stop_loss_take_profit.assert_not_called()
    fake.clear_symbol_halt.assert_called_once_with(
        "WLD-USDT-SWAP",
        source="sidecar_monitor_exchange_flat",
    )
    rows = [json.loads(line) for line in Path(paths.audit).read_text().splitlines()]
    assert rows[-1]["event_type"] == "monitor_reconciled_flat"
    assert rows[-1]["cleared_symbol_halt"] is True
    assert rows[-1]["cleared_global_halt"] is True
    halt_state = json.loads(Path(paths.halt_state).read_text())
    assert halt_state["halted"] is False


def test_monitor_skips_flat_reconciliation_when_exchange_fetch_fails(tmp_path):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
        positions=str(tmp_path / "positions.json"),
        risk_state=str(tmp_path / "risk_state.json"),
        halt_state=str(tmp_path / "halt_state.json"),
    )
    mod.ShadowTacticalOwnerRegistry(paths.owners).record_open(
        shadow_id="s1",
        symbol="WLD-USDT-SWAP",
        side="short",
        amount_usdt=30.0,
        order_id="ord-1",
        entry_clord_id="cl-1",
        sl_algo_id="algo-1",
        sl_algo_clord_id="sl-1",
    )
    fake = SimpleNamespace(
        exchange_id="okx",
        positions={
            "WLD-USDT-SWAP": {
                "symbol": "WLD-USDT-SWAP",
                "internal_symbol": "WLD-USDT",
                "side": "short",
                "shadow_id": "s1",
                "sidecar_source": "shadow_tactical_live",
            }
        },
        _fetch_positions_with_retry=MagicMock(side_effect=RuntimeError("timeout")),
        _normalize_okx_position=MagicMock(side_effect=lambda raw: raw),
        _save_positions=MagicMock(),
        check_stop_loss_take_profit=MagicMock(return_value=None),
        logger=MagicMock(),
    )

    result = mod.monitor_sidecar_owned_exposure(paths, fake)

    owners = mod.ShadowTacticalOwnerRegistry(paths.owners).load()["owners"]
    assert owners["s1"]["status"] == "open"
    assert "WLD-USDT-SWAP" in fake.positions
    assert result["skipped"] == 1
    fake._save_positions.assert_not_called()
    fake.check_stop_loss_take_profit.assert_not_called()
    rows = [json.loads(line) for line in Path(paths.audit).read_text().splitlines()]
    assert rows[-1]["event_type"] == "monitor_skipped_exchange_unknown"


def test_monitor_records_pending_external_close_when_exchange_is_flat(tmp_path):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
        positions=str(tmp_path / "positions.json"),
        risk_state=str(tmp_path / "risk_state.json"),
        halt_state=str(tmp_path / "halt_state.json"),
    )
    mod.ShadowTacticalOwnerRegistry(paths.owners).record_open(
        shadow_id="doge-shadow",
        symbol="DOGE-USDT-SWAP",
        side="short",
        amount_usdt=30.0,
        order_id="open-1",
        entry_clord_id="entry-cl",
        sl_algo_id="sl-algo",
        sl_algo_clord_id="sl-cl",
    )
    ledger = MagicMock()
    ledger.record_pending_external_close.return_value = {
        "event_id": "pending-close-1",
        "pnl_status": "pending",
    }
    fake = SimpleNamespace(
        exchange_id="okx",
        positions={
            "DOGE-USDT-SWAP": {
                "symbol": "DOGE-USDT-SWAP",
                "internal_symbol": "DOGE-USDT",
                "side": "short",
                "shadow_id": "doge-shadow",
                "sidecar_source": "shadow_tactical_live",
                "entry_price": 0.07227,
                "amount_usdt": 30.0,
                "leverage": 5,
                "open_time": 123.0,
                "sl_algo_id": "sl-algo",
                "sl_algo_clord_id": "sl-cl",
                "gate_metadata": {"tactical_track_gate": "pass"},
            }
        },
        _fetch_positions_with_retry=MagicMock(return_value=[]),
        _normalize_okx_position=MagicMock(side_effect=lambda raw: raw),
        _save_positions=MagicMock(),
        check_stop_loss_take_profit=MagicMock(return_value=None),
        logger=MagicMock(),
        ledger=ledger,
    )

    result = mod.monitor_sidecar_owned_exposure(paths, fake)

    assert result["exchange_flat"] == 1
    ledger.record_pending_external_close.assert_called_once_with(
        symbol="DOGE-USDT-SWAP",
        side="short",
        entry_price=0.07227,
        amount_usdt=30.0,
        leverage=5,
        estimated_pnl=None,
        position_id=None,
        entry_request_id="doge-shadow",
        opened_at=123.0,
        closed_at=ANY,
        sl_algo_id="sl-algo",
        sl_algo_clord_id="sl-cl",
        entry_attribution={"tactical_track_gate": "pass"},
    )
    owners = mod.ShadowTacticalOwnerRegistry(paths.owners).load()["owners"]
    assert owners["doge-shadow"]["close_ledger_event_id"] == "pending-close-1"
    rows = [json.loads(line) for line in Path(paths.audit).read_text().splitlines()]
    assert rows[-1]["ledger_close_recorded"] is True
    assert rows[-1]["ledger_close_event_id"] == "pending-close-1"
