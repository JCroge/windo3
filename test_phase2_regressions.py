"""Phase 2 AC-PH2-03 + AC-PH2-07: Trend Saturation + Historical Regression Tests"""
import pytest
from unittest.mock import MagicMock
from agents.trading.judge import MultiJudge


def _make_judge(saturation=True):
    judge = MultiJudge.__new__(MultiJudge)
    judge._trend_saturation_enabled = saturation
    judge._confidence_split_enabled = False
    judge._momentum_probe_long_enabled = False
    judge._bucketed_ev_enabled = False
    judge._request_id_enabled = True
    judge.logger = MagicMock()
    return judge


class TestTrendSaturation:
    """AC-PH2-03: 强趋势评分饱和"""

    def test_strength_95_not_compressed_below_90(self):
        """趋势强度95不应被压到74（旧逻辑: 90-(95-90)*2=80→74 effective）"""
        judge = _make_judge(saturation=True)
        tech = {
            'rule_signal': {'entry_long': True},
            'momentum': {'rsi': 55, 'rsi_divergence': None},
            'trend': {'direction': 'bullish', 'strength': 95,
                      'higher_tf_bias': 'bullish'},
            'money_flow': {}, 'microstructure': {}, 'crowd': {},
        }
        score = judge._compute_score(tech)
        # With saturation: effective_strength=90, trend_score=20*0.9=18
        # rule_signal: +35, trend: +18, htf: +10 = 63
        assert score >= 60  # Should not be compressed to low values

    def test_strength_98_old_logic_compresses(self):
        """旧逻辑: strength=98 → effective=74, 趋势分被压低"""
        judge = _make_judge(saturation=False)
        tech = {
            'rule_signal': {'entry_long': True},
            'momentum': {'rsi': 55, 'rsi_divergence': None},
            'trend': {'direction': 'bullish', 'strength': 98,
                      'higher_tf_bias': 'bullish'},
            'money_flow': {}, 'microstructure': {}, 'crowd': {},
        }
        score = judge._compute_score(tech)
        # Old: effective=90-(98-90)*2=74, trend_score=20*0.74=14.8
        # rule_signal: +35, trend: +14.8, htf: +10 = 59.8
        assert score < 63  # Compressed more than saturation version

    def test_4h_rsi_dynamic_discount_mild(self):
        """4h RSI 71 → 轻度衰减30% (Phase 2) vs 固定50% (旧)"""
        judge = _make_judge(saturation=True)
        tech = {
            'rule_signal': {'entry_long': True},
            'momentum': {'rsi': 55, 'rsi_divergence': None},
            'trend': {'direction': 'bullish', 'strength': 80,
                      'higher_tf_bias': 'bullish', 'tf_4h_rsi': 71},
            'money_flow': {}, 'microstructure': {}, 'crowd': {},
        }
        score_new = judge._compute_score(tech)

        judge_old = _make_judge(saturation=False)
        score_old = judge_old._compute_score(tech)

        # New: score * 0.7 (mild), Old: score * 0.5 (harsh)
        assert score_new > score_old

    def test_4h_rsi_82_heavy_discount(self):
        """4h RSI 82 → 重度衰减70% (Phase 2)"""
        judge = _make_judge(saturation=True)
        tech = {
            'rule_signal': {'entry_long': True},
            'momentum': {'rsi': 55, 'rsi_divergence': None},
            'trend': {'direction': 'bullish', 'strength': 80,
                      'higher_tf_bias': 'bullish', 'tf_4h_rsi': 82},
            'money_flow': {}, 'microstructure': {}, 'crowd': {},
        }
        score = judge._compute_score(tech)
        # Heavy discount: score * 0.3
        # Base before 4h: ~35+16+10=61, after: 61*0.3=18.3
        assert score < 25


class TestHistoricalRegressions:
    """AC-PH2-07: 历史事故回归"""

    def test_zec_high_rsi_not_full_position(self):
        """ZEC: 1h RSI=64, 4h RSI=73.9 → 不应满仓做多"""
        judge = _make_judge(saturation=True)
        tech = {
            'rule_signal': {'ma_aligned_long': True},
            'momentum': {'rsi': 64, 'rsi_divergence': None},
            'trend': {'direction': 'bullish', 'strength': 85,
                      'higher_tf_bias': 'bullish', 'tf_4h_rsi': 73.9},
            'money_flow': {}, 'microstructure': {}, 'crowd': {},
        }
        score = judge._compute_score(tech)
        # 4h RSI 73.9 (between 70-75) → 0.7 discount
        # Base: 20+17+10=47, after 4h: 47*0.7=32.9
        # This is below the entry threshold for ma_aligned (25), so it would hold
        # or enter with reduced confidence
        assert score < 50  # Significantly reduced from full strength

    def test_strong_trend_with_divergence_still_reduced(self):
        """强趋势+背离: 不应被饱和逻辑重新放开为满仓"""
        judge = _make_judge(saturation=True)
        tech = {
            'rule_signal': {},
            'momentum': {'rsi': 78, 'rsi_divergence': 'bearish_div'},
            'trend': {'direction': 'bullish', 'strength': 95,
                      'higher_tf_bias': 'bullish'},
            'money_flow': {}, 'microstructure': {}, 'crowd': {},
        }
        score = judge._compute_score(tech)
        # RSI >= 70 → rsi_extreme_bearish, cap applies
        # bearish_div with rsi_extreme_bearish → div_score=35, score -= 35
        # The RSI cap at 70+ limits positive score
        assert score <= 25  # Capped by RSI extreme protection
