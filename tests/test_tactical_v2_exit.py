import asyncio
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _paths(tmp_path):
    return SimpleNamespace(
        namespace="testnet",
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
        tactical_v2_status=str(tmp_path / "status.json"),
    )


def _candidate(created_at=1000.0):
    return {
        "candidate_id": "candidate-live-1",
        "namespace": "testnet",
        "symbol": "WLD-USDT",
        "side": "long",
        "entry_ref": 1.0,
        "stop_loss": 0.95,
        "take_profit": 1.08,
        "leverage": 5,
        "source_shadow_id": "shadow-live-1",
        "tactical_source": "main_quality_failed",
        "created_at": created_at,
        "tf_15m_available": True,
        "tf_15m_bias": "bullish",
        "tf_15m_closed_bar_ts": 900.0,
        "tf_15m_structure_token": "break_up:wld",
        "tf_15m_block_long": False,
    }


class LiveExecutorStub:
    def __init__(self):
        self.positions = {}
        self.submissions = []
        self.close_calls = []
        self.query_result = None
        self.exchange_position = {"side": "long", "available_contracts": 500.0}
        self.before_submit = None
        self.before_query = None
        self.query_calls = 0
        self.cancel_calls = 0
        self.cancel_result = {
            "proven": True,
            "reason": "cancel_confirmed",
            "filled_qty": 0.0,
        }

    @staticmethod
    def _normalize_symbol(symbol):
        return symbol if symbol.endswith("-SWAP") else f"{symbol}-SWAP"

    @staticmethod
    def make_tactical_clord_id(intent_id, purpose):
        return f"TV2{purpose[:2]}{intent_id[:20]}"

    def submit_tactical_entry(self, intent, *, order_type):
        if self.before_submit:
            self.before_submit()
        self.submissions.append((intent.intent_id, order_type))
        return {
            "order_id": "entry-1",
            "status": "open",
            "requested_qty": 500.0,
            "margin_usdt": 100.0,
            "leverage": 5,
            "entry_client_id": self.make_tactical_clord_id(intent.intent_id, "entry"),
            "tp_client_id": self.make_tactical_clord_id(intent.intent_id, "tp"),
            "sl_client_id": self.make_tactical_clord_id(intent.intent_id, "sl"),
        }

    def query_tactical_entry(self, intent):
        self.query_calls += 1
        if self.before_query:
            self.before_query()
        return self.query_result

    def cancel_tactical_entry(self, intent):
        self.cancel_calls += 1
        return dict(self.cancel_result)

    def verify_tactical_protection(self, intent, *, filled_qty):
        return {
            "complete": True,
            "reason": "complete",
            "representation": "combined_oco",
            "protected_qty": filled_qty,
            "tp_algo_ids": ["tp-algo"],
            "sl_algo_ids": ["sl-algo"],
        }

    def cancel_tactical_protection(self, intent):
        return {"cancelled_algo_ids": ["tp-algo", "sl-algo"], "preserved_algo_ids": []}

    def _fetch_okx_position_state(self, symbol, raise_on_error=True):
        return self.exchange_position

    def close_tactical_position(self, intent, *, filled_qty, ownership_proof, reason):
        self.close_calls.append({
            "intent_id": intent.intent_id,
            "filled_qty": filled_qty,
            "ownership_proof": ownership_proof,
            "reason": reason,
        })
        return {
            "status": "submitted",
            "order_id": "close-1",
            "client_order_id": self.make_tactical_clord_id(intent.intent_id, "close"),
            "closed_qty": filled_qty,
            "reason": reason,
        }

    def _save_positions(self):
        return None


def _controller(tmp_path, executor, now=1000.0):
    from utils.tactical_v2.controller import TacticalV2Controller

    return TacticalV2Controller(
        executor=executor,
        config={"tactical_v2_mode": "live"},
        paths=_paths(tmp_path),
        logger=logging.getLogger("test_tactical_v2_exit"),
        publish=None,
        now_fn=lambda: now,
    )


def _event_rows(tmp_path):
    return [
        json.loads(line)
        for line in Path(tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.asyncio
async def test_live_persists_submitting_before_exchange_io(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)

    accepted = await controller.handle_candidate(_candidate(), now=1000.0)

    def assert_submitting_is_durable():
        rows = _event_rows(tmp_path)
        assert rows[-1]["data"]["state"] == "submitting_entry"
        assert rows[-1]["data"]["entry_client_id"].startswith("TV2")
        assert rows[-1]["data"]["entry_visibility_deadline"] == 1015.0

    executor.before_submit = assert_submitting_is_durable
    await controller.handle_quote(
        "WLD-USDT", {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0}, now=1000.0
    )

    assert accepted.accepted is True
    assert executor.submissions
    assert controller.snapshot(now=1000.0)["intents"][0]["state"] == "pending_entry"


@pytest.mark.asyncio
async def test_tick_does_not_reconcile_while_submit_io_is_in_flight(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    submit_started = threading.Event()
    release_submit = threading.Event()

    def block_submit():
        submit_started.set()
        assert release_submit.wait(timeout=2)

    executor.before_submit = block_submit
    submit_task = asyncio.create_task(controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    ))
    assert await asyncio.to_thread(submit_started.wait, 1)
    try:
        await controller.tick(now=1000.1)
        snapshot = controller.snapshot(now=1000.1)

        assert executor.query_calls == 0
        assert snapshot["intents"][0]["state"] == "submitting_entry"
        assert snapshot["integrity_halt"] is None
    finally:
        release_submit.set()
        await submit_task


@pytest.mark.asyncio
async def test_structure_invalidation_during_submit_is_durably_deferred(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    await controller.handle_candidate(_candidate(), now=1000.0)
    submit_started = threading.Event()
    release_submit = threading.Event()

    def block_submit():
        submit_started.set()
        assert release_submit.wait(timeout=2)

    executor.before_submit = block_submit
    submit_task = asyncio.create_task(controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    ))
    assert await asyncio.to_thread(submit_started.wait, 1)
    try:
        await controller.handle_structure(
            "WLD-USDT",
            {"tf_15m_block_long": True},
            now=1000.1,
        )
        snapshot = controller.snapshot(now=1000.1)

        assert executor.cancel_calls == 0
        assert snapshot["intents"][0]["state"] == "submitting_entry"
        record = next(iter(controller._intents.values()))
        assert record["deferred_cancel_reason"] == (
            "structure_invalidated"
        )
        await controller.tick(now=1900.0)
        assert record["deferred_cancel_reason"] == "structure_invalidated"
        restored = _controller(tmp_path, LiveExecutorStub(), now=1000.1)
        restored_record = next(iter(restored._intents.values()))
        assert restored_record["deferred_cancel_reason"] == "structure_invalidated"
    finally:
        release_submit.set()
        await submit_task

    snapshot = controller.snapshot(now=1000.2)
    assert executor.cancel_calls == 1
    assert snapshot["intents"][0]["state"] == "entry_terminal"
    assert snapshot["intents"][0]["terminal_reason"] == "structure_invalidated"


@pytest.mark.asyncio
async def test_restart_executes_durable_deferred_cancel_without_resubmission(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    record = controller._intents[accepted.intent_id]
    controller._persist_record_state(
        record,
        "reconciling_entry",
        1000.1,
        deferred_cancel_reason="structure_invalidated",
    )

    restarted_executor = LiveExecutorStub()
    restarted_executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": restarted_executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "open",
            "filled_qty": 0.0,
            "remaining_qty": 500.0,
            "average_price": None,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    restarted = _controller(tmp_path, restarted_executor, now=1001.0)

    await restarted.recover(now=1001.0)

    snapshot = restarted.snapshot(now=1001.0)
    assert restarted_executor.submissions == []
    assert restarted_executor.cancel_calls == 1
    assert snapshot["intents"][0]["state"] == "entry_terminal"
    assert snapshot["intents"][0]["terminal_reason"] == "structure_invalidated"


@pytest.mark.asyncio
async def test_concurrent_ticks_serialize_entry_query_per_intent(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    query_started = threading.Event()
    release_query = threading.Event()
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }

    def block_query():
        query_started.set()
        assert release_query.wait(timeout=2)

    executor.before_query = block_query
    first_tick = asyncio.create_task(controller.tick(now=1001.0))
    assert await asyncio.to_thread(query_started.wait, 1)
    second_tick = asyncio.create_task(controller.tick(now=1001.1))
    try:
        await asyncio.sleep(0.05)
        assert executor.query_calls == 1
        assert second_tick.done()
    finally:
        release_query.set()
        await asyncio.gather(first_tick, second_tick)

    assert controller.snapshot(now=1001.1)["integrity_halt"] is None


@pytest.mark.asyncio
async def test_not_found_within_visibility_grace_retries_without_halt(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }

    await controller.tick(now=1001.0)

    snapshot = controller.snapshot(now=1001.0)
    assert snapshot["intents"][0]["state"] == "reconciling_entry"
    assert snapshot["integrity_halt"] is None


@pytest.mark.asyncio
async def test_query_error_within_visibility_grace_is_not_treated_as_absent(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "query_error",
        "observation": None,
        "successful_sources": [],
        "errors": [{"source": "private_get_trade_order", "error": "timeout"}],
    }

    await controller.tick(now=1001.0)

    snapshot = controller.snapshot(now=1001.0)
    assert snapshot["intents"][0]["state"] == "reconciling_entry"
    assert snapshot["integrity_halt"] is None
    rows = _event_rows(tmp_path)
    reconcile = next(
        row for row in reversed(rows)
        if row["event_type"] == "intent_transition"
        and row["data"].get("state") == "reconciling_entry"
    )
    assert reconcile["data"]["entry_query_state"] == "query_error"


@pytest.mark.asyncio
async def test_visibility_deadline_survives_restart_and_then_halts(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    await controller.handle_candidate(_candidate(), now=1000.0)

    def crash_after_durable_submit():
        raise SystemExit("simulated process stop")

    executor.before_submit = crash_after_durable_submit
    with pytest.raises(SystemExit):
        await controller.handle_quote(
            "WLD-USDT",
            {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
            now=1000.0,
        )

    restarted_executor = LiveExecutorStub()
    restarted_executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    restarted = _controller(tmp_path, restarted_executor, now=1001.0)
    await restarted.recover(now=1001.0)
    assert restarted.snapshot(now=1001.0)["integrity_halt"] is None

    await restarted.tick(now=1015.0)

    snapshot = restarted.snapshot(now=1015.0)
    assert snapshot["intents"][0]["state"] == "integrity_required"
    assert snapshot["integrity_halt"]["reason"] == "entry_reconciliation_unknown"
    assert snapshot["integrity_halt"]["evidence"]["entry_visibility_deadline"] == 1015.0


@pytest.mark.asyncio
async def test_entry_halt_restores_exact_active_unfilled_order(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    await controller.tick(now=1015.0)
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "open",
            "filled_qty": 0.0,
            "remaining_qty": 500.0,
            "average_price": None,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.exchange_position = None

    await controller.tick(now=1045.0)

    snapshot = controller.snapshot(now=1045.0)
    assert snapshot["integrity_halt"] is None
    assert snapshot["intents"][0]["state"] == "pending_entry"
    assert controller._intents[accepted.intent_id]["remaining_qty"] == 500.0
    assert executor.cancel_calls == 0


@pytest.mark.asyncio
async def test_entry_halt_cancels_exact_expired_unfilled_order_and_clears(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    await controller.tick(now=1015.0)
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "open",
            "filled_qty": 0.0,
            "remaining_qty": 500.0,
            "average_price": None,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.exchange_position = None

    await controller.tick(now=1900.0)

    snapshot = controller.snapshot(now=1900.0)
    assert snapshot["integrity_halt"] is None
    assert snapshot["intents"][0]["state"] == "entry_terminal"
    assert snapshot["intents"][0]["terminal_reason"] == "expired"
    assert executor.cancel_calls == 1


@pytest.mark.asyncio
async def test_entry_halt_cancel_fill_is_settled_without_recursive_cancel(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    await controller.tick(now=1015.0)
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "open",
            "filled_qty": 0.0,
            "remaining_qty": 500.0,
            "average_price": None,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.cancel_result = {
        "proven": True,
        "reason": "cancel_confirmed",
        "order_id": "entry-1",
        "filled_qty": 100.0,
        "average_price": 1.001,
    }
    executor.exchange_position = {"side": "long", "available_contracts": 100.0}

    await controller.tick(now=1900.0)

    snapshot = controller.snapshot(now=1900.0)
    assert snapshot["integrity_halt"] is None
    assert snapshot["intents"][0]["state"] == "protected"
    assert executor.cancel_calls == 1


@pytest.mark.asyncio
async def test_entry_io_claim_releases_when_reconcile_persistence_fails(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    persist = controller._persist_record_state

    def fail_reconciling(record, state, evaluated_at, **fields):
        if state == "reconciling_entry":
            raise OSError("disk full")
        return persist(record, state, evaluated_at, **fields)

    controller._persist_record_state = fail_reconciling
    with pytest.raises(OSError, match="disk full"):
        await controller.tick(now=1001.0)

    assert accepted.intent_id not in controller._entry_io_inflight


@pytest.mark.asyncio
async def test_entry_io_claim_releases_when_submit_transition_persistence_fails(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    persist = controller._persist_record_state

    def fail_submitting(record, state, evaluated_at, **fields):
        if state == "submitting_entry":
            raise OSError("disk full")
        return persist(record, state, evaluated_at, **fields)

    controller._persist_record_state = fail_submitting
    with pytest.raises(OSError, match="disk full"):
        await controller.handle_quote(
            "WLD-USDT",
            {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
            now=1000.0,
        )

    assert accepted.intent_id not in controller._entry_io_inflight


@pytest.mark.asyncio
async def test_specific_entry_halt_self_heals_only_after_full_open_position_proof(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    await controller.tick(now=1015.0)
    assert controller.snapshot(now=1015.0)["integrity_halt"] is not None

    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "filled",
            "filled_qty": 500.0,
            "remaining_qty": 0.0,
            "average_price": 1.001,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    await controller.tick(now=1045.0)

    snapshot = controller.snapshot(now=1045.0)
    assert snapshot["intents"][0]["state"] == "protected"
    assert snapshot["integrity_halt"] is None
    assert executor.submissions == [(accepted.intent_id, "market")]


@pytest.mark.asyncio
async def test_filled_entry_that_is_already_flat_stays_halted_without_final_pnl(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    await controller.tick(now=1015.0)
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "filled",
            "filled_qty": 500.0,
            "remaining_qty": 0.0,
            "average_price": 1.001,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.exchange_position = None

    await controller.tick(now=1045.0)

    snapshot = controller.snapshot(now=1045.0)
    assert snapshot["integrity_halt"]["reason"] == "entry_reconciliation_unknown"
    assert snapshot["intents"][0]["state"] == "integrity_required"
    assert executor._last_removed_symbols == ["WLD-USDT-SWAP"]
    recovery = executor._removed_positions_data[0]
    assert recovery["strategy_owner"] == "tactical_v2"
    assert recovery["position_id"] == f"tv2:{accepted.intent_id}"
    assert recovery["entry_request_id"] == executor.make_tactical_clord_id(
        accepted.intent_id, "entry"
    )
    rows = _event_rows(tmp_path)
    transition = next(
        row for row in reversed(rows)
        if row["event_type"] == "intent_transition"
        and row["data"].get("integrity_reason")
    )
    assert transition["data"]["integrity_reason"] == (
        "entry_fill_flat_awaiting_final_pnl"
    )


@pytest.mark.asyncio
async def test_protection_failure_reconciles_safe_close_and_releases_slot(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "filled",
            "filled_qty": 500.0,
            "remaining_qty": 0.0,
            "average_price": 1.001,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.verify_tactical_protection = lambda intent, filled_qty: {
        "complete": False,
        "reason": "price_mismatch",
        "representation": "incomplete",
        "protected_qty": 0.0,
        "tp_algo_ids": [],
        "sl_algo_ids": [],
    }
    await controller.tick(now=1001.0)
    assert controller.snapshot(now=1001.0)["intents"][0]["state"] == (
        "integrity_required"
    )
    assert controller.snapshot(now=1001.0)["integrity_halt"]["reason"] == (
        "tactical_protection_incomplete"
    )

    executor.exchange_position = None
    await controller.tick(now=1032.0)

    snapshot = controller.snapshot(now=1032.0)
    assert snapshot["intents"][0]["state"] == "exchange_closed_pending_pnl"
    assert snapshot["active_slots"] == 0
    assert snapshot["integrity_halt"]["reason"] == (
        "tactical_protection_incomplete"
    )
    assert executor._last_removed_symbols == ["WLD-USDT-SWAP"]
    recovery = executor._removed_positions_data[0]
    assert recovery["strategy_owner"] == "tactical_v2"
    assert recovery["position_id"] == f"tv2:{accepted.intent_id}"


@pytest.mark.asyncio
async def test_protection_failure_final_pnl_clears_halt_idempotently(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "filled",
            "filled_qty": 500.0,
            "remaining_qty": 0.0,
            "average_price": 1.001,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.verify_tactical_protection = lambda intent, filled_qty: {
        "complete": False,
        "reason": "price_mismatch",
        "representation": "incomplete",
        "protected_qty": 0.0,
        "tp_algo_ids": [],
        "sl_algo_ids": [],
    }
    await controller.tick(now=1001.0)
    executor.exchange_position = None
    await controller.tick(now=1032.0)
    record = controller._intents[accepted.intent_id]

    payload = {
        "resolution_id": "corr-protection-1",
        "strategy_owner": "tactical_v2",
        "intent_id": accepted.intent_id,
        "episode_id": accepted.episode_id,
        "plan_hash": record["intent"].plan_hash,
        "position_id": f"tv2:{accepted.intent_id}",
        "entry_request_id": executor.make_tactical_clord_id(
            accepted.intent_id, "entry"
        ),
        "pnl_status": "final",
        "realized_pnl_net_usdt": -0.95,
        "timestamp": 1033.0,
        "close_cause": "external_unknown",
        "tactical_v2_proof": {
            "complete": True,
            "entry_request_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "entry_order_ids": ["entry-1"],
            "close_order_ids": ["close-1"],
            "entry_qty": 500.0,
            "close_qty": 500.0,
            "entry_fee_usdt": 0.10,
        },
    }

    await controller.handle_pnl_resolution(payload)
    snapshot = controller.snapshot(now=1033.0)
    assert snapshot["intents"][0]["state"] == "closed_final"
    assert snapshot["active_slots"] == 0
    assert snapshot["integrity_halt"] is None

    await controller.handle_pnl_resolution(payload)
    rows = _event_rows(tmp_path)
    assert [
        row for row in rows if row["event_type"] == "governor_final_applied"
    ].__len__() == 1


@pytest.mark.asyncio
async def test_protection_recovery_snapshot_requeues_after_restart(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "filled",
            "filled_qty": 500.0,
            "remaining_qty": 0.0,
            "average_price": 1.001,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.verify_tactical_protection = lambda intent, filled_qty: {
        "complete": False,
        "reason": "price_mismatch",
        "representation": "incomplete",
        "protected_qty": 0.0,
        "tp_algo_ids": [],
        "sl_algo_ids": [],
    }
    await controller.tick(now=1001.0)
    executor.exchange_position = None
    await controller.tick(now=1032.0)
    assert controller._intents[accepted.intent_id]["pnl_recovery_queued"] is True

    restarted_executor = LiveExecutorStub()
    restarted_executor.exchange_position = None
    restarted = _controller(tmp_path, restarted_executor, now=1033.0)
    await restarted.recover(now=1033.0)

    assert restarted_executor._last_removed_symbols == ["WLD-USDT-SWAP"]
    assert len(restarted_executor._removed_positions_data) == 1
    assert restarted_executor._removed_positions_data[0]["position_id"] == (
        f"tv2:{accepted.intent_id}"
    )


@pytest.mark.asyncio
async def test_entry_visibility_deadline_does_not_overwrite_newer_halt(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }

    def activate_newer_halt():
        controller.governor.activate_integrity_halt(
            "newer_manual_halt",
            evidence={"source": "concurrent_check"},
        )

    executor.before_query = activate_newer_halt

    await controller.tick(now=1015.0)

    halt = controller.snapshot(now=1015.0)["integrity_halt"]
    assert halt["reason"] == "newer_manual_halt"
    assert halt["evidence"] == {"source": "concurrent_check"}

    assert controller.governor.clear_integrity_halt(
        "manual-newer-cleared",
        {
            "ownership": True,
            "orders": True,
            "positions": True,
            "protection": True,
        },
    ) is True
    executor.before_query = None
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "canceled",
            "filled_qty": 0.0,
            "remaining_qty": 0.0,
            "average_price": None,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.exchange_position = None
    query_calls = executor.query_calls

    await controller.tick(now=1045.0)

    assert executor.query_calls == query_calls + 1
    assert controller.snapshot(now=1045.0)["intents"][0]["state"] == \
        "entry_terminal"


@pytest.mark.asyncio
async def test_unproven_cancel_does_not_overwrite_newer_halt(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )

    def cancel_with_concurrent_halt(intent):
        controller.governor.activate_integrity_halt(
            "newer_manual_halt",
            evidence={"source": "concurrent_check"},
        )
        return {
            "proven": False,
            "reason": "exchange_cancel_unknown",
            "filled_qty": 0.0,
        }

    executor.cancel_tactical_entry = cancel_with_concurrent_halt

    await controller._cancel_live_entry(
        accepted.intent_id,
        reason="structure_invalidated",
        evaluated_at=1001.0,
    )

    halt = controller.snapshot(now=1001.0)["integrity_halt"]
    assert halt["reason"] == "newer_manual_halt"
    assert halt["evidence"] == {"source": "concurrent_check"}

    assert controller.governor.clear_integrity_halt(
        "manual-newer-cleared",
        {
            "ownership": True,
            "orders": True,
            "positions": True,
            "protection": True,
        },
    ) is True
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "canceled",
            "filled_qty": 0.0,
            "remaining_qty": 0.0,
            "average_price": None,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.exchange_position = None
    query_calls = executor.query_calls

    await controller.tick(now=1031.0)

    assert executor.query_calls == query_calls + 1
    assert controller.snapshot(now=1031.0)["intents"][0]["state"] == \
        "entry_terminal"


@pytest.mark.asyncio
async def test_unproven_cancel_recheck_retries_original_cancel_reason(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.cancel_result = {
        "proven": False,
        "reason": "exchange_cancel_unknown",
        "filled_qty": 0.0,
    }

    await controller._cancel_live_entry(
        accepted.intent_id,
        reason="structure_invalidated",
        evaluated_at=1001.0,
    )

    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "open",
            "filled_qty": 0.0,
            "remaining_qty": 500.0,
            "average_price": None,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.exchange_position = None
    executor.cancel_result = {
        "proven": True,
        "reason": "cancel_confirmed",
        "filled_qty": 0.0,
    }

    await controller.tick(now=1031.0)

    assert executor.cancel_calls == 2
    snapshot = controller.snapshot(now=1031.0)
    assert snapshot["intents"][0]["state"] == "entry_terminal"
    assert snapshot["intents"][0]["terminal_reason"] == "structure_invalidated"
    assert snapshot["integrity_halt"] is None


@pytest.mark.asyncio
async def test_durable_integrity_record_blocks_new_admission_without_governor_halt(
    tmp_path,
):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    record = controller._intents[accepted.intent_id]
    controller._persist_record_state(
        record,
        "integrity_required",
        1001.0,
        integrity_reason="entry_reconciliation_unknown",
    )
    assert controller.governor.integrity_halt is None

    second = await controller.handle_candidate({
        **_candidate(created_at=1002.0),
        "candidate_id": "candidate-live-2",
        "symbol": "ADA-USDT",
        "source_shadow_id": "shadow-live-2",
        "tf_15m_structure_token": "break_up:ada",
    }, now=1002.0)

    assert second.accepted is False
    assert second.reason == "integrity_halt"


@pytest.mark.asyncio
async def test_durable_integrity_record_is_reported_as_halt_without_governor_slot(
    tmp_path,
):
    from utils.tactical_v2.status import format_tactical_v2_status

    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    record = controller._intents[accepted.intent_id]
    controller._persist_record_state(
        record,
        "integrity_required",
        1001.0,
        integrity_reason="entry_reconciliation_unknown",
    )
    assert controller.governor.integrity_halt is None

    snapshot = controller.snapshot(now=1002.0)
    status = controller.operational_status(now=1002.0)
    text = format_tactical_v2_status(status, now=1002.0)

    assert snapshot["integrity_halt"]["reason"] == "entry_reconciliation_unknown"
    assert status["integrity_halt"]["reason"] == "entry_reconciliation_unknown"
    assert "integrity HALT (entry_reconciliation_unknown)" in text
    assert "circuit clear" not in text.lower()


@pytest.mark.asyncio
async def test_exact_final_pnl_clears_filled_flat_entry_halt(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor, now=1046.0)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    await controller.tick(now=1015.0)
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "filled",
            "filled_qty": 500.0,
            "remaining_qty": 0.0,
            "average_price": 1.001,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.exchange_position = None
    await controller.tick(now=1045.0)
    record = controller._intents[accepted.intent_id]
    await controller.handle_pnl_resolution({
        "resolution_id": "resolution-flat-1",
        "position_id": f"tv2:{accepted.intent_id}",
        "entry_request_id": executor.make_tactical_clord_id(
            accepted.intent_id, "entry"
        ),
        "strategy_owner": "tactical_v2",
        "intent_id": accepted.intent_id,
        "episode_id": accepted.episode_id,
        "plan_hash": record["intent"].plan_hash,
        "pnl_status": "final",
        "pnl_is_final": True,
        "realized_pnl_net_usdt": 1.25,
        "timestamp": 1046.0,
        "close_cause": "exchange_tp",
        "tactical_v2_proof": {
            "complete": True,
            "entry_request_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "entry_order_ids": ["entry-1"],
            "close_order_ids": ["close-1"],
            "entry_qty": 500.0,
            "close_qty": 500.0,
            "entry_fee_usdt": -0.25,
        },
    })

    snapshot = controller.snapshot(now=1046.0)
    assert snapshot["integrity_halt"] is None
    assert snapshot["intents"][0]["state"] == "closed_final"
    assert controller.governor.rolling_pnl == 1.25


@pytest.mark.asyncio
async def test_wrong_close_identity_cannot_clear_filled_flat_entry_halt(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor, now=1046.0)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    await controller.tick(now=1015.0)
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "filled",
            "filled_qty": 500.0,
            "remaining_qty": 0.0,
            "average_price": 1.001,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.exchange_position = None
    await controller.tick(now=1045.0)
    record = controller._intents[accepted.intent_id]
    controller.governor.activate_integrity_halt(
        "newer_manual_halt",
        evidence={"source": "concurrent_check"},
    )

    await controller.handle_pnl_resolution({
        "resolution_id": "resolution-wrong-position",
        "position_id": "tv2:other-intent",
        "entry_request_id": executor.make_tactical_clord_id(
            accepted.intent_id, "entry"
        ),
        "strategy_owner": "tactical_v2",
        "intent_id": accepted.intent_id,
        "episode_id": accepted.episode_id,
        "plan_hash": record["intent"].plan_hash,
        "pnl_status": "final",
        "pnl_is_final": True,
        "realized_pnl_net_usdt": 1.25,
        "timestamp": 1046.0,
        "close_cause": "exchange_tp",
        "tactical_v2_proof": {
            "complete": True,
            "entry_request_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "entry_order_ids": ["entry-1"],
            "close_order_ids": ["close-1"],
            "entry_qty": 500.0,
            "close_qty": 500.0,
            "entry_fee_usdt": -0.25,
        },
    })

    snapshot = controller.snapshot(now=1046.0)
    assert snapshot["integrity_halt"]["reason"] == "newer_manual_halt"
    assert snapshot["intents"][0]["state"] == "integrity_required"
    assert controller.governor.final_episode_count == 0


@pytest.mark.asyncio
async def test_newer_integrity_halt_wins_race_against_flat_pnl_recovery(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor, now=1046.0)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    await controller.tick(now=1015.0)
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": executor.make_tactical_clord_id(
                accepted.intent_id, "entry"
            ),
            "status": "filled",
            "filled_qty": 500.0,
            "remaining_qty": 0.0,
            "average_price": 1.001,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.exchange_position = None
    await controller.tick(now=1045.0)
    record = controller._intents[accepted.intent_id]
    persist = controller._persist_record_state

    def activate_newer_halt(current, state, evaluated_at, **fields):
        persist(current, state, evaluated_at, **fields)
        if state == "closed_final":
            controller.governor.activate_integrity_halt(
                "pnl_mismatch",
                evidence={"resolution_id": "newer-mismatch"},
            )

    controller._persist_record_state = activate_newer_halt
    entry_request_id = executor.make_tactical_clord_id(accepted.intent_id, "entry")
    await controller.handle_pnl_resolution({
        "resolution_id": "resolution-flat-race",
        "position_id": f"tv2:{accepted.intent_id}",
        "entry_request_id": entry_request_id,
        "strategy_owner": "tactical_v2",
        "intent_id": accepted.intent_id,
        "episode_id": accepted.episode_id,
        "plan_hash": record["intent"].plan_hash,
        "pnl_status": "final",
        "realized_pnl_net_usdt": 1.25,
        "timestamp": 1046.0,
        "tactical_v2_proof": {
            "complete": True,
            "entry_request_id": entry_request_id,
            "entry_order_ids": ["entry-1"],
            "close_order_ids": ["close-1"],
            "entry_qty": 500.0,
            "close_qty": 500.0,
            "entry_fee_usdt": -0.25,
        },
    })

    assert controller.snapshot(now=1046.0)["integrity_halt"]["reason"] == (
        "pnl_mismatch"
    )


@pytest.mark.asyncio
async def test_restart_clears_durable_flat_final_after_pre_clear_crash(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor, now=1046.0)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "query_state": "not_found",
        "observation": None,
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    await controller.tick(now=1015.0)
    entry_request_id = executor.make_tactical_clord_id(accepted.intent_id, "entry")
    executor.query_result = {
        "query_state": "found",
        "observation": {
            "order_id": "entry-1",
            "client_order_id": entry_request_id,
            "status": "filled",
            "filled_qty": 500.0,
            "remaining_qty": 0.0,
            "average_price": 1.001,
        },
        "successful_sources": ["private_get_trade_order"],
        "errors": [],
    }
    executor.exchange_position = None
    await controller.tick(now=1045.0)
    record = controller._intents[accepted.intent_id]
    clear = controller.governor.clear_integrity_halt

    def crash_before_clear(reconciliation_id, proof):
        raise SystemExit("simulated pre-clear crash")

    controller.governor.clear_integrity_halt = crash_before_clear
    with pytest.raises(SystemExit, match="pre-clear"):
        await controller.handle_pnl_resolution({
            "resolution_id": "resolution-flat-crash",
            "position_id": f"tv2:{accepted.intent_id}",
            "entry_request_id": entry_request_id,
            "strategy_owner": "tactical_v2",
            "intent_id": accepted.intent_id,
            "episode_id": accepted.episode_id,
            "plan_hash": record["intent"].plan_hash,
            "pnl_status": "final",
            "realized_pnl_net_usdt": 1.25,
            "timestamp": 1046.0,
            "tactical_v2_proof": {
                "complete": True,
                "entry_request_id": entry_request_id,
                "entry_order_ids": ["entry-1"],
                "close_order_ids": ["close-1"],
                "entry_qty": 500.0,
                "close_qty": 500.0,
                "entry_fee_usdt": -0.25,
            },
        })
    controller.governor.clear_integrity_halt = clear

    restarted = _controller(tmp_path, LiveExecutorStub(), now=1047.0)
    await restarted.recover(now=1047.0)

    snapshot = restarted.snapshot(now=1047.0)
    assert snapshot["intents"][0]["state"] == "closed_final"
    assert snapshot["integrity_halt"] is None
    assert restarted.governor.rolling_pnl == 1.25


@pytest.mark.asyncio
async def test_live_limit_is_cancelled_when_target_is_reached_before_fill(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT", {"bid": 1.009, "ask": 1.01, "timestamp": 1000.0}, now=1000.0
    )

    await controller.handle_quote(
        "WLD-USDT", {"bid": 1.08, "ask": 1.081, "timestamp": 1010.0}, now=1010.0
    )

    snapshot = controller.snapshot(now=1010.0)
    assert executor.submissions[0][1] == "limit"
    assert snapshot["active_slots"] == 0
    assert snapshot["intents"][0]["state"] == "entry_terminal"
    assert snapshot["episode_outcomes"]["missed_after_target"] == 1


@pytest.mark.asyncio
async def test_restart_recovers_submitting_entry_by_client_id_without_retry(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)

    def crash_after_exchange_boundary():
        raise SystemExit("simulated process stop")

    executor.before_submit = crash_after_exchange_boundary
    with pytest.raises(SystemExit):
        await controller.handle_quote(
            "WLD-USDT", {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0}, now=1000.0
        )

    restarted_executor = LiveExecutorStub()
    restarted_executor.query_result = {
        "order_id": "entry-accepted",
        "client_order_id": restarted_executor.make_tactical_clord_id(
            accepted.intent_id, "entry"
        ),
        "status": "open",
        "filled_qty": 0.0,
        "remaining_qty": 500.0,
        "average_price": None,
    }
    restarted = _controller(tmp_path, restarted_executor, now=1001.0)

    await restarted.recover(now=1001.0)

    assert restarted_executor.submissions == []
    assert restarted.snapshot(now=1001.0)["intents"][0]["state"] == "pending_entry"


@pytest.mark.asyncio
async def test_protected_fill_persists_full_v2_metadata_in_common_positions(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT", {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0}, now=1000.0
    )
    executor.query_result = {
        "order_id": "entry-1",
        "client_order_id": executor.make_tactical_clord_id(accepted.intent_id, "entry"),
        "status": "closed",
        "filled_qty": 500.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    }

    await controller.tick(now=1001.0)

    position = executor.positions["WLD-USDT-SWAP"]
    assert position["strategy_owner"] == "tactical_v2"
    assert position["intent_id"] == accepted.intent_id
    assert position["episode_id"] == accepted.episode_id
    assert position["plan_hash"]
    assert position["amount"] == 500.0
    assert position["amount_usdt"] == 100.0
    assert position["take_profit_levels"] == [1.08]
    assert position["tp_algo_ids"] == ["tp-algo"]
    assert position["sl_algo_ids"] == ["sl-algo"]
    assert controller.snapshot(now=1001.0)["intents"][0]["state"] == "protected"


@pytest.mark.asyncio
async def test_max_hold_closes_full_remaining_quantity_once(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT", {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0}, now=1000.0
    )
    executor.query_result = {
        "order_id": "entry-1",
        "client_order_id": executor.make_tactical_clord_id(accepted.intent_id, "entry"),
        "status": "closed",
        "filled_qty": 500.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    }
    await controller.tick(now=1001.0)

    await controller.tick(now=1001.0 + 90 * 60)
    await controller.tick(now=1002.0 + 90 * 60)

    assert executor.close_calls == [{
        "intent_id": accepted.intent_id,
        "filled_qty": 500.0,
        "ownership_proof": executor.make_tactical_clord_id(accepted.intent_id, "entry"),
        "reason": "tactical_max_hold",
    }]


@pytest.mark.asyncio
async def test_local_close_identity_is_forwarded_to_removed_snapshot(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.query_result = {
        "order_id": "entry-1",
        "client_order_id": executor.make_tactical_clord_id(
            accepted.intent_id, "entry"
        ),
        "status": "closed",
        "filled_qty": 500.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    }
    await controller.tick(now=1001.0)
    await controller.tick(now=1001.0 + 90 * 60)
    executor.exchange_position = None

    await controller.tick(now=1002.0 + 90 * 60)

    removed = executor._removed_positions_data[0]
    assert removed["close_order_id"] == "close-1"
    assert removed["close_client_id"] == executor.make_tactical_clord_id(
        accepted.intent_id,
        "close",
    )


@pytest.mark.asyncio
async def test_exchange_tp_flat_wins_race_against_max_hold(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT", {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0}, now=1000.0
    )
    executor.query_result = {
        "order_id": "entry-1",
        "client_order_id": executor.make_tactical_clord_id(accepted.intent_id, "entry"),
        "status": "closed",
        "filled_qty": 500.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    }
    await controller.tick(now=1001.0)
    executor.exchange_position = None

    await controller.tick(now=1001.0 + 90 * 60)

    assert executor.close_calls == []
    assert controller.snapshot(now=6401.0)["intents"][0]["state"] == (
        "exchange_closed_pending_pnl"
    )


@pytest.mark.asyncio
async def test_exchange_tp_releases_slot_on_next_controller_tick(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT", {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0}, now=1000.0
    )
    executor.query_result = {
        "order_id": "entry-1",
        "client_order_id": executor.make_tactical_clord_id(accepted.intent_id, "entry"),
        "status": "closed",
        "filled_qty": 500.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    }
    await controller.tick(now=1001.0)
    executor.exchange_position = None

    await controller.tick(now=1300.0)

    snapshot = controller.snapshot(now=1300.0)
    assert snapshot["active_slots"] == 0
    assert snapshot["intents"][0]["state"] == "exchange_closed_pending_pnl"
    removed = executor._removed_positions_data[0]
    assert executor._last_removed_symbols == ["WLD-USDT-SWAP"]
    assert removed["strategy_owner"] == "tactical_v2"
    assert removed["intent_id"] == accepted.intent_id
    assert removed["position_id"] == f"tv2:{accepted.intent_id}"
    assert removed["entry_request_id"] == executor.make_tactical_clord_id(
        accepted.intent_id, "entry"
    )
    assert removed["tp_algo_ids"] == ["tp-algo"]
    assert removed["sl_algo_ids"] == ["sl-algo"]


@pytest.mark.asyncio
async def test_restart_reconciles_protected_position_without_resubmitting_entry(tmp_path):
    executor = LiveExecutorStub()
    controller = _controller(tmp_path, executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT", {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0}, now=1000.0
    )
    executor.query_result = {
        "order_id": "entry-1",
        "client_order_id": executor.make_tactical_clord_id(accepted.intent_id, "entry"),
        "status": "closed",
        "filled_qty": 500.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    }
    await controller.tick(now=1001.0)

    restarted_executor = LiveExecutorStub()
    restarted = _controller(tmp_path, restarted_executor, now=1002.0)
    await restarted.recover(now=1002.0)

    assert restarted_executor.submissions == []
    restored = restarted_executor.positions["WLD-USDT-SWAP"]
    assert restored["strategy_owner"] == "tactical_v2"
    assert restored["intent_id"] == accepted.intent_id
    assert restored["amount"] == 500.0


def _root_executor(monkeypatch):
    from executor import ContractExecutor

    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = ContractExecutor.__new__(ContractExecutor)
    executor.logger = logging.getLogger("test_tactical_v2_exit_root")
    executor.exchange_id = "okx"
    executor.testnet = True
    executor._okx_pos_mode = "net_mode"
    executor.positions = {}
    executor.exchange = MagicMock()
    executor.exchange.amount_to_precision.side_effect = lambda symbol, qty: str(qty)
    executor.exchange.create_order.return_value = {"id": "close-1", "status": "open"}
    executor._exit_lock_mu = threading.Lock()
    executor._exit_locks = {}
    return executor


def _intent():
    from utils.tactical_v2.models import TacticalIntent

    return TacticalIntent.from_candidate(_candidate(), episode_id="episode-live-1")


def test_owner_bound_close_rechecks_after_protection_cleanup_and_yields_to_tp_race(monkeypatch):
    executor = _root_executor(monkeypatch)
    intent = _intent()
    executor.query_tactical_close = MagicMock(return_value=None)
    executor.cancel_tactical_protection = MagicMock(return_value={"cancelled_algo_ids": []})
    executor._fetch_okx_position_state = MagicMock(side_effect=[
        {"side": "long", "available_contracts": 500.0},
        None,
    ])

    result = executor.close_tactical_position(
        intent,
        filled_qty=500.0,
        ownership_proof=executor.make_tactical_clord_id(intent.intent_id, "entry"),
        reason="tactical_max_hold",
    )

    assert result["status"] == "already_flat"
    executor.exchange.create_order.assert_not_called()
    executor.cancel_tactical_protection.assert_called_once_with(intent)


def test_repeated_owner_bound_close_recovers_same_close_identity(monkeypatch):
    executor = _root_executor(monkeypatch)
    intent = _intent()
    close_id = executor.make_tactical_clord_id(intent.intent_id, "close")
    executor.query_tactical_close = MagicMock(side_effect=[
        None,
        {
            "order_id": "close-1",
            "client_order_id": close_id,
            "status": "open",
            "filled_qty": 0.0,
            "remaining_qty": 500.0,
        },
    ])
    executor.cancel_tactical_protection = MagicMock(return_value={"cancelled_algo_ids": []})
    executor._fetch_okx_position_state = MagicMock(return_value={
        "side": "long", "available_contracts": 500.0,
    })
    kwargs = {
        "filled_qty": 500.0,
        "ownership_proof": executor.make_tactical_clord_id(intent.intent_id, "entry"),
        "reason": "risk_forced:flash_move",
    }

    first = executor.close_tactical_position(intent, **kwargs)
    second = executor.close_tactical_position(intent, **kwargs)

    assert first["client_order_id"] == close_id
    assert second["client_order_id"] == close_id
    assert second["recovered_existing_close"] is True
    executor.exchange.create_order.assert_called_once()
    assert first["reason"] == "risk_forced:flash_move"
