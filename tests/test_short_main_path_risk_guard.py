"""Tests for _classify_short_entry_risk helper in MultiJudge (Task 2)."""
import pytest
from unittest.mock import MagicMock


def _make_judge(config=None):
    """Create a minimal MultiJudge instance for testing."""
    from agents.trading.judge import MultiJudge
    default_config = {
        'short_regime_guard_enabled': True,
        'short_live_min_score': 55,
        'short_live_min_rsi': 40,
        'short_live_min_range_pos': 0.45,
        'short_live_require_daily_bearish': True,
        'short_live_min_htf_votes': 2,
        'short_live_max_pre_move': -0.01,
    }
    if config:
        default_config.update(config)
    judge = MultiJudge.__new__(MultiJudge)
    judge._short_regime_guard_enabled = default_config['short_regime_guard_enabled']
    judge._short_live_min_score = default_config['short_live_min_score']
    judge._short_live_min_rsi = default_config['short_live_min_rsi']
    judge._short_live_min_range_pos = default_config['short_live_min_range_pos']
    judge._short_live_require_daily_bearish = default_config['short_live_require_daily_bearish']
    judge._short_live_min_htf_votes = default_config['short_live_min_htf_votes']
    judge._short_live_max_pre_move = default_config['short_live_max_pre_move']
    return judge


def _good_tech():
    return {
        'trend': {
            'daily_bias': 'bearish',
            'direction': 'bearish',
            'higher_tf_bias': 'bearish',
        },
        'short_context': {
            'position_in_24h_range': 0.8,
            'pre_12h_return_pct': 0.05,
        },
        'indicators': {'rsi': 55},
    }


def _good_plan():
    return {'is_probe': False}


class TestClassifyShortEntryRisk:
    def test_pass_when_guard_disabled(self):
        judge = _make_judge({'short_regime_guard_enabled': False})
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), _good_tech(), 60.0
        )
        assert result['allowed'] is True
        assert result['decision'] == 'pass'

    def test_pass_for_long_action(self):
        judge = _make_judge()
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_long', _good_plan(), _good_tech(), 60.0
        )
        assert result['allowed'] is True
        assert result['decision'] == 'pass'

    def test_pass_for_probe(self):
        judge = _make_judge()
        plan = {'is_probe': True}
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', plan, _good_tech(), 60.0
        )
        assert result['allowed'] is True
        assert result['decision'] == 'pass'

    def test_reject_daily_bearish_required(self):
        judge = _make_judge()
        tech = _good_tech()
        tech['trend']['daily_bias'] = 'bullish'
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), tech, 60.0
        )
        assert result['allowed'] is False
        assert result['decision'] == 'reject'
        assert result['reason'] == 'daily_bearish_required'

    def test_reject_range_position_too_low(self):
        judge = _make_judge()
        tech = _good_tech()
        tech['short_context']['position_in_24h_range'] = 0.3
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), tech, 60.0
        )
        assert result['allowed'] is False
        assert result['reason'] == 'range_position_too_low'

    def test_reject_pre_move_too_deep(self):
        judge = _make_judge()
        tech = _good_tech()
        tech['short_context']['pre_12h_return_pct'] = -0.05
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), tech, 60.0
        )
        assert result['allowed'] is False
        assert result['reason'] == 'pre_move_too_deep'

    def test_reject_rsi_too_low(self):
        judge = _make_judge()
        tech = _good_tech()
        tech['indicators']['rsi'] = 35
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), tech, 60.0
        )
        assert result['allowed'] is False
        assert result['reason'] == 'rsi_too_low_for_short'

    def test_reject_score_too_low(self):
        judge = _make_judge()
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), _good_tech(), 30.0
        )
        assert result['allowed'] is False
        assert result['reason'] == 'short_score_too_low'

    def test_reject_htf_votes_insufficient(self):
        judge = _make_judge()
        tech = _good_tech()
        tech['trend']['direction'] = 'neutral'
        tech['trend']['higher_tf_bias'] = 'neutral'
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), tech, 60.0
        )
        assert result['allowed'] is False
        assert result['reason'] == 'htf_votes_insufficient'

    def test_pass_all_gates(self):
        judge = _make_judge()
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), _good_tech(), 60.0
        )
        assert result['allowed'] is True
        assert result['decision'] == 'pass'
        assert result['short_gate_version'] == 'short_main_path_parity_v1'
        assert 'metrics' in result
        assert result['metrics']['rsi'] == 55

    def test_llm_reversal_risk_detected(self):
        judge = _make_judge()
        llm_result = {'reasoning': '市场可能出现看涨背离，注意风险'}
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), _good_tech(), 60.0, llm_result=llm_result
        )
        assert result['llm_short_reversal_risk'] is True

    def test_metrics_returned_on_reject(self):
        judge = _make_judge()
        tech = _good_tech()
        tech['trend']['daily_bias'] = 'neutral'
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), tech, 60.0
        )
        assert result['allowed'] is False
        assert 'metrics' in result
        assert 'range_position_24h' in result['metrics']
        assert 'pre_12h_return_pct' in result['metrics']
        assert 'rsi' in result['metrics']
        assert 'htf_bearish_votes' in result['metrics']
