"""AC-SHORT-01 ~ AC-SHORT-06 验收测试 — 空单 side-aware 入场门控"""

import os
import sys
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ═══ AC-SHORT-01: 普通 short 必须依赖 daily_bias ═══


class TestACShort01DailyBias:
    """Normal short requires daily_bias=bearish."""

    @pytest.fixture
    def judge(self):
        with patch.dict(os.environ, {
            'OKX_API_KEY': 'test', 'OKX_SECRET': 'test', 'OKX_PASSPHRASE': 'test',
        }):
            from agents.trading.judge import MultiJudge
            j = MultiJudge.__new__(MultiJudge)
            j._short_regime_guard_enabled = True
            j._short_live_min_score = 55
            j._short_live_min_rsi = 40
            j._short_live_min_range_pos = 0.45
            j._short_live_require_daily_bearish = True
            j._short_live_min_htf_votes = 2
            j._short_live_max_pre_move = -0.01
            j._probe_short_enabled = True
            j._probe_short_cooldown_until = 0
            j._probe_short_active = None
            j._pending_open_slots = {}
            j._symbol_tech_cache = {
                'BTC-USDT': {'trend': {'tf_4h_rsi': 65}, 'momentum': {'volume_ratio': 2.0}},
                'TEST-USDT': {'risk': {'liquidity_score': 50}},
            }
            j._probe_short_max_position_pct = 0.3
            j._probe_short_max_leverage = 3
            j._max_trade_amount = 30
            j._rr_floor_default = 1.5
            j._rr_floor_long_bullish = 1.3
            j._rr_floor_short_bullish = 1.8
            j._low_rr_slot_enabled = True
            j.logger = MagicMock()

            class _MockLedger:
                _enabled = False
            j._counterfactual_ledger = _MockLedger()

            class _MockRegime:
                _effective_regime = 'bearish'
                def snapshot(self):
                    return {'effective_regime': 'bearish', 'raw_regime': 'bearish', 'confidence': 70}
                def is_probe_short_eligible(self, btc_tech, techs):
                    return True
            j._regime_manager = _MockRegime()
            return j

    def _make_tech(self, daily_bias='bearish', range_pos=0.6, pre_move=-0.005, rsi=55):
        return {
            'trend': {'direction': 'bearish', 'daily_bias': daily_bias, 'higher_tf_bias': 'bearish'},
            'entry_timing': {'tf_15m_confirm_short': True},
            'indicators': {'rsi': rsi},
            'short_context': {
                'position_in_24h_range': range_pos,
                'pre_12h_return_pct': pre_move,
            },
        }

    def test_daily_neutral_blocks_normal_short(self, judge):
        """daily_bias=neutral → normal short blocked, routes to probe."""
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = self._make_tech(daily_bias='neutral')
        result = judge._apply_regime_policy('TEST-USDT', 'open_short', plan, -60, tech)
        # Should be blocked or routed to probe
        if result is None:
            # Routed to probe (plan mutated)
            assert plan.get('is_probe') is True
        else:
            assert 'daily_bearish_required' in result

    def test_daily_bullish_blocks_normal_short(self, judge):
        """daily_bias=bullish → normal short blocked."""
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = self._make_tech(daily_bias='bullish')
        result = judge._apply_regime_policy('TEST-USDT', 'open_short', plan, -60, tech)
        if result is None:
            assert plan.get('is_probe') is True
        else:
            assert 'daily_bearish_required' in result

    def test_daily_bearish_allows_normal_short(self, judge):
        """daily_bias=bearish → normal short passes side-aware gate."""
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = self._make_tech(daily_bias='bearish', range_pos=0.6, pre_move=-0.005)
        result = judge._apply_regime_policy('TEST-USDT', 'open_short', plan, -60, tech)
        # Should pass (no rejection from side-aware gate)
        assert result is None
        assert plan.get('is_probe') is not True


# ═══ AC-SHORT-02: 普通 short 不得是 late chase ═══


class TestACShort02LateChase:
    """Late chase detection: range_pos, pre_move, rsi gates."""

    @pytest.fixture
    def judge(self):
        with patch.dict(os.environ, {
            'OKX_API_KEY': 'test', 'OKX_SECRET': 'test', 'OKX_PASSPHRASE': 'test',
        }):
            from agents.trading.judge import MultiJudge
            j = MultiJudge.__new__(MultiJudge)
            j._short_regime_guard_enabled = True
            j._short_live_min_score = 55
            j._short_live_min_rsi = 40
            j._short_live_min_range_pos = 0.45
            j._short_live_require_daily_bearish = True
            j._short_live_min_htf_votes = 2
            j._short_live_max_pre_move = -0.01
            j._probe_short_enabled = False  # disable probe to test pure rejection
            j._probe_short_cooldown_until = 0
            j._probe_short_active = None
            j._pending_open_slots = {}
            j._symbol_tech_cache = {'BTC-USDT': {}, 'TEST-USDT': {'risk': {'liquidity_score': 50}}}
            j._probe_short_max_position_pct = 0.3
            j._probe_short_max_leverage = 3
            j._max_trade_amount = 30
            j._rr_floor_default = 1.5
            j._rr_floor_long_bullish = 1.3
            j._rr_floor_short_bullish = 1.8
            j._low_rr_slot_enabled = True
            j.logger = MagicMock()

            class _MockLedger:
                _enabled = False
            j._counterfactual_ledger = _MockLedger()

            class _MockRegime:
                _effective_regime = 'bearish'
                def snapshot(self):
                    return {'effective_regime': 'bearish', 'raw_regime': 'bearish', 'confidence': 70}
                def is_probe_short_eligible(self, btc_tech, techs):
                    return True
            j._regime_manager = _MockRegime()
            return j

    def test_range_pos_too_low_blocks(self, judge):
        """position_in_24h_range < 0.45 → blocked."""
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = {
            'trend': {'direction': 'bearish', 'daily_bias': 'bearish', 'higher_tf_bias': 'bearish'},
            'entry_timing': {'tf_15m_confirm_short': True},
            'indicators': {'rsi': 55},
            'short_context': {'position_in_24h_range': 0.20, 'pre_12h_return_pct': -0.005},
        }
        result = judge._apply_regime_policy('TEST-USDT', 'open_short', plan, -60, tech)
        assert result is not None
        assert 'range_position_too_low' in result

    def test_pre_move_too_deep_blocks(self, judge):
        """pre_12h_return_pct <= -1% → blocked."""
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = {
            'trend': {'direction': 'bearish', 'daily_bias': 'bearish', 'higher_tf_bias': 'bearish'},
            'entry_timing': {'tf_15m_confirm_short': True},
            'indicators': {'rsi': 55},
            'short_context': {'position_in_24h_range': 0.55, 'pre_12h_return_pct': -0.015},
        }
        result = judge._apply_regime_policy('TEST-USDT', 'open_short', plan, -60, tech)
        assert result is not None
        assert 'pre_move_too_deep' in result

    def test_rsi_too_low_blocks(self, judge):
        """RSI < 40 → blocked (oversold, reversal risk)."""
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = {
            'trend': {'direction': 'bearish', 'daily_bias': 'bearish', 'higher_tf_bias': 'bearish'},
            'entry_timing': {'tf_15m_confirm_short': True},
            'indicators': {'rsi': 30},
            'short_context': {'position_in_24h_range': 0.55, 'pre_12h_return_pct': -0.005},
        }
        result = judge._apply_regime_policy('TEST-USDT', 'open_short', plan, -60, tech)
        assert result is not None
        assert 'rsi_too_low_for_short' in result

    def test_score_too_low_blocks(self, judge):
        """abs(score) < SHORT_LIVE_MIN_SCORE → blocked before live short."""
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = {
            'trend': {'direction': 'bearish', 'daily_bias': 'bearish', 'higher_tf_bias': 'bearish'},
            'entry_timing': {'tf_15m_confirm_short': True},
            'indicators': {'rsi': 55},
            'short_context': {'position_in_24h_range': 0.55, 'pre_12h_return_pct': -0.005},
        }
        result = judge._apply_regime_policy('TEST-USDT', 'open_short', plan, -45, tech)
        assert result is not None
        assert 'short_score_too_low' in result

    def test_htf_votes_insufficient_blocks(self, judge):
        """HTF bearish votes < SHORT_LIVE_MIN_HTF_VOTES → blocked."""
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = {
            'trend': {'direction': 'neutral', 'daily_bias': 'bearish', 'higher_tf_bias': 'neutral'},
            'entry_timing': {'tf_15m_confirm_short': True},
            'indicators': {'rsi': 55},
            'short_context': {'position_in_24h_range': 0.55, 'pre_12h_return_pct': -0.005},
        }
        result = judge._apply_regime_policy('TEST-USDT', 'open_short', plan, -60, tech)
        assert result is not None
        assert 'htf_votes_insufficient' in result


# ═══ AC-SHORT-04: backtest 与 live 参数同构 ═══


class TestACShort04BacktestLiveSync:
    """Backtest uses same side-aware gates as live."""

    def test_backtest_blocks_neutral_daily_short(self):
        """Backtest blocks short when daily_bias != bearish."""
        from event_backtest import EventBacktest
        import pandas as pd
        import numpy as np

        eb = EventBacktest(
            initial_capital=1000,
            entry_threshold=25,
            short_regime_guard=True,
            short_live_require_daily_bearish=True,
            short_live_min_range_pos=0.45,
            short_live_max_pre_move=-0.01,
            short_live_min_rsi=40,
            probe_short_enabled=False,
        )

        # Create minimal row with short signal but neutral daily
        row = pd.Series({
            'close': 100, 'open': 101, 'high': 102, 'low': 99,
            'volume': 1000, 'volume_ma': 800,
            'ma_fast': 99, 'ma_slow': 101,
            'rsi': 55, 'atr': 2.0,
            'entry_short': 1, 'entry_long': 0,
            'ma_aligned_short': 1, 'ma_aligned_long': 0,
            'htf_bias': 'bearish', 'daily_bias': 'neutral',
            'position_in_24h_range': 0.6,
            'pre_12h_return_pct': -0.005,
        })

        result = eb._check_entry_with_regime(row, 'bearish', -10000, 100)
        # Should be blocked because daily_bias != bearish
        assert result is None

    def test_backtest_allows_bearish_daily_short(self):
        """Backtest allows short when daily_bias=bearish and all gates pass."""
        from event_backtest import EventBacktest
        import pandas as pd

        eb = EventBacktest(
            initial_capital=1000,
            entry_threshold=25,
            short_regime_guard=True,
            short_live_require_daily_bearish=True,
            short_live_min_range_pos=0.45,
            short_live_max_pre_move=-0.01,
            short_live_min_rsi=40,
            probe_short_enabled=False,
        )

        row = pd.Series({
            'close': 100, 'open': 101, 'high': 102, 'low': 99,
            'volume': 1000, 'volume_ma': 800,
            'ma_fast': 99, 'ma_slow': 101,
            'rsi': 55, 'atr': 2.0,
            'entry_short': 1, 'entry_long': 0,
            'ma_aligned_short': 1, 'ma_aligned_long': 0,
            'htf_bias': 'bearish', 'daily_bias': 'bearish',
            'position_in_24h_range': 0.6,
            'pre_12h_return_pct': -0.005,
        })

        result = eb._check_entry_with_regime(row, 'bearish', -10000, 100)
        assert result is not None
        assert result['direction'] == 'short'

    def test_backtest_blocks_low_range_pos(self):
        """Backtest blocks short when position_in_24h_range < 0.45."""
        from event_backtest import EventBacktest
        import pandas as pd

        eb = EventBacktest(
            initial_capital=1000,
            entry_threshold=25,
            short_live_require_daily_bearish=True,
            short_live_min_range_pos=0.45,
            short_live_max_pre_move=-0.01,
            short_live_min_rsi=40,
            probe_short_enabled=False,
        )

        row = pd.Series({
            'close': 100, 'open': 101, 'high': 102, 'low': 99,
            'volume': 1000, 'volume_ma': 800,
            'ma_fast': 99, 'ma_slow': 101,
            'rsi': 55, 'atr': 2.0,
            'entry_short': 1, 'entry_long': 0,
            'ma_aligned_short': 1, 'ma_aligned_long': 0,
            'htf_bias': 'bearish', 'daily_bias': 'bearish',
            'position_in_24h_range': 0.20,  # too low
            'pre_12h_return_pct': -0.005,
        })

        result = eb._check_entry_with_regime(row, 'bearish', -10000, 100)
        assert result is None


# ═══ AC-SHORT-05: long 不回退 ═══


class TestACShort05LongUnaffected:
    """Long path is not affected by short-side gates."""

    def test_long_unaffected_by_short_gates(self):
        """Long entry ignores all short-side gates."""
        from event_backtest import EventBacktest
        import pandas as pd

        eb = EventBacktest(
            initial_capital=1000,
            entry_threshold=25,
            short_live_require_daily_bearish=True,
            short_live_min_range_pos=0.45,
            short_live_max_pre_move=-0.01,
            short_live_min_rsi=40,
        )

        row = pd.Series({
            'close': 100, 'open': 99, 'high': 102, 'low': 98,
            'volume': 1000, 'volume_ma': 800,
            'ma_fast': 101, 'ma_slow': 99,
            'rsi': 55, 'atr': 2.0,
            'entry_long': 1, 'entry_short': 0,
            'htf_bias': 'bullish', 'daily_bias': 'neutral',
            'position_in_24h_range': 0.20,  # would block short
            'pre_12h_return_pct': -0.02,    # would block short
        })

        result = eb._check_entry_with_regime(row, 'bullish', -10000, 100)
        assert result is not None
        assert result['direction'] == 'long'
