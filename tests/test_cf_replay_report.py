from replay_report import build_cf_report


def test_cf_report_buckets_and_gate():
    rows = [{"reject_reason": "rr_below_floor", "effective_regime": "choppy",
             "side": "long", "outcome": "tp", "net_usdt": 2.0,
             "price_ambiguous": False, "source": "tape_exact"}] * 5
    rep = build_cf_report(rows, min_sample=30, lowconf_sample=100)
    bucket = rep["buckets"]["rr_below_floor|choppy|long"]
    assert bucket["verdict"] == "INSUFFICIENT_SAMPLE"  # 5 < 30
    assert "bias_band" in bucket


def test_cf_report_bias_band_counts_ambiguous():
    rows = [{"reject_reason": "ev_gate", "effective_regime": "bullish", "side": "long",
             "outcome": "sl", "net_usdt": -1.0, "price_ambiguous": True,
             "source": "attribution_reconstructed"}] * 3
    rep = build_cf_report(rows, min_sample=1, lowconf_sample=2)
    bucket = rep["buckets"]["ev_gate|bullish|long"]
    assert bucket["bias_band"]["ambiguous_count"] == 3
