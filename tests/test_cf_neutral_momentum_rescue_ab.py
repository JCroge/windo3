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
