"""Ranking slot reservation 测试 — 验证 flush 时预占槽位防止超发"""

import os
import sys
import time
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.candidate_ranker import CandidateRanker


class TestCandidateRankerSlots:

    def test_rank_and_select_respects_occupied_slots(self):
        """已有2仓+1pending，max=3，同窗口3候选只选0个"""
        ranker = CandidateRanker(max_slots=3, enabled=True)

        for i in range(3):
            ranker.add_candidate({
                'symbol': f'COIN{i}-USDT',
                'action': 'open_long',
                'score': 50 + i * 10,
                'plan': {'tp_pct': 0.03, 'sl_pct': 0.02},
                'tech': {'rsi': 30, 'volume_ratio': 1.5},
                'decision': {'symbol': f'COIN{i}-USDT', 'action': 'open_long'},
            })

        # 2 open + 1 pending = 3 occupied
        occupied = {'BTC-USDT', 'ETH-USDT', 'SOL-USDT'}
        selected, rejected = ranker.rank_and_select(occupied)

        assert len(selected) == 0
        assert len(rejected) == 3

    def test_rank_and_select_limits_to_available_slots(self):
        """已有2仓，max=3，5候选只选1个"""
        ranker = CandidateRanker(max_slots=3, enabled=True)

        for i in range(5):
            ranker.add_candidate({
                'symbol': f'COIN{i}-USDT',
                'action': 'open_long',
                'score': 10 + i * 20,
                'plan': {'tp_pct': 0.03, 'sl_pct': 0.02},
                'tech': {'rsi': 30, 'volume_ratio': 1.5},
                'decision': {'symbol': f'COIN{i}-USDT', 'action': 'open_long'},
            })

        occupied = {'BTC-USDT', 'ETH-USDT'}
        selected, rejected = ranker.rank_and_select(occupied)

        assert len(selected) == 1
        assert len(rejected) == 4

    def test_empty_buffer_returns_empty(self):
        """空 buffer 不崩溃"""
        ranker = CandidateRanker(max_slots=3, enabled=True)
        selected, rejected = ranker.rank_and_select(set())
        assert selected == []
        assert rejected == []

    def test_disabled_ranker_passes_all_through(self):
        """disabled 时所有候选直接通过"""
        ranker = CandidateRanker(max_slots=3, enabled=False)

        for i in range(5):
            ranker.add_candidate({
                'symbol': f'COIN{i}-USDT',
                'action': 'open_long',
                'score': 50,
                'plan': {'tp_pct': 0.03, 'sl_pct': 0.02},
                'tech': {'rsi': 30, 'volume_ratio': 1.5},
                'decision': {'symbol': f'COIN{i}-USDT', 'action': 'open_long'},
            })

        selected, rejected = ranker.rank_and_select({'BTC-USDT', 'ETH-USDT'})
        assert len(selected) == 5
        assert len(rejected) == 0


class TestJudgeSlotReservation:
    """Judge 级别的 pending slot 预占测试"""

    @pytest.fixture
    def judge_instance(self):
        """创建最小化 Judge 实例用于测试 slot 逻辑"""
        with patch.dict(os.environ, {
            'OKX_API_KEY': 'test', 'OKX_SECRET': 'test', 'OKX_PASSPHRASE': 'test',
        }):
            from agents.trading.judge import MultiJudge
            judge = MultiJudge.__new__(MultiJudge)
            judge._open_positions = set()
            judge._pending_open_symbols = set()
            judge._pending_open_ts = {}
            judge._pending_ttl = 120
            judge._max_concurrent_positions = 3
            judge._candidate_ranker = CandidateRanker(max_slots=3, enabled=True)
            judge._rank_flush_delay = 5
            judge._rank_flush_task = None
            judge.logger = MagicMock()
            judge._symbol_state = {}
            judge._state_dirty = False
            judge.publish = AsyncMock()

            class _MockLedger:
                _enabled = False
            judge._counterfactual_ledger = _MockLedger()

            class _MockRegime:
                _effective_regime = 'mixed'
                def snapshot(self):
                    return {'effective_regime': 'mixed', 'raw_regime': 'mixed', 'confidence': 50}
            judge._regime_manager = _MockRegime()
            return judge

    @pytest.mark.asyncio
    async def test_flush_adds_to_pending(self, judge_instance):
        """flush 后 selected symbols 进入 _pending_open_symbols"""
        judge = judge_instance
        judge._open_positions = {'BTC-USDT'}

        # 添加2个候选
        judge._candidate_ranker.add_candidate({
            'symbol': 'ETH-USDT', 'action': 'open_long', 'score': 80,
            'plan': {'tp_pct': 0.03, 'sl_pct': 0.02},
            'tech': {'rsi': 30, 'volume_ratio': 1.5},
            'decision': {'symbol': 'ETH-USDT', 'action': 'open_long', 'timestamp': time.time()},
        })
        judge._candidate_ranker.add_candidate({
            'symbol': 'SOL-USDT', 'action': 'open_short', 'score': 70,
            'plan': {'tp_pct': 0.03, 'sl_pct': 0.02},
            'tech': {'rsi': 70, 'volume_ratio': 1.2},
            'decision': {'symbol': 'SOL-USDT', 'action': 'open_short', 'timestamp': time.time()},
        })

        # mock _get_state
        judge._get_state = lambda s: judge._symbol_state.setdefault(s, {})

        await judge._flush_ranked_candidates()

        # max=3, open=1, 所以可选2个
        assert len(judge._pending_open_symbols) == 2
        assert 'ETH-USDT' in judge._pending_open_symbols
        assert 'SOL-USDT' in judge._pending_open_symbols

    @pytest.mark.asyncio
    async def test_pending_blocks_next_flush(self, judge_instance):
        """pending 占位后，下一批 flush 不能再超发"""
        judge = judge_instance
        judge._open_positions = {'BTC-USDT'}
        judge._pending_open_symbols = {'ETH-USDT', 'SOL-USDT'}

        # 新一批候选
        judge._candidate_ranker.add_candidate({
            'symbol': 'DOGE-USDT', 'action': 'open_long', 'score': 90,
            'plan': {'tp_pct': 0.03, 'sl_pct': 0.02},
            'tech': {'rsi': 25, 'volume_ratio': 2.0},
            'decision': {'symbol': 'DOGE-USDT', 'action': 'open_long', 'timestamp': time.time()},
        })

        judge._get_state = lambda s: judge._symbol_state.setdefault(s, {})

        await judge._flush_ranked_candidates()

        # 1 open + 2 pending = 3 = max, 所以 DOGE 应该被 reject
        assert 'DOGE-USDT' not in judge._pending_open_symbols
        # publish 应该发了 hold
        calls = judge.publish.call_args_list
        hold_calls = [c for c in calls if c[0][1].get('action') == 'hold']
        assert len(hold_calls) == 1

    @pytest.mark.asyncio
    async def test_execution_result_clears_pending(self, judge_instance):
        """execution_result 成功后从 pending 移到 open"""
        judge = judge_instance
        judge._pending_open_symbols = {'ETH-USDT'}
        judge._open_positions = {'BTC-USDT'}

        # 模拟 execution_result 处理中的 pending 清除
        symbol = 'ETH-USDT'
        judge._pending_open_symbols.discard(symbol)
        judge._open_positions.add(symbol)

        assert 'ETH-USDT' not in judge._pending_open_symbols
        assert 'ETH-USDT' in judge._open_positions

    @pytest.mark.asyncio
    async def test_execution_failure_releases_pending(self, judge_instance):
        """execution_result 失败后释放 pending slot"""
        judge = judge_instance
        judge._pending_open_symbols = {'ETH-USDT'}
        judge._open_positions = {'BTC-USDT'}

        # 模拟失败
        symbol = 'ETH-USDT'
        judge._pending_open_symbols.discard(symbol)

        assert 'ETH-USDT' not in judge._pending_open_symbols
        assert 'ETH-USDT' not in judge._open_positions
        # slot 被释放，现在 occupied = 1
        occupied = judge._open_positions | judge._pending_open_symbols
        assert len(occupied) == 1

    @pytest.mark.asyncio
    async def test_pending_ttl_sweep_releases_stale(self, judge_instance):
        """pending 超过 TTL 后自动释放"""
        judge = judge_instance
        judge._pending_ttl = 120
        judge._pending_open_symbols = {'STALE-USDT', 'FRESH-USDT'}
        judge._pending_open_ts = {
            'STALE-USDT': time.time() - 200,  # 超时
            'FRESH-USDT': time.time() - 10,   # 未超时
        }

        judge._sweep_stale_pending()

        assert 'STALE-USDT' not in judge._pending_open_symbols
        assert 'STALE-USDT' not in judge._pending_open_ts
        assert 'FRESH-USDT' in judge._pending_open_symbols
        assert 'FRESH-USDT' in judge._pending_open_ts
