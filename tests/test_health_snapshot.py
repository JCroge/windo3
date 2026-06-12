import pytest
from utils.health_snapshot import build_health_snapshot

NOW = 1_000_000.0
CFG = dict(stall_timeout_sec=60, backlog_warn_pending=200, data_stale_timeout_sec=180)
BASE = dict(agents_registered=2, tasks_alive=4, tasks_failed=0,
            halted_symbols={}, bus_dlq_size=0)


class _FakeLLM:
    def __init__(self, degraded, fails):
        self._degraded = degraded
        self.consecutive_failures = fails

    @property
    def degraded(self):
        return self._degraded


class _FakeAgent:
    def __init__(self, name, alive_ts=NOW, llm=None, data_health=None):
        self.name = name
        self._last_alive_ts = alive_ts
        self.llm = llm
        if data_health is not None:
            self._latest_data_health = data_health


def _snap(agents, bus_metrics, base=None):
    return build_health_snapshot(
        agents, bus_metrics, NOW,
        stall_timeout_sec=CFG["stall_timeout_sec"],
        backlog_warn_pending=CFG["backlog_warn_pending"],
        data_stale_timeout_sec=CFG["data_stale_timeout_sec"],
        base_stats=base or BASE,
    )


def test_base_stats_passthrough_and_ts():
    s = _snap([_FakeAgent("judge")], {"_dlq_size": 0})
    assert s["agents_registered"] == 2
    assert s["tasks_failed"] == 0
    assert s["ts"] == NOW


def test_loop_stall_detected_and_skips_unstarted():
    agents = [
        _FakeAgent("fresh", alive_ts=NOW - 5),
        _FakeAgent("stuck", alive_ts=NOW - 120),
        _FakeAgent("unstarted", alive_ts=0.0),
    ]
    s = _snap(agents, {"_dlq_size": 0})
    lh = s["loop_health"]
    assert lh["stalled_count"] == 1
    assert lh["stalled"][0]["name"] == "stuck"
    assert lh["stalled"][0]["idle_sec"] == 120


def test_queue_backlog_detected_and_ignores_dlq_key():
    bus_metrics = {
        "judge": {"pending": 10},
        "reviewer": {"pending": 250},
        "_dlq_size": 7,
    }
    s = _snap([_FakeAgent("judge")], bus_metrics)
    qh = s["queue_health"]
    assert qh["backlogged_count"] == 1
    assert qh["backlogged"][0]["name"] == "reviewer"
    assert qh["max_pending"] == 250


def test_llm_degraded_aggregates_and_skips_none():
    agents = [
        _FakeAgent("judge", llm=_FakeLLM(degraded=True, fails=4)),
        _FakeAgent("scanner", llm=None),
        _FakeAgent("tech", llm=_FakeLLM(degraded=False, fails=0)),
    ]
    s = _snap(agents, {"_dlq_size": 0})
    lh = s["llm_health"]
    assert lh["degraded"] is True
    assert len(lh["degraded_agents"]) == 1
    assert lh["degraded_agents"][0]["name"] == "judge"
    assert lh["degraded_agents"][0]["consecutive_failures"] == 4


def test_data_degraded_from_collector():
    dh = {"ts": NOW - 10, "any_degraded": True,
          "degraded_symbols": ["ETH-USDT"], "last_collect_ts": NOW - 10}
    agents = [_FakeAgent("multi_data_collector", data_health=dh)]
    s = _snap(agents, {"_dlq_size": 0})
    d = s["data_health"]
    assert d["degraded"] is True
    assert d["stale"] is False
    assert d["last_collect_ago_sec"] == 10
    assert d["degraded_symbols"] == ["ETH-USDT"]


def test_data_stale_when_collect_old():
    dh = {"ts": NOW - 500, "any_degraded": False,
          "degraded_symbols": [], "last_collect_ts": NOW - 500}
    agents = [_FakeAgent("multi_data_collector", data_health=dh)]
    s = _snap(agents, {"_dlq_size": 0})
    assert s["data_health"]["stale"] is True


def test_data_health_no_collector_is_neutral():
    s = _snap([_FakeAgent("judge")], {"_dlq_size": 0})
    d = s["data_health"]
    assert d["degraded"] is False
    assert d["stale"] is False
    assert d["present"] is False


def test_data_health_never_collected_not_stale():
    dh = {"ts": None, "any_degraded": False,
          "degraded_symbols": [], "last_collect_ts": None}
    agents = [_FakeAgent("multi_data_collector", data_health=dh)]
    s = _snap(agents, {"_dlq_size": 0})
    assert s["data_health"]["stale"] is False
    assert s["data_health"]["last_collect_ago_sec"] is None
