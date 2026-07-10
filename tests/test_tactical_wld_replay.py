import json
from datetime import datetime
from pathlib import Path

FIXTURE = Path(__file__).with_name("fixtures") / "wld_tactical_20260710.json"


def ts(value):
    return datetime.fromisoformat(value).timestamp()


def bars_from_fixture(rows):
    return [
        {
            "open_time": int(ts(b["t"]) * 1000),
            "high": b["h"],
            "low": b["l"],
            "close": b["c"],
        }
        for b in rows
    ]


def test_wld_first_short_tactical_tp_before_main_tp():
    from utils.counterfactual_pnl import resolve_counterfactual

    fixture = json.loads(FIXTURE.read_text())
    trade = fixture["first_short"]
    record = {
        "symbol": "WLD-USDT",
        "side": "short",
        "entry_price": trade["entry"],
        "stop_loss": trade["tactical_sl"],
        "take_profit": [trade["tactical_tp1"]],
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "created_at": ts(trade["entry_time"]),
    }

    result = resolve_counterfactual(
        record, bars_from_fixture(trade["bars"]),
        max_hold_sec=90 * 60, exit_profile="tactical_v1",
    )

    assert result.outcome == "tp"
    assert result.exit_profile == "tactical_v1"


def test_wld_second_short_tactical_sl_before_main_sl():
    from utils.counterfactual_pnl import resolve_counterfactual

    fixture = json.loads(FIXTURE.read_text())
    trade = fixture["second_short"]
    record = {
        "symbol": "WLD-USDT",
        "side": "short",
        "entry_price": trade["entry"],
        "stop_loss": trade["tactical_sl"],
        "take_profit": [trade["tactical_tp1"]],
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "created_at": ts(trade["entry_time"]),
    }

    result = resolve_counterfactual(
        record, bars_from_fixture(trade["bars"]),
        max_hold_sec=120 * 60, exit_profile="tactical_v1",
    )

    assert result.outcome == "sl"
    assert result.exit_profile == "tactical_v1"


def test_tactical_max_hold_records_resolution_reason():
    from utils.counterfactual_pnl import resolve_counterfactual

    entry_ts = ts("2026-07-10T10:00:00+08:00")
    record = {
        "symbol": "WLD-USDT",
        "side": "short",
        "entry_price": 0.385,
        "stop_loss": 0.3904,
        "take_profit": [0.3817],
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "created_at": entry_ts,
    }
    bars = [
        {"open_time": int((entry_ts + 30 * 60) * 1000), "high": 0.3852, "low": 0.3848, "close": 0.3850},
        {"open_time": int((entry_ts + 91 * 60) * 1000), "high": 0.3851, "low": 0.3849, "close": 0.3850},
    ]

    result = resolve_counterfactual(
        record, bars, max_hold_sec=90 * 60, exit_profile="tactical_v1",
    )

    assert result.outcome == "expired"
    assert result.resolution_reason == "tactical_max_hold"
