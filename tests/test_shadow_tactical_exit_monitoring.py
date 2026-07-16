import logging
import time
from unittest.mock import MagicMock

from executor import ContractExecutor


SYMBOL = "ONDO-USDT-SWAP"


def _executor_with_position(
    *,
    price=1.26,
    tp_filled=0,
    open_age_minutes=0,
    extra_position=None,
):
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger("test_shadow_tactical_exit_monitoring")
    ex.positions = {}
    ex._config = {
        "tactical_max_hold_minutes": 90,
        "tactical_min_progress_r": 0.15,
        "tactical_weakened_no_progress_min_minutes": 30,
    }
    ex._sl_check_failures = {}
    ex._sl_max_failures = 3
    ex._fetch_price_robust = MagicMock(return_value=price)
    ex._halt_symbol = MagicMock()
    ex._enqueue_drift_alert = MagicMock()
    ex._move_sl = MagicMock()

    position = {
        "symbol": SYMBOL,
        "internal_symbol": "ONDO-USDT",
        "side": "long",
        "entry_price": 1.25,
        "stop_loss": 1.20,
        "original_sl": 1.20,
        "take_profit": 1.32,
        "take_profit_levels": [1.32, 1.38],
        "tp_filled": tp_filled,
        "highest_price": 1.25,
        "lowest_price": 1.25,
        "atr_pct": 0.02,
        "track": "tactical",
        "open_time": time.time() - open_age_minutes * 60,
    }
    if extra_position:
        position.update(extra_position)
    ex.positions[SYMBOL] = position
    return ex


def test_tactical_tp1_returns_reduce_trigger():
    ex = _executor_with_position(price=1.32, tp_filled=0)

    assert ex.check_stop_loss_take_profit(SYMBOL) == "tactical_tp1"


def test_tactical_tp2_returns_second_reduce_trigger():
    ex = _executor_with_position(price=1.38, tp_filled=1)

    assert ex.check_stop_loss_take_profit(SYMBOL) == "partial_tp_2"


def test_tactical_invalidated_returns_close_trigger():
    ex = _executor_with_position(
        price=1.26,
        extra_position={
            "tactical_thesis_state": "invalidated",
            "tactical_thesis_reason": "15m_opposing_block",
        },
    )

    assert ex.check_stop_loss_take_profit(SYMBOL) == "tactical_invalidated"
    assert ex.positions[SYMBOL]["tactical_close_detail"] == "15m_opposing_block"


def test_tactical_weakened_without_progress_returns_close_trigger():
    ex = _executor_with_position(
        price=1.251,
        extra_position={
            "tactical_thesis_state": "weakened",
            "tactical_last_progress_time": time.time() - 31 * 60,
            "tactical_weakened_no_progress_minutes": 30,
        },
    )

    assert ex.check_stop_loss_take_profit(SYMBOL) == "tactical_weakened_no_progress"


def test_tactical_max_hold_returns_close_trigger():
    ex = _executor_with_position(price=1.26, open_age_minutes=91)

    assert ex.check_stop_loss_take_profit(SYMBOL) == "tactical_max_hold"

