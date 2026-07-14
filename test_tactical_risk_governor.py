from unittest.mock import MagicMock


def make_guard():
    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard

    guard = PortfolioRiskGuard.__new__(PortfolioRiskGuard)
    guard.logger = MagicMock()
    guard._positions = {}
    guard._tactical_daily_pnl = 0.0
    guard._tactical_daily_date = guard._tactical_day_key()
    guard._tactical_loss_streak = 0
    guard._tactical_pause_until = 0
    guard._tactical_daily_loss_limit_usdt = -10.0
    guard._tactical_loss_streak_pause_count = 3
    guard._tactical_loss_streak_pause_minutes = 60
    guard._tactical_max_concurrent_calm = 2
    guard._tactical_max_concurrent_high_vol = 1
    return guard


def test_tactical_daily_loss_blocks_new_open():
    guard = make_guard()
    guard._tactical_daily_pnl = -10.0

    allowed, reason = guard.can_open_tactical(
        "WLD-USDT", {"track": "tactical"}, {"volatility": "calm"},
    )

    assert allowed is False
    assert reason == "tactical_daily_loss_limit"


def test_tactical_concurrency_high_vol_caps_at_one():
    guard = make_guard()
    guard._positions = {"WLD-USDT": {"track": "tactical"}}

    allowed, reason = guard.can_open_tactical(
        "ETH-USDT", {"track": "tactical"}, {"volatility": "high"},
    )

    assert allowed is False
    assert reason == "tactical_concurrency_full"


def test_three_tactical_losses_pause_track():
    guard = make_guard()

    guard.record_tactical_close("A-USDT", -1.0, "tactical_sl", {})
    guard.record_tactical_close("B-USDT", -1.0, "tactical_sl", {})
    guard.record_tactical_close("C-USDT", -1.0, "tactical_sl", {})

    assert guard._tactical_loss_streak == 3
    assert guard._tactical_pause_until > 0
