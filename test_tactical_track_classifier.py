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
        judge._tactical_tp1_r = 0.60
        judge._tactical_cost_coverage_min = 4.0

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


def test_15m_opposing_block_is_hard_veto_not_tactical():
    judge = make_judge()
    tech = strong_short_tech()
    tech["entry_timing"]["tf_15m_block_short"] = True

    decision = judge._classify_track("WLD-USDT", "open_short", base_plan(), tech, -58, {}, {})

    assert decision["track"] == "reject"
    assert decision["exit_profile"] == "none"
    assert decision["reason"] == "15m_opposing_block"
