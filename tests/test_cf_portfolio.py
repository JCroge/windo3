from collections import deque
from utils.cf_portfolio import CounterfactualPortfolio


def _force_close(cf, symbol, net):
    """直接把一个已开 CF 仓的结算结果设为 net 并到期解析。"""
    cf._open[symbol] = {"resolved_ts": 1.0, "net_usdt": net,
                        "archetype": "test", "created_at": 0.0}
    cf.resolve_due(2.0)


def test_rolling_window_tracks_recent_results():
    cf = CounterfactualPortfolio(initial_equity=1000.0)
    for net in (1.0, 1.0, -1.0, 1.0, -1.0):
        _force_close(cf, "X-USDT", net)
    snap = cf.to_snapshot()
    assert snap["_recent_win_rate"] == 3 / 5


def test_rolling_window_is_capped_at_window_size():
    cf = CounterfactualPortfolio(initial_equity=1000.0, rolling_window_size=20)
    for _ in range(25):
        _force_close(cf, "X-USDT", 1.0)
    assert len(cf._cf_win_window) == 20
    assert cf.to_snapshot()["_recent_win_rate"] == 1.0


def test_window_empty_emits_none():
    cf = CounterfactualPortfolio(initial_equity=1000.0)
    assert cf.to_snapshot()["_recent_win_rate"] is None


def _price_loader_tp(symbol, created_at, window_sec=86400):
    return [{"open_time": int((created_at + 60) * 1000), "high": 111, "low": 109, "close": 110}]


def _open_decision(symbol="BTC-USDT"):
    return {"action": "open_long", "symbol": symbol, "plan": {
        "entry_ref": 100.0, "stop_loss": 95.0, "take_profit": [110.0],
        "leverage": 5, "size_usdt": 30.0}, "attribution": {"entry_type": "rule_signal"}}


def test_open_occupies_slot():
    cf = CounterfactualPortfolio(initial_equity=1000.0, max_slots=3, price_loader=_price_loader_tp)
    opened = cf.apply_decision(_open_decision(), created_at=1000.0, funding_rate=0.0, regime="bullish")
    assert opened is True
    assert "BTC-USDT" in cf.open_symbols()
    assert cf.slot_count() == 1


def test_slot_full_blocks_open():
    cf = CounterfactualPortfolio(initial_equity=1000.0, max_slots=1, price_loader=_price_loader_tp)
    cf.apply_decision(_open_decision("BTC-USDT"), 1000.0, 0.0, "bullish")
    blocked = cf.apply_decision(_open_decision("ETH-USDT"), 1000.0, 0.0, "bullish")
    assert blocked is False
    assert cf.slot_count() == 1


def test_resolve_due_realizes_pnl_and_frees_slot():
    cf = CounterfactualPortfolio(initial_equity=1000.0, max_slots=3, price_loader=_price_loader_tp)
    cf.apply_decision(_open_decision(), created_at=1000.0, funding_rate=0.0, regime="bullish")
    cf.resolve_due(now=2000.0)
    assert cf.slot_count() == 0
    assert cf.equity > 1000.0
    assert cf._total_completed_trades == 1
    assert cf._recent_wins == 1


def test_to_snapshot_format():
    cf = CounterfactualPortfolio(initial_equity=1000.0, max_slots=3, price_loader=_price_loader_tp)
    cf.apply_decision(_open_decision(), 1000.0, 0.0, "bullish")
    snap = cf.to_snapshot(regime_snapshot={"effective_regime": "bullish", "confidence": 70})
    assert set(snap["_open_positions"]) == {"BTC-USDT"}
    assert snap["_available_balance"] == cf.equity
    assert snap["_regime_manager"]["effective_regime"] == "bullish"
    assert "_archetype_cooldown" in snap and "_recent_wins" in snap


def test_daily_stop_blocks_after_loss():
    def loss_loader(symbol, created_at, window_sec=86400):
        return [{"open_time": int((created_at + 60) * 1000), "high": 96, "low": 90, "close": 94}]
    cf = CounterfactualPortfolio(initial_equity=1000.0, max_slots=5, price_loader=loss_loader,
                                 daily_pnl_hard_stop=-1.0)
    cf.apply_decision(_open_decision(), created_at=1000.0, funding_rate=0.0, regime="bullish")
    cf.resolve_due(now=2000.0)
    assert cf.equity < 1000.0
    blocked = cf.apply_decision(_open_decision("ETH-USDT"), created_at=2100.0, funding_rate=0.0, regime="bullish")
    assert blocked is False
