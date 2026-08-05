import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest


def _paths(tmp_path):
    return SimpleNamespace(
        namespace="testnet",
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
        tactical_v2_status=str(tmp_path / "status.json"),
    )


def _candidate(created_at=1000.0, *, symbol="WLD-USDT", candidate_id="candidate-parity-1"):
    return {
        "candidate_id": candidate_id,
        "namespace": "testnet",
        "symbol": symbol,
        "side": "long",
        "entry_ref": 1.0,
        "stop_loss": 0.95,
        "take_profit": 1.08,
        "leverage": 5,
        "source_shadow_id": f"shadow-{candidate_id}",
        "tactical_source": "main_quality_failed",
        "created_at": created_at,
        "tf_15m_available": True,
        "tf_15m_bias": "bullish",
        "tf_15m_closed_bar_ts": 900.0,
        "tf_15m_structure_token": f"break_up:{symbol}",
        "tf_15m_block_long": False,
    }


class ParityExecutor:
    def __init__(self):
        self.positions = {}
        self.entry_observation = {
            "order_id": "entry-1",
            "status": "open",
            "filled_qty": 0.0,
            "remaining_qty": 10.0,
            "average_price": None,
        }
        self.position_qty = 0.0

    @staticmethod
    def _normalize_symbol(symbol):
        return symbol if symbol.endswith("-SWAP") else f"{symbol}-SWAP"

    @staticmethod
    def make_tactical_clord_id(intent_id, purpose):
        return f"TV2{purpose[:2]}{intent_id[:20]}"

    def submit_tactical_entry(self, intent, *, order_type):
        return {
            **self.entry_observation,
            "requested_qty": 10.0,
            "entry_client_id": self.make_tactical_clord_id(intent.intent_id, "entry"),
            "tp_client_id": self.make_tactical_clord_id(intent.intent_id, "tp"),
            "sl_client_id": self.make_tactical_clord_id(intent.intent_id, "sl"),
        }

    def query_tactical_entry(self, intent):
        return dict(self.entry_observation)

    def cancel_tactical_entry(self, intent):
        return {"proven": True, "filled_qty": self.entry_observation["filled_qty"]}

    def verify_tactical_protection(self, intent, *, filled_qty):
        return {
            "complete": True,
            "reason": "complete",
            "representation": "separate",
            "protected_qty": filled_qty,
            "tp_algo_ids": ["tp-algo"],
            "sl_algo_ids": ["sl-algo"],
        }

    def cancel_tactical_protection(self, intent):
        return {"cancelled_algo_ids": ["tp-algo", "sl-algo"]}

    def _fetch_okx_position_state(self, symbol, raise_on_error=True):
        if self.position_qty <= 0:
            return None
        return {"symbol": symbol, "side": "long", "available_contracts": self.position_qty}

    def close_tactical_position(self, intent, **kwargs):
        self.position_qty = 0.0
        return {
            "status": "submitted",
            "order_id": "close-1",
            "client_order_id": self.make_tactical_clord_id(intent.intent_id, "close"),
        }

    def _save_positions(self):
        return None


def _controller(tmp_path, *, mode="live", executor=None):
    from utils.tactical_v2.controller import TacticalV2Controller

    return TacticalV2Controller(
        executor=executor or ParityExecutor(),
        config={"tactical_v2_mode": mode},
        paths=_paths(tmp_path),
        logger=logging.getLogger("test_tactical_v2_parity"),
        publish=None,
        now_fn=lambda: 1000.0,
    )


def test_shared_exit_reducer_uses_executable_side_and_full_tp1():
    from utils.tactical_v2.entry import ExecutableQuote
    from utils.tactical_v2.exit import classify_exit

    intent = SimpleNamespace(
        side="long",
        stop_loss=0.95,
        take_profit=1.08,
        max_hold_seconds=5400,
    )

    ask_only_touch = classify_exit(
        intent,
        entry_price=1.0,
        opened_at=1000.0,
        quote=ExecutableQuote(bid=1.079, ask=1.08, observed_at=1010.0),
        now=1010.0,
    )
    executable_touch = classify_exit(
        intent,
        entry_price=1.0,
        opened_at=1000.0,
        quote=ExecutableQuote(bid=1.08, ask=1.081, observed_at=1011.0),
        now=1011.0,
    )

    assert ask_only_touch.action == "hold"
    assert executable_touch.action == "close"
    assert executable_touch.reason == "tactical_tp1"
    assert executable_touch.close_fraction == 1.0
    assert executable_touch.executable_price == 1.08


@pytest.mark.asyncio
async def test_live_intent_records_shadow_and_live_transitions_with_attribution(tmp_path):
    executor = ParityExecutor()
    controller = _controller(tmp_path, executor=executor)
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)

    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.0, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )

    first = controller.snapshot(now=1000.0)
    assert first["intents"][0]["state"] == "pending_entry"
    assert first["intents"][0]["shadow_state"] == "protected"
    assert first["parity"] == {
        "compared_intents": 1,
        "mismatch_count": 1,
        "categories": {"exchange_fill": 1},
        "shadow_filled": 1,
        "shadow_nonfilled": 0,
    }

    executor.entry_observation.update({
        "status": "filled",
        "filled_qty": 10.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    })
    executor.position_qty = 10.0
    await controller.tick(now=1001.0)

    matched = controller.snapshot(now=1001.0)
    assert matched["intents"][0]["state"] == "protected"
    assert matched["intents"][0]["shadow_state"] == "protected"
    assert matched["parity"]["mismatch_count"] == 0

    rows = [
        json.loads(line)
        for line in Path(tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    transitions = [
        row for row in rows
        if row["event_type"] == "intent_transition"
        and row["data"].get("intent_id") == accepted.intent_id
    ]
    assert {row["data"].get("lane") for row in transitions} == {"live", "shadow"}
    assert any(
        row["event_type"] == "parity_compared"
        and row["data"].get("category") == "exchange_fill"
        for row in rows
    )


@pytest.mark.asyncio
async def test_shadow_and_live_full_tp1_close_converges_without_main_exit(tmp_path):
    executor = ParityExecutor()
    controller = _controller(tmp_path, executor=executor)
    await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.0, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.entry_observation.update({
        "status": "filled",
        "filled_qty": 10.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    })
    executor.position_qty = 10.0
    await controller.tick(now=1001.0)

    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.08, "ask": 1.081, "timestamp": 1010.0},
        now=1010.0,
    )
    projected = controller.snapshot(now=1010.0)
    assert projected["intents"][0]["shadow_state"] == "closed_final"
    assert projected["intents"][0]["shadow_close_reason"] == "tactical_tp1"
    assert projected["parity"]["categories"] == {"exchange_fill": 1}

    executor.position_qty = 0.0
    await controller.tick(now=1011.0)

    converged = controller.snapshot(now=1011.0)
    assert converged["intents"][0]["state"] == "exchange_closed_pending_pnl"
    assert converged["intents"][0]["shadow_state"] == "closed_final"
    assert converged["parity"]["mismatch_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quote,closed_at,reason",
    [
        ({"bid": 0.949, "ask": 0.95, "timestamp": 1010.0}, 1010.0, "tactical_sl"),
        ({"bid": 1.01, "ask": 1.011, "timestamp": 6400.0}, 6400.0, "tactical_max_hold"),
    ],
)
async def test_shadow_projection_uses_shared_sl_and_max_hold_lifecycle(
    tmp_path,
    quote,
    closed_at,
    reason,
):
    controller = _controller(tmp_path, mode="shadow")
    await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.0, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )

    await controller.handle_quote("WLD-USDT", quote, now=closed_at)

    intent = controller.snapshot(now=closed_at)["intents"][0]
    assert intent["state"] == "closed_final"
    assert intent["close_reason"] == reason
    assert intent["shadow_filled"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["shadow", "live"])
async def test_pending_shadow_lane_consumes_structure_invalidation(tmp_path, mode):
    controller = _controller(tmp_path, mode=mode)
    await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.019, "ask": 1.02, "timestamp": 1000.0},
        now=1000.0,
    )

    await controller.handle_structure(
        "WLD-USDT",
        {
            "entry_timing": {
                "tf_15m_available": True,
                "tf_15m_bias": "bearish",
                "tf_15m_closed_bar_ts": 1800.0,
                "tf_15m_structure_token": "break_down:wld",
                "tf_15m_block_long": True,
            }
        },
        now=1010.0,
    )

    intent = controller.snapshot(now=1010.0)["intents"][0]
    assert intent["state"] == "entry_terminal"
    assert intent["terminal_reason"] == "structure_invalidated"
    assert intent["shadow_state"] == "entry_terminal"
    assert intent["shadow_terminal_reason"] == "structure_invalidated"
    if mode == "live":
        assert intent["parity_category"] is None


@pytest.mark.asyncio
async def test_structure_invalidation_before_first_quote_consumes_live_and_shadow_lanes(tmp_path):
    controller = _controller(tmp_path, mode="live")
    await controller.handle_candidate(_candidate(), now=1000.0)

    await controller.handle_structure(
        "WLD-USDT",
        {
            "entry_timing": {
                "tf_15m_available": True,
                "tf_15m_bias": "bearish",
                "tf_15m_closed_bar_ts": 1800.0,
                "tf_15m_structure_token": "break_down:wld",
                "tf_15m_block_long": True,
            }
        },
        now=1001.0,
    )

    intent = controller.snapshot(now=1001.0)["intents"][0]
    assert intent["state"] == "entry_terminal"
    assert intent["terminal_reason"] == "structure_invalidated"
    assert intent["shadow_state"] == "entry_terminal"
    assert intent["shadow_terminal_reason"] == "structure_invalidated"
    assert intent["parity_category"] is None


@pytest.mark.asyncio
async def test_shadow_nonfill_is_reported_separately_from_filled_performance(tmp_path):
    controller = _controller(tmp_path, mode="shadow")
    await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.019, "ask": 1.02, "timestamp": 1000.0},
        now=1000.0,
    )
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.08, "ask": 1.081, "timestamp": 1010.0},
        now=1010.0,
    )

    status = controller.operational_status(now=1010.0)

    assert status["parity"]["shadow_filled"] == 0
    assert status["parity"]["shadow_nonfilled"] == 1
    assert status["episode_outcomes"]["missed_after_target"] == 1


@pytest.mark.asyncio
async def test_restart_restores_live_and_shadow_lanes_independently(tmp_path):
    executor = ParityExecutor()
    controller = _controller(tmp_path, executor=executor)
    await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.0, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )

    restarted = _controller(tmp_path, executor=executor)
    restored = restarted.snapshot(now=1001.0)

    assert restored["intents"][0]["state"] == "pending_entry"
    assert restored["intents"][0]["shadow_state"] == "protected"
    assert restored["parity"]["categories"] == {"exchange_fill": 1}

    executor.entry_observation.update({
        "status": "filled",
        "filled_qty": 10.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    })
    executor.position_qty = 10.0
    await restarted.tick(now=1001.0)

    assert restarted.snapshot(now=1001.0)["parity"]["mismatch_count"] == 0


@pytest.mark.asyncio
async def test_live_rejection_is_attributed_against_shadow_fill(tmp_path):
    executor = ParityExecutor()
    controller = _controller(tmp_path, executor=executor)
    await controller.handle_candidate(_candidate(), now=1000.0)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.0, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )
    executor.entry_observation.update({
        "status": "rejected",
        "filled_qty": 0.0,
        "remaining_qty": 0.0,
    })

    await controller.tick(now=1001.0)

    snapshot = controller.snapshot(now=1001.0)
    assert snapshot["intents"][0]["state"] == "entry_terminal"
    assert snapshot["intents"][0]["shadow_state"] == "protected"
    assert snapshot["parity"]["categories"] == {"order_rejection": 1}


@pytest.mark.asyncio
async def test_same_symbol_account_rejection_keeps_shadow_projection(tmp_path):
    executor = ParityExecutor()
    executor.positions["WLD-USDT-SWAP"] = {
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "strategy_owner": "main",
    }
    controller = _controller(tmp_path, executor=executor)

    rejected = await controller.handle_candidate(_candidate(), now=1000.0)

    assert rejected.accepted is False
    assert rejected.reason == "same_symbol_exposure"
    assert rejected.intent_id
    before_quote = controller.snapshot(now=1000.0)
    assert before_quote["active_slots"] == 0
    assert before_quote["intents"][0]["state"] == "entry_terminal"
    assert before_quote["intents"][0]["shadow_state"] == "ready_for_quote"
    assert before_quote["parity"]["categories"] == {
        "same_symbol_account_exposure": 1
    }

    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.0, "ask": 1.001, "timestamp": 1000.0},
        now=1000.0,
    )

    projected = controller.snapshot(now=1000.0)
    assert projected["intents"][0]["shadow_state"] == "protected"
    assert projected["parity"]["categories"] == {
        "same_symbol_account_exposure": 1
    }


@pytest.mark.asyncio
async def test_shadow_projection_has_independent_three_slot_cap(tmp_path):
    controller = _controller(tmp_path)
    for index, symbol in enumerate(("WLD-USDT", "SOL-USDT", "ETH-USDT")):
        accepted = await controller.handle_candidate(
            _candidate(symbol=symbol, candidate_id=f"candidate-{index}"),
            now=1000.0,
        )
        assert accepted.accepted is True
        await controller.handle_quote(
            symbol,
            {"bid": 1.0, "ask": 1.001, "timestamp": 1000.0},
            now=1000.0,
        )

    fourth = await controller.handle_candidate(
        _candidate(symbol="XRP-USDT", candidate_id="candidate-4"),
        now=1000.0,
    )

    assert fourth.accepted is False
    assert fourth.reason == "capacity_skipped"
    row = next(
        item for item in controller.snapshot(now=1000.0)["intents"]
        if item["intent_id"] == fourth.intent_id
    )
    assert row["state"] == "entry_terminal"
    assert row["shadow_state"] == "entry_terminal"
    assert row["parity_category"] is None
