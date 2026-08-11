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
async def test_invalid_payload_fields_cannot_break_receipt_append(tmp_path):
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

    receipt = _receipts(tmp_path)[0]["data"]
    assert result.reason == "invalid_candidate"
    assert set(receipt) == RECEIPT_FIELDS
    assert receipt["candidate_id"] == ""
    assert receipt["source_shadow_id"] == ""
    assert receipt["message_id"] is None
    assert receipt["symbol"] == ""
    assert receipt["side"] == ""
    assert len(receipt["payload_hash"]) == 64


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
async def test_overflow_candidate_persists_invalid_candidate_receipt(tmp_path):
    controller = _controller(tmp_path)
    raw = {**_candidate(candidate_id="overflow-invalid"), "leverage": float("inf")}

    result = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id="msg-overflow-invalid",
    )

    receipts = _receipts(tmp_path)
    assert result.reason == "invalid_candidate"
    assert len(receipts) == 1
    receipt = receipts[0]["data"]
    assert set(receipt) == RECEIPT_FIELDS
    assert receipt["accepted"] is False
    assert receipt["reason"] == "invalid_candidate"
    assert receipt["intent_id"] is None
    assert receipt["payload_hash"] == _payload_hash({
        **raw,
        "leverage": {"non_finite_float": "inf"},
    })


@pytest.mark.asyncio
async def test_recursive_invalid_payload_persists_invalid_candidate_receipt(tmp_path):
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

    receipt = _receipts(tmp_path)[0]["data"]
    assert result.reason == "invalid_candidate"
    assert receipt["reason"] == "invalid_candidate"
    assert receipt["message_id"] == "msg-recursive-invalid"
    assert len(receipt["payload_hash"]) == 64


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
async def test_replay_without_receipt_or_intent_is_unknown_without_side_effects(tmp_path):
    controller = _controller(tmp_path)
    original_events = _events(tmp_path)

    result = await controller.handle_candidate(
        _candidate(),
        now=1000.0,
        message_id="msg-unknown",
        replayed=True,
    )

    assert result.reason == "unknown_handling_evidence"
    assert result.intent_id is None
    assert result.episode_id is None
    assert _events(tmp_path) == original_events
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
    original_events = _events(tmp_path)

    result = await controller.handle_candidate(
        raw,
        now=1000.0,
        message_id=f"msg-{raw['candidate_id']}",
        replayed=True,
    )

    assert result.reason == "unknown_handling_evidence"
    assert result.intent_id is None
    assert result.episode_id is None
    assert _events(tmp_path) == original_events
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
    assert _events(tmp_path) == []
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


def test_conflicting_duplicate_message_receipts_are_both_quarantined(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    store = TacticalStore(_paths(tmp_path))
    first_raw = _candidate(candidate_id="duplicate-message-first")
    first_intent = _append_intent_created(
        store,
        first_raw,
        episode_id="episode-duplicate-message-first",
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
    second_intent = _append_intent_created(
        store,
        second_raw,
        episode_id="episode-duplicate-message-second",
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
    from utils.tactical_v2.store import TacticalStore

    raw = _candidate(candidate_id="payload-fallback-conflict")
    store = TacticalStore(_paths(tmp_path))
    intent = _append_intent_created(
        store,
        raw,
        episode_id="episode-payload-fallback-conflict",
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
