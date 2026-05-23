"""AC2-05 + AC2-06: Invalid win_rate rejection + request_id always-on."""
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


class TestInvalidWinRateRejection:
    def test_win_rate_150_marked_invalid(self):
        """win_rate=150 must not enter valid bucket."""
        judge = _make_judge()
        segmented = {
            'metrics_by_side': {'long': {'trade_count': 10, 'win_rate': 150}},
            'metrics_by_regime': {},
            'metrics_by_slot_type': {},
        }
        result = judge._parse_segmented_metrics(segmented)
        assert 'side_long' not in result

    def test_win_rate_negative_marked_invalid(self):
        """win_rate=-0.1 must not enter valid bucket."""
        judge = _make_judge()
        segmented = {
            'metrics_by_side': {'long': {'trade_count': 10, 'win_rate': -0.1}},
            'metrics_by_regime': {},
            'metrics_by_slot_type': {},
        }
        result = judge._parse_segmented_metrics(segmented)
        assert 'side_long' not in result

    def test_win_rate_65_converted_to_ratio(self):
        """win_rate=65 (percentage) auto-converts to 0.65 ratio."""
        judge = _make_judge()
        segmented = {
            'metrics_by_side': {'long': {'trade_count': 10, 'win_rate': 65}},
            'metrics_by_regime': {},
            'metrics_by_slot_type': {},
        }
        result = judge._parse_segmented_metrics(segmented)
        assert 'side_long' in result
        assert result['side_long']['win_rate_ratio'] == 0.65
        assert result['side_long']['win_rate_pct'] == 65.0

    def test_win_rate_0_65_stays_ratio(self):
        """win_rate=0.65 stays as ratio."""
        judge = _make_judge()
        segmented = {
            'metrics_by_side': {'long': {'trade_count': 10, 'win_rate': 0.65}},
            'metrics_by_regime': {},
            'metrics_by_slot_type': {},
        }
        result = judge._parse_segmented_metrics(segmented)
        assert result['side_long']['win_rate_ratio'] == 0.65

    def test_win_rate_100_boundary_valid(self):
        """win_rate=100 is boundary — converts to 1.0 ratio."""
        judge = _make_judge()
        segmented = {
            'metrics_by_side': {'long': {'trade_count': 10, 'win_rate': 100}},
            'metrics_by_regime': {},
            'metrics_by_slot_type': {},
        }
        result = judge._parse_segmented_metrics(segmented)
        assert 'side_long' in result
        assert result['side_long']['win_rate_ratio'] == 1.0


class TestRequestIdAlwaysOn:
    def test_request_id_enabled_always_true(self):
        """AC2-06: _request_id_enabled is always True regardless of config."""
        judge = _make_judge()
        assert judge._request_id_enabled is True

    def test_build_attribution_always_has_request_id(self):
        """Attribution always generates a request_id."""
        judge = _make_judge()
        judge._regime_manager._raw_regime = 'bullish'
        judge._regime_manager._confidence = 0.8
        tech = {'trend': {}, 'momentum': {}, 'risk': {}, 'rule_signal': {}, 'entry_timing': {}}
        plan = {'effective_risk_reward_ratio': 2.0, 'expected_value': 0.1}
        attr = judge._build_attribution(tech, 'open_long', 70, plan)
        assert attr['request_id'] != ''
        assert len(attr['request_id']) > 0
