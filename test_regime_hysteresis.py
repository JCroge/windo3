"""Tests for MarketRegime hysteresis and detection."""
import time
import pytest
from unittest.mock import patch
from utils.market_regime import RegimeManager, REGIME_BULLISH, REGIME_MIXED, REGIME_BEARISH


def make_tech(direction='bullish', higher_tf_bias='bullish', daily_bias='bullish',
              atr_pct=0.02, tf_4h_rsi=55, volume_ratio=1.0):
    return {
        'trend': {
            'direction': direction,
            'higher_tf_bias': higher_tf_bias,
            'daily_bias': daily_bias,
            'tf_4h_rsi': tf_4h_rsi,
        },
        'momentum': {'atr_pct': atr_pct, 'volume_ratio': volume_ratio},
        'entry_timing': {'tf_15m_bias': direction},
        'money_flow': {},
        'crowd': {'long_ratio': 0.5},
        'risk': {'liquidity_score': 50},
    }


@pytest.fixture
def regime():
    with patch.object(RegimeManager, '_load_state'):
        rm = RegimeManager({}, logger=None)
        rm._effective_regime = REGIME_BULLISH
        rm._last_changed_at = time.time() - 3600  # well past min_hold
    return rm


class TestRegimeHysteresis:
    """AC-REG-01: Flapping sequence stays stable."""

    def test_single_mixed_does_not_switch(self, regime):
        techs = {f'SYM{i}-USDT': make_tech('neutral') for i in range(5)}
        techs['BTC-USDT'] = make_tech('bullish')
        techs['ETH-USDT'] = make_tech('bullish')
        # With BTC/ETH bullish and only 5/7 neutral, might compute mixed
        # But single update should not switch from bullish
        regime.update(techs)
        assert regime._effective_regime == REGIME_BULLISH

    def test_flapping_sequence_stays_bullish(self, regime):
        """AC-REG-01: bullish, mixed, bullish, mixed, bullish → stays bullish."""
        bullish_techs = {f'SYM{i}-USDT': make_tech('bullish') for i in range(6)}
        bullish_techs['BTC-USDT'] = make_tech('bullish')
        mixed_techs = {f'SYM{i}-USDT': make_tech('neutral') for i in range(6)}
        mixed_techs['BTC-USDT'] = make_tech('neutral')

        regime.update(bullish_techs)
        assert regime._effective_regime == REGIME_BULLISH
        regime.update(mixed_techs)
        assert regime._effective_regime == REGIME_BULLISH
        regime.update(bullish_techs)
        assert regime._effective_regime == REGIME_BULLISH
        regime.update(mixed_techs)
        assert regime._effective_regime == REGIME_BULLISH

    def test_two_consecutive_switches(self, regime):
        """AC-REG-02: Two consecutive same-regime readings with confidence >= 65 → switch."""
        # Use bearish regime (can achieve confidence >= 65) to test the switch logic
        # 8/10 bearish + BTC bearish → bearish_pct=0.8, anchor_bearish=True → confidence ~82
        # bullish→bearish requires 3 confirmations, so use mixed→bearish scenario
        regime._effective_regime = REGIME_MIXED
        regime._last_changed_at = time.time() - 3600

        bearish_techs = {f'SYM{i}-USDT': make_tech('bearish') for i in range(8)}
        bearish_techs['BTC-USDT'] = make_tech('bearish', higher_tf_bias='bearish')
        bearish_techs['ETH-USDT'] = make_tech('bearish', higher_tf_bias='bearish')

        regime.update(bearish_techs)
        assert regime._effective_regime == REGIME_MIXED  # first time, need 2
        regime.update(bearish_techs)
        # After 2 consecutive with confidence >= 65, should switch
        assert regime._effective_regime == REGIME_BEARISH

    def test_two_mixed_switches_from_bullish(self, regime):
        """AC-REG-02: bullish → two consecutive mixed → switches to mixed."""
        # mixed requires: high_vol + neutral < 40%, so use high ATR with
        # a mix of bullish/bearish (not neutral) to avoid choppy branch
        mixed_techs = {f'SYM{i}-USDT': make_tech('bullish', atr_pct=0.05) for i in range(3)}
        mixed_techs.update({f'BEAR{i}-USDT': make_tech('bearish', atr_pct=0.05) for i in range(4)})
        mixed_techs['BTC-USDT'] = make_tech('neutral', atr_pct=0.05)

        regime.update(mixed_techs)
        assert regime._effective_regime == REGIME_BULLISH  # first, need 2
        regime.update(mixed_techs)
        assert regime._effective_regime == REGIME_MIXED  # confirmed, switch

    def test_two_choppy_switches_from_bullish(self, regime):
        """AC-REG-02: bullish → two consecutive choppy → switches to choppy."""
        from utils.market_regime import REGIME_CHOPPY
        # choppy: low vol + neutral >= 50%
        choppy_techs = {f'SYM{i}-USDT': make_tech('neutral', atr_pct=0.01) for i in range(6)}
        choppy_techs['BTC-USDT'] = make_tech('neutral', atr_pct=0.01)
        choppy_techs['ETH-USDT'] = make_tech('neutral', atr_pct=0.01)

        regime.update(choppy_techs)
        assert regime._effective_regime == REGIME_BULLISH  # first, need 2
        regime.update(choppy_techs)
        assert regime._effective_regime == REGIME_CHOPPY  # confirmed, switch

    def test_min_hold_period(self, regime):
        """AC-REG-03: Within min_hold, no switch."""
        regime._last_changed_at = time.time()  # just changed
        mixed_techs = {f'SYM{i}-USDT': make_tech('neutral') for i in range(10)}
        mixed_techs['BTC-USDT'] = make_tech('neutral')

        regime.update(mixed_techs)
        regime.update(mixed_techs)
        # Should NOT switch because within min_hold
        assert regime._effective_regime == REGIME_BULLISH


class TestRegimeComputation:
    def test_bullish_regime(self, regime):
        regime._effective_regime = REGIME_MIXED
        regime._last_changed_at = time.time() - 3600
        techs = {f'SYM{i}-USDT': make_tech('bullish') for i in range(8)}
        techs['BTC-USDT'] = make_tech('bullish')
        techs['ETH-USDT'] = make_tech('bullish')

        regime.update(techs)
        regime.update(techs)
        assert regime._effective_regime == REGIME_BULLISH

    def test_snapshot_fields(self, regime):
        snap = regime.snapshot()
        assert 'effective_regime' in snap
        assert 'raw_regime' in snap
        assert 'confidence' in snap
        assert 'min_hold_remaining_sec' in snap
        assert 'basis' in snap


class TestShortGuard:
    def test_short_blocked_in_bullish(self, regime):
        """AC-SHORT-01: Normal short blocked."""
        allowed, reason = regime.is_short_allowed(
            score=-45, htf_bearish_votes=1, effective_rr=1.4,
            confirm_15m_short=True, daily_bias='bullish'
        )
        assert not allowed
        assert reason == 'short_regime_guard'

    def test_strong_short_passes(self, regime):
        """AC-SHORT-02: Strong short passes."""
        allowed, reason = regime.is_short_allowed(
            score=-75, htf_bearish_votes=2, effective_rr=1.9,
            confirm_15m_short=True, daily_bias='neutral'
        )
        assert allowed
        assert reason == 'short_bullish_strong'

    def test_short_allowed_in_non_bullish(self, regime):
        regime._effective_regime = REGIME_MIXED
        allowed, _ = regime.is_short_allowed(
            score=-30, htf_bearish_votes=0, effective_rr=1.2,
            confirm_15m_short=False, daily_bias='bullish'
        )
        assert allowed


class TestProbeShort:
    def test_probe_eligible_btc_rsi_reversal(self, regime):
        """AC-SHORT-03: BTC 4h RSI reversal triggers probe eligibility."""
        btc_tech = make_tech('bullish', tf_4h_rsi=68, volume_ratio=2.0)
        techs = {f'SYM{i}-USDT': make_tech('bullish') for i in range(5)}
        assert regime.is_probe_short_eligible(btc_tech, techs)

    def test_probe_not_eligible_normal_conditions(self, regime):
        btc_tech = make_tech('bullish', tf_4h_rsi=55, volume_ratio=1.0)
        techs = {f'SYM{i}-USDT': make_tech('bullish') for i in range(5)}
        assert not regime.is_probe_short_eligible(btc_tech, techs)

    def test_probe_not_eligible_non_bullish(self, regime):
        regime._effective_regime = REGIME_MIXED
        btc_tech = make_tech('neutral', tf_4h_rsi=68, volume_ratio=2.0)
        techs = {}
        assert not regime.is_probe_short_eligible(btc_tech, techs)
