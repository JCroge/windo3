"""AC2-02: probe_long final gate in dispatcher blocks when slot full."""
import pytest
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock, patch
from agents.trading.judge import MultiJudge


def _make_judge():
    judge = MultiJudge.__new__(MultiJudge)
    judge._bucketed_ev_enabled = True
    judge._confidence_split_enabled = False
    judge._trend_saturation_enabled = False
    judge._momentum_probe_long_enabled = True
    judge._request_id_enabled = True
    judge._ev_min_threshold = 0.05
    judge._ev_strong_signal_threshold = 70
    judge._fallback_win_rate = 0.52
    judge._recent_win_rate = None
    judge._recent_profit_factor = None
    judge._total_completed_trades = 0
    judge._recent_wins = 0
    judge._ev_prior_wins = 2
    judge._ev_prior_total = 5
    judge._bucketed_metrics = {}
    judge._max_concurrent_positions = 3
    judge._probe_short_max_concurrent = 1
    judge._probe_long_max_concurrent = 1
    judge._open_positions = set()
    judge._pending_open_symbols = set()
    judge._pending_open_ts = {}
    judge._pending_open_slots = {}
    judge._position_slots = {}
    judge._stale_pending_timeout = 120
    judge._pending_ttl = 120
    judge.logger = MagicMock()
    judge._regime_manager = MagicMock()
    judge._regime_manager._effective_regime = 'bullish'
    judge._regime_manager._raw_regime = 'bullish'
    judge._regime_manager._confidence = 0.8
    judge._regime_manager.snapshot = MagicMock(return_value={
        'effective_regime': 'bullish', 'raw_regime': 'bullish', 'confidence': 0.8
    })
    judge._candidate_ranker = MagicMock()
    judge._candidate_ranker.low_rr_extra_slot = 1
    judge.publish = AsyncMock()
    judge._low_rr_slot_enabled = True
    judge._rr_floor_long_bullish = 1.5
    judge._counterfactual_ledger = MagicMock()
    judge._counterfactual_ledger._enabled = False
    return judge


class TestProbeLongDispatcherGate:
    def test_probe_long_gate_blocks_when_active(self):
        """Active probe_long position blocks new probe_long in dispatcher."""
        judge = _make_judge()
        judge._open_positions = {'SOL-USDT'}
        judge._position_slots = {'SOL-USDT': 'probe_long'}

        decision = {
            'symbol': 'BTC-USDT', 'action': 'open_long', 'confidence': 70,
            'plan': {'slot_type': 'probe_long', 'is_probe': True},
        }
        result = asyncio.get_event_loop().run_until_complete(
            judge._gate_and_publish_open('BTC-USDT', decision, {})
        )
        assert result is False
        assert 'BTC-USDT' not in judge._pending_open_symbols
        assert 'BTC-USDT' not in judge._pending_open_slots

    def test_probe_long_gate_blocks_when_pending(self):
        """Pending probe_long blocks new probe_long in dispatcher."""
        judge = _make_judge()
        judge._pending_open_symbols = {'ETH-USDT'}
        judge._pending_open_slots = {'ETH-USDT': 'probe_long'}
        judge._pending_open_ts = {'ETH-USDT': time.time()}

        decision = {
            'symbol': 'BTC-USDT', 'action': 'open_long', 'confidence': 70,
            'plan': {'slot_type': 'probe_long', 'is_probe': True},
        }
        result = asyncio.get_event_loop().run_until_complete(
            judge._gate_and_publish_open('BTC-USDT', decision, {})
        )
        assert result is False

    def test_main_slot_full_does_not_block_probe_long(self):
        """Main slot full should NOT block probe_long."""
        judge = _make_judge()
        judge._open_positions = {'BTC-USDT', 'ETH-USDT', 'SOL-USDT'}
        judge._position_slots = {'BTC-USDT': 'main', 'ETH-USDT': 'main', 'SOL-USDT': 'main'}

        decision = {
            'symbol': 'DOGE-USDT', 'action': 'open_long', 'confidence': 70,
            'plan': {'slot_type': 'probe_long', 'is_probe': True},
        }
        result = asyncio.get_event_loop().run_until_complete(
            judge._gate_and_publish_open('DOGE-USDT', decision, {})
        )
        assert result is True
        assert 'DOGE-USDT' in judge._pending_open_symbols

    def test_probe_long_gate_publishes_hold_with_attribution(self):
        """Blocked probe_long publishes hold with proper attribution."""
        judge = _make_judge()
        judge._open_positions = {'SOL-USDT'}
        judge._position_slots = {'SOL-USDT': 'probe_long'}

        decision = {
            'symbol': 'BTC-USDT', 'action': 'open_long', 'confidence': 70,
            'plan': {'slot_type': 'probe_long', 'is_probe': True},
        }
        asyncio.get_event_loop().run_until_complete(
            judge._gate_and_publish_open('BTC-USDT', decision, {})
        )
        call_args = judge.publish.call_args
        payload = call_args[0][1]
        assert payload['action'] == 'hold'
        assert 'probe_long_full' in payload['key_factors'][0]
        attr = payload.get('attribution', {})
        assert attr.get('blocked_by') == 'probe_long_slot_full'
        assert attr.get('slot_type') == 'probe_long'
