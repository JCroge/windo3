import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


RECEIPT_FIELDS = {
    "candidate_id",
    "source_shadow_id",
    "message_id",
    "symbol",
    "side",
    "accepted",
    "reason",
    "episode_id",
    "intent_id",
    "evaluated_at",
    "replayed",
    "payload_hash",
}


def _paths(tmp_path, namespace="testnet"):
    return SimpleNamespace(
        namespace=namespace,
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
        tactical_v2_status=str(tmp_path / "status.json"),
    )


def _candidate(
    *,
    symbol="WLD-USDT",
    candidate_id="cand-1",
    side="long",
    created_at=1000.0,
):
    short = side == "short"
    return {
        "candidate_id": candidate_id,
        "namespace": "testnet",
        "symbol": symbol,
        "side": side,
        "entry_ref": 1.0,
        "stop_loss": 1.05 if short else 0.95,
        "take_profit": 0.92 if short else 1.08,
        "leverage": 5,
        "source_shadow_id": f"shadow-{candidate_id}",
        "tactical_source": "main_quality_failed",
        "created_at": created_at,
        "tf_15m_available": True,
        "tf_15m_bias": "bearish" if short else "bullish",
        "tf_15m_closed_bar_ts": 900.0,
        "tf_15m_structure_token": f"break:{symbol}:{side}",
        "tf_15m_block_long": False,
        "tf_15m_block_short": False,
    }


def _executor():
    return SimpleNamespace(
        positions={},
        create_order=MagicMock(side_effect=AssertionError("shadow called create_order")),
        cancel_order=MagicMock(side_effect=AssertionError("shadow called cancel_order")),
        close_position=MagicMock(side_effect=AssertionError("shadow called close_position")),
    )


def _controller(tmp_path, *, mode="shadow"):
    from utils.tactical_v2.controller import TacticalV2Controller

    return TacticalV2Controller(
        executor=_executor(),
        config={"tactical_v2_mode": mode},
        paths=_paths(tmp_path),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        publish=None,
        now_fn=lambda: 1000.0,
    )


def _events(tmp_path):
    path = Path(tmp_path) / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _receipts(tmp_path):
    return [row for row in _events(tmp_path) if row["event_type"] == "candidate_handled"]


def _payload_hash(payload):
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _append_intent_created(store, raw, *, episode_id):
    from utils.tactical_v2.models import TacticalCandidate, TacticalIntent

    candidate = TacticalCandidate.from_raw(raw)
    intent = TacticalIntent.from_candidate(candidate, episode_id)
    store.append(
        "intent_created",
        {
            "intent_id": intent.intent_id,
            "episode_id": intent.episode_id,
            "intent": asdict(intent),
            "state": "ready_for_quote",
            "lane": "shadow",
            "shadow_state": None,
            "terminal_reason": None,
            "replayed": False,
            "updated_at": 1000.0,
        },
        emitted_at=1000.0,
    )
    return intent


def _append_episode(store, raw):
    from utils.tactical_v2.episodes import EpisodeRegistry
    from utils.tactical_v2.models import TacticalCandidate

    assignment = EpisodeRegistry(store, namespace="testnet").assign(
        TacticalCandidate.from_raw(raw),
        {
            "tf_15m_available": raw["tf_15m_available"],
            "tf_15m_bias": raw["tf_15m_bias"],
            "tf_15m_closed_bar_ts": raw["tf_15m_closed_bar_ts"],
            "tf_15m_structure_token": raw["tf_15m_structure_token"],
            "tf_15m_block_long": raw["tf_15m_block_long"],
            "tf_15m_block_short": raw["tf_15m_block_short"],
        },
    )
    return assignment.episode_id


def _candidate_receipt(raw, intent, *, message_id, **overrides):
    receipt = {
        "candidate_id": raw["candidate_id"],
        "source_shadow_id": raw["source_shadow_id"],
        "message_id": message_id,
        "symbol": raw["symbol"],
        "side": raw["side"],
        "accepted": True,
        "reason": "accepted",
        "episode_id": intent.episode_id,
        "intent_id": intent.intent_id,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": _payload_hash(raw),
    }
    receipt.update(overrides)
    return receipt


def _invalid_candidate_receipt(raw, *, message_id):
    return {
        "candidate_id": raw["candidate_id"],
        "source_shadow_id": raw["source_shadow_id"],
        "message_id": message_id,
        "symbol": raw["symbol"],
        "side": raw["side"],
        "accepted": "false",
        "reason": "invalid_candidate",
        "episode_id": None,
        "intent_id": None,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": _payload_hash(raw),
    }


def _rejected_candidate_receipt(raw, *, message_id, reason="invalid_candidate"):
    return {
        "candidate_id": raw["candidate_id"],
        "source_shadow_id": raw["source_shadow_id"],
        "message_id": message_id,
        "symbol": raw["symbol"],
        "side": raw["side"],
        "accepted": False,
        "reason": reason,
        "episode_id": None,
        "intent_id": None,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": _payload_hash(raw),
    }


def _integrity_proof(**overrides):
    proof = {
        "ownership": True,
        "orders": True,
        "positions": True,
        "protection": True,
    }
    proof.update(overrides)
    return proof


@pytest.mark.asyncio
async def test_accepted_candidate_receipt_has_canonical_schema_hash_and_ordering(tmp_path):
    controller = _controller(tmp_path)
    raw = {
        **_candidate(symbol="WLD/USDT:USDT"),
        "side": " LONG ",
    }

    result = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-accepted",
    )

    events = _events(tmp_path)
    receipt_event = _receipts(tmp_path)[0]
    receipt = receipt_event["data"]
    assert set(receipt) == RECEIPT_FIELDS
    assert receipt == {
        "candidate_id": "cand-1",
        "source_shadow_id": "shadow-cand-1",
        "message_id": "msg-accepted",
        "symbol": "WLD-USDT",
        "side": "long",
        "accepted": True,
        "reason": "accepted",
        "episode_id": result.episode_id,
        "intent_id": result.intent_id,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": _payload_hash(raw),
    }
    intent_event = next(row for row in events if row["event_type"] == "intent_created")
    assert intent_event["seq"] < receipt_event["seq"]
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["candidate_handling"] == {
        "receipt_count": 1,
        "unknown_handling_evidence": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "now", "mode", "reason"),
    [
        ({**_candidate(), "namespace": "live"}, 1000.0, "shadow", "namespace_mismatch"),
        ({**_candidate(), "side": "invalid"}, 1000.0, "shadow", "invalid_candidate"),
        (_candidate(created_at=1001.0), 1000.0, "shadow", "candidate_from_future"),
        (_candidate(created_at=99.0), 1000.0, "shadow", "candidate_expired"),
        (_candidate(), 1000.0, "off", "admission_disabled"),
    ],
)
async def test_pre_admission_rejections_each_persist_one_receipt(
    tmp_path,
    raw,
    now,
    mode,
    reason,
):
    controller = _controller(tmp_path, mode=mode)

    result = await controller.handle_candidate(
        raw,
        now=now,
        message_id=f"msg-{reason}",
    )

    receipts = _receipts(tmp_path)
    assert result.reason == reason
    assert len(receipts) == 1
    assert receipts[0]["data"]["reason"] == reason
    assert receipts[0]["data"]["accepted"] is False
    assert receipts[0]["data"]["intent_id"] is None
    assert receipts[0]["data"]["replayed"] is False


@pytest.mark.asyncio
async def test_untrusted_invalid_payload_fields_fail_closed_without_receipt(tmp_path):
    controller = _controller(tmp_path)
    raw = {
        "namespace": "testnet",
        "candidate_id": None,
        "source_shadow_id": {"unexpected", "set"},
        "symbol": ["bad"],
        "side": float("nan"),
        "opaque": b"bytes",
    }

    result = await controller.handle_candidate(raw, now=1000.0, message_id=object())

    assert result.reason == "unknown_handling_evidence"
    assert _receipts(tmp_path) == []
    assert [event["event_type"] for event in _events(tmp_path)] == [
        "candidate_payload_integrity_rejected",
    ]
    assert "payload_hash" not in _events(tmp_path)[0]["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        pytest.param([], id="list"),
        pytest.param(False, id="false"),
        pytest.param(0, id="zero"),
        pytest.param("", id="empty-string"),
        pytest.param(None, id="null"),
    ],
)
async def test_non_mapping_payload_persists_invalid_candidate_receipt(tmp_path, raw):
    controller = _controller(tmp_path)

    result = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-non-mapping",
    )

    receipts = _receipts(tmp_path)
    assert result.reason == "invalid_candidate"
    assert len(receipts) == 1
    assert receipts[0]["data"] == {
        "candidate_id": "",
        "source_shadow_id": "",
        "message_id": "msg-non-mapping",
        "symbol": "",
        "side": "",
        "accepted": False,
        "reason": "invalid_candidate",
        "episode_id": None,
        "intent_id": None,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": _payload_hash(raw),
    }


@pytest.mark.asyncio
async def test_falsey_json_payload_hashes_remain_distinct(tmp_path):
    hashes = []
    for index, raw in enumerate(([], False, 0, "", None)):
        controller = _controller(tmp_path / str(index))
        await controller.handle_candidate(
            raw,
            now=1000.0,
            message_id=f"msg-falsey-{index}",
        )
        payload_hash = _receipts(tmp_path / str(index))[0]["data"]["payload_hash"]
        assert payload_hash == _payload_hash(raw)
        hashes.append(payload_hash)

    assert len(set(hashes)) == 5


@pytest.mark.asyncio
async def test_overflow_candidate_fails_closed_without_payload_identity(tmp_path):
    controller = _controller(tmp_path)
    raw = {**_candidate(candidate_id="overflow-invalid"), "leverage": float("inf")}

    result = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-overflow-invalid",
    )

    assert result.reason == "unknown_handling_evidence"
    assert _receipts(tmp_path) == []
    assert _events(tmp_path)[0]["event_type"] == (
        "candidate_payload_integrity_rejected"
    )
    assert "payload_hash" not in _events(tmp_path)[0]["data"]


@pytest.mark.asyncio
async def test_recursive_invalid_payload_fails_closed_without_receipt(tmp_path):
    controller = _controller(tmp_path)
    recursive_mapping = {}
    recursive_mapping["self"] = recursive_mapping
    recursive_list = []
    recursive_list.append(recursive_list)
    raw = {
        "namespace": "testnet",
        "candidate_id": "recursive-invalid",
        "recursive_mapping": recursive_mapping,
        "recursive_list": recursive_list,
    }

    result = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-recursive-invalid",
    )

    assert result.reason == "unknown_handling_evidence"
    assert _receipts(tmp_path) == []
    assert _events(tmp_path)[0]["event_type"] == (
        "candidate_payload_integrity_rejected"
    )
    assert "payload_hash" not in _events(tmp_path)[0]["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "non_finite",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-inf"),
        pytest.param(float("-inf"), id="negative-inf"),
    ],
)
async def test_non_finite_anywhere_is_durable_unknown_without_receipt(
    tmp_path,
    non_finite,
):
    raw = {
        **_candidate(candidate_id="nested-non-finite"),
        "finite_extra": {"items": [1, {"invalid": non_finite}]},
    }
    controller = _controller(tmp_path)

    first = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-nested-non-finite",
    )
    events_after_first = _events(tmp_path)
    repeated = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-nested-non-finite",
        replayed=True,
    )
    restarted = _controller(tmp_path)
    after_restart = await restarted.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-nested-non-finite",
    )

    assert first.reason == "unknown_handling_evidence"
    assert repeated == first
    assert after_restart == first
    assert [event["event_type"] for event in events_after_first] == [
        "candidate_payload_integrity_rejected",
    ]
    assert "payload_hash" not in events_after_first[0]["data"]
    assert _events(tmp_path) == events_after_first
    assert _receipts(tmp_path) == []
    assert restarted.snapshot(now=1002.0)["intents"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cycle_kind", ["mapping", "list"])
async def test_cyclic_payload_is_durable_unknown_without_receipt(tmp_path, cycle_kind):
    cycle = {} if cycle_kind == "mapping" else []
    if cycle_kind == "mapping":
        cycle["self"] = cycle
    else:
        cycle.append(cycle)
    raw = {**_candidate(candidate_id=f"cycle-{cycle_kind}"), "extra": cycle}
    controller = _controller(tmp_path)

    first = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id=f"msg-cycle-{cycle_kind}",
    )
    events_after_first = _events(tmp_path)
    restarted = _controller(tmp_path)
    repeated = await restarted.handle_candidate(
        raw,
        now=1001.0,
        message_id=f"msg-cycle-{cycle_kind}",
        replayed=True,
    )

    assert first.reason == "unknown_handling_evidence"
    assert repeated == first
    assert [event["event_type"] for event in events_after_first] == [
        "candidate_payload_integrity_rejected",
    ]
    assert _events(tmp_path) == events_after_first
    assert _receipts(tmp_path) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsupported",
    [
        pytest.param(b"bytes", id="bytes"),
        pytest.param((1, 2), id="tuple"),
        pytest.param({1, 2}, id="set"),
        pytest.param({1: "non-string-key"}, id="non-string-key"),
    ],
)
async def test_non_json_payload_identity_is_durable_unknown(tmp_path, unsupported):
    raw = {
        **_candidate(candidate_id="unsupported-extra"),
        "unsupported": unsupported,
    }
    controller = _controller(tmp_path)

    result = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-unsupported-extra",
    )

    assert result.reason == "unknown_handling_evidence"
    assert [event["event_type"] for event in _events(tmp_path)] == [
        "candidate_payload_integrity_rejected",
    ]
    assert _receipts(tmp_path) == []


@pytest.mark.asyncio
async def test_marker_lookalike_and_finite_json_extras_keep_canonical_hash(tmp_path):
    raw = {
        **_candidate(candidate_id="marker-lookalike"),
        "extras": {
            "non_finite_float": "nan",
            "recursive_reference": True,
            "finite": [0.0, -12.5, {"nested": "value"}],
        },
    }
    controller = _controller(tmp_path)

    accepted = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-marker-lookalike",
    )
    restarted = _controller(tmp_path)
    replayed = await restarted.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-marker-lookalike",
        replayed=True,
    )

    assert accepted.accepted is True
    assert replayed == accepted
    assert _receipts(tmp_path)[0]["data"]["payload_hash"] == _payload_hash(raw)


@pytest.mark.asyncio
async def test_duplicate_and_opposing_block_receipts_keep_existing_episode(tmp_path):
    duplicate_controller = _controller(tmp_path / "duplicate")
    first = await duplicate_controller.handle_candidate(
        _candidate(candidate_id="cand-first"),
        now=1000.0,
        message_id="msg-first",
    )
    duplicate = await duplicate_controller.handle_candidate(
        _candidate(candidate_id="cand-duplicate"),
        now=1001.0,
        message_id="msg-duplicate",
    )

    duplicate_receipt = _receipts(tmp_path / "duplicate")[-1]["data"]
    assert duplicate.reason == "duplicate_episode"
    assert duplicate_receipt["episode_id"] == first.episode_id
    assert duplicate_receipt["intent_id"] is None

    blocked_controller = _controller(tmp_path / "blocked")
    blocked_first = await blocked_controller.handle_candidate(
        _candidate(candidate_id="cand-open"),
        now=1000.0,
        message_id="msg-open",
    )
    blocked = await blocked_controller.handle_candidate(
        {
            **_candidate(candidate_id="cand-blocked"),
            "tf_15m_block_long": True,
            "tf_15m_closed_bar_ts": 915.0,
        },
        now=1001.0,
        message_id="msg-blocked",
    )

    blocked_receipt = _receipts(tmp_path / "blocked")[-1]["data"]
    assert blocked.reason == "opposing_block"
    assert blocked_receipt["episode_id"] == blocked_first.episode_id
    assert blocked_receipt["intent_id"] is None


@pytest.mark.asyncio
async def test_governor_same_symbol_capacity_and_integrity_rejections_have_receipts(tmp_path):
    same_symbol_controller = _controller(tmp_path / "same-symbol")
    await same_symbol_controller.handle_candidate(
        _candidate(candidate_id="cand-long"),
        now=1000.0,
        message_id="msg-long",
    )
    same_symbol = await same_symbol_controller.handle_candidate(
        _candidate(candidate_id="cand-short", side="short"),
        now=1000.0,
        message_id="msg-short",
    )
    assert same_symbol.reason == "same_symbol_exposure"
    assert _receipts(tmp_path / "same-symbol")[-1]["data"]["intent_id"] is None

    capacity_controller = _controller(tmp_path / "capacity")
    for index, symbol in enumerate(("WLD-USDT", "ETH-USDT", "SOL-USDT")):
        await capacity_controller.handle_candidate(
            _candidate(symbol=symbol, candidate_id=f"cand-{index}"),
            now=1000.0,
            message_id=f"msg-{index}",
        )
    capacity = await capacity_controller.handle_candidate(
        _candidate(symbol="XRP-USDT", candidate_id="cand-capacity"),
        now=1000.0,
        message_id="msg-capacity",
    )
    assert capacity.reason == "capacity_skipped"
    assert _receipts(tmp_path / "capacity")[-1]["data"]["intent_id"] is None

    integrity_controller = _controller(tmp_path / "integrity")
    integrity_controller.governor.activate_integrity_halt("test_halt")
    integrity = await integrity_controller.handle_candidate(
        _candidate(candidate_id="cand-integrity"),
        now=1000.0,
        message_id="msg-integrity",
    )
    assert integrity.reason == "integrity_halt"
    assert _receipts(tmp_path / "integrity")[-1]["data"]["intent_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", ["msg-idempotent", None])
async def test_restart_replay_is_idempotent_by_message_or_payload_identity(
    tmp_path,
    message_id,
):
    raw = _candidate()
    first_controller = _controller(tmp_path)
    first = await first_controller.handle_candidate(
        raw,
        now=1000.0,
        message_id=message_id,
    )
    original_events = _events(tmp_path)

    restarted = _controller(tmp_path)
    replayed = await restarted.handle_candidate(
        raw,
        now=1001.0,
        message_id=message_id,
        replayed=True,
    )

    assert replayed == first
    assert _events(tmp_path) == original_events
    assert len(_receipts(tmp_path)) == 1
    assert len(restarted.snapshot(now=1001.0)["intents"]) == 1


@pytest.mark.asyncio
async def test_message_id_payload_conflict_fails_closed_and_halts(tmp_path):
    controller = _controller(tmp_path)
    original = _candidate()
    changed = {**original, "entry_ref": 1.01}
    accepted = await controller.handle_candidate(
        original,
        now=1000.0,
        message_id="msg-conflict",
    )

    conflicted = await controller.handle_candidate(
        changed,
        now=1001.0,
        message_id="msg-conflict",
    )

    events = _events(tmp_path)
    assert accepted.accepted is True
    assert conflicted.accepted is False
    assert conflicted.reason == "message_identity_conflict"
    assert conflicted.intent_id is None
    assert conflicted.episode_id is None
    assert len(_receipts(tmp_path)) == 1
    assert len([row for row in events if row["event_type"] == "intent_created"]) == 1
    halt = controller.snapshot(now=1001.0)["integrity_halt"]
    assert halt["reason"] == "message_identity_conflict"
    assert halt["evidence"] == {
        "message_id": "msg-conflict",
        "stored_payload_hash": _payload_hash(original),
        "incoming_payload_hash": _payload_hash(changed),
    }
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["integrity_halt"] == halt


@pytest.mark.asyncio
async def test_repeated_receipt_message_identity_conflict_records_one_halt(tmp_path):
    controller = _controller(tmp_path)
    original = _candidate(candidate_id="repeat-receipt-conflict")
    changed = {**original, "entry_ref": 1.01}
    await controller.handle_candidate(
        original,
        now=1000.0,
        message_id="msg-repeat-receipt-conflict",
    )
    controller._refresh_status = MagicMock()

    results = [
        await controller.handle_candidate(
            changed,
            now=1001.0 + index,
            message_id="msg-repeat-receipt-conflict",
        )
        for index in range(3)
    ]

    assert [result.reason for result in results] == [
        "message_identity_conflict",
    ] * 3
    halt_events = [
        event
        for event in _events(tmp_path)
        if event["event_type"] == "governor_integrity_halted"
    ]
    assert len(halt_events) == 1
    assert controller._refresh_status.call_count == 1
    assert halt_events[0]["data"]["evidence"] == {
        "message_id": "msg-repeat-receipt-conflict",
        "stored_payload_hash": _payload_hash(original),
        "incoming_payload_hash": _payload_hash(changed),
    }


@pytest.mark.asyncio
async def test_repeated_gap_message_identity_conflict_records_one_halt(
    tmp_path,
    monkeypatch,
):
    controller = _controller(tmp_path)
    original = _candidate(candidate_id="repeat-gap-conflict")
    changed = {**original, "entry_ref": 1.01}
    original_append = controller.store.append

    def fail_receipt_append(event_type, data, **kwargs):
        if event_type == "candidate_handled":
            raise OSError("injected receipt gap")
        return original_append(event_type, data, **kwargs)

    monkeypatch.setattr(controller.store, "append", fail_receipt_append)
    with pytest.raises(OSError, match="injected receipt gap"):
        await controller.handle_candidate(
            original,
            now=1000.0,
            message_id="msg-repeat-gap-conflict",
        )
    monkeypatch.setattr(controller.store, "append", original_append)
    controller._refresh_status = MagicMock()

    results = [
        await controller.handle_candidate(
            changed,
            now=1001.0 + index,
            message_id="msg-repeat-gap-conflict",
        )
        for index in range(3)
    ]

    assert [result.reason for result in results] == [
        "message_identity_conflict",
    ] * 3
    assert len([
        event
        for event in _events(tmp_path)
        if event["event_type"] == "governor_integrity_halted"
    ]) == 1
    assert controller._refresh_status.call_count == 1


@pytest.mark.asyncio
async def test_message_identity_conflict_preserves_existing_protection_halt(tmp_path):
    controller = _controller(tmp_path)
    original = _candidate(candidate_id="protected-conflict")
    changed = {**original, "entry_ref": 1.01}
    await controller.handle_candidate(
        original,
        now=1000.0,
        message_id="msg-protected-conflict",
    )
    controller.governor.activate_integrity_halt(
        "tactical_protection_incomplete",
        evidence={"intent_id": "protected-intent"},
    )
    original_halt = controller.snapshot(now=1000.0)["integrity_halt"]
    events_before_conflict = _events(tmp_path)

    for index in range(3):
        result = await controller.handle_candidate(
            changed,
            now=1001.0 + index,
            message_id="msg-protected-conflict",
        )
        assert result.reason == "message_identity_conflict"

    assert controller.snapshot(now=1004.0)["integrity_halt"] == original_halt
    assert _events(tmp_path) == events_before_conflict


@pytest.mark.asyncio
async def test_message_identity_conflict_halts_once_after_prior_safety_halt_clears(
    tmp_path,
):
    controller = _controller(tmp_path)
    original = _candidate(candidate_id="conflict-after-safety-clear")
    changed = {**original, "entry_ref": 1.01}
    await controller.handle_candidate(
        original,
        now=1000.0,
        message_id="msg-conflict-after-safety-clear",
    )
    controller.governor.activate_integrity_halt(
        "tactical_protection_incomplete",
        evidence={"intent_id": "protected-intent"},
    )
    await controller.handle_candidate(
        changed,
        now=1001.0,
        message_id="msg-conflict-after-safety-clear",
    )
    assert controller.governor.clear_integrity_halt(
        "protection-reconciled",
        _integrity_proof(),
    ) is True

    for index in range(2):
        result = await controller.handle_candidate(
            changed,
            now=1002.0 + index,
            message_id="msg-conflict-after-safety-clear",
        )
        assert result.reason == "message_identity_conflict"

    assert controller.governor.integrity_halt["reason"] == (
        "message_identity_conflict"
    )
    assert len([
        event
        for event in _events(tmp_path)
        if event["event_type"] == "governor_integrity_halted"
    ]) == 2


@pytest.mark.asyncio
async def test_message_identity_conflict_incident_stays_deduplicated_after_restart(
    tmp_path,
):
    controller = _controller(tmp_path)
    original = _candidate(candidate_id="restart-deduplicated-conflict")
    changed = {**original, "entry_ref": 1.01}
    await controller.handle_candidate(
        original,
        now=1000.0,
        message_id="msg-restart-deduplicated-conflict",
    )
    await controller.handle_candidate(
        changed,
        now=1001.0,
        message_id="msg-restart-deduplicated-conflict",
    )
    assert controller.governor.clear_integrity_halt(
        "message-conflict-reconciled",
        _integrity_proof(),
    ) is True
    events_before_restart = _events(tmp_path)

    restarted = _controller(tmp_path)
    redelivered = await restarted.handle_candidate(
        changed,
        now=1002.0,
        message_id="msg-restart-deduplicated-conflict",
    )

    assert redelivered.reason == "message_identity_conflict"
    assert restarted.snapshot(now=1002.0)["integrity_halt"] is None
    assert _events(tmp_path) == events_before_restart


@pytest.mark.asyncio
async def test_replay_without_receipt_or_intent_records_one_durable_gap(tmp_path):
    controller = _controller(tmp_path)

    result = await controller.handle_candidate(
        _candidate(),
        now=1000.0,
        message_id="msg-unknown",
        replayed=True,
    )

    assert result.reason == "unknown_handling_evidence"
    assert result.intent_id is None
    assert result.episode_id is None
    assert [event["event_type"] for event in _events(tmp_path)] == [
        "candidate_handling_gap_recorded",
    ]
    assert controller.snapshot(now=1000.0)["intents"] == []
    assert controller.snapshot(now=1000.0)["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }


@pytest.mark.asyncio
async def test_unknown_replay_identity_stays_unknown_for_normal_delivery_and_restart(
    tmp_path,
):
    raw = _candidate(candidate_id="sticky-unknown")
    controller = _controller(tmp_path)

    replayed = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-sticky-unknown",
        replayed=True,
    )
    events_after_unknown = _events(tmp_path)
    normal = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-sticky-unknown",
    )
    restarted = _controller(tmp_path)
    after_restart = await restarted.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-sticky-unknown",
    )

    assert replayed.reason == "unknown_handling_evidence"
    assert normal == replayed
    assert after_restart == replayed
    assert [event["event_type"] for event in events_after_unknown] == [
        "candidate_handling_gap_recorded",
    ]
    assert _events(tmp_path) == events_after_unknown
    assert not _receipts(tmp_path)
    assert controller.snapshot(now=1002.0)["intents"] == []
    assert restarted.snapshot(now=1002.0)["intents"] == []


@pytest.mark.asyncio
async def test_intent_append_failure_records_durable_gap_before_memory(
    tmp_path,
    monkeypatch,
):
    controller = _controller(tmp_path)
    raw = _candidate(candidate_id="intent-append-gap")
    original_append = controller.store.append
    failed_once = False

    def fail_intent_once(event_type, data, **kwargs):
        nonlocal failed_once
        if event_type == "intent_created" and not failed_once:
            failed_once = True
            raise OSError("injected intent append failure")
        return original_append(event_type, data, **kwargs)

    monkeypatch.setattr(controller.store, "append", fail_intent_once)
    with pytest.raises(OSError, match="injected intent append failure"):
        await controller.handle_candidate(
            raw,
            now=1000.0,
            message_id="msg-intent-append-gap",
        )
    monkeypatch.setattr(controller.store, "append", original_append)

    events_after_failure = _events(tmp_path)
    assert [event["event_type"] for event in events_after_failure] == [
        "episode_assigned",
        "candidate_handling_gap_recorded",
    ]
    assert controller.snapshot(now=1001.0)["intents"] == []
    same_process = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-intent-append-gap",
    )
    restarted = _controller(tmp_path)
    after_restart = await restarted.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-intent-append-gap",
    )

    assert same_process.reason == "unknown_handling_evidence"
    assert after_restart == same_process
    assert _events(tmp_path) == events_after_failure
    assert not _receipts(tmp_path)
    assert restarted.snapshot(now=1002.0)["intents"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "mode"),
    [
        ({"namespace": "testnet", "candidate_id": "invalid"}, "shadow"),
        (_candidate(candidate_id="expired", created_at=99.0), "shadow"),
        (_candidate(candidate_id="disabled"), "off"),
    ],
)
async def test_replay_without_receipt_is_unknown_before_validation_ttl_or_mode(
    tmp_path,
    raw,
    mode,
):
    controller = _controller(tmp_path, mode=mode)
    result = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id=f"msg-{raw['candidate_id']}",
        replayed=True,
    )

    assert result.reason == "unknown_handling_evidence"
    assert result.intent_id is None
    assert result.episode_id is None
    assert [event["event_type"] for event in _events(tmp_path)] == [
        "candidate_handling_gap_recorded",
    ]
    assert controller.snapshot(now=1000.0)["candidate_handling"][
        "unknown_handling_evidence"
    ] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", ["msg-repeat-unknown", None])
async def test_repeated_unknown_replay_identity_is_counted_once(tmp_path, message_id):
    controller = _controller(tmp_path)
    raw = _candidate(candidate_id="repeat-unknown")

    first = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id=message_id,
        replayed=True,
    )
    second = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id=message_id,
        replayed=True,
    )

    assert second == first
    assert [event["event_type"] for event in _events(tmp_path)] == [
        "candidate_handling_gap_recorded",
    ]
    assert controller.snapshot(now=1001.0)["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }


@pytest.mark.asyncio
async def test_legacy_intent_without_receipt_remains_unknown_and_is_not_synthesized(tmp_path):
    from utils.tactical_v2.episodes import EpisodeRegistry
    from utils.tactical_v2.models import TacticalCandidate, TacticalIntent
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    store = TacticalStore(paths)
    raw = _candidate()
    candidate = TacticalCandidate.from_raw(raw)
    assignment = EpisodeRegistry(store, namespace="testnet").assign(candidate, raw)
    intent = TacticalIntent.from_candidate(candidate, assignment.episode_id)
    store.append(
        "intent_created",
        {
            "intent_id": intent.intent_id,
            "episode_id": intent.episode_id,
            "intent": asdict(intent),
            "state": "ready_for_quote",
            "lane": "shadow",
            "shadow_state": None,
            "terminal_reason": None,
            "replayed": False,
            "updated_at": 1000.0,
        },
        emitted_at=1000.0,
    )
    legacy_events = _events(tmp_path)

    controller = _controller(tmp_path)
    snapshot = controller.snapshot(now=1001.0)
    replayed = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id="legacy-msg",
        replayed=True,
    )

    assert snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    assert snapshot["intents"][0]["handling_evidence"] == "unknown_handling_evidence"
    assert replayed.reason == "unknown_handling_evidence"
    assert replayed.intent_id == intent.intent_id
    assert _events(tmp_path) == legacy_events
    after_replay = controller.snapshot(now=1001.0)
    assert after_replay["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["candidate_handling"] == after_replay["candidate_handling"]


@pytest.mark.asyncio
async def test_normal_redelivery_after_receipt_append_failure_remains_unknown(
    tmp_path,
    monkeypatch,
):
    controller = _controller(tmp_path)
    raw = _candidate(candidate_id="receipt-append-failure")
    original_append = controller.store.append

    def fail_receipt_append(event_type, data, **kwargs):
        if event_type == "candidate_handled":
            raise OSError("injected candidate receipt append failure")
        return original_append(event_type, data, **kwargs)

    monkeypatch.setattr(controller.store, "append", fail_receipt_append)
    with pytest.raises(OSError, match="injected candidate receipt append failure"):
        await controller.handle_candidate(
            raw,
            now=1000.0,
            message_id="msg-receipt-append-failure",
        )
    monkeypatch.setattr(controller.store, "append", original_append)
    events_after_failure = _events(tmp_path)
    created = next(
        row for row in events_after_failure if row["event_type"] == "intent_created"
    )

    same_process = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-receipt-append-failure",
    )
    assert same_process.reason == "unknown_handling_evidence"
    assert same_process.intent_id == created["data"]["intent_id"]
    assert same_process.episode_id == created["data"]["episode_id"]
    assert _events(tmp_path) == events_after_failure

    restarted = _controller(tmp_path)
    after_restart = await restarted.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-receipt-append-failure",
    )

    assert after_restart == same_process
    assert _events(tmp_path) == events_after_failure
    assert len(_receipts(tmp_path)) == 0
    assert len(restarted.snapshot(now=1002.0)["intents"]) == 1
    assert restarted.snapshot(now=1002.0)["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }


@pytest.mark.asyncio
async def test_governor_rejection_receipt_gap_remains_unknown_after_restart(
    tmp_path,
    monkeypatch,
):
    controller = _controller(tmp_path)
    raw = _candidate(candidate_id="governor-receipt-gap")
    controller.governor.can_open = lambda **kwargs: SimpleNamespace(
        allowed=False,
        reason="account_reject",
    )
    original_append = controller.store.append

    def fail_receipt_append(event_type, data, **kwargs):
        if event_type == "candidate_handled":
            raise OSError("injected governor receipt append failure")
        return original_append(event_type, data, **kwargs)

    monkeypatch.setattr(controller.store, "append", fail_receipt_append)
    with pytest.raises(OSError, match="injected governor receipt append failure"):
        await controller.handle_candidate(
            raw,
            now=1000.0,
            message_id="msg-governor-receipt-gap",
        )
    monkeypatch.setattr(controller.store, "append", original_append)
    events_after_failure = _events(tmp_path)
    terminal = events_after_failure[-1]
    episode_id = terminal["data"]["episode_id"]

    assert [row["event_type"] for row in events_after_failure] == [
        "episode_assigned",
        "episode_terminal",
    ]
    assert terminal["data"]["evidence"] == {
        "reason": "account_reject",
        "candidate_handling_gap": {
            "candidate_id": "governor-receipt-gap",
            "message_id": "msg-governor-receipt-gap",
            "payload_hash": _payload_hash(raw),
        },
    }
    assert len(_receipts(tmp_path)) == 0

    same_process = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-governor-receipt-gap",
    )
    assert same_process.reason == "unknown_handling_evidence"
    assert same_process.intent_id is None
    assert same_process.episode_id == episode_id
    assert _events(tmp_path) == events_after_failure

    restarted = _controller(tmp_path)
    after_restart = await restarted.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-governor-receipt-gap",
    )

    assert after_restart == same_process
    assert _events(tmp_path) == events_after_failure
    assert len(_receipts(tmp_path)) == 0
    assert restarted.snapshot(now=1002.0)["intents"] == []
    assert restarted.snapshot(now=1002.0)["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "invalid",
        "future",
        "expired",
        "disabled",
        "duplicate",
        "opposing",
    ],
)
async def test_all_rejection_receipt_append_failures_remain_unknown(
    tmp_path,
    monkeypatch,
    scenario,
):
    mode = "off" if scenario == "disabled" else "shadow"
    controller = _controller(tmp_path, mode=mode)
    raw = _candidate(candidate_id=f"receipt-gap-{scenario}")
    first_episode_id = None
    handled_at = 1000.0
    redelivered_at = 1001.0
    expected_reason = scenario

    if scenario == "invalid":
        raw["side"] = "invalid"
        expected_reason = "invalid_candidate"
    elif scenario == "future":
        raw["created_at"] = 1001.0
        expected_reason = "candidate_from_future"
    elif scenario == "expired":
        raw["created_at"] = 0.0
        expected_reason = "candidate_expired"
    elif scenario == "disabled":
        expected_reason = "admission_disabled"
    else:
        first = await controller.handle_candidate(
            _candidate(candidate_id=f"receipt-gap-{scenario}-seed"),
            now=1000.0,
            message_id=f"msg-receipt-gap-{scenario}-seed",
        )
        first_episode_id = first.episode_id
        if scenario == "opposing":
            raw["tf_15m_block_long"] = True
            expected_reason = "opposing_block"
        else:
            expected_reason = "duplicate_episode"

    original_append = controller.store.append
    failed_receipts = []

    def fail_receipt_append(event_type, data, **kwargs):
        if event_type == "candidate_handled":
            failed_receipts.append(dict(data))
            raise OSError(f"injected {scenario} receipt append failure")
        return original_append(event_type, data, **kwargs)

    message_id = f"msg-receipt-gap-{scenario}"
    monkeypatch.setattr(controller.store, "append", fail_receipt_append)
    with pytest.raises(OSError, match=f"injected {scenario} receipt append failure"):
        await controller.handle_candidate(
            raw,
            now=handled_at,
            message_id=message_id,
        )
    monkeypatch.setattr(controller.store, "append", original_append)
    events_after_failure = _events(tmp_path)
    receipt_count_after_failure = len(_receipts(tmp_path))
    assert [receipt["reason"] for receipt in failed_receipts] == [expected_reason]

    same_process = await controller.handle_candidate(
        raw,
        now=redelivered_at,
        message_id=message_id,
    )
    restarted = _controller(tmp_path, mode=mode)
    replayed_after_restart = await restarted.handle_candidate(
        raw,
        now=redelivered_at,
        message_id=message_id,
        replayed=True,
    )

    assert same_process.reason == "unknown_handling_evidence"
    assert replayed_after_restart.reason == "unknown_handling_evidence"
    assert same_process.intent_id is None
    assert replayed_after_restart.intent_id is None
    assert same_process.episode_id == first_episode_id
    assert replayed_after_restart.episode_id == first_episode_id
    assert _events(tmp_path) == events_after_failure
    assert len(_receipts(tmp_path)) == receipt_count_after_failure


@pytest.mark.asyncio
async def test_receipt_hit_still_returns_original_rejection_without_new_event(tmp_path):
    raw = {**_candidate(), "side": "invalid"}
    first_controller = _controller(tmp_path)
    first = await first_controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-invalid-receipt",
    )
    original_events = _events(tmp_path)

    restarted = _controller(tmp_path)
    replayed = await restarted.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-invalid-receipt",
        replayed=True,
    )

    assert replayed == first
    assert replayed.reason == "invalid_candidate"
    assert _events(tmp_path) == original_events


@pytest.mark.asyncio
async def test_restored_receipt_with_string_accepted_is_not_authoritative(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="malformed-bool")
    store = TacticalStore(_paths(tmp_path))
    intent = _append_intent_created(store, raw, episode_id="episode-malformed-bool")
    store.append(
        "candidate_handled",
        _candidate_receipt(
            raw,
            intent,
            message_id="msg-malformed-bool",
            accepted="false",
        ),
        emitted_at=1000.0,
    )

    controller = _controller(tmp_path)
    replayed = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-malformed-bool",
        replayed=True,
    )
    snapshot = controller.snapshot(now=1001.0)

    assert replayed.reason == "unknown_handling_evidence"
    assert replayed.intent_id == intent.intent_id
    assert snapshot["intents"][0]["handling_evidence"] == "unknown_handling_evidence"
    assert snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    assert snapshot["integrity_halt"]["reason"] == "candidate_receipt_invalid"
    assert snapshot["integrity_halt"]["evidence"]["message_id"] == (
        "msg-malformed-bool"
    )


@pytest.mark.parametrize(
    ("receipt_overrides", "expected_error"),
    [
        ({"payload_hash": "not-a-sha256"}, "payload_hash_type"),
        ({"intent_id": "arbitrary-intent"}, "accepted_intent_missing"),
    ],
)
def test_restored_receipt_with_bad_hash_or_arbitrary_intent_is_quarantined(
    tmp_path,
    receipt_overrides,
    expected_error,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id=f"malformed-{expected_error}")
    store = TacticalStore(_paths(tmp_path))
    intent = _append_intent_created(store, raw, episode_id=f"episode-{expected_error}")
    store.append(
        "candidate_handled",
        _candidate_receipt(
            raw,
            intent,
            message_id=f"msg-{expected_error}",
            **receipt_overrides,
        ),
        emitted_at=1000.0,
    )

    snapshot = _controller(tmp_path).snapshot(now=1001.0)

    assert snapshot["intents"][0]["handling_evidence"] == "unknown_handling_evidence"
    assert snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    assert snapshot["integrity_halt"]["reason"] == "candidate_receipt_invalid"
    assert snapshot["integrity_halt"]["evidence"]["validation_error"] == expected_error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "episode_id",
    [pytest.param(None, id="missing"), pytest.param("", id="blank")],
)
async def test_duplicate_episode_receipt_without_episode_id_is_quarantined(
    tmp_path,
    episode_id,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="malformed-duplicate-episode")
    store = TacticalStore(_paths(tmp_path))
    store.append(
        "candidate_handled",
        {
            "candidate_id": raw["candidate_id"],
            "source_shadow_id": raw["source_shadow_id"],
            "message_id": "msg-malformed-duplicate-episode",
            "symbol": raw["symbol"],
            "side": raw["side"],
            "accepted": False,
            "reason": "duplicate_episode",
            "episode_id": episode_id,
            "intent_id": None,
            "evaluated_at": 1000.0,
            "replayed": False,
            "payload_hash": _payload_hash(raw),
        },
        emitted_at=1000.0,
    )

    controller = _controller(tmp_path)
    replayed = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-malformed-duplicate-episode",
        replayed=True,
    )
    snapshot = controller.snapshot(now=1001.0)

    assert replayed.reason == "unknown_handling_evidence"
    assert replayed.intent_id is None
    assert replayed.episode_id is None
    assert snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    assert snapshot["integrity_halt"]["reason"] == "candidate_receipt_invalid"
    assert snapshot["integrity_halt"]["evidence"]["validation_error"] == (
        "duplicate_episode_episode_id"
    )
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["candidate_handling"] == snapshot["candidate_handling"]
    assert status["integrity_halt"] == snapshot["integrity_halt"]
    assert len(_receipts(tmp_path)) == 1


@pytest.mark.parametrize("history_kind", ["malformed", "conflicting"])
def test_receipt_quarantine_restore_is_event_log_read_only(tmp_path, history_kind):
    from utils.tactical_v2.store import TacticalStore

    store = TacticalStore(_paths(tmp_path))
    raw = _candidate(candidate_id=f"restore-read-only-{history_kind}")
    base_receipt = {
        "candidate_id": raw["candidate_id"],
        "source_shadow_id": raw["source_shadow_id"],
        "message_id": f"msg-restore-read-only-{history_kind}",
        "symbol": raw["symbol"],
        "side": raw["side"],
        "accepted": False,
        "reason": "invalid_candidate",
        "episode_id": None,
        "intent_id": None,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": _payload_hash(raw),
    }
    if history_kind == "malformed":
        store.append(
            "candidate_handled",
            {**base_receipt, "accepted": "false"},
            emitted_at=1000.0,
        )
    else:
        store.append("candidate_handled", base_receipt, emitted_at=1000.0)
        store.append(
            "candidate_handled",
            {
                **base_receipt,
                "candidate_id": "restore-read-only-conflict-other",
                "payload_hash": _payload_hash({**raw, "candidate_id": "other"}),
            },
            emitted_at=1001.0,
        )
    original_events = _events(tmp_path)

    first = _controller(tmp_path)
    after_first_restore = _events(tmp_path)
    second = _controller(tmp_path)

    assert after_first_restore == original_events
    assert _events(tmp_path) == original_events
    assert first.snapshot(now=1002.0)["integrity_halt"]["reason"].startswith(
        "candidate_receipt_"
    )
    assert second.snapshot(now=1002.0)["integrity_halt"] == first.snapshot(
        now=1002.0
    )["integrity_halt"]
    assert first.snapshot(now=1002.0)["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    assert second.snapshot(now=1002.0)["candidate_handling"] == first.snapshot(
        now=1002.0
    )["candidate_handling"]


@pytest.mark.asyncio
@pytest.mark.parametrize("history_kind", ["malformed", "conflicting"])
async def test_quarantined_candidate_identity_cannot_regain_authority(
    tmp_path,
    history_kind,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id=f"blocked-quarantine-{history_kind}")
    message_id = f"msg-blocked-quarantine-{history_kind}"
    store = TacticalStore(_paths(tmp_path))
    first_receipt = _rejected_candidate_receipt(raw, message_id=message_id)
    if history_kind == "malformed":
        store.append(
            "candidate_handled",
            {**first_receipt, "accepted": "false"},
            emitted_at=1000.0,
        )
    else:
        store.append("candidate_handled", first_receipt, emitted_at=1000.0)
        store.append(
            "candidate_handled",
            {
                **first_receipt,
                "candidate_id": f"{raw['candidate_id']}-conflict",
                "payload_hash": _payload_hash({**raw, "extra": "conflict"}),
            },
            emitted_at=1001.0,
        )

    controller = _controller(tmp_path)
    original_events = _events(tmp_path)
    untrusted_redelivery = {**raw, "extra": float("nan")}
    deliveries = [
        (raw, message_id, False),
        (untrusted_redelivery, message_id, True),
        (raw, None, False),
        (raw, None, True),
    ]
    results = [
        await controller.handle_candidate(
            payload,
            now=1002.0 + index,
            message_id=delivery_message_id,
            replayed=replayed,
        )
        for index, (payload, delivery_message_id, replayed) in enumerate(deliveries)
    ]

    assert {result.reason for result in results} == {"unknown_handling_evidence"}
    assert _events(tmp_path) == original_events
    assert controller.snapshot(now=1006.0)["intents"] == []

    restarted = _controller(tmp_path)
    restarted_result = await restarted.handle_candidate(
        raw,
        now=1007.0,
        message_id=message_id,
    )
    assert restarted_result.reason == "unknown_handling_evidence"
    assert _events(tmp_path) == original_events


@pytest.mark.parametrize(
    ("reconciliation_id", "proof"),
    [
        ("", _integrity_proof()),
        ("receipt-reconcile-invalid", _integrity_proof(protection=False)),
        ("receipt-reconcile-invalid", {"ownership": True}),
        ("receipt-reconcile-invalid", None),
    ],
)
def test_invalid_candidate_receipt_integrity_acknowledgement_is_rejected(
    tmp_path,
    reconciliation_id,
    proof,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="invalid-receipt-ack")
    store = TacticalStore(_paths(tmp_path))
    store.append(
        "candidate_handled",
        _invalid_candidate_receipt(raw, message_id="msg-invalid-receipt-ack"),
        emitted_at=1000.0,
    )
    controller = _controller(tmp_path)
    halt_before = controller.snapshot(now=1001.0)["integrity_halt"]
    events_before = _events(tmp_path)

    acknowledged = controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id=halt_before["incident_id"],
        reconciliation_id=reconciliation_id,
        proof=proof,
    )

    assert acknowledged is False
    assert _events(tmp_path) == events_before
    assert controller.snapshot(now=1001.0)["integrity_halt"] == halt_before


@pytest.mark.asyncio
async def test_valid_candidate_receipt_integrity_acknowledgement_unblocks_admission(
    tmp_path,
):
    from utils.tactical_v2.store import TacticalStore

    malformed = _candidate(candidate_id="valid-receipt-ack-malformed")
    store = TacticalStore(_paths(tmp_path))
    source_event = store.append(
        "candidate_handled",
        _invalid_candidate_receipt(
            malformed,
            message_id="msg-valid-receipt-ack-malformed",
        ),
        emitted_at=1000.0,
    )
    controller = _controller(tmp_path)
    incident = controller.snapshot(now=1001.0)["integrity_halt"]
    proof = _integrity_proof(operator="alice", ticket="INC-42")

    acknowledged = controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id=incident["incident_id"],
        reconciliation_id="receipt-reconcile-valid",
        proof=proof,
    )
    admitted = await controller.handle_candidate(
        _candidate(
            symbol="XRP-USDT",
            candidate_id="candidate-after-receipt-ack",
        ),
        now=1001.0,
        message_id="msg-candidate-after-receipt-ack",
    )

    assert acknowledged is True
    assert admitted.accepted is True
    assert controller.snapshot(now=1001.0)["integrity_halt"] is None
    acknowledgements = [
        event
        for event in _events(tmp_path)
        if event["event_type"] == "candidate_receipt_integrity_acknowledged"
    ]
    assert len(acknowledgements) == 1
    assert acknowledgements[0]["data"] == {
        "reconciliation_id": "receipt-reconcile-valid",
        "incident_id": incident["incident_id"],
        "proof": proof,
        "acknowledged_at": 1000.0,
    }
    assert incident["incident_id"] == source_event["event_id"]
    assert set(_receipts(tmp_path)[-1]["data"]) == RECEIPT_FIELDS


@pytest.mark.asyncio
async def test_candidate_receipt_integrity_acknowledgement_survives_restart_and_keeps_quarantine(
    tmp_path,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="restart-receipt-ack")
    store = TacticalStore(_paths(tmp_path))
    store.append(
        "candidate_handled",
        _invalid_candidate_receipt(raw, message_id="msg-restart-receipt-ack"),
        emitted_at=1000.0,
    )
    controller = _controller(tmp_path)
    incident = controller.snapshot(now=1001.0)["integrity_halt"]
    assert controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id=incident["incident_id"],
        reconciliation_id="receipt-reconcile-restart",
        proof=_integrity_proof(),
    ) is True

    restarted = _controller(tmp_path)
    snapshot = restarted.snapshot(now=1001.0)
    events_after_ack = _events(tmp_path)
    quarantined_redelivery = await restarted.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-restart-receipt-ack",
    )

    assert snapshot["integrity_halt"] is None
    assert quarantined_redelivery.reason == "unknown_handling_evidence"
    assert _events(tmp_path) == events_after_ack
    assert snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    assert len(_receipts(tmp_path)) == 1
    assert len([
        event
        for event in _events(tmp_path)
        if event["event_type"] == "candidate_receipt_integrity_acknowledged"
    ]) == 1


def test_new_candidate_receipt_corruption_after_acknowledgement_rehalts_on_restart(
    tmp_path,
):
    from utils.tactical_v2.store import TacticalStore

    first_raw = _candidate(candidate_id="receipt-corruption-before-ack")
    store = TacticalStore(_paths(tmp_path))
    first_event = store.append(
        "candidate_handled",
        _invalid_candidate_receipt(
            first_raw,
            message_id="msg-receipt-corruption-before-ack",
        ),
        emitted_at=1000.0,
    )
    controller = _controller(tmp_path)
    assert controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id=first_event["event_id"],
        reconciliation_id="receipt-reconcile-before-new-corruption",
        proof=_integrity_proof(),
    ) is True
    second_raw = _candidate(candidate_id="receipt-corruption-after-ack")
    second_event = controller.store.append(
        "candidate_handled",
        _invalid_candidate_receipt(
            second_raw,
            message_id="msg-receipt-corruption-after-ack",
        ),
        emitted_at=1002.0,
    )

    restarted = _controller(tmp_path)
    snapshot = restarted.snapshot(now=1003.0)

    assert snapshot["integrity_halt"]["reason"] == "candidate_receipt_invalid"
    assert snapshot["integrity_halt"]["incident_id"] == second_event["event_id"]
    assert snapshot["integrity_halt"]["incident_id"] != first_event["event_id"]
    assert snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 2,
    }
    assert _controller(tmp_path).snapshot(now=1003.0)["integrity_halt"] == (
        snapshot["integrity_halt"]
    )


@pytest.mark.parametrize("identity_kind", ["message", "payload"])
def test_known_candidate_receipt_conflict_after_acknowledgement_rehalts(
    tmp_path,
    identity_kind,
):
    from utils.tactical_v2.store import TacticalStore

    store = TacticalStore(_paths(tmp_path))
    if identity_kind == "message":
        message_id = "msg-known-conflict-after-ack"
        receipts = [
            _rejected_candidate_receipt(
                _candidate(candidate_id=f"known-message-conflict-{index}"),
                message_id=message_id,
            )
            for index in range(3)
        ]
    else:
        raw = _candidate(candidate_id="known-payload-conflict")
        receipts = [
            _rejected_candidate_receipt(
                raw,
                message_id=None,
                reason=reason,
            )
            for reason in (
                "invalid_candidate",
                "namespace_mismatch",
                "candidate_from_future",
            )
        ]
    store.append("candidate_handled", receipts[0], emitted_at=1000.0)
    store.append("candidate_handled", receipts[1], emitted_at=1001.0)
    controller = _controller(tmp_path)
    incident = controller.snapshot(now=1002.0)["integrity_halt"]
    assert controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id=incident["incident_id"],
        reconciliation_id=f"known-{identity_kind}-conflict-reconciled",
        proof=_integrity_proof(),
    ) is True
    later_corruption = controller.store.append(
        "candidate_handled",
        receipts[2],
        emitted_at=1003.0,
    )

    restarted = _controller(tmp_path)
    halt = restarted.snapshot(now=1004.0)["integrity_halt"]

    assert halt["reason"] == f"candidate_receipt_{identity_kind}_conflict"
    assert halt["incident_id"] == later_corruption["event_id"]
    assert len(_receipts(tmp_path)) == 3


def test_multiple_receipt_corruptions_require_ordered_acknowledgements(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    store = TacticalStore(_paths(tmp_path))
    first_raw = _candidate(candidate_id="ordered-corruption-first")
    second_raw = _candidate(candidate_id="ordered-corruption-second")
    first_event = store.append(
        "candidate_handled",
        _invalid_candidate_receipt(
            first_raw,
            message_id="msg-ordered-corruption-first",
        ),
        emitted_at=1000.0,
    )
    second_event = store.append(
        "candidate_handled",
        _invalid_candidate_receipt(
            second_raw,
            message_id="msg-ordered-corruption-second",
        ),
        emitted_at=1001.0,
    )
    controller = _controller(tmp_path)

    initial = controller.snapshot(now=1002.0)
    assert initial["integrity_halt"]["incident_id"] == first_event["event_id"]
    assert initial["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 2,
    }
    assert controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id=first_event["event_id"],
        reconciliation_id="ordered-corruption-first-ack",
        proof=_integrity_proof(),
    ) is True
    after_first_ack = controller.snapshot(now=1002.0)
    assert after_first_ack["integrity_halt"]["incident_id"] == (
        second_event["event_id"]
    )
    assert _controller(tmp_path).snapshot(now=1002.0)["integrity_halt"] == (
        after_first_ack["integrity_halt"]
    )
    events_after_first_ack = _events(tmp_path)
    restarted_after_first_ack = _controller(tmp_path)
    assert restarted_after_first_ack.acknowledge_candidate_receipt_integrity(
        expected_incident_id=first_event["event_id"],
        reconciliation_id="ordered-corruption-first-ack",
        proof=_integrity_proof(),
    ) is True
    assert restarted_after_first_ack.snapshot(now=1002.0)["integrity_halt"] == (
        after_first_ack["integrity_halt"]
    )
    assert _events(tmp_path) == events_after_first_ack
    assert controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id=first_event["event_id"],
        reconciliation_id="ordered-corruption-first-ack",
        proof=_integrity_proof(),
    ) is True
    assert controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id=first_event["event_id"],
        reconciliation_id="different-stale-command",
        proof=_integrity_proof(),
    ) is False
    assert controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id="future-or-unknown-incident",
        reconciliation_id="future-or-unknown-command",
        proof=_integrity_proof(),
    ) is False
    assert _events(tmp_path) == events_after_first_ack

    controller.store.append(
        "candidate_receipt_integrity_acknowledged",
        {
            "reconciliation_id": "stale-first-incident-ack",
            "incident_id": first_event["event_id"],
            "proof": _integrity_proof(),
            "acknowledged_at": 1003.0,
        },
        emitted_at=1003.0,
    )
    after_stale_ack = _controller(tmp_path)
    assert after_stale_ack.snapshot(now=1003.0)["integrity_halt"] == (
        after_first_ack["integrity_halt"]
    )
    assert after_stale_ack.acknowledge_candidate_receipt_integrity(
        expected_incident_id=second_event["event_id"],
        reconciliation_id="ordered-corruption-second-ack",
        proof=_integrity_proof(),
    ) is True
    assert after_stale_ack.snapshot(now=1004.0)["integrity_halt"] is None

    final_restart = _controller(tmp_path)
    assert final_restart.snapshot(now=1004.0)["integrity_halt"] is None
    assert final_restart.snapshot(now=1004.0)["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 2,
    }
    assert len(_receipts(tmp_path)) == 2


def test_duplicate_receipt_corruption_event_identity_is_queued_once(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="duplicate-corruption-event-id")
    store = TacticalStore(_paths(tmp_path))
    receipt = _invalid_candidate_receipt(
        raw,
        message_id="msg-duplicate-corruption-event-id",
    )
    for emitted_at in (1000.0, 1001.0):
        store.append(
            "candidate_handled",
            receipt,
            emitted_at=emitted_at,
            event_id="duplicate-corruption-event-id",
        )
    controller = _controller(tmp_path)

    assert controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id="duplicate-corruption-event-id",
        reconciliation_id="duplicate-corruption-event-id-ack",
        proof=_integrity_proof(),
    ) is True
    assert controller.snapshot(now=1002.0)["integrity_halt"] is None
    assert _controller(tmp_path).snapshot(now=1002.0)["integrity_halt"] is None
    assert len(_receipts(tmp_path)) == 2


def test_candidate_receipt_acknowledgement_does_not_clear_governor_halt(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="receipt-ack-with-governor-halt")
    store = TacticalStore(_paths(tmp_path))
    receipt_event = store.append(
        "candidate_handled",
        _invalid_candidate_receipt(
            raw,
            message_id="msg-receipt-ack-with-governor-halt",
        ),
        emitted_at=1000.0,
    )
    controller = _controller(tmp_path)
    controller.governor.activate_integrity_halt(
        "tactical_protection_incomplete",
        evidence={"intent_id": "protected-intent"},
    )
    governor_halt = controller.governor.integrity_halt

    assert controller.acknowledge_candidate_receipt_integrity(
        expected_incident_id=receipt_event["event_id"],
        reconciliation_id="receipt-reconcile-independent-governor-halt",
        proof=_integrity_proof(),
    ) is True

    assert controller.governor.integrity_halt == governor_halt
    assert controller.snapshot(now=1001.0)["integrity_halt"] == governor_halt
    restarted = _controller(tmp_path)
    assert restarted.governor.integrity_halt == governor_halt
    assert restarted.snapshot(now=1001.0)["integrity_halt"] == governor_halt
    assert not any(
        event["event_type"] == "governor_integrity_cleared"
        for event in _events(tmp_path)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "episode_id", "expected_error"),
    [
        *[
            (reason, "fabricated-episode", "pre_assignment_episode_id")
            for reason in (
                "invalid_candidate",
                "namespace_mismatch",
                "candidate_from_future",
                "candidate_expired",
                "admission_disabled",
            )
        ],
        *[
            (
                reason,
                None,
                (
                    "duplicate_episode_episode_id"
                    if reason == "duplicate_episode"
                    else "episode_reason_episode_id"
                ),
            )
            for reason in (
                "duplicate_episode",
                "opposing_block",
                "capacity_skipped",
                "integrity_halt",
                "loss_streak_pause",
                "rolling_loss_pause",
                "same_symbol_exposure",
                "account_reject",
            )
        ],
        ("unknown_rejection", None, "rejected_reason"),
        ("unknown_rejection", "fabricated-episode", "rejected_reason"),
    ],
)
async def test_restored_rejection_enforces_reason_episode_phase(
    tmp_path,
    reason,
    episode_id,
    expected_error,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id=f"phase-{reason}")
    TacticalStore(_paths(tmp_path)).append(
        "candidate_handled",
        {
            "candidate_id": raw["candidate_id"],
            "source_shadow_id": raw["source_shadow_id"],
            "message_id": f"msg-phase-{reason}",
            "symbol": raw["symbol"],
            "side": raw["side"],
            "accepted": False,
            "reason": reason,
            "episode_id": episode_id,
            "intent_id": None,
            "evaluated_at": 1000.0,
            "replayed": False,
            "payload_hash": _payload_hash(raw),
        },
        emitted_at=1000.0,
    )

    controller = _controller(tmp_path)
    replayed = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id=f"msg-phase-{reason}",
        replayed=True,
    )
    snapshot = controller.snapshot(now=1001.0)

    assert replayed.reason == "unknown_handling_evidence"
    assert snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    assert snapshot["integrity_halt"]["reason"] == "candidate_receipt_invalid"
    assert snapshot["integrity_halt"]["evidence"]["validation_error"] == (
        expected_error
    )
    assert len(_receipts(tmp_path)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", ["msg-episode-reference", None])
@pytest.mark.parametrize("mismatch", ["missing", "symbol", "side"])
async def test_restored_episode_rejection_must_match_real_episode(
    tmp_path,
    message_id,
    mismatch,
):
    from utils.tactical_v2.store import TacticalStore

    seed_raw = _candidate(candidate_id=f"episode-reference-seed-{mismatch}")
    seed = await _controller(tmp_path).handle_candidate(
        seed_raw,
        now=1000.0,
        message_id=f"msg-episode-reference-seed-{mismatch}",
    )
    rejected_raw = _candidate(candidate_id=f"episode-reference-{mismatch}")
    episode_id = seed.episode_id
    if mismatch == "missing":
        episode_id = "never-assigned"
    elif mismatch == "symbol":
        rejected_raw["symbol"] = "ETH-USDT"
    else:
        rejected_raw["side"] = "short"

    TacticalStore(_paths(tmp_path)).append(
        "candidate_handled",
        {
            "candidate_id": rejected_raw["candidate_id"],
            "source_shadow_id": rejected_raw["source_shadow_id"],
            "message_id": message_id,
            "symbol": rejected_raw["symbol"],
            "side": rejected_raw["side"],
            "accepted": False,
            "reason": "duplicate_episode",
            "episode_id": episode_id,
            "intent_id": None,
            "evaluated_at": 1001.0,
            "replayed": False,
            "payload_hash": _payload_hash(rejected_raw),
        },
        emitted_at=1001.0,
    )
    original_events = _events(tmp_path)

    snapshot = _controller(tmp_path).snapshot(now=1002.0)

    assert snapshot["candidate_handling"]["receipt_count"] == 1
    assert snapshot["integrity_halt"]["reason"] == "candidate_receipt_invalid"
    assert snapshot["integrity_halt"]["evidence"]["validation_error"] == (
        "rejected_episode_mismatch"
    )
    assert _events(tmp_path) == original_events


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", ["msg-accepted-episode", None])
@pytest.mark.parametrize("mismatch", ["missing", "symbol", "side"])
async def test_restored_accepted_receipt_must_match_real_episode(
    tmp_path,
    message_id,
    mismatch,
):
    from utils.tactical_v2.store import TacticalStore

    store = TacticalStore(_paths(tmp_path))
    episode_owner = _candidate(candidate_id=f"accepted-episode-owner-{mismatch}")
    if mismatch == "missing":
        episode_id = "fabricated-accepted-episode"
        intent_raw = _candidate(candidate_id="accepted-fabricated-episode")
    else:
        episode_id = _append_episode(store, episode_owner)
        intent_raw = _candidate(
            candidate_id=f"accepted-episode-{mismatch}",
            symbol="ETH-USDT" if mismatch == "symbol" else "WLD-USDT",
            side="short" if mismatch == "side" else "long",
        )
    intent = _append_intent_created(store, intent_raw, episode_id=episode_id)
    store.append(
        "candidate_handled",
        _candidate_receipt(intent_raw, intent, message_id=message_id),
        emitted_at=1000.0,
    )
    original_events = _events(tmp_path)

    first = _controller(tmp_path)
    replayed = await first.handle_candidate(
        intent_raw,
        now=1001.0,
        message_id=message_id,
        replayed=True,
    )
    first_snapshot = first.snapshot(now=1001.0)
    restarted = _controller(tmp_path)

    assert replayed.reason == "unknown_handling_evidence"
    assert replayed.intent_id == intent.intent_id
    assert replayed.episode_id == intent.episode_id
    assert first_snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    assert first_snapshot["integrity_halt"]["reason"] == (
        "candidate_receipt_invalid"
    )
    assert first_snapshot["integrity_halt"]["evidence"]["validation_error"] == (
        "accepted_episode_mismatch"
    )
    assert restarted.snapshot(now=1001.0)["candidate_handling"] == (
        first_snapshot["candidate_handling"]
    )
    assert restarted.snapshot(now=1001.0)["integrity_halt"] == (
        first_snapshot["integrity_halt"]
    )
    assert _events(tmp_path) == original_events


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_kind", ["accepted", "duplicate_episode"])
@pytest.mark.parametrize("episode_order", ["preceding", "future"])
async def test_receipt_episode_reference_is_validated_against_ledger_prefix(
    tmp_path,
    receipt_kind,
    episode_order,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id=f"prefix-{receipt_kind}-{episode_order}")
    reference_path = tmp_path / "episode-reference"
    episode_id = _append_episode(TacticalStore(_paths(reference_path)), raw)
    episode_event = _events(reference_path)[0]
    store = TacticalStore(_paths(tmp_path))
    if episode_order == "preceding":
        store.append(
            "episode_assigned",
            episode_event["data"],
            emitted_at=999.0,
        )
    message_id = f"msg-prefix-{receipt_kind}-{episode_order}"
    if receipt_kind == "accepted":
        intent = _append_intent_created(store, raw, episode_id=episode_id)
        receipt = _candidate_receipt(raw, intent, message_id=message_id)
    else:
        receipt = {
            **_rejected_candidate_receipt(
                raw,
                message_id=message_id,
                reason="duplicate_episode",
            ),
            "episode_id": episode_id,
        }
    store.append("candidate_handled", receipt, emitted_at=1000.0)
    if episode_order == "future":
        store.append(
            "episode_assigned",
            episode_event["data"],
            emitted_at=1001.0,
        )
    original_events = _events(tmp_path)

    controller = _controller(tmp_path)
    replayed = await controller.handle_candidate(
        raw,
        now=1002.0,
        message_id=message_id,
        replayed=True,
    )
    snapshot = controller.snapshot(now=1002.0)

    if episode_order == "preceding":
        assert replayed.reason == receipt["reason"]
        assert snapshot["candidate_handling"]["receipt_count"] == 1
        assert snapshot["integrity_halt"] is None
    else:
        assert replayed.reason == "unknown_handling_evidence"
        assert snapshot["candidate_handling"]["receipt_count"] == 0
        assert snapshot["integrity_halt"]["reason"] == "candidate_receipt_invalid"
        assert snapshot["integrity_halt"]["evidence"]["validation_error"] == (
            "accepted_episode_mismatch"
            if receipt_kind == "accepted"
            else "rejected_episode_mismatch"
        )
    assert _events(tmp_path) == original_events
    assert _controller(tmp_path).snapshot(now=1002.0)["integrity_halt"] == (
        snapshot["integrity_halt"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", ["msg-exact-duplicate", None])
async def test_exact_duplicate_receipt_rows_dedupe_in_read_model_across_restart(
    tmp_path,
    message_id,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="exact-duplicate")
    receipt = {
        "candidate_id": raw["candidate_id"],
        "source_shadow_id": raw["source_shadow_id"],
        "message_id": message_id,
        "symbol": raw["symbol"],
        "side": raw["side"],
        "accepted": False,
        "reason": "invalid_candidate",
        "episode_id": None,
        "intent_id": None,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": _payload_hash(raw),
    }
    store = TacticalStore(_paths(tmp_path))
    store.append("candidate_handled", receipt, emitted_at=1000.0)
    store.append("candidate_handled", receipt, emitted_at=1001.0)
    original_events = _events(tmp_path)

    first = _controller(tmp_path)
    replayed = await first.handle_candidate(
        raw,
        now=1002.0,
        message_id=message_id,
        replayed=True,
    )
    restarted = _controller(tmp_path)

    assert replayed.reason == "invalid_candidate"
    assert first.snapshot(now=1002.0)["candidate_handling"] == {
        "receipt_count": 1,
        "unknown_handling_evidence": 0,
    }
    assert restarted.snapshot(now=1002.0)["candidate_handling"] == {
        "receipt_count": 1,
        "unknown_handling_evidence": 0,
    }
    assert first.snapshot(now=1002.0)["integrity_halt"] is None
    assert restarted.snapshot(now=1002.0)["integrity_halt"] is None
    assert _events(tmp_path) == original_events
    assert len(_receipts(tmp_path)) == 2


@pytest.mark.parametrize("message_id", ["msg-rejected-conflict", None])
def test_conflicting_rejected_receipts_count_one_unknown_across_restart(
    tmp_path,
    message_id,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="rejected-conflict")
    first_receipt = {
        "candidate_id": raw["candidate_id"],
        "source_shadow_id": raw["source_shadow_id"],
        "message_id": message_id,
        "symbol": raw["symbol"],
        "side": raw["side"],
        "accepted": False,
        "reason": "invalid_candidate",
        "episode_id": None,
        "intent_id": None,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": _payload_hash(raw),
    }
    store = TacticalStore(_paths(tmp_path))
    store.append("candidate_handled", first_receipt, emitted_at=1000.0)
    store.append(
        "candidate_handled",
        {**first_receipt, "reason": "admission_disabled"},
        emitted_at=1001.0,
    )
    original_events = _events(tmp_path)

    first = _controller(tmp_path)
    first_snapshot = first.snapshot(now=1002.0)
    restarted = _controller(tmp_path)

    assert first_snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    assert restarted.snapshot(now=1002.0)["candidate_handling"] == (
        first_snapshot["candidate_handling"]
    )
    assert _events(tmp_path) == original_events
    assert len(_receipts(tmp_path)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("event_order", ["message_first", "fallback_first"])
async def test_cross_identity_payload_conflict_quarantines_fallback_across_restart(
    tmp_path,
    event_order,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="cross-identity-conflict")
    payload_hash = _payload_hash(raw)
    message_receipt = {
        "candidate_id": raw["candidate_id"],
        "source_shadow_id": raw["source_shadow_id"],
        "message_id": "msg-cross-identity",
        "symbol": raw["symbol"],
        "side": raw["side"],
        "accepted": False,
        "reason": "invalid_candidate",
        "episode_id": None,
        "intent_id": None,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": payload_hash,
    }
    fallback_receipt = {
        **message_receipt,
        "message_id": None,
        "reason": "admission_disabled",
        "evaluated_at": 1001.0,
        "replayed": True,
    }
    ordered = (
        (message_receipt, fallback_receipt)
        if event_order == "message_first"
        else (fallback_receipt, message_receipt)
    )
    store = TacticalStore(_paths(tmp_path))
    for index, receipt in enumerate(ordered):
        store.append("candidate_handled", receipt, emitted_at=1000.0 + index)
    original_events = _events(tmp_path)

    first = _controller(tmp_path)
    message_replay = await first.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-cross-identity",
        replayed=True,
    )
    fallback_replay = await first.handle_candidate(
        raw,
        now=1002.0,
        message_id=None,
        replayed=True,
    )
    first_snapshot = first.snapshot(now=1002.0)
    restarted = _controller(tmp_path)
    restarted_fallback = await restarted.handle_candidate(
        raw,
        now=1003.0,
        message_id=None,
        replayed=True,
    )

    assert message_replay.reason == "invalid_candidate"
    assert fallback_replay.reason == "unknown_handling_evidence"
    assert restarted_fallback == fallback_replay
    assert first_snapshot["candidate_handling"] == {
        "receipt_count": 1,
        "unknown_handling_evidence": 1,
    }
    assert first_snapshot["integrity_halt"]["reason"] == (
        "candidate_receipt_payload_conflict"
    )
    assert first_snapshot["integrity_halt"]["evidence"] == {
        "payload_hash": payload_hash,
        "message_id": "msg-cross-identity",
        "message_reason": "invalid_candidate",
        "fallback_reason": "admission_disabled",
    }
    assert restarted.snapshot(now=1003.0)["candidate_handling"] == (
        first_snapshot["candidate_handling"]
    )
    assert restarted.snapshot(now=1003.0)["integrity_halt"] == (
        first_snapshot["integrity_halt"]
    )
    assert _events(tmp_path) == original_events
    assert len(_receipts(tmp_path)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("event_order", ["message_first", "fallback_first"])
async def test_cross_identity_equivalent_receipts_ignore_delivery_metadata(
    tmp_path,
    event_order,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="cross-identity-equivalent")
    message_receipt = {
        "candidate_id": raw["candidate_id"],
        "source_shadow_id": raw["source_shadow_id"],
        "message_id": "msg-cross-equivalent",
        "symbol": raw["symbol"],
        "side": raw["side"],
        "accepted": False,
        "reason": "invalid_candidate",
        "episode_id": None,
        "intent_id": None,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": _payload_hash(raw),
    }
    fallback_receipt = {
        **message_receipt,
        "message_id": None,
        "evaluated_at": 1001.0,
        "replayed": True,
    }
    ordered = (
        (message_receipt, fallback_receipt)
        if event_order == "message_first"
        else (fallback_receipt, message_receipt)
    )
    store = TacticalStore(_paths(tmp_path))
    for index, receipt in enumerate(ordered):
        store.append("candidate_handled", receipt, emitted_at=1000.0 + index)
    original_events = _events(tmp_path)

    first = _controller(tmp_path)
    message_replay = await first.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-cross-equivalent",
        replayed=True,
    )
    fallback_replay = await first.handle_candidate(
        raw,
        now=1002.0,
        message_id=None,
        replayed=True,
    )
    restarted = _controller(tmp_path)

    assert message_replay.reason == "invalid_candidate"
    assert fallback_replay.reason == "invalid_candidate"
    assert first.snapshot(now=1002.0)["candidate_handling"] == {
        "receipt_count": 2,
        "unknown_handling_evidence": 0,
    }
    assert restarted.snapshot(now=1002.0)["candidate_handling"] == {
        "receipt_count": 2,
        "unknown_handling_evidence": 0,
    }
    assert first.snapshot(now=1002.0)["integrity_halt"] is None
    assert restarted.snapshot(now=1002.0)["integrity_halt"] is None
    assert _events(tmp_path) == original_events


@pytest.mark.asyncio
async def test_distinct_message_ids_remain_independently_authoritative(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="distinct-message-identities")
    first_receipt = {
        "candidate_id": raw["candidate_id"],
        "source_shadow_id": raw["source_shadow_id"],
        "message_id": "msg-distinct-first",
        "symbol": raw["symbol"],
        "side": raw["side"],
        "accepted": False,
        "reason": "invalid_candidate",
        "episode_id": None,
        "intent_id": None,
        "evaluated_at": 1000.0,
        "replayed": False,
        "payload_hash": _payload_hash(raw),
    }
    second_receipt = {
        **first_receipt,
        "message_id": "msg-distinct-second",
        "reason": "admission_disabled",
        "evaluated_at": 1001.0,
    }
    store = TacticalStore(_paths(tmp_path))
    store.append("candidate_handled", first_receipt, emitted_at=1000.0)
    store.append("candidate_handled", second_receipt, emitted_at=1001.0)
    original_events = _events(tmp_path)

    controller = _controller(tmp_path)
    first = await controller.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-distinct-first",
        replayed=True,
    )
    second = await controller.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-distinct-second",
        replayed=True,
    )

    assert first.reason == "invalid_candidate"
    assert second.reason == "admission_disabled"
    assert controller.snapshot(now=1002.0)["candidate_handling"] == {
        "receipt_count": 2,
        "unknown_handling_evidence": 0,
    }
    assert controller.snapshot(now=1002.0)["integrity_halt"] is None
    assert _events(tmp_path) == original_events


@pytest.mark.asyncio
async def test_payload_fallback_fails_closed_for_ambiguous_message_decisions(
    tmp_path,
):
    raw = _candidate(candidate_id="ambiguous-message-decisions")
    controller = _controller(tmp_path)
    accepted = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-ambiguous-accepted",
    )
    duplicate = await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-ambiguous-duplicate",
    )
    events_before_fallback = _events(tmp_path)

    accepted_replay = await controller.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-ambiguous-accepted",
        replayed=True,
    )
    duplicate_replay = await controller.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-ambiguous-duplicate",
        replayed=True,
    )
    refresh_status = controller._refresh_status
    controller._refresh_status = MagicMock(wraps=refresh_status)
    fallback_replays = [
        await controller.handle_candidate(
            raw,
            now=1002.0 + index,
            message_id=None,
            replayed=True,
        )
        for index in range(3)
    ]

    assert accepted_replay == accepted
    assert duplicate_replay == duplicate
    assert accepted.accepted is True
    assert duplicate.reason == "duplicate_episode"
    assert [result.reason for result in fallback_replays] == [
        "candidate_receipt_payload_ambiguity",
    ] * 3
    assert all(result.accepted is False for result in fallback_replays)
    assert all(result.intent_id is None for result in fallback_replays)
    assert all(result.episode_id is None for result in fallback_replays)
    assert controller._refresh_status.call_count == 1
    assert _events(tmp_path) == events_before_fallback
    snapshot = controller.snapshot(now=1004.0)
    assert snapshot["candidate_handling"] == {
        "receipt_count": 2,
        "unknown_handling_evidence": 0,
    }
    assert snapshot["integrity_halt"]["reason"] == (
        "candidate_receipt_payload_ambiguity"
    )
    assert snapshot["integrity_halt"]["evidence"]["payload_hash"] == (
        _payload_hash(raw)
    )
    assert {
        snapshot["integrity_halt"]["evidence"]["stored_message_id"],
        snapshot["integrity_halt"]["evidence"]["conflicting_message_id"],
    } == {"msg-ambiguous-accepted", "msg-ambiguous-duplicate"}
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["integrity_halt"] == snapshot["integrity_halt"]

    restarted = _controller(tmp_path)
    restarted_accepted = await restarted.handle_candidate(
        raw,
        now=1005.0,
        message_id="msg-ambiguous-accepted",
        replayed=True,
    )
    restarted_duplicate = await restarted.handle_candidate(
        raw,
        now=1005.0,
        message_id="msg-ambiguous-duplicate",
        replayed=True,
    )
    restarted_fallback = await restarted.handle_candidate(
        raw,
        now=1005.0,
        message_id=None,
        replayed=True,
    )

    assert restarted_accepted == accepted
    assert restarted_duplicate == duplicate
    assert restarted_fallback == fallback_replays[0]
    assert restarted.snapshot(now=1005.0)["integrity_halt"] == (
        snapshot["integrity_halt"]
    )
    assert _events(tmp_path) == events_before_fallback
    assert all(set(event["data"]) == RECEIPT_FIELDS for event in _receipts(tmp_path))


@pytest.mark.asyncio
async def test_integrity_halt_snapshot_is_deeply_isolated(tmp_path):
    raw = _candidate(candidate_id="snapshot-deep-copy")
    controller = _controller(tmp_path)
    await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-snapshot-deep-copy-first",
    )
    await controller.handle_candidate(
        raw,
        now=1001.0,
        message_id="msg-snapshot-deep-copy-second",
    )
    await controller.handle_candidate(
        raw,
        now=1002.0,
        message_id=None,
        replayed=True,
    )

    exposed = controller.snapshot(now=1002.0)["integrity_halt"]
    original_reason = exposed["evidence"]["stored_decision"]["reason"]
    exposed["evidence"]["stored_decision"]["reason"] = "caller-mutated"

    assert controller.snapshot(now=1002.0)["integrity_halt"]["evidence"][
        "stored_decision"
    ]["reason"] == original_reason


@pytest.mark.asyncio
async def test_payload_fallback_remains_authoritative_for_equal_message_decisions(
    tmp_path,
):
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="equal-message-decisions")
    store = TacticalStore(_paths(tmp_path))
    episode_id = _append_episode(store, raw)
    intent = _append_intent_created(store, raw, episode_id=episode_id)
    first_receipt = _candidate_receipt(
        raw,
        intent,
        message_id="msg-equal-decision-first",
    )
    second_receipt = {
        **first_receipt,
        "message_id": "msg-equal-decision-second",
        "evaluated_at": 1001.0,
        "replayed": True,
    }
    store.append("candidate_handled", first_receipt, emitted_at=1000.0)
    store.append("candidate_handled", second_receipt, emitted_at=1001.0)
    original_events = _events(tmp_path)

    controller = _controller(tmp_path)
    first = await controller.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-equal-decision-first",
        replayed=True,
    )
    second = await controller.handle_candidate(
        raw,
        now=1002.0,
        message_id="msg-equal-decision-second",
        replayed=True,
    )
    fallback = await controller.handle_candidate(
        raw,
        now=1002.0,
        message_id=None,
        replayed=True,
    )

    assert first.accepted is True
    assert second == first
    assert fallback == first
    assert controller.snapshot(now=1002.0)["integrity_halt"] is None
    restarted = _controller(tmp_path)
    assert await restarted.handle_candidate(
        raw,
        now=1003.0,
        message_id=None,
        replayed=True,
    ) == first
    assert restarted.snapshot(now=1003.0)["integrity_halt"] is None
    assert _events(tmp_path) == original_events
    assert all(set(event["data"]) == RECEIPT_FIELDS for event in _receipts(tmp_path))


def test_conflicting_duplicate_message_receipts_are_both_quarantined(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    store = TacticalStore(_paths(tmp_path))
    first_raw = _candidate(candidate_id="duplicate-message-first")
    first_episode_id = _append_episode(store, first_raw)
    first_intent = _append_intent_created(
        store,
        first_raw,
        episode_id=first_episode_id,
    )
    store.append(
        "candidate_handled",
        _candidate_receipt(
            first_raw,
            first_intent,
            message_id="msg-duplicate-conflict",
        ),
        emitted_at=1000.0,
    )
    second_raw = _candidate(
        symbol="ETH-USDT",
        candidate_id="duplicate-message-second",
    )
    second_episode_id = _append_episode(store, second_raw)
    second_intent = _append_intent_created(
        store,
        second_raw,
        episode_id=second_episode_id,
    )
    store.append(
        "candidate_handled",
        _candidate_receipt(
            second_raw,
            second_intent,
            message_id="msg-duplicate-conflict",
        ),
        emitted_at=1001.0,
    )

    snapshot = _controller(tmp_path).snapshot(now=1002.0)

    assert snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 2,
    }
    assert {
        row["intent_id"]: row["handling_evidence"] for row in snapshot["intents"]
    } == {
        first_intent.intent_id: "unknown_handling_evidence",
        second_intent.intent_id: "unknown_handling_evidence",
    }
    halt = snapshot["integrity_halt"]
    assert halt["reason"] == "candidate_receipt_message_conflict"
    assert halt["evidence"] == {
        "message_id": "msg-duplicate-conflict",
        "stored_payload_hash": _payload_hash(first_raw),
        "conflicting_payload_hash": _payload_hash(second_raw),
    }
    assert len(_receipts(tmp_path)) == 2


@pytest.mark.asyncio
async def test_conflicting_payload_fallback_receipts_are_quarantined_across_restart(
    tmp_path,
):
    from utils.tactical_v2.episodes import EpisodeRegistry
    from utils.tactical_v2.models import TacticalCandidate
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="payload-fallback-conflict")
    store = TacticalStore(_paths(tmp_path))
    assignment = EpisodeRegistry(store, namespace="testnet").assign(
        TacticalCandidate.from_raw(raw),
        {
            "tf_15m_available": True,
            "tf_15m_bias": "bullish",
            "tf_15m_closed_bar_ts": 900.0,
            "tf_15m_structure_token": "payload-fallback-conflict",
            "tf_15m_block_long": False,
            "tf_15m_block_short": False,
        },
    )
    intent = _append_intent_created(
        store,
        raw,
        episode_id=assignment.episode_id,
    )
    accepted = _candidate_receipt(raw, intent, message_id=None)
    rejected = {
        **accepted,
        "accepted": False,
        "reason": "capacity_skipped",
        "intent_id": None,
    }
    store.append("candidate_handled", accepted, emitted_at=1000.0)
    store.append("candidate_handled", rejected, emitted_at=1001.0)

    first = _controller(tmp_path)
    first_snapshot = first.snapshot(now=1002.0)
    events_after_first_restore = _events(tmp_path)

    assert first_snapshot["candidate_handling"] == {
        "receipt_count": 0,
        "unknown_handling_evidence": 1,
    }
    assert first_snapshot["intents"][0]["handling_evidence"] == (
        "unknown_handling_evidence"
    )
    assert first_snapshot["integrity_halt"]["reason"] == (
        "candidate_receipt_payload_conflict"
    )
    assert first_snapshot["integrity_halt"]["evidence"] == {
        "payload_hash": _payload_hash(raw),
        "stored_reason": "accepted",
        "conflicting_reason": "capacity_skipped",
    }
    assert len(_receipts(tmp_path)) == 2

    restarted = _controller(tmp_path)
    replayed = await restarted.handle_candidate(
        raw,
        now=1003.0,
        message_id=None,
        replayed=True,
    )

    assert replayed.reason == "unknown_handling_evidence"
    assert replayed.intent_id == intent.intent_id
    assert restarted.snapshot(now=1003.0)["candidate_handling"] == (
        first_snapshot["candidate_handling"]
    )
    assert restarted.snapshot(now=1003.0)["intents"][0]["handling_evidence"] == (
        "unknown_handling_evidence"
    )
    assert _events(tmp_path) == events_after_first_restore


def _assert_live_governor_rejection_without_intent(tmp_path, result, reason, intent_count):
    events = _events(tmp_path)
    receipt = _receipts(tmp_path)[-1]["data"]
    assert result.reason == reason
    assert result.intent_id is None
    assert receipt["accepted"] is False
    assert receipt["reason"] == reason
    assert receipt["intent_id"] is None
    assert len([row for row in events if row["event_type"] == "intent_created"]) == intent_count
    assert any(
        row["event_type"] == "episode_terminal"
        and row["data"]["registry_state"]["terminal_reason"] == reason
        for row in events
    )


@pytest.mark.asyncio
async def test_live_same_symbol_rejection_does_not_create_terminal_intent(tmp_path):
    controller = _controller(tmp_path, mode="live")
    await controller.handle_candidate(
        _candidate(candidate_id="live-long"),
        now=1000.0,
        message_id="msg-live-long",
    )

    rejected = await controller.handle_candidate(
        _candidate(candidate_id="live-short", side="short"),
        now=1000.0,
        message_id="msg-live-short",
    )

    _assert_live_governor_rejection_without_intent(
        tmp_path,
        rejected,
        "same_symbol_exposure",
        intent_count=1,
    )
    assert len(controller.snapshot(now=1000.0)["intents"]) == 1


@pytest.mark.asyncio
async def test_live_capacity_rejection_does_not_create_terminal_intent(tmp_path):
    controller = _controller(tmp_path, mode="live")
    for index, symbol in enumerate(("WLD-USDT", "ETH-USDT", "SOL-USDT")):
        await controller.handle_candidate(
            _candidate(symbol=symbol, candidate_id=f"live-{index}"),
            now=1000.0,
            message_id=f"msg-live-{index}",
        )

    rejected = await controller.handle_candidate(
        _candidate(symbol="XRP-USDT", candidate_id="live-capacity"),
        now=1000.0,
        message_id="msg-live-capacity",
    )

    _assert_live_governor_rejection_without_intent(
        tmp_path,
        rejected,
        "capacity_skipped",
        intent_count=3,
    )
    assert len(controller.snapshot(now=1000.0)["intents"]) == 3


@pytest.mark.asyncio
async def test_live_integrity_rejection_does_not_create_terminal_intent(tmp_path):
    controller = _controller(tmp_path, mode="live")
    controller.governor.activate_integrity_halt("test_halt")

    rejected = await controller.handle_candidate(
        _candidate(candidate_id="live-integrity"),
        now=1000.0,
        message_id="msg-live-integrity",
    )

    _assert_live_governor_rejection_without_intent(
        tmp_path,
        rejected,
        "integrity_halt",
        intent_count=0,
    )
    assert controller.snapshot(now=1000.0)["intents"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], False, 0, "", None])
async def test_executor_live_candidate_forwards_message_id(payload):
    from agents.trading.executor import MultiExecutor

    controller = SimpleNamespace(handle_candidate=AsyncMock())
    executor = MultiExecutor.__new__(MultiExecutor)
    executor._tactical_v2_controller = controller

    await executor.on_message({
        "type": "tactical_candidate.v2",
        "msg_id": "msg-live",
        "payload": payload,
    })

    call = controller.handle_candidate.await_args
    assert call.args[0] is payload
    assert call.kwargs == {"message_id": "msg-live"}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], False, 0, "", None])
async def test_executor_startup_replay_forwards_message_id_and_replay_flag(
    monkeypatch,
    payload,
):
    import agents.trading.executor as executor_module

    class FakeContractExecutor:
        def __init__(self, **kwargs):
            self.exchange = object()
            self.ledger = None

    controller = SimpleNamespace(handle_candidate=AsyncMock())
    paths = _paths(Path("unused"))
    journal = SimpleNamespace(replay_messages=lambda *a, **k: [{
        "msg_id": "msg-replay",
        "payload": payload,
    }])
    monkeypatch.setattr(executor_module, "ContractExecutor", FakeContractExecutor)
    monkeypatch.setattr(executor_module, "RealizedPnlResolver", lambda *a, **k: object())
    monkeypatch.setattr(executor_module, "TacticalV2Controller", lambda **kwargs: controller)
    monkeypatch.setattr(executor_module, "get_state_paths", lambda: paths)
    monkeypatch.setattr(executor_module, "get_event_journal", lambda: journal)

    executor = executor_module.MultiExecutor.__new__(executor_module.MultiExecutor)
    executor.config = {"exchange": "okx", "use_testnet": True}
    executor.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    executor._recover_tactical_v2_startup = AsyncMock()

    await executor.setup()

    call = controller.handle_candidate.await_args
    assert call.args[0] is payload
    assert call.kwargs == {
        "message_id": "msg-replay",
        "replayed": True,
    }
