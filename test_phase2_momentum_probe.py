"""Phase 2 AC-PH2-02: Momentum Probe Long Tests"""
import pytest
from unittest.mock import MagicMock
from agents.trading.judge import MultiJudge


def _make_judge(probe_enabled=True):
    judge = MultiJudge.__new__(MultiJudge)
    judge._momentum_probe_long_enabled = probe_enabled
    judge._probe_long_max_concurrent = 1
    judge._probe_long_max_position_pct = 0.3
    judge._probe_long_max_leverage = 3
    judge._probe_long_rsi_min = 70
    judge._probe_long_rsi_max = 85
    judge._max_trade_amount = 10
    judge._open_positions = set()
    judge._pending_open_symbols = set()
    judge._position_slots = {}
    judge._pending_open_slots = {}
    judge.logger = MagicMock()
    return judge


def _make_tech(rsi=75, direction='bullish', strength=80, htf='bullish', div=None):
    return {
        'momentum': {'rsi': rsi, 'rsi_divergence': div},
        'trend': {'direction': direction, 'strength': strength, 'higher_tf_bias': htf},
        'risk': {'liquidity_score': 50},
    }


class TestMomentumProbeLong:
    def test_eligible_rsi_75_strong_trend(self):
        judge = _make_judge()
        tech = _make_tech(rsi=75, strength=85, htf='bullish')
        ok, reason = judge._can_route_probe_long('BTC-USDT', tech, 50)
        assert ok is True
        assert reason == 'probe_long_eligible'

    def test_blocked_rsi_too_high(self):
        judge = _make_judge()
        tech = _make_tech(rsi=88)
        ok, reason = judge._can_route_probe_long('BTC-USDT', tech, 50)
        assert ok is False
        assert 'rsi_out_of_range' in reason

    def test_blocked_rsi_too_low(self):
        judge = _make_judge()
        tech = _make_tech(rsi=65)
        ok, reason = judge._can_route_probe_long('BTC-USDT', tech, 50)
        assert ok is False
        assert 'rsi_out_of_range' in reason

    def test_blocked_bearish_divergence(self):
        judge = _make_judge()
        tech = _make_tech(rsi=75, div='bearish_div')
        ok, reason = judge._can_route_probe_long('BTC-USDT', tech, 50)
        assert ok is False
        assert reason == 'bearish_divergence'

    def test_blocked_weak_trend(self):
        judge = _make_judge()
        tech = _make_tech(rsi=75, strength=60)
        ok, reason = judge._can_route_probe_long('BTC-USDT', tech, 50)
        assert ok is False
        assert reason == 'trend_not_strong_bullish'

    def test_blocked_htf_not_bullish(self):
        judge = _make_judge()
        tech = _make_tech(rsi=75, htf='neutral')
        ok, reason = judge._can_route_probe_long('BTC-USDT', tech, 50)
        assert ok is False
        assert reason == 'htf_not_bullish'

    def test_blocked_slot_full(self):
        judge = _make_judge()
        judge._open_positions = {'ETH-USDT'}
        judge._position_slots = {'ETH-USDT': 'probe_long'}
        tech = _make_tech(rsi=75)
        ok, reason = judge._can_route_probe_long('BTC-USDT', tech, 50)
        assert ok is False
        assert reason == 'probe_long_slot_full'

    def test_blocked_disabled(self):
        judge = _make_judge(probe_enabled=False)
        tech = _make_tech(rsi=75)
        ok, reason = judge._can_route_probe_long('BTC-USDT', tech, 50)
        assert ok is False
        assert reason == 'probe_long_disabled'

    def test_blocked_zero_liquidity(self):
        judge = _make_judge()
        tech = _make_tech(rsi=75)
        tech['risk']['liquidity_score'] = 0
        ok, reason = judge._can_route_probe_long('BTC-USDT', tech, 50)
        assert ok is False
        assert reason == 'liquidity_zero'

    def test_route_to_probe_long_mutates_plan(self):
        judge = _make_judge()
        plan = {'size_usdt': 10, 'leverage': 10}
        judge._route_to_probe_long(plan, 'BTC-USDT')
        assert plan['size_usdt'] == 3.0  # 10 * 0.3
        assert plan['leverage'] == 3  # capped
        assert plan['is_probe'] is True
        assert plan['slot_type'] == 'probe_long'
        assert plan['entry_type'] == 'momentum_probe_long'
