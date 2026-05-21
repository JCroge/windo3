"""Tests for CandidateRanker low R:R slot logic."""
import pytest
from utils.candidate_ranker import CandidateRanker


def make_candidate(symbol, score, rr, is_low_rr=False, entry_type='rule_signal'):
    plan = {
        'effective_risk_reward_ratio': rr,
        'expected_value': 1.0,
        'is_low_rr': is_low_rr,
    }
    return {
        'symbol': symbol,
        'score': score,
        'plan': plan,
        'tech': {},
        'attribution': {
            'htf_votes': 2,
            'llm_relation': 'agree',
            'liquidity_bucket': 'high',
        },
        'entry_type': entry_type,
    }


class TestLowRRPenalty:
    """AC-RR-02: High R:R beats low R:R in ranking."""

    def test_low_rr_gets_penalty(self):
        ranker = CandidateRanker(max_slots=3, low_rr_extra_slot=1)
        normal = make_candidate('A-USDT', 50, 1.7)
        low_rr = make_candidate('B-USDT', 60, 1.32, is_low_rr=True)

        score_normal = ranker._compute_rank_score(normal)
        score_low_rr = ranker._compute_rank_score(low_rr)

        # Despite higher signal score, low_rr should rank lower due to penalty
        assert score_normal > score_low_rr

    def test_high_rr_always_above_low_rr(self):
        ranker = CandidateRanker(max_slots=3, low_rr_extra_slot=1)
        high_rr = make_candidate('A-USDT', 40, 2.0)
        low_rr = make_candidate('B-USDT', 80, 1.25, is_low_rr=True)

        score_high = ranker._compute_rank_score(high_rr)
        score_low = ranker._compute_rank_score(low_rr)
        assert score_high > score_low


class TestExtraSlot:
    """AC-RR-01, AC-RR-03: Low R:R long uses extra slot."""

    def test_normal_fills_main_first(self):
        ranker = CandidateRanker(max_slots=2, low_rr_extra_slot=1)
        ranker.add_candidate(make_candidate('A-USDT', 60, 1.8))
        ranker.add_candidate(make_candidate('B-USDT', 50, 1.6))
        ranker.add_candidate(make_candidate('C-USDT', 70, 1.35, is_low_rr=True))

        selected, rejected = ranker.rank_and_select(set())
        symbols = [c['symbol'] for c in selected]

        # A and B fill main slots, C gets extra slot
        assert len(selected) == 3
        assert 'C-USDT' in symbols

    def test_extra_slot_limited_to_one(self):
        """AC-RR-03: Only 1 extra slot for low R:R."""
        ranker = CandidateRanker(max_slots=2, low_rr_extra_slot=1)
        ranker.add_candidate(make_candidate('A-USDT', 60, 1.8))
        ranker.add_candidate(make_candidate('B-USDT', 50, 1.6))
        # Two low R:R candidates
        ranker.add_candidate(make_candidate('C-USDT', 70, 1.35, is_low_rr=True))
        ranker.add_candidate(make_candidate('D-USDT', 65, 1.30, is_low_rr=True))

        selected, rejected = ranker.rank_and_select(set())
        # Main: A, B (2 slots). Extra: only 1 low_rr
        assert len(selected) == 3
        rejected_symbols = [c['symbol'] for c in rejected]
        assert len(rejected) == 1

    def test_low_rr_can_use_main_if_available(self):
        """If main slots available, low R:R can fill them."""
        ranker = CandidateRanker(max_slots=3, low_rr_extra_slot=1)
        ranker.add_candidate(make_candidate('A-USDT', 60, 1.8))
        ranker.add_candidate(make_candidate('B-USDT', 50, 1.35, is_low_rr=True))

        selected, rejected = ranker.rank_and_select(set())
        # Both fit in main slots (3 available, only 2 candidates)
        assert len(selected) == 2
        assert len(rejected) == 0

    def test_main_full_low_rr_uses_extra(self):
        """Main slots full → low R:R goes to extra."""
        ranker = CandidateRanker(max_slots=2, low_rr_extra_slot=1)
        # 2 positions already open
        open_pos = {'X-USDT', 'Y-USDT'}
        ranker.add_candidate(make_candidate('C-USDT', 70, 1.35, is_low_rr=True))

        selected, rejected = ranker.rank_and_select(open_pos)
        # Main full (0 available), but extra slot allows 1 low_rr
        assert len(selected) == 1
        assert selected[0]['symbol'] == 'C-USDT'

    def test_no_extra_slot_when_disabled(self):
        """Extra slot = 0 means no low R:R bypass."""
        ranker = CandidateRanker(max_slots=2, low_rr_extra_slot=0)
        open_pos = {'X-USDT', 'Y-USDT'}
        ranker.add_candidate(make_candidate('C-USDT', 70, 1.35, is_low_rr=True))

        selected, rejected = ranker.rank_and_select(open_pos)
        assert len(selected) == 0
        assert len(rejected) == 1
