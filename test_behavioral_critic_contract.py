"""AC-P1-001 / AC-P1-002 BehavioralCritic 字段统一契约测试

覆盖：
- schema 默认字段名为 counter_recommendation / confidence_in_challenge
- legacy LLM 输出 counter_action / confidence 时由 _normalize_critic_payload 别名补齐
- _rule_fallback 输出标准字段
- PositionAnalyst._arbitrate 同时兼容 canonical 与 legacy 字段
- 坏 JSON / 缺字段 case 走 fallback 不抛异常
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agents.llm_client import BEHAVIORAL_CRITIC_SCHEMA, validate_against_schema
from agents.trading.behavioral_critic import BehavioralCritic
from agents.trading.position_analyst import PositionAnalyst


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- AC-P1-001 schema canonical 字段 ---

def test_schema_uses_canonical_counter_recommendation():
    assert 'counter_recommendation' in BEHAVIORAL_CRITIC_SCHEMA
    assert 'confidence_in_challenge' in BEHAVIORAL_CRITIC_SCHEMA
    assert 'counter_action' not in BEHAVIORAL_CRITIC_SCHEMA
    spec = BEHAVIORAL_CRITIC_SCHEMA['counter_recommendation']
    assert 'hold' in spec['allowed']
    assert 'close' in spec['allowed']


def test_schema_validates_canonical_payload():
    payload = {
        'bias_detected': 'fomo',
        'severity': 'medium',
        'challenge': 'overbought add',
        'counter_recommendation': 'hold',
        'confidence_in_challenge': 65,
    }
    cleaned, errors = validate_against_schema(payload, BEHAVIORAL_CRITIC_SCHEMA)
    assert cleaned['counter_recommendation'] == 'hold'
    assert cleaned['confidence_in_challenge'] == 65
    assert errors == []


def test_schema_rejects_invalid_counter():
    cleaned, errors = validate_against_schema(
        {'counter_recommendation': 'liquidate'}, BEHAVIORAL_CRITIC_SCHEMA
    )
    assert cleaned['counter_recommendation'] == ''
    assert any('not_allowed:counter_recommendation' in e for e in errors)


# --- legacy LLM payload 别名 ---

def test_normalize_promotes_legacy_counter_action():
    legacy = {
        'bias_detected': 'fomo',
        'severity': 'medium',
        'counter_action': 'reduce',
        'confidence': 70,
    }
    out = BehavioralCritic._normalize_critic_payload(dict(legacy))
    assert out['counter_recommendation'] == 'reduce'
    assert out['confidence_in_challenge'] == 70


def test_normalize_does_not_overwrite_canonical():
    payload = {
        'counter_recommendation': 'hold',
        'confidence_in_challenge': 80,
        'counter_action': 'close',
        'confidence': 30,
    }
    out = BehavioralCritic._normalize_critic_payload(dict(payload))
    assert out['counter_recommendation'] == 'hold'
    assert out['confidence_in_challenge'] == 80


def test_normalize_handles_empty_legacy():
    payload = {'bias_detected': 'none'}
    out = BehavioralCritic._normalize_critic_payload(dict(payload))
    assert out.get('counter_recommendation', '') == ''


# --- _rule_fallback 输出 canonical ---

def test_rule_fallback_emits_canonical_fields():
    critic = BehavioralCritic({})
    review = {
        'symbol': 'BTC-USDT',
        'action': 'hold',
        'context': {
            'side': 'long',
            'pnl_pct': -9,
            'hours_held': 6,
            'leverage': 5,
            'trend': 'bearish',
            'higher_trend': 'bearish',
        },
    }
    out = critic._rule_fallback(review)
    assert out['counter_recommendation'] is not None
    assert 'confidence_in_challenge' in out
    assert 'counter_action' not in out


def test_rule_fallback_no_bias_when_trend_aligned():
    critic = BehavioralCritic({})
    review = {
        'symbol': 'BTC-USDT',
        'action': 'hold',
        'context': {
            'side': 'long',
            'pnl_pct': -6,
            'hours_held': 6,
            'leverage': 5,
            'trend': 'bullish',
            'higher_trend': 'bullish',
        },
    }
    out = critic._rule_fallback(review)
    assert out['bias_detected'] is None
    assert out['counter_recommendation'] is None


# --- _critique end-to-end with mocked LLM ---

class _StubBus:
    def __init__(self):
        self.published = []

    async def publish(self, source, msg_type, payload, to='broadcast', symbol=None):
        self.published.append({'type': msg_type, 'payload': payload, 'symbol': symbol})


def _make_critic_with_stub_bus():
    critic = BehavioralCritic({})
    critic.bus = _StubBus()
    return critic


def test_critique_canonical_llm_payload():
    critic = _make_critic_with_stub_bus()

    async def fake_llm(*a, **kw):
        return {
            'bias_detected': 'fomo',
            'severity': 'medium',
            'challenge': 'price stretched',
            'counter_recommendation': 'reduce',
            'confidence_in_challenge': 70,
        }

    with patch.object(BehavioralCritic, 'ask_claude_json', new=AsyncMock(side_effect=fake_llm)):
        review = {'symbol': 'ETH-USDT', 'action': 'add', 'context': {'side': 'long'}}
        _run(critic._critique(review))

    out = critic.bus.published[0]['payload']
    assert out['counter_recommendation'] == 'reduce'
    assert out['confidence_in_challenge'] == 70
    assert out['symbol'] == 'ETH-USDT'


def test_critique_legacy_llm_payload_aliased():
    critic = _make_critic_with_stub_bus()

    async def fake_llm(*a, **kw):
        # 模拟 LLM 输出旧字段名（counter_action / confidence）
        return {
            'bias_detected': 'overconfidence',
            'severity': 'high',
            'challenge': 'leverage too high',
            'counter_action': 'close',
            'confidence': 80,
        }

    with patch.object(BehavioralCritic, 'ask_claude_json', new=AsyncMock(side_effect=fake_llm)):
        review = {'symbol': 'SOL-USDT', 'action': 'add', 'context': {'side': 'long'}}
        _run(critic._critique(review))

    out = critic.bus.published[0]['payload']
    assert out['counter_recommendation'] == 'close'
    assert out['confidence_in_challenge'] == 80


def test_critique_bad_llm_falls_back_to_rule():
    critic = _make_critic_with_stub_bus()

    async def fake_llm(*a, **kw):
        return None

    with patch.object(BehavioralCritic, 'ask_claude_json', new=AsyncMock(side_effect=fake_llm)):
        review = {
            'symbol': 'AAA-USDT',
            'action': 'hold',
            'context': {
                'side': 'long', 'pnl_pct': -10, 'hours_held': 4,
                'leverage': 5, 'trend': 'bearish', 'higher_trend': 'bearish',
            },
        }
        _run(critic._critique(review))

    out = critic.bus.published[0]['payload']
    assert 'counter_recommendation' in out
    assert 'confidence_in_challenge' in out


def test_critique_llm_exception_falls_back_to_rule():
    critic = _make_critic_with_stub_bus()

    async def boom(*a, **kw):
        raise RuntimeError('LLM boom')

    with patch.object(BehavioralCritic, 'ask_claude_json', new=AsyncMock(side_effect=boom)):
        review = {
            'symbol': 'BBB-USDT',
            'action': 'hold',
            'context': {
                'side': 'long', 'pnl_pct': -10, 'hours_held': 4,
                'leverage': 5, 'trend': 'bearish', 'higher_trend': 'bearish',
            },
        }
        _run(critic._critique(review))

    out = critic.bus.published[0]['payload']
    assert 'counter_recommendation' in out
    assert 'confidence_in_challenge' in out


# --- AC-P1-002 PositionAnalyst 兼容旧字段 ---

def _make_pa():
    pa = PositionAnalyst({})
    return pa


def _analyst(action='hold', conviction=60, pnl_pct=-6.0):
    return {
        'symbol': 'XYZ-USDT',
        'action': action,
        'conviction': conviction,
        'context': {'side': 'long', 'pnl_pct': pnl_pct, 'higher_trend': 'bearish'},
    }


def test_arbitrate_reads_canonical_counter():
    pa = _make_pa()
    final = pa._arbitrate(
        _analyst(action='hold'),
        {
            'bias_detected': 'loss_aversion',
            'severity': 'medium',
            'counter_recommendation': 'close',
        },
    )
    assert final['final_action'] in ('reduce', 'close')


def test_arbitrate_reads_legacy_counter_action():
    pa = _make_pa()
    final = pa._arbitrate(
        _analyst(action='hold'),
        {
            'bias_detected': 'loss_aversion',
            'severity': 'medium',
            # 旧字段：仅 counter_action 存在
            'counter_action': 'close',
        },
    )
    assert final['final_action'] in ('reduce', 'close')


def test_arbitrate_no_counter_keeps_action():
    pa = _make_pa()
    final = pa._arbitrate(
        _analyst(action='hold'),
        {
            'bias_detected': None,
            'severity': 'none',
        },
    )
    assert final['final_action'] == 'hold'
