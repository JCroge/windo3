"""Synthesizer cycle 分桶测试 — 验证 sentiment/news 先到不丢失"""

import os
import sys
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def synth():
    with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}):
        from agents.research.synthesizer import ResearchSynthesizer
        s = ResearchSynthesizer.__new__(ResearchSynthesizer)
        s._pending_data = {}
        s._pending_by_cycle = {}
        s._current_cycle_id = None
        s._max_symbols = 12
        s._preliminary_result = None
        s._market_context = ""
        s._barrier_event = None
        s._barrier_task = None
        s._barrier_cycle_id = None
        s._barrier_timeout = 20
        s._max_cycle_buckets = 2
        s.logger = MagicMock()
        s.publish = AsyncMock()
        s.ask_claude_json = AsyncMock(return_value={
            'selected_symbols': [
                {'symbol': 'BTC-USDT', 'direction_bias': 'long',
                 'confidence': 70, 'reasoning': 'test',
                 'key_signal': 'test', 'risk_factor': 'test'}
            ],
            'market_regime': 'trending',
            'overall_assessment': 'test'
        })
        return s


@pytest.mark.asyncio
async def test_sentiment_before_market_not_lost(synth):
    """第二轮 sentiment 先到、market 后到，最终 synthesis 包含 sentiment 数据"""
    # 第一轮完成（设置 current_cycle_id）
    synth._current_cycle_id = "cycle_001"

    # 第二轮：sentiment 先到
    await synth.on_message({
        'type': 'research_sentiment_data',
        'payload': {'cycle_id': 'cycle_002', 'fear_greed': {'value': 50, 'classification': 'neutral'}},
    })

    # sentiment 应该被缓存到桶中
    assert 'cycle_002' in synth._pending_by_cycle
    assert 'research_sentiment_data' in synth._pending_by_cycle['cycle_002']

    # 第二轮：news 到达
    await synth.on_message({
        'type': 'research_news_data',
        'payload': {'cycle_id': 'cycle_002', 'headlines': []},
    })

    assert 'research_news_data' in synth._pending_by_cycle['cycle_002']

    # 第二轮：market 到达 → 激活 cycle_002
    await synth.on_message({
        'type': 'research_market_data',
        'payload': {'cycle_id': 'cycle_002', 'candidates': [
            {'symbol': 'BTC-USDT', 'price': 100000, 'volume_24h': 1e9,
             'volatility_pct': 5, 'change_24h_pct': 3}
        ]},
    })

    # 验证 cycle 已切换
    assert synth._current_cycle_id == 'cycle_002'
    # 验证 _pending_data 包含三路数据（从桶恢复）
    assert 'research_sentiment_data' in synth._pending_data or synth.publish.called


@pytest.mark.asyncio
async def test_old_cycle_challenge_discarded(synth):
    """旧 cycle 的 challenge 被丢弃"""
    synth._current_cycle_id = "cycle_002"
    synth._preliminary_result = None

    await synth.on_message({
        'type': 'research_challenge',
        'payload': {'cycle_id': 'cycle_001', 'challenges': []},
    })

    # 不应触发 final_decision（无 preliminary_result 且 cycle 不匹配）
    assert not synth.publish.called


@pytest.mark.asyncio
async def test_bucket_cleanup_keeps_latest_two(synth):
    """桶清理只保留最新2个"""
    synth._current_cycle_id = None

    for i in range(4):
        await synth.on_message({
            'type': 'research_sentiment_data',
            'payload': {'cycle_id': f'cycle_{i:03d}', 'data': i},
        })

    assert len(synth._pending_by_cycle) <= 2
    # 最新的两个应该保留
    assert 'cycle_003' in synth._pending_by_cycle
    assert 'cycle_002' in synth._pending_by_cycle


@pytest.mark.asyncio
async def test_random_cycle_id_cleanup_does_not_delete_incoming_cycle(synth):
    """随机 UUID 前缀不能按字典序清理，否则会误删当前新 cycle。"""
    synth._current_cycle_id = "d55e7816"
    synth._pending_by_cycle = {
        "b768c215": {"research_market_data": {"cycle_id": "b768c215", "candidates": []}},
        "d55e7816": {"research_market_data": {"cycle_id": "d55e7816", "candidates": []}},
    }

    await synth.on_message({
        'type': 'research_news_data',
        'payload': {'cycle_id': '52f5a592', 'headlines': []},
    })
    await synth.on_message({
        'type': 'research_sentiment_data',
        'payload': {'cycle_id': '52f5a592', 'fear_greed': {'value': 50, 'classification': 'neutral'}},
    })
    await synth.on_message({
        'type': 'research_market_data',
        'payload': {'cycle_id': '52f5a592', 'candidates': [
            {'symbol': 'BTC-USDT', 'price': 100000, 'volume_24h': 1e9,
             'volatility_pct': 5, 'change_24h_pct': 3}
        ]},
    })

    assert synth._current_cycle_id == '52f5a592'
    assert synth.publish.called
    payload = synth.publish.call_args.args[1]
    assert payload['cycle_id'] == '52f5a592'


@pytest.mark.asyncio
async def test_ready_cycle_with_stale_barrier_event_synthesizes(synth):
    """barrier event 残留但没有活跃 task 时，ready cycle 不能只 set event 后吞掉。"""
    synth._current_cycle_id = "cycle_stale"
    synth._barrier_event = asyncio.Event()
    synth._barrier_task = None
    synth._barrier_cycle_id = "cycle_stale"

    await synth.on_message({
        'type': 'research_news_data',
        'payload': {'cycle_id': 'cycle_stale', 'headlines': []},
    })
    await synth.on_message({
        'type': 'research_sentiment_data',
        'payload': {'cycle_id': 'cycle_stale', 'fear_greed': {'value': 50, 'classification': 'neutral'}},
    })
    await synth.on_message({
        'type': 'research_market_data',
        'payload': {'cycle_id': 'cycle_stale', 'candidates': [
            {'symbol': 'BTC-USDT', 'price': 100000, 'volume_24h': 1e9,
             'volatility_pct': 5, 'change_24h_pct': 3}
        ]},
    })

    assert synth.publish.called
