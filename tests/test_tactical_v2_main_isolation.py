import logging
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _v2_position(**overrides):
    position = {
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "entry_price": 1.0,
        "amount": 100.0,
        "amount_usdt": 100.0,
        "leverage": 5,
        "stop_loss": 0.95,
        "original_sl": 0.95,
        "take_profit": 1.08,
        "take_profit_levels": [1.08],
        "tp_filled": 0,
        "highest_price": 1.0,
        "lowest_price": 1.0,
        "open_time": 1000.0,
        "track": "tactical",
        "exit_profile": "tactical_v2",
        "strategy_owner": "tactical_v2",
        "intent_id": "intent-v2",
    }
    position.update(overrides)
    return position


def _position_analyst():
    from agents.trading.position_analyst import PositionAnalyst

    analyst = PositionAnalyst.__new__(PositionAnalyst)
    analyst.config = {}
    analyst.logger = logging.getLogger("test_tactical_v2_main_isolation")
    analyst._positions = {}
    analyst._tech_cache = {}
    analyst._prices = {}
    analyst._pending_reviews = {}
    analyst.publish = AsyncMock()
    return analyst


def _multi_executor(position):
    from agents.trading.executor import MultiExecutor

    agent = MultiExecutor.__new__(MultiExecutor)
    agent.config = {"early_review_enabled": True}
    agent.logger = logging.getLogger("test_tactical_v2_main_isolation")
    agent.publish = AsyncMock()
    agent.executor = SimpleNamespace(
        positions={position["symbol"]: position},
        get_all_positions=MagicMock(return_value={position["symbol"]: position}),
        get_position=MagicMock(return_value=position),
        check_stop_loss_take_profit=MagicMock(return_value="tactical_tp1"),
        reduce_position=MagicMock(),
        close_position=MagicMock(),
        move_protective_sl=MagicMock(return_value={"ok": True}),
        exchange=SimpleNamespace(fetch_ticker=MagicMock(return_value={"last": 0.97})),
        _normalize_symbol=lambda value: value,
    )
    agent._tactical_v2_controller = SimpleNamespace(close_for_safety=AsyncMock())
    return agent


def test_position_analyst_preserves_v2_owner_from_execution_result():
    analyst = _position_analyst()

    analyst._handle_execution({
        "status": "executed",
        "action": "open_long",
        "symbol": "WLD-USDT",
        "strategy_owner": "tactical_v2",
        "result": {
            "symbol": "WLD-USDT",
            "entry_price": 1.0,
            "amount_usdt": 100.0,
            "leverage": 5,
            "stop_loss": 0.95,
            "take_profit": 1.08,
            "strategy_owner": "tactical_v2",
            "intent_id": "intent-v2",
        },
    })

    assert analyst._positions["WLD-USDT"]["strategy_owner"] == "tactical_v2"
    assert analyst._positions["WLD-USDT"]["intent_id"] == "intent-v2"


@pytest.mark.asyncio
async def test_position_analyst_never_evaluates_or_publishes_v2(monkeypatch, tmp_path):
    analyst = _position_analyst()
    analyst._positions["WLD-USDT"] = _v2_position(symbol="WLD-USDT")
    analyst._compute_position_score = MagicMock(return_value={"action": "close"})
    monkeypatch.setattr(
        "utils.state_paths.get_state_paths",
        lambda: SimpleNamespace(positions=str(tmp_path / "missing.json")),
    )

    evaluated = await analyst._evaluate_all_positions()

    assert evaluated is False
    analyst._compute_position_score.assert_not_called()
    analyst.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_analyst_final_command_is_dropped_for_v2():
    analyst = _position_analyst()
    analyst._positions["WLD-USDT"] = _v2_position(symbol="WLD-USDT")

    await analyst._execute_final_decision({
        "symbol": "WLD-USDT",
        "final_action": "close",
        "reduce_pct": 1.0,
        "reasoning": "legacy review",
    })

    analyst.publish.assert_not_awaited()


def test_technical_invalidation_does_not_mutate_v2_position():
    position = _v2_position()
    before = deepcopy(position)
    agent = _multi_executor(position)

    agent._mark_tactical_thesis_from_tech(
        position["symbol"],
        {"entry_timing": {"tf_15m_block_long": True, "tf_15m_reason": "opposing"}},
    )

    assert position == before


@pytest.mark.asyncio
async def test_multi_executor_skips_v2_early_review_and_partial_tp():
    position = _v2_position(open_time=0)
    agent = _multi_executor(position)

    await agent._check_all_positions()
    direct_review = await agent._early_review(position["symbol"], position, 25)
    await agent._handle_partial_tp_trigger(position["symbol"], "tactical_tp1")

    assert direct_review is False
    agent.executor.exchange.fetch_ticker.assert_not_called()
    agent.executor.check_stop_loss_take_profit.assert_not_called()
    agent.executor.reduce_position.assert_not_called()
    agent.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_analyst_close_cannot_reach_generic_v2_close():
    position = _v2_position()
    agent = _multi_executor(position)
    agent._trading_halted = False
    agent._halt_state = SimpleNamespace(can_open_new=True)
    agent.min_confidence = 60

    await agent._execute_decision({
        "action": "close",
        "confidence": 90,
        "symbol": position["symbol"],
        "source": "position_analyst",
        "request_id": "pa-v2-close",
    })

    agent.executor.close_position.assert_not_called()


@pytest.mark.asyncio
async def test_global_safety_uses_owner_bound_v2_close_and_risk_attribution():
    position = _v2_position()
    agent = _multi_executor(position)

    await agent._handle_risk_alert({
        "type": "emergency_close",
        "symbol": position["symbol"],
        "reason": "manual_emergency",
    })

    agent._tactical_v2_controller.close_for_safety.assert_awaited_once_with(
        position["symbol"], source="manual_emergency"
    )
    agent.executor.close_position.assert_not_called()


def test_main_algo_migration_preserves_v2_protection_before_local_position_exists(
    monkeypatch,
):
    from executor import ContractExecutor

    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = ContractExecutor.__new__(ContractExecutor)
    executor.logger = logging.getLogger("test_tactical_v2_main_isolation")
    executor.exchange_id = "okx"
    executor.testnet = True
    executor.positions = {}
    executor._load_sidecar_owner_registry = MagicMock(return_value=None)
    v2_tp_id = executor.make_tactical_clord_id("intent-v2", "tp")
    executor._list_pending_algos = MagicMock(return_value=[{
        "algoId": "v2-oco",
        "algoClOrdId": v2_tp_id,
        "ordType": "oco",
        "side": "sell",
        "tp_trigger": "1.08",
        "sl_trigger": "0.95",
        "quantity": "500",
    }])
    executor._cancel_algo_by_id = MagicMock(return_value=True)
    executor._sidecar_symbol_exchange_state = MagicMock(return_value="absent")

    summary = executor._migrate_okx_algos_for_symbol("WLD-USDT-SWAP")

    assert summary["tactical_v2_preserved_algos"] == 1
    executor._cancel_algo_by_id.assert_not_called()
