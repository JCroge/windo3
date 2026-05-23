"""AC-02/AC-07/AC-08: Metrics contract tests — win_rate units, bucket fields"""
import pytest
from unittest.mock import MagicMock
from agents.trading.judge import MultiJudge


def _make_judge():
    judge = MultiJudge.__new__(MultiJudge)
    judge._bucketed_ev_enabled = True
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
    judge._regime_manager = MagicMock()
    judge._regime_manager._effective_regime = 'bullish'
    return judge


class TestMetricsContract:
    def test_parse_segmented_metrics_adds_ratio_fields(self):
        """Parsed metrics must have win_rate_ratio and win_rate_pct"""
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
        for key, bucket in result.items():
            assert 'win_rate_ratio' in bucket, f"{key} missing win_rate_ratio"
            assert 'win_rate_pct' in bucket, f"{key} missing win_rate_pct"
            assert 0 <= bucket['win_rate_ratio'] <= 1.0
            assert 0 <= bucket['win_rate_pct'] <= 100

    def test_percentage_win_rate_auto_converted(self):
        """win_rate > 1 is treated as percentage and converted to ratio"""
        judge = _make_judge()
        segmented = {
            'metrics_by_side': {'long': {'trade_count': 10, 'win_rate': 65}},
            'metrics_by_regime': {},
            'metrics_by_slot_type': {},
        }
        result = judge._parse_segmented_metrics(segmented)
        assert result['side_long']['win_rate_ratio'] == 0.65
        assert result['side_long']['win_rate_pct'] == 65.0

    def test_ev_formula_positive_loss(self):
        """EV formula: p_win * profit - (1-p_win) * loss, loss is positive"""
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
        # EV = 0.2*5 - 0.8*3 = 1.0 - 2.4 = -1.4 < 0.05
        assert result is False
        assert plan['expected_value'] == pytest.approx(-1.4, abs=0.01)

    def test_ev_positive_case_passes(self):
        """Positive EV passes the gate"""
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
        # EV = 0.7*5 - 0.3*3 = 3.5 - 0.9 = 2.6 > 0.05
        assert result is True
        assert plan['expected_value'] == pytest.approx(2.6, abs=0.01)

    def test_bucket_metrics_has_required_fields(self):
        """Bucket metrics must have all AC-07 required fields"""
        judge = _make_judge()
        segmented = {
            'metrics_by_side': {
                'long': {
                    'trade_count': 10, 'win_rate': 0.6,
                    'profit_factor': 2.0, 'total_pnl': 15.0,
                    'gross_profit': 20.0, 'gross_loss': 5.0,
                    'insufficient_sample': False,
                }
            },
            'metrics_by_regime': {},
            'metrics_by_slot_type': {},
        }
        result = judge._parse_segmented_metrics(segmented)
        bucket = result['side_long']
        required = ['trade_count', 'win_rate_ratio', 'win_rate_pct',
                    'profit_factor', 'gross_profit', 'gross_loss', 'insufficient_sample']
        for field in required:
            assert field in bucket, f"Missing required field: {field}"
