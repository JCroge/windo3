import cf_neutral_momentum_rescue_ab as drv


def _rec(direction="neutral", regime="choppy", daily="bullish", htf="neutral",
         pre12h=0.05, range_pos=0.5, strength=25, decision="reject", replayable=True):
    return {
        "decision": decision, "replayable": replayable, "regime_state": regime,
        "symbol": "AAA-USDT", "timestamp": 1000.0, "price_at_decision": 10.0,
        "tech_analysis": {
            "trend": {"direction": direction, "daily_bias": daily,
                      "higher_tf_bias": htf, "strength": strength},
            "entry_context": {"pre_12h_return_pct": pre12h,
                              "position_in_24h_range": range_pos},
        },
    }


def test_predicate_hit_daily_bullish():
    assert drv.rescue_predicate(_rec(daily="bullish", htf="neutral",
                                     pre12h=0.05, range_pos=0.5), 0.03, 0.92) is True


def test_predicate_hit_htf_bullish():
    assert drv.rescue_predicate(_rec(daily="neutral", htf="bullish",
                                     pre12h=0.04, range_pos=0.8), 0.03, 0.92) is True


def test_predicate_excludes_no_bullish_bias():
    assert drv.rescue_predicate(_rec(daily="bearish", htf="bearish",
                                     pre12h=0.05, range_pos=0.5), 0.03, 0.92) is False


def test_predicate_excludes_low_pre12h():
    assert drv.rescue_predicate(_rec(daily="bullish", pre12h=0.01,
                                     range_pos=0.5), 0.03, 0.92) is False


def test_predicate_excludes_high_range_pos():
    assert drv.rescue_predicate(_rec(daily="bullish", pre12h=0.05,
                                     range_pos=0.95), 0.03, 0.92) is False


def test_predicate_ignores_strength():
    # 高 strength 但动量不足 → 仍 False(证明不依赖 strength 救入)
    assert drv.rescue_predicate(_rec(daily="bullish", pre12h=0.01,
                                     range_pos=0.5, strength=100), 0.03, 0.92) is False
    # 低 strength 但动量充分 → True(证明不被 strength 挡出)
    assert drv.rescue_predicate(_rec(daily="bullish", pre12h=0.05,
                                     range_pos=0.5, strength=10), 0.03, 0.92) is True


def test_synthesize_long_geometry():
    rec = _rec()  # price_at_decision=10.0, timestamp=1000.0
    f = drv.synthesize_settle_fields(rec, sl_dist=0.02, tp1_dist=0.03)
    assert f["_side"] == "long"
    assert f["symbol"] == "AAA-USDT"
    assert f["_created"] == 1000.0
    assert abs(f["_sl_dist"] - 0.02) < 1e-9
    assert abs(f["_tp1_dist"] - 0.03) < 1e-9
    p = f["_plan"]
    assert p["side"] == "long"
    assert abs(p["entry_price"] - 10.0) < 1e-9
    assert p["created_at"] == 1000.0
    assert abs(p["stop_loss"] - 9.8) < 1e-9          # 10*(1-0.02)
    assert abs(p["take_profit"][0] - 10.3) < 1e-9    # 10*(1+0.03)
    assert "entry_ref" not in p                        # 契约:用 entry_price


def test_synthesize_invalid_dist_returns_none():
    assert drv.synthesize_settle_fields(_rec(), 0.0, 0.03) is None
    assert drv.synthesize_settle_fields(_rec(), 0.02, -0.01) is None


def test_synthesize_missing_price_returns_none():
    rec = _rec()
    rec["price_at_decision"] = None
    assert drv.synthesize_settle_fields(rec, 0.02, 0.03) is None


def test_derive_geometry_fallback(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    sl, tp = drv.derive_strategy_geometry(str(empty))
    assert abs(sl - 0.015) < 1e-9 and abs(tp - 0.0225) < 1e-9


def test_settle_records_tp_and_sl_via_fake_bars(monkeypatch):
    # 两个不同 symbol 的候选:一个走到 TP,一个走到 SL
    recs = [_rec(), _rec()]
    recs[0]["symbol"] = "WIN-USDT"
    recs[1]["symbol"] = "LOSE-USDT"

    def fake_bars(db, sym, created, window=86400):
        if sym == "WIN-USDT":
            return [{"open_time": int((created + 60) * 1000),
                     "high": 11.0, "low": 9.95, "close": 11.0}]   # 命中 tp(10.3)
        return [{"open_time": int((created + 60) * 1000),
                 "high": 10.05, "low": 9.5, "close": 9.5}]         # 命中 sl(9.8)

    monkeypatch.setattr(drv, "load_bars", fake_bars)
    clusters, settle, v = drv.settle_records(recs, sl_dist=0.02, tp1_dist=0.03)
    assert settle["tp"] == 1
    assert settle["sl"] == 1
    assert settle["resolved"] == 2
    # net_R = (tp1_dist/sl_dist) + (-1) = 1.5 - 1 = 0.5
    assert abs(settle["net_R"] - 0.5) < 1e-6


def test_settle_records_nodata_skipped(monkeypatch):
    monkeypatch.setattr(drv, "load_bars", lambda *a, **k: [])
    clusters, settle, v = drv.settle_records([_rec()], 0.02, 0.03)
    assert settle["nodata"] == 1
    assert settle["resolved"] == 0


def test_bucket_verdict_thin_sample_insufficient(monkeypatch):
    # 单簇 → n=1 < 30 → INSUFFICIENT
    monkeypatch.setattr(drv, "load_bars", lambda *a, **k: [
        {"open_time": 1060000, "high": 11.0, "low": 9.95, "close": 11.0}])
    _, _, v = drv.settle_records([_rec()], 0.02, 0.03)
    assert "INSUFFICIENT" in v["verdict"]
