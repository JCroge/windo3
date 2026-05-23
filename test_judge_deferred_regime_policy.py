"""Test: deferred paths apply the same regime policy as the main open path."""

import time
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(__file__))

from agents.trading.judge import MultiJudge
from utils.market_regime import RegimeManager, REGIME_BULLISH, REGIME_MIXED


def _make_judge_with_regime(regime='bullish'):
    """Construct Judge with a real RegimeManager in a specific regime."""
    config = {
        'max_trade_amount': 10,
        'short_regime_guard_enabled': True,
        'probe_short_enabled': False,
        'low_rr_slot_enabled': True,
        'rr_floor_default': 1.5,
        'rr_floor_long_bullish': 1.30,
        'rr_floor_short_bullish': 1.80,
        'low_rr_max_leverage': 5,
        'low_rr_max_position_pct': 0.5,
    }
    judge = MultiJudge.__new__(MultiJudge)
    judge._short_regime_guard_enabled = True
    judge._short_live_min_score = 55
    judge._short_live_min_rsi = 40
    judge._short_live_min_range_pos = 0.45
    judge._short_live_require_daily_bearish = True
    judge._short_live_min_htf_votes = 2
    judge._short_live_max_pre_move = -0.01
    judge._probe_short_enabled = False
    judge._low_rr_slot_enabled = True
    judge._rr_floor_default = 1.5
    judge._rr_floor_long_bullish = 1.30
    judge._rr_floor_short_bullish = 1.80
    judge._low_rr_max_leverage = 5
    judge._low_rr_max_position_pct = 0.5
    judge._probe_short_max_position_pct = 0.3
    judge._probe_short_max_leverage = 3
    judge._probe_short_cooldown_until = 0
    judge._probe_short_active = None
    judge._max_trade_amount = 10
    judge._symbol_tech_cache = {}
    judge._pending_open_slots = {}

    with patch.object(RegimeManager, '_load_state'):
        rm = RegimeManager({})
        rm._effective_regime = regime
        rm._last_changed_at = time.time() - 7200
    judge._regime_manager = rm

    class FakeLedger:
        _enabled = True
        def record_rejection(self, *a, **kw):
            pass
    judge._counterfactual_ledger = FakeLedger()
    judge.logger = MagicMock()

    return judge


def _make_tech(direction='bullish'):
    return {
        'trend': {'direction': direction, 'higher_tf_bias': direction, 'daily_bias': direction},
        'momentum': {'rsi': 55, 'atr_pct': 0.02},
        'entry_timing': {'tf_15m_confirm_short': False, 'tf_15m_bias': direction},
        'money_flow': {},
        'crowd': {'long_ratio': 0.5},
        'risk': {'liquidity_score': 50},
    }


class TestDeferredRegimePolicy:

    def test_short_blocked_in_bullish_regime(self):
        """Deferred short in bullish regime should be blocked by short guard."""
        judge = _make_judge_with_regime('bullish')
        plan = {'size_usdt': 5, 'leverage': 3, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = _make_tech('bearish')

        result = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, -60, tech)
        assert result is not None
        assert 'short_regime_guard' in result

    def test_short_allowed_in_mixed_regime(self):
        """Deferred short in mixed regime should pass."""
        judge = _make_judge_with_regime('mixed')
        plan = {'size_usdt': 5, 'leverage': 3, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = _make_tech('bearish')

        result = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, -60, tech)
        assert result is None

    def test_rr_below_floor_rejected(self):
        """Plan with R:R below dynamic floor should be rejected."""
        judge = _make_judge_with_regime('bullish')
        # Long in bullish: floor = 1.30
        plan = {'size_usdt': 5, 'leverage': 3, 'risk_reward_ratio': 1.2,
                'effective_risk_reward_ratio': 1.2}
        tech = _make_tech('bullish')

        result = judge._apply_regime_policy('BTC-USDT', 'open_long', plan, 50, tech)
        assert result is not None
        assert 'rr_below_floor' in result

    def test_rr_above_floor_passes(self):
        """Plan with R:R above dynamic floor should pass."""
        judge = _make_judge_with_regime('bullish')
        plan = {'size_usdt': 5, 'leverage': 3, 'risk_reward_ratio': 1.6,
                'effective_risk_reward_ratio': 1.6}
        tech = _make_tech('bullish')

        result = judge._apply_regime_policy('BTC-USDT', 'open_long', plan, 50, tech)
        assert result is None
        assert not plan.get('is_low_rr')

    def test_low_rr_long_scaled_down(self):
        """Long in bullish with R:R between 1.30-1.50 should be scaled down."""
        judge = _make_judge_with_regime('bullish')
        plan = {'size_usdt': 10, 'leverage': 10, 'risk_reward_ratio': 1.4,
                'effective_risk_reward_ratio': 1.4}
        tech = _make_tech('bullish')

        result = judge._apply_regime_policy('BTC-USDT', 'open_long', plan, 50, tech)
        assert result is None
        assert plan['is_low_rr'] is True
        assert plan['slot_type'] == 'low_rr_extra'
        assert plan['size_usdt'] < 10
        assert plan['leverage'] <= 5

    def test_short_rr_floor_higher_in_bullish(self):
        """Short in bullish with R:R < 1.8 is blocked even with strong score.
        The short guard requires effective_rr >= 1.8 as part of strong short conditions.
        """
        judge = _make_judge_with_regime('bullish')
        plan = {'size_usdt': 5, 'leverage': 3, 'risk_reward_ratio': 1.7,
                'effective_risk_reward_ratio': 1.7}
        tech = _make_tech('bearish')
        tech['entry_timing']['tf_15m_confirm_short'] = True
        tech['trend']['higher_tf_bias'] = 'bearish'
        tech['trend']['daily_bias'] = 'bearish'

        # Score -75 with htf_bearish=3 and confirm_15m but R:R=1.7 < 1.8
        result = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, -75, tech)
        assert result is not None
        assert 'short_regime_guard' in result

    def test_strong_short_passes_with_high_rr(self):
        """Strong short in bullish with R:R >= 1.8 and daily bearish passes normally."""
        judge = _make_judge_with_regime('bullish')
        plan = {'size_usdt': 5, 'leverage': 3, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = _make_tech('bearish')
        tech['entry_timing']['tf_15m_confirm_short'] = True
        tech['trend']['higher_tf_bias'] = 'bearish'
        tech['trend']['daily_bias'] = 'bearish'

        result = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, -75, tech)
        assert result is None
        assert not plan.get('is_probe')

    def test_strong_short_degraded_to_probe_when_daily_bullish(self):
        """Strong short in bullish regime with daily still bullish → probe sizing (when probe enabled)."""
        judge = _make_judge_with_regime('bullish')
        judge._probe_short_enabled = True
        judge._probe_short_active = None
        judge._probe_short_cooldown_until = 0
        # Mock is_probe_short_eligible to return True
        judge._regime_manager.is_probe_short_eligible = lambda *a, **kw: True
        # Provide tech cache with liquidity for the symbol
        judge._symbol_tech_cache = {'BTC-USDT': {'risk': {'liquidity_score': 50}}}
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = _make_tech('bearish')
        tech['entry_timing']['tf_15m_confirm_short'] = True
        tech['trend']['higher_tf_bias'] = 'bearish'
        tech['trend']['daily_bias'] = 'bullish'  # daily still bullish

        result = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, -75, tech)
        assert result is None  # not rejected, but degraded
        assert plan['is_probe'] is True
        assert plan['slot_type'] == 'probe_short'
        assert plan['size_usdt'] < 10  # scaled down
        assert plan['leverage'] <= 3  # capped

    def test_degrade_to_probe_rejected_when_probe_disabled(self):
        """degrade_to_probe should reject when PROBE_SHORT_ENABLED=false."""
        judge = _make_judge_with_regime('bullish')
        judge._probe_short_enabled = False
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = _make_tech('bearish')
        tech['entry_timing']['tf_15m_confirm_short'] = True
        tech['trend']['higher_tf_bias'] = 'bearish'
        tech['trend']['daily_bias'] = 'bullish'

        result = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, -75, tech)
        assert result is not None
        assert 'probe_disabled' in result

    def test_degrade_to_probe_rejected_when_probe_active(self):
        """degrade_to_probe should reject when another probe is already active."""
        judge = _make_judge_with_regime('bullish')
        judge._probe_short_enabled = True
        judge._probe_short_active = 'ETH-USDT'  # already active
        judge._probe_short_cooldown_until = 0
        judge._regime_manager.is_probe_short_eligible = lambda *a, **kw: True
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = _make_tech('bearish')
        tech['entry_timing']['tf_15m_confirm_short'] = True
        tech['trend']['higher_tf_bias'] = 'bearish'
        tech['trend']['daily_bias'] = 'bullish'

        result = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, -75, tech)
        assert result is not None
        assert 'probe_active_full' in result

    def test_degrade_to_probe_rejected_during_cooldown(self):
        """degrade_to_probe should reject during probe cooldown period."""
        judge = _make_judge_with_regime('bullish')
        judge._probe_short_enabled = True
        judge._probe_short_active = None
        judge._probe_short_cooldown_until = time.time() + 3600  # 1h remaining
        judge._regime_manager.is_probe_short_eligible = lambda *a, **kw: True
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0}
        tech = _make_tech('bearish')
        tech['entry_timing']['tf_15m_confirm_short'] = True
        tech['trend']['higher_tf_bias'] = 'bearish'
        tech['trend']['daily_bias'] = 'bullish'

        result = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, -75, tech)
        assert result is not None
        assert 'probe_cooldown' in result

    def test_probe_short_uses_probe_rr_floor_not_strong_short_floor(self):
        """Probe short with R:R>=1.30 should not be rejected by bullish short floor 1.80."""
        judge = _make_judge_with_regime('bullish')
        judge._probe_short_enabled = True
        judge._probe_short_active = None
        judge._probe_short_cooldown_until = 0
        judge._regime_manager.is_probe_short_eligible = lambda *a, **kw: True
        judge._symbol_tech_cache = {
            'BTC-USDT': {
                'trend': {'tf_4h_rsi': 65},
                'momentum': {'volume_ratio': 2.0},
                'risk': {'liquidity_score': 50},
            },
        }
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.35,
                'effective_risk_reward_ratio': 1.35}
        tech = _make_tech('bearish')
        tech['entry_timing']['tf_15m_confirm_short'] = True
        tech['trend']['higher_tf_bias'] = 'bearish'
        tech['trend']['daily_bias'] = 'bullish'

        result = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, -55, tech)
        assert result is None
        assert plan['is_probe'] is True
        assert plan['slot_type'] == 'probe_short'
        assert plan['leverage'] <= 3

    def test_short_guard_disabled_uses_default_rr_floor(self):
        """When SHORT_REGIME_GUARD_ENABLED=false, short in bullish uses default floor (1.5), not 1.8."""
        judge = _make_judge_with_regime('bullish')
        judge._short_regime_guard_enabled = False
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.6,
                'effective_risk_reward_ratio': 1.6}
        tech = _make_tech('bearish')

        result = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, -60, tech)
        # R:R=1.6 >= default 1.5, should pass (would fail with 1.8 floor)
        assert result is None
