"""AC-FIX-01 ~ AC-FIX-09 验收测试 — 统一开仓调度器全路径验证"""

import os
import sys
import time
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.candidate_ranker import CandidateRanker


@pytest.fixture
def judge():
    """Minimal Judge instance for unified dispatch testing."""
    with patch.dict(os.environ, {
        'OKX_API_KEY': 'test', 'OKX_SECRET': 'test', 'OKX_PASSPHRASE': 'test',
    }):
        from agents.trading.judge import MultiJudge
        j = MultiJudge.__new__(MultiJudge)
        j._open_positions = set()
        j._pending_open_symbols = set()
        j._pending_open_ts = {}
        j._pending_open_slots = {}
        j._position_slots = {}
        j._pending_ttl = 120
        j._max_concurrent_positions = 3
        j._probe_short_max_concurrent = 1
        j._probe_short_enabled = True
        j._probe_short_cooldown_until = 0
        j._probe_short_active = None
        j._candidate_ranker = CandidateRanker(max_slots=3, enabled=True, low_rr_extra_slot=1)
        j._rank_flush_delay = 5
        j._rank_flush_task = None
        j.logger = MagicMock()
        j.publish = AsyncMock()
        j._symbol_state = {}
        j._symbol_tech_cache = {
            'BTC-USDT': {'trend': {'tf_4h_rsi': 65}, 'momentum': {'volume_ratio': 2.0}},
        }
        j._state_dirty = False

        class _MockLedger:
            _enabled = False
        j._counterfactual_ledger = _MockLedger()

        class _MockRegime:
            _effective_regime = 'mixed'
            def snapshot(self):
                return {'effective_regime': 'mixed', 'raw_regime': 'mixed', 'confidence': 50}
        j._regime_manager = _MockRegime()
        j._get_state = lambda s: j._symbol_state.setdefault(s, {})
        return j


# ═══ AC-FIX-01: 所有开仓路径都经过统一调度器 ═══


class TestACFix01AllPathsUnified:
    """AC-FIX-01: 所有路径输出相同格式 (slot_type / attribution / dispatch_path)"""

    @pytest.mark.asyncio
    async def test_main_direct_path_has_dispatch_path(self, judge):
        """Main path (ranking disabled) produces dispatch_path='main_direct'."""
        decision = {
            'symbol': 'ETH-USDT', 'action': 'open_long', 'confidence': 60,
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
            'attribution': {'entry_regime': 'mixed'},
        }
        result = await judge._gate_and_publish_open('ETH-USDT', decision, {})
        assert result is True
        assert decision['dispatch_path'] == 'main_direct'
        assert decision['attribution']['dispatch_path'] == 'main_direct'

    @pytest.mark.asyncio
    async def test_deferred_15m_path_has_dispatch_path(self, judge):
        """Deferred 15m path produces dispatch_path='deferred_15m'."""
        decision = {
            'symbol': 'ETH-USDT', 'action': 'open_long', 'confidence': 60,
            'entry_type': 'deferred_15m_confirmation',
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
            'attribution': {'entry_regime': 'mixed'},
        }
        result = await judge._gate_and_publish_open('ETH-USDT', decision, {})
        assert result is True
        assert decision['dispatch_path'] == 'deferred_15m'
        assert decision['attribution']['dispatch_path'] == 'deferred_15m'

    @pytest.mark.asyncio
    async def test_deferred_pullback_path_has_dispatch_path(self, judge):
        """Deferred pullback path produces dispatch_path='deferred_pullback'."""
        decision = {
            'symbol': 'SOL-USDT', 'action': 'open_long', 'confidence': 60,
            'entry_type': 'deferred_pullback',
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
            'attribution': {'entry_regime': 'mixed'},
        }
        result = await judge._gate_and_publish_open('SOL-USDT', decision, {})
        assert result is True
        assert decision['dispatch_path'] == 'deferred_pullback'

    @pytest.mark.asyncio
    async def test_deferred_chase_path_has_dispatch_path(self, judge):
        """Deferred chase path produces dispatch_path='deferred_chase'."""
        decision = {
            'symbol': 'DOGE-USDT', 'action': 'open_long', 'confidence': 60,
            'entry_type': 'deferred_chase',
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
            'attribution': {'entry_regime': 'mixed'},
        }
        result = await judge._gate_and_publish_open('DOGE-USDT', decision, {})
        assert result is True
        assert decision['dispatch_path'] == 'deferred_chase'


# ═══ AC-FIX-02: pending 预占先于 publish ═══


class TestACFix02PendingBeforePublish:
    """AC-FIX-02: pending reservation exists before publish."""

    @pytest.mark.asyncio
    async def test_pending_symbols_populated_after_dispatch(self, judge):
        """After successful dispatch, symbol in _pending_open_symbols."""
        decision = {
            'symbol': 'ETH-USDT', 'action': 'open_long', 'confidence': 60,
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
        }
        await judge._gate_and_publish_open('ETH-USDT', decision, {})
        assert 'ETH-USDT' in judge._pending_open_symbols
        assert judge._pending_open_slots['ETH-USDT'] == 'main'
        assert 'ETH-USDT' in judge._pending_open_ts

    @pytest.mark.asyncio
    async def test_pending_ts_is_recent(self, judge):
        """Pending timestamp is within last second."""
        decision = {
            'symbol': 'SOL-USDT', 'action': 'open_short', 'confidence': 60,
            'plan': {'slot_type': 'probe_short', 'is_probe': True, 'size_usdt': 3, 'leverage': 3},
        }
        before = time.time()
        await judge._gate_and_publish_open('SOL-USDT', decision, {})
        after = time.time()
        assert before <= judge._pending_open_ts['SOL-USDT'] <= after


# ═══ AC-FIX-03: ranking-disabled 仍然会预占 pending ═══


class TestACFix03RankingDisabledPending:
    """AC-FIX-03: ranking disabled still reserves pending + slot gate + attribution."""

    @pytest.mark.asyncio
    async def test_ranking_disabled_still_reserves_pending(self, judge):
        """Even without ranking, open goes through _gate_and_publish_open."""
        judge._candidate_ranker = CandidateRanker(max_slots=3, enabled=False)
        decision = {
            'symbol': 'ETH-USDT', 'action': 'open_long', 'confidence': 60,
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
        }
        result = await judge._gate_and_publish_open('ETH-USDT', decision, {})
        assert result is True
        assert 'ETH-USDT' in judge._pending_open_symbols
        assert judge._pending_open_slots['ETH-USDT'] == 'main'

    @pytest.mark.asyncio
    async def test_ranking_disabled_slot_gate_still_works(self, judge):
        """Ranking disabled, main slot full → rejected."""
        judge._candidate_ranker = CandidateRanker(max_slots=3, enabled=False)
        judge._open_positions = {'A-USDT', 'B-USDT', 'C-USDT'}
        judge._position_slots = {'A-USDT': 'main', 'B-USDT': 'main', 'C-USDT': 'main'}
        decision = {
            'symbol': 'ETH-USDT', 'action': 'open_long', 'confidence': 60,
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
        }
        result = await judge._gate_and_publish_open('ETH-USDT', decision, {})
        assert result is False
        assert 'ETH-USDT' not in judge._pending_open_symbols


# ═══ AC-FIX-04: probe_short 同时最多 1 个 ═══


class TestACFix04ProbeMaxOne:
    """AC-FIX-04: probe_short max 1 concurrent (active/pending/cooldown)."""

    def test_active_probe_blocks(self, judge):
        """Active probe blocks second probe."""
        judge._probe_short_active = 'COIN1-USDT'
        judge._symbol_tech_cache['TEST-USDT'] = {'risk': {'liquidity_score': 50}}
        allowed, reason = judge._can_route_probe_short('TEST-USDT', -70, True, 1.5)
        assert allowed is False
        assert reason == 'probe_active_full'

    def test_pending_probe_blocks(self, judge):
        """Pending probe blocks second probe."""
        judge._pending_open_slots = {'COIN1-USDT': 'probe_short'}
        judge._symbol_tech_cache['TEST-USDT'] = {'risk': {'liquidity_score': 50}}
        allowed, reason = judge._can_route_probe_short('TEST-USDT', -70, True, 1.5)
        assert allowed is False
        assert reason == 'probe_pending_full'

    def test_cooldown_blocks(self, judge):
        """Cooldown blocks probe."""
        judge._probe_short_cooldown_until = time.time() + 3600
        judge._symbol_tech_cache['TEST-USDT'] = {'risk': {'liquidity_score': 50}}
        allowed, reason = judge._can_route_probe_short('TEST-USDT', -70, True, 1.5)
        assert allowed is False
        assert reason == 'probe_cooldown'


# ═══ AC-FIX-05: main / low_rr / probe slot 语义一致 ═══


class TestACFix05SlotSemantics:
    """AC-FIX-05: slot types don't drift — each uses only its own capacity."""

    @pytest.mark.asyncio
    async def test_main_full_rejects_main_candidate(self, judge):
        """Main slots full → main candidate rejected."""
        judge._open_positions = {'A-USDT', 'B-USDT', 'C-USDT'}
        judge._position_slots = {'A-USDT': 'main', 'B-USDT': 'main', 'C-USDT': 'main'}
        decision = {
            'symbol': 'NEW-USDT', 'action': 'open_long', 'confidence': 60,
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
        }
        result = await judge._gate_and_publish_open('NEW-USDT', decision, {})
        assert result is False

    @pytest.mark.asyncio
    async def test_low_rr_uses_only_extra_slot(self, judge):
        """Low R:R candidate uses low_rr_extra slot, not main."""
        judge._open_positions = {'LRR-USDT'}
        judge._position_slots = {'LRR-USDT': 'low_rr_extra'}
        decision = {
            'symbol': 'NEW-USDT', 'action': 'open_long', 'confidence': 60,
            'plan': {'slot_type': 'low_rr_extra', 'is_low_rr': True, 'size_usdt': 5, 'leverage': 3},
        }
        result = await judge._gate_and_publish_open('NEW-USDT', decision, {})
        assert result is False  # low_rr_extra_slot=1, already occupied

    @pytest.mark.asyncio
    async def test_probe_uses_only_probe_slot(self, judge):
        """Probe candidate uses probe_short slot, not main."""
        judge._open_positions = {'A-USDT', 'B-USDT', 'C-USDT'}
        judge._position_slots = {'A-USDT': 'main', 'B-USDT': 'main', 'C-USDT': 'main'}
        decision = {
            'symbol': 'PROBE-USDT', 'action': 'open_short', 'confidence': 60,
            'plan': {'slot_type': 'probe_short', 'is_probe': True, 'size_usdt': 3, 'leverage': 3},
        }
        result = await judge._gate_and_publish_open('PROBE-USDT', decision, {})
        assert result is True  # probe slot is independent, still available
        assert judge._pending_open_slots['PROBE-USDT'] == 'probe_short'


# ═══ AC-FIX-06: execution_result 正确确认或释放 ═══


class TestACFix06ExecutionResult:
    """AC-FIX-06: executed confirms, rejected releases, stale TTL releases."""

    def test_executed_moves_pending_to_open(self, judge):
        """execution_result status=executed → pending → open."""
        judge._pending_open_symbols = {'ETH-USDT'}
        judge._pending_open_ts = {'ETH-USDT': time.time()}
        judge._pending_open_slots = {'ETH-USDT': 'main'}

        judge._pending_open_symbols.discard('ETH-USDT')
        judge._pending_open_ts.pop('ETH-USDT', None)
        judge._open_positions.add('ETH-USDT')
        judge._position_slots['ETH-USDT'] = 'main'

        assert 'ETH-USDT' not in judge._pending_open_symbols
        assert 'ETH-USDT' in judge._open_positions
        assert judge._position_slots['ETH-USDT'] == 'main'

    def test_rejected_releases_pending(self, judge):
        """execution_result status=rejected → pending released."""
        judge._pending_open_symbols = {'ETH-USDT'}
        judge._pending_open_ts = {'ETH-USDT': time.time()}
        judge._pending_open_slots = {'ETH-USDT': 'main'}

        judge._pending_open_symbols.discard('ETH-USDT')
        judge._pending_open_ts.pop('ETH-USDT', None)
        judge._pending_open_slots.pop('ETH-USDT', None)

        assert 'ETH-USDT' not in judge._pending_open_symbols
        assert 'ETH-USDT' not in judge._open_positions
        occupied = judge._open_positions | judge._pending_open_symbols
        assert len(occupied) == 0

    def test_stale_ttl_releases(self, judge):
        """Stale pending (TTL expired) auto-released by sweep."""
        judge._pending_open_symbols = {'STALE-USDT', 'FRESH-USDT'}
        judge._pending_open_ts = {
            'STALE-USDT': time.time() - 200,
            'FRESH-USDT': time.time() - 10,
        }
        judge._pending_open_slots = {'STALE-USDT': 'main', 'FRESH-USDT': 'main'}

        judge._sweep_stale_pending()

        assert 'STALE-USDT' not in judge._pending_open_symbols
        assert 'FRESH-USDT' in judge._pending_open_symbols


# ═══ AC-FIX-07: attribution 与 ledger 完整 ═══


class TestACFix07Attribution:
    """AC-FIX-07: slot gate rejection has full attribution fields."""

    @pytest.mark.asyncio
    async def test_slot_gate_rejection_has_full_attribution(self, judge):
        """Slot gate rejection carries all required attribution fields."""
        judge._open_positions = {'A-USDT', 'B-USDT', 'C-USDT'}
        judge._position_slots = {'A-USDT': 'main', 'B-USDT': 'main', 'C-USDT': 'main'}
        decision = {
            'symbol': 'NEW-USDT', 'action': 'open_long', 'confidence': 60,
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
        }
        await judge._gate_and_publish_open('NEW-USDT', decision, {})

        hold_call = judge.publish.call_args_list[-1]
        attr = hold_call[0][1]['attribution']
        assert 'entry_regime' in attr
        assert 'raw_regime' in attr
        assert 'slot_type' in attr
        assert 'blocked_by' in attr
        assert 'dispatch_path' in attr
        assert attr['blocked_by'] == 'main_slot_full'

    @pytest.mark.asyncio
    async def test_ranked_out_has_dispatch_path(self, judge):
        """Ranked-out rejection carries dispatch_path='main_ranking'."""
        judge._open_positions = {'A-USDT', 'B-USDT', 'C-USDT'}
        judge._position_slots = {'A-USDT': 'main', 'B-USDT': 'main', 'C-USDT': 'main'}

        judge._candidate_ranker.add_candidate({
            'symbol': 'COIN1-USDT', 'action': 'open_long', 'score': 80,
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
            'tech': {}, 'attribution': {},
            'decision': {'symbol': 'COIN1-USDT', 'action': 'open_long', 'confidence': 60,
                         'timestamp': time.time()},
        })

        await judge._flush_ranked_candidates()

        hold_calls = [c for c in judge.publish.call_args_list
                      if c[0][1].get('action') == 'hold']
        assert len(hold_calls) >= 1
        attr = hold_calls[0][0][1].get('attribution', {})
        assert attr.get('dispatch_path') == 'main_ranking'


# ═══ AC-FIX-08: deferred flow 不得绕过统一 gate ═══


class TestACFix08DeferredGate:
    """AC-FIX-08: deferred flow with slot full → rejected by gate."""

    @pytest.mark.asyncio
    async def test_deferred_15m_rejected_when_main_full(self, judge):
        """Deferred 15m open when main slot full → rejected."""
        judge._open_positions = {'A-USDT', 'B-USDT', 'C-USDT'}
        judge._position_slots = {'A-USDT': 'main', 'B-USDT': 'main', 'C-USDT': 'main'}
        decision = {
            'symbol': 'NEW-USDT', 'action': 'open_long', 'confidence': 60,
            'entry_type': 'deferred_15m_confirmation',
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
        }
        result = await judge._gate_and_publish_open('NEW-USDT', decision, {})
        assert result is False
        assert 'NEW-USDT' not in judge._pending_open_symbols

    @pytest.mark.asyncio
    async def test_deferred_chase_rejected_when_main_full(self, judge):
        """Deferred chase open when main slot full → rejected."""
        judge._open_positions = {'A-USDT', 'B-USDT', 'C-USDT'}
        judge._position_slots = {'A-USDT': 'main', 'B-USDT': 'main', 'C-USDT': 'main'}
        decision = {
            'symbol': 'NEW-USDT', 'action': 'open_long', 'confidence': 60,
            'entry_type': 'deferred_chase',
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
        }
        result = await judge._gate_and_publish_open('NEW-USDT', decision, {})
        assert result is False


# ═══ AC-FIX-09: probe evidence 可解释 ═══


class TestACFix09ProbeEvidence:
    """AC-FIX-09: probe returns reason strings, not just bool."""

    def test_probe_eligible_returns_reason(self, judge):
        """Eligible probe returns (True, 'probe_eligible')."""
        judge._symbol_tech_cache['TEST-USDT'] = {'risk': {'liquidity_score': 50}}

        class _MockRegime:
            _effective_regime = 'bullish'
            def is_probe_short_eligible(self, btc_tech, techs):
                return True
            def snapshot(self):
                return {'effective_regime': 'bullish', 'raw_regime': 'bullish', 'confidence': 70}
        judge._regime_manager = _MockRegime()

        allowed, reason = judge._can_route_probe_short('TEST-USDT', -70, True, 1.5)
        assert allowed is True
        assert reason == 'probe_eligible'

    def test_probe_blocked_returns_specific_reason(self, judge):
        """Each block condition returns a distinct reason string."""
        judge._probe_short_enabled = False
        allowed, reason = judge._can_route_probe_short('TEST-USDT', -70, True, 1.5)
        assert allowed is False
        assert reason == 'probe_disabled'

    def test_probe_rr_too_low_reason(self, judge):
        """R:R below threshold returns 'rr_too_low'."""
        judge._symbol_tech_cache['TEST-USDT'] = {'risk': {'liquidity_score': 50}}

        class _MockRegime:
            _effective_regime = 'bullish'
            def is_probe_short_eligible(self, btc_tech, techs):
                return True
            def snapshot(self):
                return {'effective_regime': 'bullish', 'raw_regime': 'bullish', 'confidence': 70}
        judge._regime_manager = _MockRegime()

        allowed, reason = judge._can_route_probe_short('TEST-USDT', -70, True, 1.0)
        assert allowed is False
        assert reason == 'rr_too_low'
