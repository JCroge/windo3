import time


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


def test_tactical_stop_loss_still_triggers():
    ex = make_executor()
    pos = tactical_position()
    ex.positions = {"WLD-USDT-SWAP": pos}
    ex._fetch_price_robust = lambda symbol: 0.3905

    assert ex.check_stop_loss_take_profit("WLD-USDT-SWAP") == "stop_loss"
