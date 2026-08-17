import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def make_judge():
    with patch.dict(os.environ, {
        "OKX_API_KEY": "test",
        "OKX_SECRET": "test",
        "OKX_PASSWORD": "test",
        "OKX_PASSPHRASE": "test",
    }):
        from agents.trading.judge import MultiJudge

        judge = MultiJudge.__new__(MultiJudge)
        judge.logger = MagicMock()
        judge._tactical_track_enabled = True
        judge._tactical_shadow_only = False
        judge._main_quality_gate_enabled = True
        judge._main_quality_min_provenance = 0.20
        judge._main_quality_block_llm_reversal = True
        judge._main_quality_allow_mixed_override = False
        judge._main_quality_require_volume_or_oi = True
        judge._tactical_max_leverage = 5
        judge._tactical_default_position_pct = 0.70
        judge._tactical_very_near_position_pct = 1.00
        judge._tactical_stop_cap_r_main = 0.60
        judge._tactical_very_near_stop_r_main = 0.40
        judge._tactical_tp1_r = 1.00
        judge._tactical_cost_coverage_min = 4.0
        judge._tactical_min_rr_for_track = 0.75
        judge._tactical_min_ev_for_track = -0.04

        class Regime:
            _effective_regime = "bearish"

            def snapshot(self):
                return {
                    "effective_regime": self._effective_regime,
                    "raw_regime": self._effective_regime,
                    "confidence": 70,
                }

        judge._regime_manager = Regime()
        return judge


def strong_short_tech():
    return {
        "trend": {
            "direction": "bearish",
            "higher_tf_bias": "bearish",
            "daily_bias": "bearish",
            "strength": 75,
        },
        "entry_timing": {
            "tf_15m_bias": "bearish",
            "tf_15m_entry_status": "confirmed",
            "tf_15m_block_short": False,
        },
        "momentum": {"volume_ratio": 1.4, "atr_pct": 0.01},
        "market": {"oi_1h_change_pct": -0.002},
        "risk": {"liquidity_score": 50},
    }


def base_plan():
    return {
        "side": "short",
        "entry_ref": 0.385,
        "entry_zone": [0.3848, 0.3852],
        "stop_loss": 0.394,
        "take_profit": [0.367, 0.358, 0.349],
        "leverage": 20,
        "size_usdt": 30.0,
        "effective_risk_reward_ratio": 1.55,
        "effective_rr_ladder": 1.55,
        "effective_rr_tp1": 1.31,
        "net_profit_usdt": 21.3,
        "net_loss_usdt": 16.2,
    }


def enable_main_short_guards(judge):
    judge._short_regime_guard_enabled = True
    judge._short_live_min_score = 55
    judge._short_live_min_rsi = 40
    judge._short_live_min_range_pos = 0.45
    judge._short_live_require_daily_bearish = True
    judge._short_live_min_htf_votes = 2
    judge._short_live_max_pre_move = -0.01
    judge._long_live_position_guard_enabled = True
    judge._long_live_max_range_pos = 0.82
    judge._long_live_daily_gain_range_pos = 0.75
    judge._long_live_max_pre_move = 0.08
    judge._long_live_max_daily_gain = 0.12
    judge._long_live_pullback_min_pct = 0.005
    judge._long_live_regime_aware_range_enabled = True
    judge._long_live_max_range_pos_choppy = 0.55
    judge._long_live_daily_gain_range_pos_choppy = 0.50


def low_range_short_tech():
    tech = strong_short_tech()
    tech["short_context"] = {
        "position_in_24h_range": 0.0814,
        "pre_12h_return_pct": -0.0112,
    }
    tech["entry_context"] = {
        "position_in_24h_range": 0.0814,
        "pre_12h_return_pct": -0.0112,
        "prev_daily_return_pct": -0.0099,
    }
    tech["indicators"] = {"rsi": 55, "price": 1.0}
    return tech


def test_clean_aligned_candidate_stays_main():
    judge = make_judge()
    llm = {"risk_warnings": [], "reasoning": ""}
    attribution = {"provenance": {"weakest_confidence": 0.45}}

    decision = judge._classify_track(
        "WLD-USDT", "open_short", base_plan(), strong_short_tech(), -70, llm, attribution,
    )

    assert decision["track"] == "main"
    assert decision["exit_profile"] == "trend_runner"


def test_wld_like_aligned_but_weak_candidate_is_not_main():
    judge = make_judge()
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

    decision = judge._classify_track(
        "WLD-USDT", "open_short", base_plan(), tech, -58, llm, attribution,
    )

    assert decision["track"] == "tactical"
    assert decision["track"] != "main"
    assert "main_quality" in decision["reason"]


def test_tactical_short_that_passed_tactical_gate_bypasses_main_range_guards():
    judge = make_judge()
    enable_main_short_guards(judge)
    tech = low_range_short_tech()

    main_gate = judge._classify_short_entry_risk(
        "XRP-USDT", "open_short", base_plan(), tech, -70, llm_result={}
    )
    assert main_gate["reason"] == "range_position_too_low"

    tactical_plan = base_plan()
    tactical_plan["size_usdt"] = 8.57
    tactical_plan = judge._apply_tactical_profile(tactical_plan, tech, {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "reason": "main_quality_failed:weak_volume_oi",
    })
    assert tactical_plan["track"] == "tactical"
    assert tactical_plan["tactical_track_gate"] == "pass"

    tactical_short_gate = judge._classify_short_entry_risk(
        "XRP-USDT", "open_short", tactical_plan, tech, -70, llm_result={}
    )
    tactical_position_gate = judge._check_entry_position_policy(
        "XRP-USDT", "open_short", tactical_plan, tech, -70, context="main"
    )

    assert tactical_short_gate["allowed"] is True
    assert tactical_position_gate["allowed"] is True


def test_tactical_profile_exports_exact_quality_flag_booleans():
    judge = make_judge()
    plan = base_plan()
    plan["size_usdt"] = 8.57

    profiled = judge._apply_tactical_profile(plan, strong_short_tech(), {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "reason": "main_quality_failed:weak_volume_oi,weak_provenance",
        "quality_flags": {
            "trend_exhaustion_warning": False,
            "weak_volume_oi": True,
            "weak_provenance": True,
        },
    })

    assert profiled["tactical_trend_exhaustion_warning"] is False
    assert profiled["tactical_weak_volume_oi"] is True
    assert profiled["tactical_weak_provenance"] is True


def test_15m_opposing_block_is_hard_veto_not_tactical():
    judge = make_judge()
    tech = strong_short_tech()
    tech["entry_timing"]["tf_15m_block_short"] = True

    decision = judge._classify_track("WLD-USDT", "open_short", base_plan(), tech, -58, {}, {})

    assert decision["track"] == "reject"
    assert decision["exit_profile"] == "none"
    assert decision["reason"] == "15m_opposing_block"
