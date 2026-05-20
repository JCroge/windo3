"""P2-3: 15m 入场确认端到端集成测试

验证完整链路: tech dict (含 15m entry_timing) → _make_decision → trade_decision hold/deferred
不直接调用私有 _check_15m_entry_timing，而是走 _make_decision 主路径。
"""

import pytest
import asyncio
import time
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import AsyncMock, MagicMock, patch
from agents.trading.judge import MultiJudge


def _build_judge(config_overrides=None):
    """构造可调用 _make_decision 的 Judge 实例"""
    config = {
        'exchange': 'okx', 'max_trade_amount': 10, 'leverage': 3,
        'min_confidence': 60, 'min_deferred_signal_score': 45,
        'min_liquidity_score_for_weak_signal': 1, 'max_concurrent_positions': 3,
        'entry_timing_15m_enabled': True, 'entry_timing_15m_required': True,
        'entry_timing_15m_neutral_allows_strong_signal': True,
        'entry_timing_15m_strong_score_threshold': 70,
        'entry_timing_15m_defer_on_block': True, 'entry_timing_15m_timeout_hours': 4,
        'archetype_cooldown_enabled': False, 'ranking_enabled': False,
        'ev_prior_wins': 2, 'ev_prior_total': 5, 'ev_strong_signal_threshold': 70,
    }
    if config_overrides:
        config.update(config_overrides)

    judge = MultiJudge(config)
    judge.logger = MagicMock()
    judge.exchange = MagicMock()
    judge._available_balance = 1000.0
    judge._balance_adapter = MagicMock()
    judge._balance_adapter.get_total.return_value = 1000.0
    judge._balance_adapter.get_free_async = AsyncMock(return_value=900.0)
    judge._llm_client = None
    judge.bus = MagicMock()
    judge.bus.publish = AsyncMock()
    judge.publish = AsyncMock()
    # Bypass EV gate: set enough trade history with good win rate
    judge._total_completed_trades = 20
    judge._recent_win_rate = 0.65
    judge._recent_wins = 13
    judge._recent_profit_factor = 2.0
    # Mock LLM to agree with rule signal direction
    async def mock_ask_llm(symbol, tech, score):
        action = 'open_long' if score > 0 else ('open_short' if score < 0 else 'hold')
        return {'action': action, 'confidence': 70, 'reasoning': 'test', 'key_factors': [], 'risk_warnings': []}
    judge._ask_llm = mock_ask_llm
    return judge


def _build_tech_long_setup(entry_timing_bias='bearish', entry_timing_rsi=42):
    """构造 1h 做多 setup + 可配置的 15m entry_timing"""
    ma_alignment = entry_timing_bias
    recent_closes = 'down' if entry_timing_bias == 'bearish' else ('up' if entry_timing_bias == 'bullish' else 'mixed')
    momentum = 'falling' if entry_timing_bias == 'bearish' else ('rising' if entry_timing_bias == 'bullish' else 'flat')

    block_long = (
        (ma_alignment == 'bearish' and entry_timing_rsi < 48) or
        (recent_closes == 'down' and entry_timing_rsi < 50) or
        (entry_timing_bias == 'bearish' and momentum == 'falling')
    )
    block_short = (
        (ma_alignment == 'bullish' and entry_timing_rsi > 52) or
        (recent_closes == 'up' and entry_timing_rsi > 50) or
        (entry_timing_bias == 'bullish' and momentum == 'rising')
    )
    confirm_long = (entry_timing_bias == 'bullish') and not block_long
    confirm_short = (entry_timing_bias == 'bearish') and not block_short

    return {
        'symbol': 'BTC-USDT',
        'trend': {
            'direction': 'bullish',
            'strength': 65,
            'higher_tf_bias': 'bullish',
            'daily_bias': 'bullish',
            'htf_votes': 2,
        },
        'momentum': {
            'rsi': 55,
            'rsi_4h': 55,
            'atr_pct': 0.01,
        },
        'key_levels': {
            'nearest_resistance': 72000,
            'nearest_support': 63000,
            'daily_near_resistance': False,
            'daily_near_support': False,
        },
        'levels': {
            'support': [66000],
            'resistance': [70000],
            'daily_support': [65000],
            'daily_resistance': [72000],
        },
        'risk': {
            'liquidity_score': 80,
            'atr_pct': 0.02,
            'volatility': 'medium',
        },
        'indicators': {
            'price': 67000,
            'ma_fast': 66800,
            'ma_slow': 66500,
            'volume_ratio': 1.2,
        },
        'rule_signal': {
            'entry_long': True,
            'type': 'ma_crossover',
            'direction': 'long',
            'strength': 70,
        },
        'funding': {
            'rate': 0.0001,
            'direction': 'long_pays',
        },
        'microstructure': {},
        'sentiment': {},
        'data_quality': {
            'degraded': False,
            'dimensions_ok': 8,
            'dimensions_total': 9,
            'tf_15m_ok': True,
            'tf_15m_stale': False,
        },
        'entry_timing': {
            'tf_15m_available': True,
            'tf_15m_bias': entry_timing_bias,
            'tf_15m_ma_alignment': ma_alignment,
            'tf_15m_rsi': entry_timing_rsi,
            'tf_15m_momentum': momentum,
            'tf_15m_recent_closes': recent_closes,
            'tf_15m_confirm_long': confirm_long,
            'tf_15m_confirm_short': confirm_short,
            'tf_15m_block_long': block_long,
            'tf_15m_block_short': block_short,
            'tf_15m_reason': f"bias={entry_timing_bias} MA={ma_alignment} RSI={entry_timing_rsi}",
        },
    }


class TestE2E15mFilter:
    """端到端: tech_analysis → _make_decision → trade_decision"""

    @pytest.mark.asyncio
    async def test_15m_bearish_blocks_long_setup(self):
        """1h 做多 setup + 15m bearish → hold 或 deferred（不开仓）"""
        judge = _build_judge()
        tech = _build_tech_long_setup(entry_timing_bias='bearish', entry_timing_rsi=42)

        await judge._make_decision('BTC-USDT', tech)

        judge.publish.assert_called()
        call_args = judge.publish.call_args_list
        decisions = [c for c in call_args if c[0][0] == 'trade_decision']
        assert len(decisions) >= 1

        decision_payload = decisions[-1][0][1]
        action = decision_payload.get('action', 'hold')
        assert action == 'hold', f"Expected hold but got {action}"

        state = judge._get_state('BTC-USDT')
        deferred = state.get('deferred_entry')
        if deferred:
            assert deferred['entry_type'] == 'deferred_15m_confirmation'

    @pytest.mark.asyncio
    async def test_15m_bullish_confirms_long_setup(self):
        """1h 做多 setup + 15m bullish → open_long"""
        judge = _build_judge()
        tech = _build_tech_long_setup(entry_timing_bias='bullish', entry_timing_rsi=58)

        await judge._make_decision('BTC-USDT', tech)

        judge.publish.assert_called()
        call_args = judge.publish.call_args_list
        decisions = [c for c in call_args if c[0][0] == 'trade_decision']
        assert len(decisions) >= 1

        decision_payload = decisions[-1][0][1]
        action = decision_payload.get('action', 'hold')
        assert action == 'open_long', f"Expected open_long but got {action}"

    @pytest.mark.asyncio
    async def test_15m_unavailable_required_blocks(self):
        """15m 数据不可用 + required=True → hold"""
        judge = _build_judge()
        tech = _build_tech_long_setup(entry_timing_bias='bullish', entry_timing_rsi=55)
        tech['entry_timing']['tf_15m_available'] = False
        tech['entry_timing']['tf_15m_confirm_long'] = False
        tech['entry_timing']['tf_15m_block_long'] = False
        tech['data_quality']['tf_15m_ok'] = False

        await judge._make_decision('BTC-USDT', tech)

        judge.publish.assert_called()
        call_args = judge.publish.call_args_list
        decisions = [c for c in call_args if c[0][0] == 'trade_decision']
        assert len(decisions) >= 1

        decision_payload = decisions[-1][0][1]
        action = decision_payload.get('action', 'hold')
        assert action == 'hold', f"Expected hold but got {action}"

    @pytest.mark.asyncio
    async def test_15m_disabled_allows_entry(self):
        """15m 功能关闭时，bearish 15m 不阻止开仓"""
        judge = _build_judge({'entry_timing_15m_enabled': False})
        tech = _build_tech_long_setup(entry_timing_bias='bearish', entry_timing_rsi=42)

        await judge._make_decision('BTC-USDT', tech)

        judge.publish.assert_called()
        call_args = judge.publish.call_args_list
        decisions = [c for c in call_args if c[0][0] == 'trade_decision']
        assert len(decisions) >= 1

        decision_payload = decisions[-1][0][1]
        action = decision_payload.get('action', 'hold')
        assert action == 'open_long', f"Expected open_long when 15m disabled, got {action}"

    @pytest.mark.asyncio
    async def test_15m_attribution_present_on_open(self):
        """开仓决策包含 15m attribution 字段"""
        judge = _build_judge()
        tech = _build_tech_long_setup(entry_timing_bias='bullish', entry_timing_rsi=58)

        await judge._make_decision('BTC-USDT', tech)

        call_args = judge.publish.call_args_list
        decisions = [c for c in call_args if c[0][0] == 'trade_decision']
        assert len(decisions) >= 1

        decision_payload = decisions[-1][0][1]
        if decision_payload.get('action') == 'open_long':
            plan = decision_payload.get('plan', {})
            attribution = plan.get('attribution', {})
            assert 'tf_15m_bias' in attribution or 'tf_15m_entry_status' in attribution
