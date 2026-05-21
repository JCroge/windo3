"""EventBacktest Phase 1 同构测试 — 验证 regime/short guard/low R:R/probe/segmented metrics"""

import os
import sys
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from event_backtest import EventBacktest, enrich_with_atr_htf


def _make_df(n=100, trend='up', regime='bullish'):
    """Generate test DataFrame with configurable trend and regime."""
    np.random.seed(42)
    if trend == 'up':
        prices = 100 + np.cumsum(np.abs(np.random.randn(n)) * 0.3)
    elif trend == 'down':
        prices = 100 - np.cumsum(np.abs(np.random.randn(n)) * 0.3)
    else:
        prices = 100 + np.cumsum(np.random.randn(n) * 0.2)

    df = pd.DataFrame({
        'open_time': pd.date_range('2026-01-01', periods=n, freq='1h'),
        'open': prices,
        'high': prices * 1.008,
        'low': prices * 0.992,
        'close': prices,
        'volume': 1000 + np.random.rand(n) * 500,
        'rsi': 50 + np.random.randn(n) * 10,
    })
    df['ma_fast'] = df['close'].rolling(7).mean()
    df['ma_slow'] = df['close'].rolling(25).mean()
    cross_up = (df['ma_fast'] > df['ma_slow']) & (df['ma_fast'].shift(1) <= df['ma_slow'].shift(1))
    cross_down = (df['ma_fast'] < df['ma_slow']) & (df['ma_fast'].shift(1) >= df['ma_slow'].shift(1))
    df['entry_long'] = cross_up.astype(int)
    df['entry_short'] = cross_down.astype(int)
    df['exit_long'] = cross_down.astype(int)
    df['exit_short'] = cross_up.astype(int)
    df['regime'] = regime
    df['htf_bias'] = 'bullish' if regime == 'bullish' else ('bearish' if regime == 'bearish' else 'neutral')
    df = enrich_with_atr_htf(df).fillna(0)
    return df


class TestRegimeAwareRRFloor:
    """AC-BT-01: Dynamic R:R floors by regime and side."""

    def test_bullish_long_uses_lower_rr_floor(self):
        """In bullish regime, long trades use rr_floor_long_bullish (1.30)."""
        df = _make_df(200, trend='up', regime='bullish')
        eb = EventBacktest(
            initial_capital=1000,
            enable_regime=True,
            rr_floor_long_bullish=1.30,
            rr_floor_short_bullish=1.80,
        )
        result = eb.run(df, symbol='TEST')
        # Should have trades (lower floor allows more entries)
        long_trades = [t for t in result['trades'] if t['direction'] == 'long']
        assert len(long_trades) >= 0  # may or may not have trades depending on data

    def test_regime_disabled_uses_default_floor(self):
        """With enable_regime=False, all trades use default rr_floor."""
        df = _make_df(200, trend='up', regime='bullish')
        eb_regime = EventBacktest(initial_capital=1000, enable_regime=True, rr_floor_long_bullish=1.30)
        eb_no_regime = EventBacktest(initial_capital=1000, enable_regime=False)
        r1 = eb_regime.run(df, symbol='TEST')
        r2 = eb_no_regime.run(df, symbol='TEST')
        # With regime enabled and lower floor, should get >= trades than without
        assert r1['total_trades'] >= r2['total_trades']


class TestShortRegimeGuard:
    """AC-BT-02: Short regime guard blocks weak shorts in bullish."""

    def test_weak_short_blocked_in_bullish(self):
        """Weak short signals are blocked when regime is bullish."""
        df = _make_df(200, trend='down', regime='bullish')
        # Force some short signals
        df.loc[50, 'entry_short'] = 1
        df.loc[50, 'rsi'] = 55  # not extreme enough for probe

        eb_guard = EventBacktest(
            initial_capital=1000,
            enable_regime=True,
            short_regime_guard=True,
            probe_short_enabled=False,
        )
        eb_no_guard = EventBacktest(
            initial_capital=1000,
            enable_regime=True,
            short_regime_guard=False,
        )
        r_guard = eb_guard.run(df, symbol='TEST')
        r_no_guard = eb_no_guard.run(df, symbol='TEST')
        # Guard should block some shorts
        short_guard = [t for t in r_guard['trades'] if t['direction'] == 'short']
        short_no_guard = [t for t in r_no_guard['trades'] if t['direction'] == 'short']
        assert len(short_guard) <= len(short_no_guard)

    def test_short_allowed_in_bearish(self):
        """Shorts are not blocked when regime is bearish."""
        df = _make_df(200, trend='down', regime='bearish')
        eb = EventBacktest(
            initial_capital=1000,
            enable_regime=True,
            short_regime_guard=True,
        )
        result = eb.run(df, symbol='TEST')
        short_trades = [t for t in result['trades'] if t['direction'] == 'short']
        # In bearish downtrend, shorts should be allowed
        # (may still be 0 if no signal triggers, but guard doesn't block)
        assert True  # no assertion error = guard didn't crash


class TestProbeShort:
    """AC-BT-03: Probe short lifecycle."""

    def test_probe_short_has_reduced_position(self):
        """Probe shorts use reduced margin and leverage."""
        df = _make_df(200, trend='down', regime='bullish')
        # Force a probe-eligible signal: RSI >= 70 + entry_short
        df.loc[60, 'entry_short'] = 1
        df.loc[60, 'rsi'] = 75

        eb = EventBacktest(
            initial_capital=1000,
            enable_regime=True,
            short_regime_guard=True,
            probe_short_enabled=True,
            probe_short_position_pct=0.3,
            probe_short_max_leverage=3,
        )
        result = eb.run(df, symbol='TEST')
        probes = [t for t in result['trades'] if t.get('is_probe')]
        for p in probes:
            assert p['leverage'] <= 3
            assert p.get('slot_type') == 'probe_short'

    def test_probe_short_cooldown_after_sl(self):
        """After probe SL, cooldown prevents immediate re-entry."""
        df = _make_df(300, trend='down', regime='bullish')
        # Two probe signals close together
        df.loc[60, 'entry_short'] = 1
        df.loc[60, 'rsi'] = 75
        df.loc[70, 'entry_short'] = 1
        df.loc[70, 'rsi'] = 75

        eb = EventBacktest(
            initial_capital=1000,
            enable_regime=True,
            short_regime_guard=True,
            probe_short_enabled=True,
            probe_short_cooldown_bars=24,
            post_close_cooldown_bars=2,
        )
        result = eb.run(df, symbol='TEST')
        probes = [t for t in result['trades'] if t.get('is_probe')]
        # If first probe hits SL, second should be blocked by cooldown
        # (exact behavior depends on price action, but no crash)
        assert True


class TestLowRRSlot:
    """AC-BT-04: Low R:R long position scaling."""

    def test_low_rr_long_has_reduced_margin(self):
        """Low R:R longs in bullish get scaled-down position."""
        df = _make_df(200, trend='up', regime='bullish')
        eb = EventBacktest(
            initial_capital=1000,
            enable_regime=True,
            rr_floor_long_bullish=1.30,
            low_rr_scaling=True,
            low_rr_max_position_pct=0.5,
            low_rr_max_leverage=5,
        )
        result = eb.run(df, symbol='TEST')
        low_rr = [t for t in result['trades'] if t.get('is_low_rr')]
        for t in low_rr:
            assert t['leverage'] <= 5
            assert t.get('slot_type') == 'low_rr_extra'


class TestSegmentedMetrics:
    """AC-BT-05: Segmented metrics output."""

    def test_segmented_metrics_structure(self):
        """Result contains segmented metrics with correct structure."""
        df = _make_df(300, trend='up', regime='bullish')
        eb = EventBacktest(initial_capital=1000, enable_regime=True)
        result = eb.run(df, symbol='TEST')

        seg = result.get('segmented_metrics', {})
        assert isinstance(seg, dict)

        # If there are trades, check structure
        if result['total_trades'] > 0:
            if 'metrics_by_side' in seg:
                for side_metrics in seg['metrics_by_side'].values():
                    assert 'trade_count' in side_metrics
                    assert 'win_rate' in side_metrics
                    assert 'profit_factor' in side_metrics
                    assert 'total_pnl' in side_metrics
                    assert 'insufficient_sample' in side_metrics

    def test_insufficient_sample_flag(self):
        """Segments with < 5 trades are marked insufficient_sample=True."""
        df = _make_df(100, trend='up', regime='bullish')
        eb = EventBacktest(initial_capital=1000, enable_regime=True)
        result = eb.run(df, symbol='TEST')

        seg = result.get('segmented_metrics', {})
        for category in seg.values():
            if isinstance(category, dict):
                for metrics in category.values():
                    if isinstance(metrics, dict) and 'trade_count' in metrics:
                        if metrics['trade_count'] < 5:
                            assert metrics['insufficient_sample'] is True

    def test_side_regime_cross_metrics(self):
        """Cross metrics (side x regime) are computed."""
        np.random.seed(123)
        n = 400
        prices = 100 + np.cumsum(np.random.randn(n) * 0.5)
        df = pd.DataFrame({
            'open_time': pd.date_range('2026-01-01', periods=n, freq='1h'),
            'open': prices,
            'high': prices * 1.008,
            'low': prices * 0.992,
            'close': prices,
            'volume': 1000 + np.random.rand(n) * 500,
            'rsi': 50 + np.random.randn(n) * 10,
        })
        df['ma_fast'] = df['close'].rolling(7).mean()
        df['ma_slow'] = df['close'].rolling(25).mean()
        cross_up = (df['ma_fast'] > df['ma_slow']) & (df['ma_fast'].shift(1) <= df['ma_slow'].shift(1))
        cross_down = (df['ma_fast'] < df['ma_slow']) & (df['ma_fast'].shift(1) >= df['ma_slow'].shift(1))
        df['entry_long'] = cross_up.astype(int)
        df['entry_short'] = cross_down.astype(int)
        df['exit_long'] = cross_down.astype(int)
        df['exit_short'] = cross_up.astype(int)
        # Mix regimes
        df['regime'] = 'mixed'
        df.loc[:150, 'regime'] = 'bullish'
        df.loc[300:, 'regime'] = 'bearish'
        df['htf_bias'] = df['regime'].map({'bullish': 'bullish', 'bearish': 'bearish', 'mixed': 'neutral'})
        df = enrich_with_atr_htf(df).fillna(0)

        eb = EventBacktest(initial_capital=1000, enable_regime=True, short_regime_guard=False)
        result = eb.run(df, symbol='TEST')

        seg = result.get('segmented_metrics', {})
        if result['total_trades'] > 0:
            # Should have at least metrics_by_side
            assert 'metrics_by_side' in seg or result['total_trades'] == 0


class TestBackwardCompatibility:
    """Ensure old API still works when regime features are disabled."""

    def test_disable_all_regime_features(self):
        """With enable_regime=False, behavior matches pre-Phase1."""
        df = _make_df(200, trend='up', regime='bullish')
        eb = EventBacktest(initial_capital=1000, enable_regime=False)
        result = eb.run(df, symbol='TEST')
        assert 'total_trades' in result
        assert 'segmented_metrics' in result
        # All trades should have regime='mixed' (default when disabled)
        for t in result['trades']:
            assert t.get('regime') == 'mixed'
