import time

import pandas as pd

from agents.trading.position_analyst import PositionAnalyst
from agents.trading.reviewer import ReviewerAgent
from event_backtest import EventBacktest


def test_reviewer_segmented_metrics_include_side_regime_slot_and_gross_fields():
    reviewer = ReviewerAgent.__new__(ReviewerAgent)
    reviewer.rolling_window_size = 20
    reviewer.trade_history = [
        {
            'pnl': 2.0, 'side': 'long', 'entry_regime': 'bullish',
            'slot_type': 'low_rr_extra',
        },
        {
            'pnl': -1.0, 'side': 'long', 'entry_regime': 'bullish',
            'slot_type': 'low_rr_extra',
        },
        {
            'pnl': 3.0, 'side': 'short', 'entry_regime': 'bearish',
            'slot_type': 'main',
        },
    ]

    metrics = reviewer._calculate_segmented_metrics()

    long_metrics = metrics['metrics_by_side']['long']
    assert long_metrics['trade_count'] == 2
    assert long_metrics['gross_profit'] == 2.0
    assert long_metrics['gross_loss'] == 1.0
    assert metrics['metrics_by_slot_type']['low_rr_extra']['trade_count'] == 2
    assert metrics['metrics_by_side_regime']['long_bullish']['trade_count'] == 2
    assert metrics['metrics_by_side_regime']['long_bullish']['insufficient_sample'] is True


def test_position_analyst_regime_grace_downgrades_fresh_low_rr_reduce_to_hold():
    analyst = PositionAnalyst.__new__(PositionAnalyst)
    analyst.config = {'max_trade_amount': 30}
    analyst._get_current_regime_snapshot = lambda: {
        'effective_regime': 'mixed',
        'last_changed_at': time.time() - 120,
    }

    pos = {
        'side': 'long',
        'entry_price': 100,
        'stop_loss': 95,
        'open_time': time.time() - 1200,
        'is_low_rr': True,
        'entry_regime': 'bullish',
    }
    verdict = {
        'symbol': 'TEST-USDT',
        'action': 'reduce',
        'conviction': 55,
        'context': {
            'pnl_pct': -2.0,
            'hours_held': 0.3,
            'higher_trend': 'bearish',
        },
        'factors': {'momentum_shift': 0, 'rr_bonus': 0},
        'reasoning': 'score=-40, 入场逻辑失效',
    }

    override = analyst._check_hard_override('TEST-USDT', pos, verdict)

    assert override is None
    assert verdict['action'] == 'hold'
    assert verdict['context']['current_regime'] == 'mixed'
    assert verdict['context']['regime_grace_active'] is True


def test_event_backtest_probe_short_uses_probe_rr_floor():
    eb = EventBacktest(
        initial_capital=1000,
        max_margin=30,
        rr_floor_short_bullish=1.80,
        probe_short_enabled=True,
    )
    row = pd.Series({
        'close': 100.0,
        'atr': 1.0,
        'funding_rate': 0.0,
    })

    plan = eb._build_plan_with_regime(
        row, 'short', equity=1000, regime='bullish',
        entry_result={'direction': 'short', 'score': -55, 'is_probe': True}
    )

    assert plan is not None
    assert plan['rr_floor_used'] == 1.30
    assert plan['slot_type'] == 'probe_short'
    assert plan['leverage'] <= 3
    assert plan['margin'] <= 30 * 0.3
