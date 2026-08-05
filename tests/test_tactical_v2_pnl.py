import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _paths(tmp_path):
    return SimpleNamespace(
        namespace="testnet",
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
        tactical_v2_status=str(tmp_path / "status.json"),
    )


def _controller(tmp_path, now=1000.0):
    from utils.tactical_v2.controller import TacticalV2Controller

    executor = SimpleNamespace(positions={})
    controller = TacticalV2Controller(
        executor=executor,
        config={"tactical_v2_mode": "shadow"},
        paths=_paths(tmp_path),
        logger=logging.getLogger("test_tactical_v2_pnl"),
        publish=None,
        now_fn=lambda: now,
    )
    return controller


def _final_payload(resolution_id="r1", pnl=-9.0):
    return {
        "resolution_id": resolution_id,
        "position_id": "tv2-position-1",
        "entry_request_id": "entry-client-1",
        "strategy_owner": "tactical_v2",
        "intent_id": "intent-1",
        "episode_id": "episode-1",
        "plan_hash": "plan-1",
        "pnl_status": "final",
        "pnl_is_final": True,
        "realized_pnl_net_usdt": pnl,
        "timestamp": 1000.0,
        "close_cause": "exchange_sl",
        "tp_algo_ids": ["tp-1"],
        "sl_algo_ids": ["sl-1"],
    }


@pytest.mark.asyncio
async def test_controller_routes_bus_final_and_correction_exactly_once(tmp_path):
    controller = _controller(tmp_path)

    await controller.handle_pnl_resolution(_final_payload("r1", -9.0))
    await controller.handle_pnl_resolution(_final_payload("r1", -9.0))
    await controller.handle_pnl_resolution(_final_payload("r2", -4.0))

    assert controller.governor.rolling_pnl == -4.0
    assert controller.governor.final_episode_count == 1
    event_types = [
        json.loads(line)["event_type"]
        for line in Path(tmp_path / "events.jsonl").read_text().splitlines()
    ]
    assert event_types.count("governor_final_applied") == 2


@pytest.mark.asyncio
async def test_non_v2_final_is_not_imported(tmp_path):
    controller = _controller(tmp_path)

    await controller.handle_pnl_resolution(
        {**_final_payload(), "strategy_owner": "main", "intent_id": ""}
    )

    assert controller.governor.final_episode_count == 0


@pytest.mark.asyncio
async def test_explicit_non_v2_owner_cannot_borrow_known_intent_id(tmp_path):
    controller = _controller(tmp_path)
    accepted = await controller.handle_candidate({
        "candidate_id": "candidate-1",
        "namespace": "testnet",
        "symbol": "WLD-USDT",
        "side": "long",
        "entry_ref": 1.0,
        "stop_loss": 0.95,
        "take_profit": 1.08,
        "leverage": 5,
        "source_shadow_id": "shadow-1",
        "tactical_source": "main_quality_failed",
        "created_at": 1000.0,
        "tf_15m_available": True,
        "tf_15m_bias": "bullish",
        "tf_15m_closed_bar_ts": 900.0,
        "tf_15m_structure_token": "break_up:wld",
        "tf_15m_block_long": False,
    }, now=1000.0)

    await controller.handle_pnl_resolution({
        **_final_payload(),
        "strategy_owner": "main",
        "intent_id": accepted.intent_id,
    })

    assert controller.governor.final_episode_count == 0


@pytest.mark.asyncio
async def test_final_delivery_uses_receive_time_for_fresh_status(tmp_path):
    controller = _controller(tmp_path, now=2000.0)
    payload = {
        **_final_payload("r-late", -2.0),
        "timestamp": 1000.0,
        "status": "closed_externally",
        "pnl_status": "final",
    }

    await controller.handle_pnl_resolution(payload)

    status = json.loads((tmp_path / "status.json").read_text())
    assert controller.governor.rolling_pnl == -2.0
    assert status["updated_at"] == 2000.0


@pytest.mark.asyncio
async def test_executor_routes_final_to_controller_before_other_agents():
    from agents.trading.executor import MultiExecutor

    observed = []
    agent = MultiExecutor.__new__(MultiExecutor)
    agent._tactical_v2_controller = SimpleNamespace(
        handle_pnl_resolution=AsyncMock(side_effect=lambda payload: observed.append("controller")),
        handle_pnl_mismatch=AsyncMock(),
    )

    async def publish(topic, payload, symbol=None):
        observed.append("publish")

    agent.publish = publish
    await agent._route_pnl_event("pnl_resolved", _final_payload(), symbol="WLD-USDT")

    assert observed == ["controller", "publish"]


@pytest.mark.asyncio
async def test_resolver_forwards_tactical_v2_proof_to_controller():
    from agents.trading.executor import MultiExecutor

    proof = {
        "complete": True,
        "entry_request_id": "entry-client-1",
        "entry_order_ids": ["entry-1"],
        "close_order_ids": ["close-1"],
        "entry_qty": 500.0,
        "close_qty": 500.0,
        "entry_fee_usdt": -0.25,
    }
    resolution = {
        "pnl_status": "final",
        "pnl_source": "okx_fills_history",
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "position_id": "tv2:intent-1",
        "entry_request_id": "entry-client-1",
        "realized_pnl_net_usdt": 1.25,
        "gross_close_pnl_usdt": 1.65,
        "fee_usdt": -0.40,
        "funding_usdt": 0.0,
        "order_ids": ["close-1"],
        "bill_ids": [],
        "entry_attribution": {
            "strategy_owner": "tactical_v2",
            "intent_id": "intent-1",
            "episode_id": "episode-1",
            "plan_hash": "plan-1",
        },
        "tactical_v2_proof": proof,
    }
    agent = MultiExecutor.__new__(MultiExecutor)
    agent.executor = SimpleNamespace(ledger=None)
    agent.logger = MagicMock()
    agent._pnl_resolver = SimpleNamespace(
        resolve_external_close=MagicMock(return_value=resolution)
    )
    agent._route_pnl_event = AsyncMock()

    await agent._resolve_external_close_async(
        {
            "symbol": "WLD-USDT-SWAP",
            "side": "long",
            "strategy_owner": "tactical_v2",
            "intent_id": "intent-1",
            "episode_id": "episode-1",
            "plan_hash": "plan-1",
            "position_id": "tv2:intent-1",
            "entry_request_id": "entry-client-1",
            "attribution": resolution["entry_attribution"],
        },
        {"closed_at": 1000.0},
        "entry-client-1",
    )

    payload = agent._route_pnl_event.await_args.args[1]
    assert payload["tactical_v2_proof"] == proof


@pytest.mark.asyncio
async def test_external_close_pending_payload_keeps_v2_owner_metadata():
    from agents.trading.executor import MultiExecutor

    position = {
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "entry_price": 1.0,
        "amount_usdt": 100.0,
        "leverage": 5,
        "strategy_owner": "tactical_v2",
        "intent_id": "intent-1",
        "episode_id": "episode-1",
        "plan_hash": "plan-1",
        "entry_request_id": "entry-client-1",
        "position_id": "tv2-position-1",
        "tp_algo_ids": ["tp-1"],
        "sl_algo_ids": ["sl-1"],
        "tactical_close_reason": "exchange_tp_or_sl",
        "attribution": {"strategy_owner": "tactical_v2"},
    }
    executor = SimpleNamespace(
        ledger=None,
        get_removed_symbols=MagicMock(return_value=["WLD-USDT-SWAP"]),
        get_removed_positions_data=MagicMock(return_value=[position]),
    )
    published = []
    agent = MultiExecutor.__new__(MultiExecutor)
    agent.executor = executor
    agent.logger = MagicMock()
    agent._pnl_resolver = None
    agent._tactical_v2_controller = None
    agent._estimate_close_pnl = lambda pos: 1.25
    agent._make_correlation_id = lambda symbol: "corr-1"

    async def publish(topic, payload, symbol=None):
        published.append((topic, payload, symbol))

    agent.publish = publish
    await agent._notify_removed_positions()

    result = published[0][1]["result"]
    assert result["strategy_owner"] == "tactical_v2"
    assert result["intent_id"] == "intent-1"
    assert result["episode_id"] == "episode-1"
    assert result["plan_hash"] == "plan-1"
    assert result["tp_algo_ids"] == ["tp-1"]
    assert result["sl_algo_ids"] == ["sl-1"]


@pytest.mark.asyncio
async def test_final_arriving_before_flat_poll_consumes_episode_for_later_structure(tmp_path):
    from tests.test_tactical_v2_parity import ParityExecutor, _candidate
    from utils.tactical_v2.controller import TacticalV2Controller

    executor = ParityExecutor()
    paths = SimpleNamespace(
        namespace="testnet",
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
        tactical_v2_status=str(tmp_path / "status.json"),
    )
    controller = TacticalV2Controller(
        executor=executor,
        config={"tactical_v2_mode": "live"},
        paths=paths,
        logger=logging.getLogger("test_tactical_v2_pnl_ordering"),
        publish=None,
        now_fn=lambda: 1010.0,
    )
    accepted = await controller.handle_candidate(_candidate(), now=1000.0)
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
    record = controller._intents[accepted.intent_id]

    await controller.handle_pnl_resolution({
        "resolution_id": "resolution-early",
        "position_id": f"tv2:{accepted.intent_id}",
        "entry_request_id": executor.make_tactical_clord_id(accepted.intent_id, "entry"),
        "strategy_owner": "tactical_v2",
        "intent_id": accepted.intent_id,
        "episode_id": accepted.episode_id,
        "plan_hash": record["intent"].plan_hash,
        "pnl_status": "final",
        "realized_pnl_net_usdt": 1.0,
        "timestamp": 1010.0,
        "close_cause": "exchange_tp",
    })
    executor.position_qty = 0.0
    executor.positions.clear()

    renewed = await controller.handle_candidate({
        **_candidate(created_at=1011.0, candidate_id="candidate-new-structure"),
        "tf_15m_closed_bar_ts": 1800.0,
        "tf_15m_structure_token": "break_up:wld:new",
    }, now=1011.0)

    assert controller.snapshot(now=1011.0)["intents"][0]["state"] == "closed_final"
    assert renewed.accepted is True
    assert renewed.episode_id != accepted.episode_id
