"""Tests for LLM streaming resilience: global rate limit, truncation detection, retry."""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.llm_client import LLMClient, StreamTruncatedError


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset class-level state between tests."""
    LLMClient._global_last_call = 0.0
    yield
    LLMClient._global_last_call = 0.0


class TestGlobalRateLimit:

    def test_shared_across_instances(self):
        """Two LLMClient instances share the same global timestamp."""
        with patch.dict('os.environ', {'BOT_LLM_API_KEY': 'test', 'BOT_LLM_BASE_URL': 'http://localhost'}):
            c1 = LLMClient()
            c2 = LLMClient()
        assert c1._global_lock is c2._global_lock
        assert c1._global_min_interval == c2._global_min_interval == 2.0

    def test_rate_limit_enforces_interval(self):
        """Second call sleeps to respect 2s global interval."""
        with patch.dict('os.environ', {'BOT_LLM_API_KEY': 'test', 'BOT_LLM_BASE_URL': 'http://localhost'}):
            c = LLMClient()
        LLMClient._global_last_call = time.time()

        slept = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            slept.append(duration)

        async def run():
            with patch('asyncio.sleep', mock_sleep):
                async with LLMClient._global_lock:
                    now = time.time()
                    elapsed = now - LLMClient._global_last_call
                    if elapsed < LLMClient._global_min_interval:
                        await mock_sleep(LLMClient._global_min_interval - elapsed)
                    LLMClient._global_last_call = time.time()

        asyncio.run(run())
        assert len(slept) == 1
        assert slept[0] > 0


class TestStreamTruncation:

    def test_finish_reason_stop_no_error(self):
        """finish_reason='stop' returns normally."""
        with patch.dict('os.environ', {'BOT_LLM_API_KEY': 'test', 'BOT_LLM_BASE_URL': 'http://localhost'}):
            c = LLMClient()
            c.available = True

        chunk1 = MagicMock()
        chunk1.choices = [MagicMock(delta=MagicMock(content='hello'), finish_reason=None)]
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock(delta=MagicMock(content=' world'), finish_reason='stop')]

        async def fake_stream():
            for ch in [chunk1, chunk2]:
                yield ch

        c.client = MagicMock()
        c.client.chat.completions.create = AsyncMock(return_value=fake_stream())

        async def run():
            return await c.chat("sys", "user")

        result = asyncio.run(run())
        assert result == 'hello world'

    def test_finish_reason_length_raises(self):
        """finish_reason='length' raises StreamTruncatedError."""
        with patch.dict('os.environ', {'BOT_LLM_API_KEY': 'test', 'BOT_LLM_BASE_URL': 'http://localhost'}):
            c = LLMClient()
            c.available = True

        chunk1 = MagicMock()
        chunk1.choices = [MagicMock(delta=MagicMock(content='partial'), finish_reason=None)]
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock(delta=MagicMock(content=' json'), finish_reason='length')]

        async def fake_stream():
            for ch in [chunk1, chunk2]:
                yield ch

        c.client = MagicMock()
        c.client.chat.completions.create = AsyncMock(return_value=fake_stream())

        async def run():
            return await c.chat("sys", "user")

        with pytest.raises(StreamTruncatedError):
            asyncio.run(run())

    def test_finish_reason_none_raises(self):
        """Missing finish_reason (connection drop) raises StreamTruncatedError."""
        with patch.dict('os.environ', {'BOT_LLM_API_KEY': 'test', 'BOT_LLM_BASE_URL': 'http://localhost'}):
            c = LLMClient()
            c.available = True

        chunk1 = MagicMock()
        chunk1.choices = [MagicMock(delta=MagicMock(content='data'), finish_reason=None)]

        async def fake_stream():
            yield chunk1

        c.client = MagicMock()
        c.client.chat.completions.create = AsyncMock(return_value=fake_stream())

        async def run():
            return await c.chat("sys", "user")

        with pytest.raises(StreamTruncatedError):
            asyncio.run(run())


class TestChatJsonRetry:

    def test_truncation_retry_succeeds(self):
        """chat_json retries on StreamTruncatedError and succeeds."""
        with patch.dict('os.environ', {'BOT_LLM_API_KEY': 'test', 'BOT_LLM_BASE_URL': 'http://localhost'}):
            c = LLMClient()
            c.available = True

        call_count = [0]

        async def mock_chat(sys, user, max_tokens=2000, temperature=0.3):
            call_count[0] += 1
            if call_count[0] == 1:
                raise StreamTruncatedError("truncated")
            return '{"action": "hold", "confidence": 50}'

        c.chat = mock_chat

        async def run():
            return await c.chat_json("sys", "msg", caller="test")

        result = asyncio.run(run())
        assert result['action'] == 'hold'
        assert call_count[0] == 2
