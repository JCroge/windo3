"""Verify Judge._build_plan emits entry_ref/sl_pct/tp_pct anchor fields."""
import pytest
from unittest.mock import MagicMock
from agents.trading.judge import MultiJudge as Judge


def _make_judge():
    j = Judge.__new__(Judge)
    j.logger = MagicMock()
    j._recent_win_rate = None
    j._recent_wins = 0
    j._total_completed_trades = 0
    j._min_trades_for_ev_gate = 30
    j._fallback_win_rate = 0.45
    j._ev_prior_wins = 2
    j._ev_prior_total = 5
    j._bucketed_ev_enabled = False
    # attributes required by _calc_risk_budget
    j._available_balance = 1000.0
    j._effective_balance_cap = None
    j._max_trade_amount = 10
    return j


def _tech(price, atr_pct=0.02):
    return {
        'levels': {'support': [price * 0.97], 'resistance': [price * 1.03]},
        'risk': {},
        'microstructure': {},
        'momentum': {'atr_pct': atr_pct},
        'trend': {'15m': 'bullish', '1h': 'bullish'},
    }


def test_build_plan_emits_entry_ref():
    j = _make_judge()
    plan = j._build_plan(_tech(100.0), 'open_long', 100.0, 70, 60)
    assert plan['entry_ref'] == pytest.approx(100.0, rel=1e-3)


def test_build_plan_emits_sl_pct():
    j = _make_judge()
    plan = j._build_plan(_tech(100.0), 'open_long', 100.0, 70, 60)
    expected = abs(plan['stop_loss'] - 100.0) / 100.0
    assert plan['sl_pct'] == pytest.approx(expected, rel=1e-4)


def test_build_plan_emits_tp_pct_list():
    j = _make_judge()
    plan = j._build_plan(_tech(100.0), 'open_long', 100.0, 70, 60)
    assert isinstance(plan['tp_pct'], list)
    assert len(plan['tp_pct']) == len(plan['take_profit'])
    for pct, tp in zip(plan['tp_pct'], plan['take_profit']):
        assert pct == pytest.approx(abs(tp - 100.0) / 100.0, rel=1e-4)


def test_build_plan_short_side_pcts_positive():
    """sl_pct and tp_pct should always be positive magnitudes."""
    j = _make_judge()
    plan = j._build_plan(_tech(100.0), 'open_short', 100.0, 70, 60)
    assert plan['sl_pct'] > 0
    assert all(p > 0 for p in plan['tp_pct'])
