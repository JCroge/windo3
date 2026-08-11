import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _paths(tmp_path, namespace="testnet"):
    return SimpleNamespace(
        namespace=namespace,
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
        tactical_v2_status=str(tmp_path / "status.json"),
    )


def _candidate(symbol="WLD-USDT", candidate_id="cand-1", created_at=1000.0):
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
        "tf_15m_closed_bar_ts": 900,
        "tf_15m_structure_token": f"break_up:{symbol}",
        "tf_15m_block_long": False,
    }


def _executor():
    return SimpleNamespace(
        positions={},
        create_order=MagicMock(side_effect=AssertionError("shadow called create_order")),
        cancel_order=MagicMock(side_effect=AssertionError("shadow called cancel_order")),
        close_position=MagicMock(side_effect=AssertionError("shadow called close_position")),
    )


def _controller(tmp_path, mode="shadow"):
    from utils.tactical_v2.controller import TacticalV2Controller

    executor = _executor()
    controller = TacticalV2Controller(
        executor=executor,
        config={"tactical_v2_mode": mode},
        paths=_paths(tmp_path),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        publish=None,
        now_fn=lambda: 1000.0,
    )
    return controller, executor


@pytest.mark.asyncio
async def test_shadow_candidate_and_quote_never_reach_exchange_methods(tmp_path):
    controller, executor = _controller(tmp_path)

    accepted = await controller.handle_candidate(_candidate(), now=1000)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.0, "ask": 1.001, "timestamp": 1000},
        now=1000,
    )
    snapshot = controller.snapshot(now=1000)

    assert accepted.reason == "accepted"
    assert snapshot["active_slots"] == 1
    assert snapshot["intents"][0]["state"] == "protected"
    executor.create_order.assert_not_called()
    executor.cancel_order.assert_not_called()
    executor.close_position.assert_not_called()


@pytest.mark.asyncio
async def test_three_ready_or_pending_slots_are_counted_and_fourth_is_terminal(tmp_path):
    controller, _ = _controller(tmp_path)

    for index, symbol in enumerate(("WLD-USDT", "ETH-USDT", "SOL-USDT")):
        result = await controller.handle_candidate(
            _candidate(symbol=symbol, candidate_id=f"cand-{index}"),
            now=1000,
        )
        assert result.reason == "accepted"
    fourth = await controller.handle_candidate(
        _candidate(symbol="XRP-USDT", candidate_id="cand-4"),
        now=1000,
    )

    snapshot = controller.snapshot(now=1000)
    assert fourth.reason == "capacity_skipped"
    assert snapshot["active_slots"] == 3
    assert snapshot["episode_outcomes"]["capacity_skipped"] == 1


@pytest.mark.asyncio
async def test_repeated_same_structure_is_not_retried(tmp_path):
    controller, _ = _controller(tmp_path)

    first = await controller.handle_candidate(_candidate(candidate_id="cand-1"), now=1000)
    repeated = await controller.handle_candidate(
        {**_candidate(candidate_id="cand-2"), "entry_ref": 1.001},
        now=1001,
    )

    assert first.reason == "accepted"
    assert repeated.reason == "duplicate_episode"
    assert controller.snapshot(now=1001)["active_slots"] == 1


@pytest.mark.asyncio
async def test_old_shadow_fill_closes_once_after_registry_advances_epoch(tmp_path):
    controller, _ = _controller(tmp_path)
    first = await controller.handle_candidate(_candidate(), now=1000)
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 1.0, "ask": 1.001, "timestamp": 1000},
        now=1000,
    )
    await controller.handle_structure(
        "WLD-USDT",
        {
            "tf_15m_available": True,
            "tf_15m_bias": "neutral",
            "tf_15m_closed_bar_ts": 915,
            "tf_15m_structure_token": None,
            "tf_15m_block_long": False,
        },
        now=1001,
    )
    renewed = await controller.handle_candidate(
        {
            **_candidate(candidate_id="cand-2"),
            "tf_15m_closed_bar_ts": 930,
            "tf_15m_structure_token": "break_up:WLD-USDT:2",
        },
        now=1001,
    )

    assert first.accepted is True
    assert renewed.reason == "same_symbol_exposure"
    assert renewed.episode_id != first.episode_id

    target_quote = {"bid": 1.08, "ask": 1.081, "timestamp": 1002}
    await controller.handle_quote("WLD-USDT", target_quote, now=1002)
    await controller.handle_quote("WLD-USDT", target_quote, now=1003)

    snapshot = controller.snapshot(now=1003)
    first_intent = next(
        row for row in snapshot["intents"] if row["episode_id"] == first.episode_id
    )
    assert first_intent["state"] == "closed_final"
    assert first_intent["close_reason"] == "tactical_tp1"
    assert snapshot["episode_outcomes"]["tactical_tp1"] == 1
    assert snapshot["active_slots"] == 0

    restarted, _ = _controller(tmp_path)
    restored = restarted.snapshot(now=1003)
    restored_first = next(
        row for row in restored["intents"] if row["episode_id"] == first.episode_id
    )
    assert restored_first["state"] == "closed_final"
    assert restored["episode_outcomes"]["tactical_tp1"] == 1


@pytest.mark.asyncio
async def test_cross_namespace_and_stale_replay_without_receipts_remain_unknown(tmp_path):
    controller, _ = _controller(tmp_path)

    cross = await controller.handle_candidate(
        {**_candidate(), "namespace": "live"},
        now=1000,
        replayed=True,
    )
    stale = await controller.handle_candidate(
        _candidate(candidate_id="stale", created_at=99),
        now=1000,
        replayed=True,
    )

    assert cross.reason == "unknown_handling_evidence"
    assert stale.reason == "unknown_handling_evidence"
    snapshot = controller.snapshot(now=1000)
    assert snapshot["active_slots"] == 0
    assert snapshot["candidate_handling"]["unknown_handling_evidence"] == 2
    assert [
        event["event_type"] for event in controller.store.read_events()
    ] == [
        "candidate_handling_gap_recorded",
        "candidate_handling_gap_recorded",
    ]


def test_shadow_projection_does_not_block_main_or_consume_live_capacity(tmp_path):
    controller, _ = _controller(tmp_path)

    assert controller.blocks_main_symbol("WLD-USDT") is False


def test_multi_executor_subscribes_to_all_controller_inputs():
    from agents.trading.executor import MultiExecutor

    assert "tactical_candidate.v2" in MultiExecutor.subscriptions
    assert "price_tick:*" in MultiExecutor.subscriptions
    assert "tech_analysis:*" in MultiExecutor.subscriptions
    assert "pnl_resolved" in MultiExecutor.subscriptions
    assert "pnl_mismatch" in MultiExecutor.subscriptions


@pytest.mark.asyncio
async def test_material_transition_and_periodic_tick_refresh_atomic_status(tmp_path):
    controller, _ = _controller(tmp_path)

    initial = json.loads((tmp_path / "status.json").read_text())
    assert initial["slots"] == {"active": 0, "pending": 0, "free": 3}

    await controller.handle_candidate(_candidate(), now=1000.0)
    admitted = json.loads((tmp_path / "status.json").read_text())
    assert admitted["slots"] == {"active": 0, "pending": 1, "free": 2}
    assert admitted["symbols"]["pending"] == ["WLD-USDT"]

    await controller.tick(now=1031.0)
    periodic = json.loads((tmp_path / "status.json").read_text())
    assert periodic["updated_at"] == 1031.0


@pytest.mark.asyncio
async def test_pnl_mismatch_status_is_integrity_halt_not_timed_pause(tmp_path):
    controller, _ = _controller(tmp_path)

    await controller.handle_pnl_mismatch({
        "strategy_owner": "tactical_v2",
        "resolution_id": "mismatch-1",
    })

    snapshot = json.loads((tmp_path / "status.json").read_text())
    assert snapshot["integrity_halt"]["reason"] == "pnl_mismatch"
    assert snapshot["timed_pause_until"] == 0.0
