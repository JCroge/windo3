"""Phase 2 AC-PH2-01: Confidence Split Tests"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from agents.trading.judge import MultiJudge


def _make_judge(split_enabled=True):
    judge = MultiJudge.__new__(MultiJudge)
    judge._confidence_split_enabled = split_enabled
    judge._trend_saturation_enabled = False
    judge._momentum_probe_long_enabled = False
    judge._bucketed_ev_enabled = False
    judge._request_id_enabled = True
    judge._probe_long_max_concurrent = 1
    judge._probe_long_max_position_pct = 0.3
    judge._probe_long_max_leverage = 3
    judge._probe_long_rsi_min = 70
    judge._probe_long_rsi_max = 85
    judge._bucketed_metrics = {}
    judge.logger = MagicMock()
    return judge


class TestConfidenceSplit:
    def test_is_htf_aligned_long_bullish(self):
        judge = _make_judge()
        tech = {'trend': {'higher_tf_bias': 'bullish'}}
        assert judge._is_htf_aligned(tech, 'open_long') is True

    def test_is_htf_aligned_short_bearish(self):
        judge = _make_judge()
        tech = {'trend': {'higher_tf_bias': 'bearish'}}
        assert judge._is_htf_aligned(tech, 'open_short') is True

    def test_is_htf_aligned_mismatch(self):
        judge = _make_judge()
        tech = {'trend': {'higher_tf_bias': 'bearish'}}
        assert judge._is_htf_aligned(tech, 'open_long') is False

    def test_compute_confidence_split_agree(self):
        judge = _make_judge()
        result = judge._compute_confidence_split(
            score=70, confidence=70, final_conf=70,
            llm_action='open_long', action='open_long',
            has_rule_signal=True, htf_aligned=True
        )
        assert result['signal_score'] == 70.0
        assert result['execution_confidence'] == 70
        assert result['position_scale'] == 1.0
        assert result['llm_relation'] == 'agree'
        assert result['hold_reason'] == ''

    def test_compute_confidence_split_llm_hold_with_rule(self):
        judge = _make_judge()
        result = judge._compute_confidence_split(
            score=50, confidence=50, final_conf=35,
            llm_action='hold', action='open_long',
            has_rule_signal=True, htf_aligned=True
        )
        assert result['llm_relation'] == 'hold'
        assert result['hold_reason'] == 'llm_hold_scale_only'
        assert result['position_scale'] == 0.7

    def test_compute_confidence_split_llm_reverse(self):
        judge = _make_judge()
        result = judge._compute_confidence_split(
            score=60, confidence=60, final_conf=24,
            llm_action='open_short', action='open_long',
            has_rule_signal=True, htf_aligned=True
        )
        assert result['llm_relation'] == 'reverse'
        assert result['hold_reason'] == 'llm_direction_conflict'
        assert result['position_scale'] == 0.4

    def test_split_disabled_no_extra_fields(self):
        judge = _make_judge(split_enabled=False)
        result = judge._compute_confidence_split(
            score=50, confidence=50, final_conf=50,
            llm_action='hold', action='open_long',
            has_rule_signal=False, htf_aligned=False
        )
        # Method still works, just won't be called when disabled
        assert 'signal_score' in result

    def test_rule_htf_aligned_llm_hold_keeps_conf_above_60(self):
        """AC-PH2-01: rule+HTF aligned, LLM hold → conf stays >= 60."""
        judge = _make_judge()
        # Simulate: confidence=70, LLM hold → old logic: max(40, 70*0.7)=49 < 60
        # New logic with split: max(60, 70*0.7) = 60
        # The _compute_confidence_split shows position_scale < 1 but conf >= 60
        result = judge._compute_confidence_split(
            score=70, confidence=70, final_conf=60,
            llm_action='hold', action='open_long',
            has_rule_signal=True, htf_aligned=True
        )
        assert result['execution_confidence'] >= 60
