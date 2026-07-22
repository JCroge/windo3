import json

from utils.shadow_tactical_live import (
    ShadowTacticalOwnerRegistry,
    SidecarStateStore,
    append_audit_event,
    blocks_same_symbol_account_exposure,
    canonical_sidecar_symbols,
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


def test_tactical_identity_accepts_only_true_open_tactical_records():
    assert is_tactical_shadow_event(
        _event(_tactical_record(track="tactical", tactical_track_gate="pass"))
    )
    assert not is_tactical_shadow_event(
        _event(
            _tactical_record(
                track="main",
                exit_profile="tactical_v1",
                tactical_track_gate="pass",
            )
        )
    )
    assert not is_tactical_shadow_event(
        _event(_tactical_record(track="main", exit_profile="trend_runner"))
    )
    assert not is_tactical_shadow_event(
        {"event_type": "shadow_tp", "record": _tactical_record()}
    )


def test_tactical_shadow_event_requires_true_open_track_and_gate_pass():
    assert is_tactical_shadow_event(
        _event(_tactical_record(track="tactical", tactical_track_gate="pass"))
    )

    assert not is_tactical_shadow_event(
        _event(_tactical_record(track="shadow_only", tactical_track_gate="fail"))
    )

    assert not is_tactical_shadow_event(
        _event(_tactical_record(track="tactical", tactical_track_gate="fail"))
    )


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


def test_map_shadow_record_accepts_scalar_take_profit():
    plan = map_shadow_record_to_plan(_tactical_record(take_profit=1.32))

    assert plan["take_profit"] == [1.32]


def test_map_shadow_record_accepts_numeric_string_take_profit():
    plan = map_shadow_record_to_plan(_tactical_record(take_profit="1.32"))

    assert plan["take_profit"] == [1.32]


def test_map_shadow_record_rejects_invalid_take_profit_levels():
    for invalid_tp in ("0", "nan", "inf", ["1.32", "nan"]):
        plan, reason = map_shadow_record_to_plan(
            _tactical_record(take_profit=invalid_tp),
            return_error=True,
        )

        assert plan is None
        assert reason == "missing_take_profit"


def test_canonical_sidecar_symbols_split_internal_and_exchange():
    assert canonical_sidecar_symbols("ONDO-USDT") == {
        "internal_symbol": "ONDO-USDT",
        "exchange_symbol": "ONDO-USDT-SWAP",
    }
    assert canonical_sidecar_symbols("ONDO/USDT:USDT") == {
        "internal_symbol": "ONDO-USDT",
        "exchange_symbol": "ONDO-USDT-SWAP",
    }


def test_missing_required_field_rejects_without_plan():
    plan, reason = map_shadow_record_to_plan(
        _tactical_record(stop_loss=0), return_error=True
    )

    assert plan is None
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

    append_audit_event(
        str(path),
        "rejected",
        {"shadow_id": "s1", "reason": "missing_stop_loss"},
    )

    row = json.loads(path.read_text().strip())
    assert row["event_type"] == "rejected"
    assert row["shadow_id"] == "s1"
    assert row["reason"] == "missing_stop_loss"


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


def test_owner_registry_migrates_legacy_symbol_rows(tmp_path):
    path = tmp_path / "owners.json"
    path.write_text(
        json.dumps(
            {
                "owners": {
                    "shadow-1": {
                        "shadow_id": "shadow-1",
                        "symbol": "ONDO-USDT",
                        "side": "long",
                        "status": "open",
                    }
                }
            }
        )
    )
    reg = ShadowTacticalOwnerRegistry(str(path))

    row = reg.active_for("ONDO-USDT", "long")

    assert row["internal_symbol"] == "ONDO-USDT"
    assert row["exchange_symbol"] == "ONDO-USDT-SWAP"
    assert row["symbol"] == "ONDO-USDT-SWAP"


def test_same_symbol_guard_blocks_sidecar_owned_exposure(tmp_path):
    reg = ShadowTacticalOwnerRegistry(str(tmp_path / "owners.json"))
    reg.record_open(
        "shadow-1",
        "WLD-USDT-SWAP",
        "long",
        30.0,
        "ord-1",
        "stl1",
        "algo-1",
        "castlive1",
    )
    exchange_positions = [{"symbol": "WLD/USDT:USDT", "side": "long", "contracts": 10}]

    blocked, reason = blocks_same_symbol_account_exposure(
        exchange_positions,
        "WLD-USDT-SWAP",
        "long",
        reg,
    )

    assert blocked is True
    assert reason == "same_symbol_sidecar_active"


def test_same_symbol_guard_blocks_internal_sidecar_rows(tmp_path):
    reg = ShadowTacticalOwnerRegistry(str(tmp_path / "owners.json"))
    reg.record_open(
        "shadow-1",
        "ONDO-USDT",
        "long",
        30.0,
        "ord-1",
        "stl1",
        "algo-1",
        "castlive1",
    )
    exchange_positions = [{"symbol": "ONDO/USDT:USDT", "side": "long", "contracts": 10}]

    blocked, reason = blocks_same_symbol_account_exposure(
        exchange_positions,
        "ONDO-USDT",
        "long",
        reg,
    )

    assert blocked is True
    assert reason == "same_symbol_sidecar_active"


def test_same_symbol_guard_blocks_active_owner_without_exchange_positions(tmp_path):
    reg = ShadowTacticalOwnerRegistry(str(tmp_path / "owners.json"))
    reg.record_open(
        "shadow-1",
        "WLD-USDT-SWAP",
        "long",
        30.0,
        "ord-1",
        "stl1",
        "algo-1",
        "castlive1",
    )

    blocked, reason = blocks_same_symbol_account_exposure(
        [],
        "WLD-USDT-SWAP",
        "long",
        reg,
    )

    assert blocked is True
    assert reason == "same_symbol_sidecar_active"


def test_same_symbol_guard_blocks_non_sidecar_exposure(tmp_path):
    reg = ShadowTacticalOwnerRegistry(str(tmp_path / "owners.json"))
    exchange_positions = [{"symbol": "WLD/USDT:USDT", "side": "long", "contracts": 10}]

    blocked, reason = blocks_same_symbol_account_exposure(
        exchange_positions,
        "WLD-USDT-SWAP",
        "long",
        reg,
    )

    assert blocked is True
    assert reason == "same_symbol_account_exposure"


def test_same_symbol_guard_blocks_opposite_side_non_sidecar_exposure(tmp_path):
    reg = ShadowTacticalOwnerRegistry(str(tmp_path / "owners.json"))
    exchange_positions = [{"symbol": "WLD-USDT-SWAP", "side": "short", "contracts": 1}]

    blocked, reason = blocks_same_symbol_account_exposure(
        exchange_positions,
        "WLD-USDT-SWAP",
        "long",
        reg,
    )

    assert blocked is True
    assert reason == "same_symbol_account_exposure"
