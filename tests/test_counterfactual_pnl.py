from utils.counterfactual_pnl import resolve_counterfactual, CfResult


def _rec(**kw):
    base = dict(symbol="BTC-USDT", side="long", entry_price=100.0, stop_loss=95.0,
                take_profit=[110.0], leverage=5, size_usdt=30.0,
                created_at=1000.0, funding_rate=0.0001)
    base.update(kw); return base


def _bars(seq):
    # seq: list of (open_time_ms, high, low) -> close 取 (h+l)/2
    return [{"open_time": t, "high": h, "low": l, "close": (h + l) / 2} for t, h, l in seq]


def test_single_tp_hit_net_usdt_positive():
    rec = _rec()
    bars = _bars([(1_001_000, 111, 109)])  # high>=TP 110, low 不破 SL
    r = resolve_counterfactual(rec, bars)
    assert r.outcome == "tp"
    assert r.price_ambiguous is False
    assert r.net_usdt > 0


def test_single_sl_hit_net_usdt_negative():
    rec = _rec()
    bars = _bars([(1_001_000, 99, 94)])  # low<=SL 95
    r = resolve_counterfactual(rec, bars)
    assert r.outcome == "sl"
    assert r.net_usdt < 0


def test_same_bar_conflict_takes_sl_first():
    rec = _rec()
    bars = _bars([(1_001_000, 111, 94)])  # 同根：high 触 TP 且 low 触 SL
    r = resolve_counterfactual(rec, bars)
    assert r.outcome == "sl"
    assert r.price_ambiguous is True


def test_expired_mark_to_market():
    rec = _rec()
    bars = _bars([(1_001_000, 101, 99)])
    r = resolve_counterfactual(rec, bars, max_hold_sec=86400)
    assert r.outcome == "expired"


def test_funding_flagged_approx():
    rec = _rec()
    bars = _bars([(1_001_000, 111, 109)])
    r = resolve_counterfactual(rec, bars)
    assert r.funding_approx is True


def test_short_side_symmetry():
    rec = _rec(side="short", stop_loss=105.0, take_profit=[90.0])
    bars = _bars([(1_001_000, 91, 89)])  # low<=TP 90
    r = resolve_counterfactual(rec, bars)
    assert r.outcome == "tp" and r.net_usdt > 0
