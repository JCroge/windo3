import asyncio
import time
import pytest
from agents.message_bus import MessageBus
from agents.base import BaseAgent


class _Probe(BaseAgent):
    name = "probe_hb"
    subscriptions = []

    def __init__(self, config=None):
        super().__init__(config)
        self.seen = []

    async def setup(self):
        pass

    async def on_message(self, msg):
        self.seen.append(msg)


@pytest.fixture(autouse=True)
def _reset_bus():
    MessageBus._instance = None
    yield
    MessageBus._instance = None


def test_init_heartbeat_fields_default_zero():
    a = _Probe()
    assert a._last_alive_ts == 0.0
    assert a._last_work_ts == 0.0


@pytest.mark.asyncio
async def test_message_loop_stamps_alive_even_without_messages():
    a = _Probe()
    a._running = True
    t0 = time.time()
    task = asyncio.create_task(a._message_loop())
    await asyncio.sleep(0.6)
    a._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert a._last_alive_ts >= t0
    assert a._last_work_ts == 0.0


@pytest.mark.asyncio
async def test_message_loop_stamps_work_on_message():
    bus = MessageBus.get_instance()
    a = _Probe()
    a._running = True
    task = asyncio.create_task(a._message_loop())
    await bus.publish("tester", "probe_topic", {"x": 1}, to=a.name)
    await asyncio.sleep(0.6)
    a._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert a._last_work_ts > 0.0
    assert a.seen, "agent 应收到消息"


class _TickProbe(BaseAgent):
    name = "tick_probe"
    subscriptions = []

    async def setup(self):
        pass

    async def on_message(self, msg):
        pass

    async def tick(self):
        await asyncio.sleep(0.05)


def test_init_tick_fields_default_zero():
    a = _TickProbe()
    assert a._tick_enter_ts == 0.0
    assert a._tick_exit_ts == 0.0


@pytest.mark.asyncio
async def test_periodic_loop_stamps_tick_enter_and_exit():
    a = _TickProbe()
    a._running = True
    task = asyncio.create_task(a._periodic_loop())
    await asyncio.sleep(0.25)
    a._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert a._tick_enter_ts > 0.0
    assert a._tick_exit_ts > 0.0
