from utils.cf_honesty_gate import summarize_bucket


def test_thin_sample_refuses():
    v = summarize_bucket(wins=3, losses=2, net_usdt_samples=[1, -1, 2, -1, 0],
                         min_sample=30, lowconf_sample=100)
    assert v["verdict"] == "INSUFFICIENT_SAMPLE"
    assert v.get("direction") is None


def test_mid_sample_low_confidence():
    v = summarize_bucket(wins=30, losses=20, net_usdt_samples=[1.0] * 30 + [-1.0] * 20,
                         min_sample=30, lowconf_sample=100)
    assert v["verdict"] == "low_confidence"
    assert "win_rate_ci" in v and "net_pnl_ci" in v


def test_actionable_when_ci_excludes_zero():
    samples = [2.0] * 80 + [1.0] * 40
    v = summarize_bucket(wins=120, losses=0, net_usdt_samples=samples,
                         min_sample=30, lowconf_sample=100)
    assert v["verdict"] == "actionable"
    assert v["net_pnl_ci"][0] > 0


def test_single_trade_dominance_not_actionable():
    samples = [-0.1] * 119 + [500.0]
    v = summarize_bucket(wins=1, losses=119, net_usdt_samples=samples,
                         min_sample=30, lowconf_sample=100)
    assert v["verdict"] != "actionable"
    assert v["net_pnl_ci"][0] <= 0 <= v["net_pnl_ci"][1]


def test_wilson_handles_extreme():
    v = summarize_bucket(wins=100, losses=0, net_usdt_samples=[1.0] * 100,
                         min_sample=30, lowconf_sample=100)
    lo, hi = v["win_rate_ci"]
    assert 0.0 <= lo <= hi <= 1.0 and lo < 1.0
