"""Phase 2 AC-PH2-06: Replay Report Tests"""
import pytest
import json
from replay_report import compute_bucket_metrics, generate_report, filter_by_window


class TestReplayReport:
    def test_compute_bucket_metrics_empty(self):
        result = compute_bucket_metrics([])
        assert result == {}

    def test_compute_bucket_metrics_single_bucket(self):
        trades = [
            {'side': 'long', 'entry_regime': 'bullish', 'entry_type': 'rule_signal',
             'slot_type': 'main', 'pnl': 5.0},
            {'side': 'long', 'entry_regime': 'bullish', 'entry_type': 'rule_signal',
             'slot_type': 'main', 'pnl': -2.0},
            {'side': 'long', 'entry_regime': 'bullish', 'entry_type': 'rule_signal',
             'slot_type': 'main', 'pnl': 3.0},
        ]
        result = compute_bucket_metrics(trades)
        key = 'long_bullish_rule_signal_main'
        assert key in result
        assert result[key]['trade_count'] == 3
        assert result[key]['win_rate'] == round(2/3, 3)
        assert result[key]['insufficient_sample'] is True  # < 5

    def test_filter_by_window(self):
        records = [
            {'timestamp': 100}, {'timestamp': 200},
            {'timestamp': 300}, {'timestamp': 400},
        ]
        result = filter_by_window(records, 150, 350)
        assert len(result) == 2
        assert result[0]['timestamp'] == 200
        assert result[1]['timestamp'] == 300

    def test_generate_report_empty_data(self):
        report = generate_report(0, 100, 'test')
        assert report['window'] == 'test'
        assert report['total_decisions'] == 0
        assert report['bucket_pf'] == {}
        assert report['shadow_tp'] == 0
