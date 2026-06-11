from agents.orchestrator import Orchestrator


class _FakeTask:
    def __init__(self, done, exc=None, cancelled=False):
        self._done, self._exc, self._cancelled = done, exc, cancelled

    def done(self):
        return self._done

    def cancelled(self):
        return self._cancelled

    def exception(self):
        return self._exc


class _StubAgent:
    def __init__(self, name):
        self.name = name


class _FakeBus:
    def __init__(self):
        self.published = []

    async def publish(self, src, mtype, payload, to):
        self.published.append((mtype, payload))


def _orch():
    # 绕过 __init__（其会做 live 凭证校验），只装被测方法需要的属性
    import logging
    o = Orchestrator.__new__(Orchestrator)
    o._research_agents = [_StubAgent("market_scanner")]
    o._trading_agents = [_StubAgent("multi_data_collector")]
    o._tasks = []
    o._alerted_failed_tasks = set()
    o._latest_failed_tasks = []
    o.bus = _FakeBus()
    o.logger = logging.getLogger("test-orch")
    return o


def test_collect_task_stats_maps_index_to_agent_name():
    o = _orch()
    exc = RuntimeError("boom")
    # index 0 -> market_scanner(alive), index 1 -> multi_data_collector(failed)
    o._tasks = [_FakeTask(done=False), _FakeTask(done=True, exc=exc)]
    alive, failed = o._collect_task_stats()
    assert alive == 1
    assert failed == [("multi_data_collector", repr(exc))]


def test_unknown_index_uses_unknown_agent_label():
    o = _orch()
    exc = RuntimeError("x")
    # 3 个 task 但只有 2 个 agent -> index 2 越界
    o._tasks = [_FakeTask(False), _FakeTask(False), _FakeTask(True, exc)]
    _, failed = o._collect_task_stats()
    assert failed == [("unknown-agent", repr(exc))]


def test_cancelled_task_not_counted_as_failed():
    o = _orch()
    o._tasks = [_FakeTask(done=True, cancelled=True)]
    _, failed = o._collect_task_stats()
    assert failed == []


async def test_alert_published_once_then_deduped():
    o = _orch()
    failed = [("multi_data_collector", "RuntimeError('boom')")]
    await o._maybe_alert_task_failure(failed)
    await o._maybe_alert_task_failure(failed)  # 同一失败再来一次
    alerts = [p for (t, p) in o.bus.published if t == "telegram_alert"]
    assert len(alerts) == 1
    assert alerts[0]["type"] == "agent_task_failed"
    assert alerts[0]["agent"] == "multi_data_collector"
