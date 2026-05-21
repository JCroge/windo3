"""Test: slot occupancy accounting — P0-3 fix verification.

Verifies that:
1. low_rr_extra positions don't count against main slot cap
2. Ranker respects slot_occupancy and won't double-allocate extra slots
3. Pending slots are tracked by type
"""

import time
import sys
import os
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))

from utils.candidate_ranker import CandidateRanker


def _candidate(symbol, is_low_rr=False, score=60, rr=2.0):
    plan = {
        'size_usdt': 5, 'leverage': 3,
        'risk_reward_ratio': rr, 'effective_risk_reward_ratio': rr,
        'is_low_rr': is_low_rr,
    }
    if is_low_rr:
        plan['slot_type'] = 'low_rr_extra'
    return {
        'symbol': symbol,
        'action': 'open_long',
        'score': score,
        'plan': plan,
        'tech': {},
        'attribution': {},
        'decision': {'symbol': symbol, 'action': 'open_long', 'plan': plan},
    }


class TestSlotOccupancy:

    def test_low_rr_uses_extra_slot_not_main(self):
        """When main slots full, low_rr candidate can still use extra slot."""
        ranker = CandidateRanker(max_slots=3, enabled=True, low_rr_extra_slot=1)
        ranker.add_candidate(_candidate('ETH-USDT', is_low_rr=True))

        # 3 main slots occupied, 0 low_rr occupied
        open_positions = {'BTC-USDT', 'SOL-USDT', 'DOGE-USDT'}
        slot_occupancy = {'main': 3, 'low_rr_extra': 0, 'probe_short': 0}

        selected, rejected = ranker.rank_and_select(open_positions, slot_occupancy)
        assert len(selected) == 1
        assert selected[0]['symbol'] == 'ETH-USDT'

    def test_low_rr_extra_slot_already_occupied(self):
        """When extra slot already used, second low_rr is rejected."""
        ranker = CandidateRanker(max_slots=3, enabled=True, low_rr_extra_slot=1)
        ranker.add_candidate(_candidate('ETH-USDT', is_low_rr=True))

        # 3 main + 1 low_rr already occupied
        open_positions = {'BTC-USDT', 'SOL-USDT', 'DOGE-USDT', 'AVAX-USDT'}
        slot_occupancy = {'main': 3, 'low_rr_extra': 1, 'probe_short': 0}

        selected, rejected = ranker.rank_and_select(open_positions, slot_occupancy)
        assert len(selected) == 0
        assert len(rejected) == 1

    def test_normal_candidate_blocked_when_main_full(self):
        """Normal (non-low_rr) candidate blocked when main slots full."""
        ranker = CandidateRanker(max_slots=3, enabled=True, low_rr_extra_slot=1)
        ranker.add_candidate(_candidate('ETH-USDT', is_low_rr=False))

        open_positions = {'BTC-USDT', 'SOL-USDT', 'DOGE-USDT'}
        slot_occupancy = {'main': 3, 'low_rr_extra': 0, 'probe_short': 0}

        selected, rejected = ranker.rank_and_select(open_positions, slot_occupancy)
        assert len(selected) == 0
        assert len(rejected) == 1

    def test_mixed_batch_main_and_low_rr(self):
        """Batch with both normal and low_rr: normal fills main, low_rr fills extra."""
        ranker = CandidateRanker(max_slots=3, enabled=True, low_rr_extra_slot=1)
        ranker.add_candidate(_candidate('ETH-USDT', is_low_rr=False, score=80))
        ranker.add_candidate(_candidate('SOL-USDT', is_low_rr=True, score=50))

        # 2 main occupied, 0 low_rr
        open_positions = {'BTC-USDT', 'DOGE-USDT'}
        slot_occupancy = {'main': 2, 'low_rr_extra': 0, 'probe_short': 0}

        selected, rejected = ranker.rank_and_select(open_positions, slot_occupancy)
        # ETH fills last main slot, SOL fills extra slot
        assert len(selected) == 2
        symbols = {c['symbol'] for c in selected}
        assert 'ETH-USDT' in symbols
        assert 'SOL-USDT' in symbols

    def test_fallback_without_slot_occupancy(self):
        """Without slot_occupancy arg, falls back to simple position count."""
        ranker = CandidateRanker(max_slots=3, enabled=True, low_rr_extra_slot=1)
        ranker.add_candidate(_candidate('ETH-USDT', is_low_rr=True))

        open_positions = {'BTC-USDT', 'SOL-USDT', 'DOGE-USDT'}
        # No slot_occupancy → old behavior: all counted as main
        selected, rejected = ranker.rank_and_select(open_positions)
        # main full (3/3), but low_rr_extra_slot=1 and low_rr_used=0 (fallback)
        assert len(selected) == 1
