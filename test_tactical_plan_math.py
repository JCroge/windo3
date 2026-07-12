from test_tactical_track_classifier import base_plan, make_judge, strong_short_tech


def test_tactical_profile_recalculates_stop_size_and_rr():
    judge = make_judge()
    plan = base_plan()
    plan["size_usdt"] = 8.57

    out = judge._apply_tactical_profile(plan.copy(), strong_short_tech(), {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "reason": "main_quality_failed:weak_volume_oi",
    })

    assert out["track"] == "tactical"
    assert out["exit_profile"] == "tactical_v1"
    assert out["slot_type"] == "tactical"
    assert out["leverage"] == 5
    assert out["size_usdt"] == 6.0
    assert out["main_diagnostic_effective_rr"] == 1.55
    assert out["tactical_effective_rr"] > 0
    assert out["effective_risk_reward_ratio"] == out["tactical_effective_rr"]
    assert out["take_profit"][0] > 0.367


def test_tactical_profile_fails_min_rr_ev_gate_after_cost_gate_passes():
    judge = make_judge()
    plan = base_plan()

    out = judge._apply_tactical_profile(plan.copy(), strong_short_tech(), {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "reason": "main_quality_failed:weak_volume_oi",
    })

    assert out["tactical_cost_gate"] == "pass"
    assert out["tactical_effective_rr"] == 0.75
    assert out["tactical_expected_value"] == -0.0627
    assert out["track"] == "shadow_only"
    assert out["exit_profile"] == "tactical_v1"
    assert out["tactical_track_gate"] == "fail"
    assert out["tactical_gate_failed"] == "min_rr_or_ev"
    assert out["tactical_min_rr_for_track"] == 0.75
    assert out["tactical_min_ev_for_track"] == -0.04


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


def test_tactical_shadow_only_still_builds_tactical_counterfactual_plan():
    judge = make_judge()
    judge._tactical_shadow_only = True
    tech = strong_short_tech()
    tech["momentum"]["volume_ratio"] = 0.41
    llm = {
        "reasoning": "趋势强度=100可能处于趋势末期，追空存在反弹风险",
        "risk_warnings": ["趋势末期追空风险"],
    }
    attribution = {
        "llm_short_reversal_risk": True,
        "provenance": {"weakest_confidence": 0.13},
    }
    track_decision = judge._classify_track(
        "WLD-USDT", "open_short", base_plan(), tech, -58, llm, attribution,
    )

    out = judge._apply_tactical_shadow_profile(
        base_plan().copy(), tech, track_decision,
    )

    assert track_decision["track"] == "shadow_only"
    assert track_decision["reason"].endswith(":tactical_shadow_only")
    assert out["track"] == "shadow_only"
    assert out["exit_profile"] == "tactical_v1"
    assert out["tactical_gate_failed"] == "min_rr_or_ev"
    assert out["take_profit"] == [0.3796]
    assert out["tactical_max_hold_minutes"] == 90
