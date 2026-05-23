"""Phase 2 AC-PH2-04: Bucketed EV Gate Tests"""
import pytest
from unittest.mock import MagicMock
from agents.trading.judge import MultiJudge


def _make_judge(bucketed=True):
    judge = MultiJudge.__new__(MultiJudge)
    judge._bucketed_ev_enabled = bucketed
    judge._confidence_split_enabled = False
    judge._trend_saturation_enabled = False
    judge._momentum_probe_long_enabled = False
    judge._request_id_enabled = True
    judge._ev_min_threshold = 0.05
    judge._ev_strong_signal_threshold = 70
    judge._fallback_win_rate = 0.52
    judge._recent_win_rate = None
    judge._recent_profit_factor = None
    judge._total_completed_trades = 0
    judge._recent_wins = 0
    judge._ev_prior_wins = 2
    judge._ev_prior_total = 5
    judge._bucketed_metrics = {}
    judge.logger = MagicMock()

    # Regime manager mock
    judge._regime_manager = MagicMock()
    judge._regime_manager._effective_regime = 'bullish'
    return judge


class TestBucketedEV:
    def test_no_bucket_data_falls_through(self):
        judge = _make_judge()
        plan = {
            'expected_value': 0.1, 'p_win_used': 0.55,
            'p_win_source': 'fallback', 'net_profit_usdt': 5,
            'net_loss_usdt': 3, 'entry_type': 'rule_signal',
            'slot_type': 'main', 'side': 'long',
        }
        assert judge._check_expected_value('BTC-USDT', plan, 50) is True

    def test_bucket_with_good_win_rate_passes(self):
        judge = _make_judge()
        judge._bucketed_metrics = {
            'long_bullish_rule_signal_main': {
                'trade_count': 10, 'win_rate': 0.7, 'profit_factor': 2.5,
            }
        }
        plan = {
            'expected_value': 0.1, 'p_win_used': 0.52,
            'p_win_source': 'fallback', 'net_profit_usdt': 5,
            'net_loss_usdt': 3, 'entry_type': 'rule_signal',
            'slot_type': 'main', 'side': 'long',
        }
        result = judge._check_expected_value('BTC-USDT', plan, 50)
        assert result is True
        # p_win should be updated to bucket value
        assert plan['p_win_used'] == 0.7
        assert 'bucket:' in plan['p_win_source']

    def test_bucket_with_bad_win_rate_blocks(self):
        judge = _make_judge()
        judge._bucketed_metrics = {
            'long_bullish_rule_signal_main': {
                'trade_count': 10, 'win_rate': 0.2, 'profit_factor': 0.5,
            }
        }
        plan = {
            'expected_value': 0.1, 'p_win_used': 0.52,
            'p_win_source': 'fallback', 'net_profit_usdt': 5,
            'net_loss_usdt': 3, 'entry_type': 'rule_signal',
            'slot_type': 'main', 'side': 'long',
        }
        result = judge._check_expected_value('BTC-USDT', plan, 50)
        # EV recalculated: 0.2*5 - 0.8*3 = 1.0 - 2.4 = -1.4 < 0.05
        assert result is False

    def test_insufficient_sample_scales_down_not_blocks(self):
        """AC-PH2-04: 样本不足时缩仓不冻结强信号"""
        judge = _make_judge()
        judge._bucketed_metrics = {
            'long_bullish_rule_signal_main': {
                'trade_count': 3, 'win_rate': 0.6, 'profit_factor': 1.5,
            }
        }
        plan = {
            'expected_value': 0.1, 'p_win_used': 0.52,
            'p_win_source': 'fallback', 'net_profit_usdt': 5,
            'net_loss_usdt': 3, 'entry_type': 'rule_signal',
            'slot_type': 'main', 'side': 'long',
            'size_usdt': 10.0,
        }
        # score=75 >= ev_strong_signal_threshold=70 → scale down, not block
        result = judge._check_expected_value('BTC-USDT', plan, 75)
        assert result is True
        assert plan['size_usdt'] == 6.0  # 10 * 0.6

    def test_fallback_to_side_bucket(self):
        judge = _make_judge()
        judge._bucketed_metrics = {
            'side_long': {
                'trade_count': 15, 'win_rate': 0.65, 'profit_factor': 2.0,
            }
        }
        plan = {
            'expected_value': 0.1, 'p_win_used': 0.52,
            'p_win_source': 'fallback', 'net_profit_usdt': 5,
            'net_loss_usdt': 3, 'entry_type': 'rule_signal',
            'slot_type': 'main', 'side': 'long',
        }
        result = judge._check_expected_value('BTC-USDT', plan, 50)
        assert result is True
        assert plan['p_win_used'] == 0.65

    def test_disabled_uses_global_ev(self):
        judge = _make_judge(bucketed=False)
        plan = {
            'expected_value': 0.1, 'p_win_used': 0.55,
            'p_win_source': 'fallback', 'net_profit_usdt': 5,
            'net_loss_usdt': 3, 'entry_type': 'rule_signal',
            'slot_type': 'main',
        }
        result = judge._check_expected_value('BTC-USDT', plan, 50)
        assert result is True
        # p_win unchanged
        assert plan['p_win_used'] == 0.55

    def test_parse_segmented_metrics(self):
        judge = _make_judge()
        segmented = {
            'metrics_by_side': {'long': {'trade_count': 10, 'win_rate': 0.6}},
            'metrics_by_regime': {'bullish': {'trade_count': 8, 'win_rate': 0.7}},
            'metrics_by_slot_type': {'main': {'trade_count': 12, 'win_rate': 0.55}},
            'metrics_by_bucket': {
                'long_bullish_rule_signal_main': {'trade_count': 5, 'win_rate': 0.8}
            },
        }
        result = judge._parse_segmented_metrics(segmented)
        assert 'side_long' in result
        assert 'regime_bullish' in result
        assert 'slot_type_main' in result
        assert 'long_bullish_rule_signal_main' in result
        assert result['long_bullish_rule_signal_main']['win_rate'] == 0.8
