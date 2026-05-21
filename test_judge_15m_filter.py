"""RQ-15M-03/04/05: Judge 15m 入场过滤集成测试"""

import pytest
import time
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from agents.trading.judge import MultiJudge


def _make_judge(config_overrides: dict = None) -> MultiJudge:
    """构造 Judge 实例（不启动 event loop）"""
    config = {
        'max_trade_amount': 10,
        'entry_timing_15m_enabled': True,
        'entry_timing_15m_required': True,
        'entry_timing_15m_neutral_allows_strong_signal': True,
        'entry_timing_15m_strong_score_threshold': 70,
        'entry_timing_15m_defer_on_block': True,
        'entry_timing_15m_timeout_hours': 4,
    }
    if config_overrides:
        config.update(config_overrides)
    judge = MultiJudge.__new__(MultiJudge)
    judge._15m_enabled = config['entry_timing_15m_enabled']
    judge._15m_required = config['entry_timing_15m_required']
    judge._15m_neutral_allows_strong = config['entry_timing_15m_neutral_allows_strong_signal']
    judge._15m_strong_score_threshold = config['entry_timing_15m_strong_score_threshold']
    judge._15m_defer_on_block = config['entry_timing_15m_defer_on_block']
    judge._15m_timeout_hours = config['entry_timing_15m_timeout_hours']

    class _MockRegime:
        _effective_regime = 'mixed'
        _raw_regime = 'mixed'
        _confidence = 50
    judge._regime_manager = _MockRegime()
    return judge


def _tech_with_entry_timing(bias='bullish', rsi=55, ma_alignment='bullish',
                            recent_closes='up', momentum='rising',
                            available=True) -> dict:
    """构造包含 entry_timing 的 tech dict"""
    confirm_long = (bias == 'bullish')
    confirm_short = (bias == 'bearish')
    block_long = (
        (ma_alignment == 'bearish' and rsi < 48) or
        (recent_closes == 'down' and rsi < 50) or
        (bias == 'bearish' and momentum == 'falling')
    )
    block_short = (
        (ma_alignment == 'bullish' and rsi > 52) or
        (recent_closes == 'up' and rsi > 50) or
        (bias == 'bullish' and momentum == 'rising')
    )
    return {
        'trend': {'direction': 'bullish', 'strength': 60, 'higher_tf_bias': 'bullish', 'daily_bias': 'bullish'},
        'momentum': {'rsi': 55},
        'risk': {'liquidity_score': 50},
        'rule_signal': {},
        'entry_timing': {
            'tf_15m_available': available,
            'tf_15m_bias': bias,
            'tf_15m_ma_alignment': ma_alignment,
            'tf_15m_rsi': rsi,
            'tf_15m_momentum': momentum,
            'tf_15m_recent_closes': recent_closes,
            'tf_15m_confirm_long': confirm_long and not block_long,
            'tf_15m_confirm_short': confirm_short and not block_short,
            'tf_15m_block_long': block_long,
            'tf_15m_block_short': block_short,
            'tf_15m_reason': f"bias={bias} MA={ma_alignment} RSI={rsi}",
        },
    }


class TestJudge15mFilter:
    """RQ-15M-03: 开仓硬过滤"""

    def test_bearish_15m_blocks_long(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='bearish', rsi=42, ma_alignment='bearish',
                                       recent_closes='down', momentum='falling')
        allowed, reason, should_defer = judge._check_15m_entry_timing(tech, 'open_long', 75)
        assert allowed is False
        assert should_defer is True
        assert reason  # non-empty reason

    def test_bullish_15m_blocks_short(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='bullish', rsi=61, ma_alignment='bullish',
                                       recent_closes='up', momentum='rising')
        allowed, reason, should_defer = judge._check_15m_entry_timing(tech, 'open_short', -75)
        assert allowed is False
        assert should_defer is True

    def test_bullish_15m_confirms_long(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='bullish', rsi=55, ma_alignment='bullish',
                                       recent_closes='up', momentum='rising')
        allowed, reason, should_defer = judge._check_15m_entry_timing(tech, 'open_long', 65)
        assert allowed is True
        assert 'confirmed' in reason

    def test_bearish_15m_confirms_short(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='bearish', rsi=42, ma_alignment='bearish',
                                       recent_closes='down', momentum='falling')
        allowed, reason, should_defer = judge._check_15m_entry_timing(tech, 'open_short', -65)
        assert allowed is True
        assert 'confirmed' in reason

    def test_neutral_allows_strong_signal(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='neutral', rsi=50, ma_alignment='neutral',
                                       recent_closes='mixed', momentum='flat')
        allowed, reason, should_defer = judge._check_15m_entry_timing(tech, 'open_long', 78)
        assert allowed is True
        assert 'strong' in reason

    def test_neutral_blocks_weak_signal(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='neutral', rsi=50, ma_alignment='neutral',
                                       recent_closes='mixed', momentum='flat')
        allowed, reason, should_defer = judge._check_15m_entry_timing(tech, 'open_long', 55)
        assert allowed is False
        assert should_defer is True

    def test_unavailable_blocks_when_required(self):
        judge = _make_judge({'entry_timing_15m_required': True})
        tech = _tech_with_entry_timing(available=False)
        allowed, reason, should_defer = judge._check_15m_entry_timing(tech, 'open_long', 75)
        assert allowed is False
        assert 'unavailable' in reason
        assert should_defer is False

    def test_unavailable_allows_when_not_required(self):
        judge = _make_judge({'entry_timing_15m_required': False})
        tech = _tech_with_entry_timing(available=False)
        allowed, reason, should_defer = judge._check_15m_entry_timing(tech, 'open_long', 75)
        assert allowed is True

    def test_disabled_always_allows(self):
        judge = _make_judge({'entry_timing_15m_enabled': False})
        tech = _tech_with_entry_timing(bias='bearish', rsi=30, ma_alignment='bearish',
                                       recent_closes='down', momentum='falling')
        allowed, reason, should_defer = judge._check_15m_entry_timing(tech, 'open_long', 50)
        assert allowed is True

    def test_defer_on_block_false_no_defer(self):
        judge = _make_judge({'entry_timing_15m_defer_on_block': False})
        tech = _tech_with_entry_timing(bias='bearish', rsi=42, ma_alignment='bearish',
                                       recent_closes='down', momentum='falling')
        allowed, reason, should_defer = judge._check_15m_entry_timing(tech, 'open_long', 75)
        assert allowed is False
        assert should_defer is False


class TestJudge15mDeferredReconfirm:
    """RQ-15M-05: deferred 触发时 15m 二次确认"""

    def test_bearish_blocks_deferred_long(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='bearish', rsi=42, ma_alignment='bearish',
                                       recent_closes='down', momentum='falling')
        result = judge._check_15m_deferred_reconfirm(tech, 'open_long')
        assert result != ""
        assert 'bearish' in result or 'long' in result

    def test_bullish_blocks_deferred_short(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='bullish', rsi=61, ma_alignment='bullish',
                                       recent_closes='up', momentum='rising')
        result = judge._check_15m_deferred_reconfirm(tech, 'open_short')
        assert result != ""

    def test_neutral_allows_deferred_long(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='neutral', rsi=50, ma_alignment='neutral',
                                       recent_closes='mixed', momentum='flat')
        result = judge._check_15m_deferred_reconfirm(tech, 'open_long')
        assert result == ""

    def test_low_rsi_blocks_deferred_long(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='neutral', rsi=40, ma_alignment='neutral',
                                       recent_closes='mixed', momentum='flat')
        result = judge._check_15m_deferred_reconfirm(tech, 'open_long')
        assert result != ""
        assert '45' in result or 'rsi' in result.lower()

    def test_high_rsi_blocks_deferred_short(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='neutral', rsi=60, ma_alignment='neutral',
                                       recent_closes='mixed', momentum='flat')
        result = judge._check_15m_deferred_reconfirm(tech, 'open_short')
        assert result != ""
        assert '55' in result or 'rsi' in result.lower()

    def test_closes_down_blocks_deferred_long(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='neutral', rsi=50, ma_alignment='neutral',
                                       recent_closes='down', momentum='flat')
        result = judge._check_15m_deferred_reconfirm(tech, 'open_long')
        assert result != ""

    def test_closes_up_blocks_deferred_short(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='neutral', rsi=50, ma_alignment='neutral',
                                       recent_closes='up', momentum='flat')
        result = judge._check_15m_deferred_reconfirm(tech, 'open_short')
        assert result != ""

    def test_disabled_always_passes(self):
        judge = _make_judge({'entry_timing_15m_enabled': False})
        tech = _tech_with_entry_timing(bias='bearish', rsi=30, ma_alignment='bearish',
                                       recent_closes='down', momentum='falling')
        result = judge._check_15m_deferred_reconfirm(tech, 'open_long')
        assert result == ""


class TestJudge15mAttribution:
    """RQ-15M-06: 归因字段包含 15m"""

    def test_attribution_contains_15m_fields(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='bullish', rsi=55, ma_alignment='bullish',
                                       recent_closes='up', momentum='rising')
        plan = {
            'effective_risk_reward_ratio': 2.0,
            'expected_value': 0.1,
            'p_win_used': 0.6,
            'p_win_source': 'rolling',
        }
        attribution = judge._build_attribution(tech, 'open_long', 70, plan, None, 'rule_signal')
        assert 'tf_15m_bias' in attribution
        assert attribution['tf_15m_bias'] == 'bullish'
        assert attribution['tf_15m_rsi'] == 55
        assert attribution['tf_15m_entry_status'] == 'confirmed'
        assert attribution['tf_15m_ma_alignment'] == 'bullish'
        assert attribution['tf_15m_recent_closes'] == 'up'

    def test_attribution_blocked_status(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(bias='bearish', rsi=42, ma_alignment='bearish',
                                       recent_closes='down', momentum='falling')
        plan = {
            'effective_risk_reward_ratio': 2.0,
            'expected_value': 0.1,
            'p_win_used': 0.6,
            'p_win_source': 'rolling',
        }
        attribution = judge._build_attribution(tech, 'open_long', 70, plan, None, 'rule_signal')
        assert attribution['tf_15m_entry_status'] == 'blocked'
        assert attribution['tf_15m_block_reason'] != ''

    def test_attribution_unavailable_status(self):
        judge = _make_judge()
        tech = _tech_with_entry_timing(available=False)
        plan = {
            'effective_risk_reward_ratio': 2.0,
            'expected_value': 0.1,
            'p_win_used': 0.6,
            'p_win_source': 'rolling',
        }
        attribution = judge._build_attribution(tech, 'open_long', 70, plan, None, 'rule_signal')
        assert attribution['tf_15m_entry_status'] == 'unavailable'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
