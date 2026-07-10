from test_tactical_track_classifier import base_plan, make_judge, strong_short_tech


def test_tactical_profile_recalculates_stop_size_and_rr():
    judge = make_judge()
    plan = base_plan()

    out = judge._apply_tactical_profile(plan.copy(), strong_short_tech(), {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "reason": "main_quality_failed:weak_volume_oi",
    })

    assert out["track"] == "tactical"
    assert out["exit_profile"] == "tactical_v1"
    assert out["slot_type"] == "tactical"
    assert out["leverage"] == 5
    assert out["size_usdt"] == 21.0
    assert out["main_diagnostic_effective_rr"] == 1.55
    assert out["tactical_effective_rr"] > 0
    assert out["effective_risk_reward_ratio"] == out["tactical_effective_rr"]
    assert out["take_profit"][0] > 0.367


def test_tactical_profile_does_not_use_main_ladder_rr_to_pass_cost_gate():
    judge = make_judge()
    plan = base_plan()
    plan["take_profit"] = [0.3847, 0.36, 0.35]

    out = judge._apply_tactical_profile(plan.copy(), strong_short_tech(), {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "reason": "main_quality_failed:weak_volume_oi",
    })

    assert out["track"] == "shadow_only"
    assert out["tactical_cost_gate"] == "fail"
