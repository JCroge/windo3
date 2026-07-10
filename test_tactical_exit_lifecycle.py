import time

import pytest


def make_executor():
    from executor import ContractExecutor

    ex = ContractExecutor.__new__(ContractExecutor)
    ex._config = {"tactical_max_hold_minutes": 90}
    ex._move_sl = lambda symbol, pos, price: pos.update({"stop_loss": price})
    ex.positions = {}
    ex._sl_check_failures = {}
    ex._sl_max_failures = 3
    ex.logger = type(
        "L",
        (),
        {
            "info": lambda *a, **k: None,
            "warning": lambda *a, **k: None,
            "error": lambda *a, **k: None,
        },
    )()
    return ex


def tactical_position():
    return {
        "symbol": "WLD-USDT-SWAP",
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "side": "short",
        "entry_price": 0.385,
        "stop_loss": 0.3904,
        "original_sl": 0.3904,
        "take_profit": 0.38176,
        "take_profit_levels": [0.38176],
        "tp_filled": 0,
        "highest_price": 0.385,
        "lowest_price": 0.385,
        "open_time": time.time(),
        "tactical_max_hold_minutes": 90,
    }


def test_tactical_tp1_triggers_local_partial():
    ex = make_executor()
    pos = tactical_position()

    assert ex._update_trailing("WLD-USDT-SWAP", pos, 0.3817) == "tactical_tp1"


def test_tactical_max_hold_triggers_close():
    ex = make_executor()
    pos = tactical_position()
    pos["open_time"] = time.time() - 91 * 60

    assert ex._update_trailing("WLD-USDT-SWAP", pos, 0.3840) == "tactical_max_hold"


def test_tactical_invalidated_thesis_triggers_fast_close():
    ex = make_executor()
    pos = tactical_position()
    pos["tactical_thesis_state"] = "invalidated"
    pos["tactical_thesis_reason"] = "15m_opposing_close"

    assert ex._update_trailing("WLD-USDT-SWAP", pos, 0.3840) == "tactical_invalidated"
    assert pos["tactical_close_reason"] == "tactical_invalidated"
    assert pos["tactical_close_detail"] == "15m_opposing_close"


def test_tactical_weakened_no_progress_triggers_close_after_window():
    ex = make_executor()
    ex._config.update({
        "tactical_weakened_no_progress_min_minutes": 30,
        "tactical_min_progress_r": 0.15,
    })
    pos = tactical_position()
    pos["tactical_thesis_state"] = "weakened"
    pos["tactical_last_progress_time"] = time.time() - 31 * 60
    pos["tactical_best_profit_r"] = 0.0

    assert ex._update_trailing("WLD-USDT-SWAP", pos, 0.3848) == "tactical_weakened_no_progress"
    assert pos["tactical_close_reason"] == "tactical_weakened_no_progress"


@pytest.mark.asyncio
async def test_tactical_15m_opposing_tech_marks_thesis_invalidated():
    from agents.trading.executor import MultiExecutor

    agent = MultiExecutor.__new__(MultiExecutor)
    pos = tactical_position()
    agent.executor = type("E", (), {"positions": {"WLD-USDT": pos}})()
    agent.logger = type("L", (), {"info": lambda *a, **k: None})()

    await agent.on_message({
        "type": "tech_analysis",
        "symbol": "WLD-USDT",
        "payload": {
            "symbol": "WLD-USDT",
            "entry_timing": {
                "tf_15m_block_short": True,
                "tf_15m_reason": "15m opposing close",
            },
        },
    })

    assert pos.get("tactical_thesis_state") == "invalidated"
    assert pos.get("tactical_thesis_reason") == "15m opposing close"


def test_tactical_stop_loss_still_triggers():
    ex = make_executor()
    pos = tactical_position()
    ex.positions = {"WLD-USDT-SWAP": pos}
    ex._fetch_price_robust = lambda symbol: 0.3905

    assert ex.check_stop_loss_take_profit("WLD-USDT-SWAP") == "stop_loss"
