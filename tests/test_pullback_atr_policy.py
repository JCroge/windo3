"""Pullback ATR policy: 0.5% pullback limit + 2.0×ATR SL + 2R TP, no fallback.

Backtest backing: 21 真实 closed live samples + OKX 1m OHLCV →
  baseline 实际 PnL = -19.86U
  policy 应用后 net = +23.03U (fills=19/21, win=8/19)
"""
import pytest
from unittest.mock import MagicMock

from agents.trading.judge import MultiJudge as Judge


def _make_judge():
    j = Judge.__new__(Judge)
    j.logger = MagicMock()
    # rr floor knobs
    j._rr_floor_default = 1.50
    j._rr_floor_long_bullish = 1.30
    j._rr_floor_long_aligned_choppy = 1.30
    j._rr_floor_short_bullish = 1.80
    j._probe_rr_floor = 1.30
    j._low_rr_slot_enabled = True
    j._low_rr_long_aligned_enabled = True
    j._short_regime_guard_enabled = True
    j._min_deferred_signal_score = 45
    # regime manager
    rm = MagicMock()
    rm.snapshot.return_value = {'effective_regime': 'mixed'}
    rm._effective_regime = 'mixed'
    rm._raw_regime = 'mixed'
    rm._confidence = 50
    j._regime_manager = rm
    return j


def _base_plan(side='long', atr_pct=0.005, entry=100.0, sl_pct_orig=0.025, tp_pct_orig=0.05):
    if side == 'long':
        sl = entry * (1 - sl_pct_orig)
        tp = [entry * (1 + tp_pct_orig), entry * (1 + tp_pct_orig * 1.6), entry * (1 + tp_pct_orig * 2.2)]
    else:
        sl = entry * (1 + sl_pct_orig)
        tp = [entry * (1 - tp_pct_orig), entry * (1 - tp_pct_orig * 1.6), entry * (1 - tp_pct_orig * 2.2)]
    return {
        'side': side,
        'entry_ref': entry,
        'sl_pct': sl_pct_orig,
        'tp_pct': [tp_pct_orig, tp_pct_orig * 1.6, tp_pct_orig * 2.2],
        'entry_zone': [entry, entry],
        'stop_loss': sl,
        'take_profit': tp,
        'leverage': 5,
        'size_usdt': 30.0,
        'order_type': 'market',
        'risk_reward_ratio': 2.0,
        'effective_risk_reward_ratio': 1.8,
        'atr_pct': atr_pct,
    }


def _tech():
    return {'momentum': {'atr_pct': 0.005}, 'trend': {'direction': 'bullish'}}


def test_ma_aligned_long_rewrites_to_pullback_atr_policy():
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.005, entry=100.0)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'ma_aligned')

    assert out['order_type'] == 'limit'
    assert out['limit_no_fallback'] is True
    assert out['limit_timeout_sec'] == 1800
    assert out['pullback_policy'] == 'pullback_atr_v1'
    assert out['atr_sl_multiplier'] == 2.0
    assert out['atr_tp_r'] == 2.0

    # 0.5% 回调入场
    expected_target = 100.0 * (1 - 0.005)
    assert abs(out['pullback_target'] - expected_target) < 0.01

    # SL = target × (1 − 2×ATR)，sl_pct ≈ 0.01
    assert out['sl_pct'] == pytest.approx(0.01, rel=0.05)
    # TP1 ≈ 2R = 0.02
    assert out['tp_pct'][0] == pytest.approx(0.02, rel=0.05)
    # TP 三档单调递增
    assert out['take_profit'][0] < out['take_profit'][1] < out['take_profit'][2]
    # entry_zone 是窄区间
    low, high = out['entry_zone']
    assert high > low
    assert (high - low) / out['pullback_target'] < 0.01


def test_momentum_probe_long_also_applies():
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.004, entry=50.0)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'momentum_probe_long')
    assert out['order_type'] == 'limit'
    assert out['limit_no_fallback'] is True
    assert out['pullback_policy'] == 'pullback_atr_v1'


def test_short_side_rewrites_symmetrically():
    j = _make_judge()
    plan = _base_plan(side='short', atr_pct=0.005, entry=100.0)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'ma_aligned')

    expected_target = 100.0 * (1 + 0.005)
    assert abs(out['pullback_target'] - expected_target) < 0.01
    # short：SL > target，TP < target
    assert out['stop_loss'] > out['pullback_target']
    assert out['take_profit'][0] < out['pullback_target']


def test_deferred_15m_skipped_unchanged():
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.005, entry=100.0)
    plan_before = dict(plan)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'deferred_15m_confirmation')
    assert out == plan_before
    assert 'pullback_policy' not in out


def test_deferred_pullback_skipped_unchanged():
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.005, entry=100.0)
    plan_before = dict(plan)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'deferred_pullback')
    assert out == plan_before


def test_rule_signal_skipped_unchanged():
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.005, entry=100.0)
    plan_before = dict(plan)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'rule_signal')
    assert out == plan_before


def test_llm_driven_skipped_unchanged():
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.005, entry=100.0)
    plan_before = dict(plan)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'llm_driven')
    assert out == plan_before


def test_zero_atr_returns_original_plan():
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.0, entry=100.0)
    plan_before = dict(plan)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'ma_aligned')
    assert out == plan_before


def test_missing_entry_ref_returns_original_plan():
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.005, entry=100.0)
    plan['entry_ref'] = 0
    plan_before = dict(plan)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'ma_aligned')
    assert out == plan_before


def test_extreme_atr_skips_when_sl_too_wide():
    """ATR 极大（>25% × 2 = 50%）时不应改写，避免破坏单。"""
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.30, entry=100.0)
    plan_before = dict(plan)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'ma_aligned')
    assert out == plan_before


def test_attribution_fields_present_for_observability():
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.005, entry=100.0)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'ma_aligned')
    assert out.get('pullback_policy') == 'pullback_atr_v1'
    assert out.get('pullback_target') > 0
    assert out.get('atr_sl_multiplier') == 2.0
    assert out.get('atr_tp_r') == 2.0


def test_sl_pct_and_tp_pct_recomputed_for_drift_gate():
    """entry drift Gate 1 需要 sl_pct/tp_pct，policy 改写后必须同步更新。"""
    j = _make_judge()
    plan = _base_plan(side='long', atr_pct=0.005, entry=100.0)
    out = j._apply_pullback_atr_policy(plan, _tech(), 'ma_aligned')
    target = out['pullback_target']
    assert out['sl_pct'] == pytest.approx(abs(out['stop_loss'] - target) / target, rel=1e-3)
    for i, tp in enumerate(out['take_profit']):
        assert out['tp_pct'][i] == pytest.approx(abs(tp - target) / target, rel=1e-3)
