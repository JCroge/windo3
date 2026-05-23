"""AC2-01: CandidateRanker logger path does not crash."""
import pytest
from unittest.mock import MagicMock
from utils.candidate_ranker import CandidateRanker


def _make_candidate(symbol, slot_type='main', is_probe=False, is_low_rr=False, score=60):
    return {
        'symbol': symbol,
        'action': 'open_long',
        'score': score,
        'plan': {
            'slot_type': slot_type,
            'is_probe': is_probe,
            'is_low_rr': is_low_rr,
            'effective_risk_reward_ratio': 2.0,
        },
        'tech': {'trend': {'direction': 'bullish', 'higher_tf_bias': 'bullish'}},
        'attribution': {'llm_relation': 'agree', 'htf_votes': 2, 'liquidity_bucket': 'high'},
        'entry_type': 'rule_signal',
        'decision': {'symbol': symbol, 'action': 'open_long', 'confidence': 70},
    }


class TestRankerLoggerNoCrash:
    def test_logger_with_main_candidates(self):
        ranker = CandidateRanker(max_slots=3, enabled=True, logger=MagicMock())
        ranker.add_candidate(_make_candidate('BTC-USDT'))
        ranker.add_candidate(_make_candidate('ETH-USDT', score=50))
        selected, rejected = ranker.rank_and_select(set())
        assert len(selected) == 2
        ranker.logger.info.assert_called()

    def test_logger_with_low_rr_candidates(self):
        ranker = CandidateRanker(max_slots=1, enabled=True, low_rr_extra_slot=1, logger=MagicMock())
        ranker.add_candidate(_make_candidate('BTC-USDT'))
        ranker.add_candidate(_make_candidate('ETH-USDT', is_low_rr=True, score=40))
        selected, rejected = ranker.rank_and_select(set())
        assert len(selected) >= 1

    def test_logger_with_probe_short(self):
        ranker = CandidateRanker(max_slots=1, enabled=True, logger=MagicMock())
        ranker.add_candidate(_make_candidate('BTC-USDT'))
        ranker.add_candidate(_make_candidate('ETH-USDT', slot_type='probe_short', is_probe=True, score=45))
        selected, rejected = ranker.rank_and_select(set())
        assert len(selected) >= 1

    def test_logger_with_probe_long(self):
        ranker = CandidateRanker(max_slots=1, enabled=True, logger=MagicMock())
        ranker.add_candidate(_make_candidate('BTC-USDT'))
        ranker.add_candidate(_make_candidate('SOL-USDT', slot_type='probe_long', is_probe=True, score=55))
        selected, rejected = ranker.rank_and_select(set())
        assert len(selected) >= 1

    def test_logger_with_all_types(self):
        """All 4 slot types present — must not crash."""
        ranker = CandidateRanker(max_slots=2, enabled=True, low_rr_extra_slot=1, logger=MagicMock())
        ranker.add_candidate(_make_candidate('BTC-USDT', score=80))
        ranker.add_candidate(_make_candidate('ETH-USDT', is_low_rr=True, score=40))
        ranker.add_candidate(_make_candidate('SOL-USDT', slot_type='probe_short', is_probe=True, score=50))
        ranker.add_candidate(_make_candidate('DOGE-USDT', slot_type='probe_long', is_probe=True, score=55))
        selected, rejected = ranker.rank_and_select(set())
        assert len(selected) == 4
        log_msg = ranker.logger.info.call_args_list[0][0][0]
        assert 'probe_short=' in log_msg
        assert 'probe_long=' in log_msg
        assert 'rejected=' in log_msg

    def test_no_probe_selected_name_error(self):
        """Regression: probe_selected was undefined after split."""
        ranker = CandidateRanker(max_slots=3, enabled=True, logger=MagicMock())
        ranker.add_candidate(_make_candidate('BTC-USDT'))
        ranker.add_candidate(_make_candidate('ETH-USDT', score=50))
        # Must not raise NameError
        ranker.rank_and_select(set())
