import pytest
from utils.health_snapshot import build_health_snapshot

NOW = 1_000_000.0
CFG = dict(stall_timeout_sec=60, backlog_warn_pending=200, data_stale_timeout_sec=180,
           tick_stall_timeout_sec=120)
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
    def __init__(self, name, alive_ts=NOW, llm=None, data_health=None,
                 tick_enter_ts=0.0, tick_exit_ts=0.0):
        self.name = name
        self._last_alive_ts = alive_ts
        self._tick_enter_ts = tick_enter_ts
        self._tick_exit_ts = tick_exit_ts
        self.llm = llm
        if data_health is not None:
            self._latest_data_health = data_health


def _snap(agents, bus_metrics, base=None):
    return build_health_snapshot(
        agents, bus_metrics, NOW,
        stall_timeout_sec=CFG["stall_timeout_sec"],
        backlog_warn_pending=CFG["backlog_warn_pending"],
        data_stale_timeout_sec=CFG["data_stale_timeout_sec"],
        tick_stall_timeout_sec=CFG["tick_stall_timeout_sec"],
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


def test_loop_stall_exact_boundary_not_stalled():
    # idle == stall_timeout_sec (60) → 不算 stall（严格 > 才算）
    agents = [_FakeAgent("edge", alive_ts=NOW - 60)]
    s = _snap(agents, {"_dlq_size": 0})
    assert s["loop_health"]["stalled_count"] == 0
    # 略超阈值才算（61 > 60）
    agents2 = [_FakeAgent("over", alive_ts=NOW - 61)]
    s2 = _snap(agents2, {"_dlq_size": 0})
    assert s2["loop_health"]["stalled_count"] == 1


def test_queue_backlog_exact_boundary_not_flagged():
    # pending == backlog_warn_pending (200) → 不算 backlog（严格 >）
    s = _snap([_FakeAgent("judge")], {"reviewer": {"pending": 200}, "_dlq_size": 0})
    assert s["queue_health"]["backlogged_count"] == 0
    assert s["queue_health"]["max_pending"] == 200
    # 201 才算
    s2 = _snap([_FakeAgent("judge")], {"reviewer": {"pending": 201}, "_dlq_size": 0})
    assert s2["queue_health"]["backlogged_count"] == 1


def test_loop_multiple_stalled_agents():
    agents = [
        _FakeAgent("a", alive_ts=NOW - 200),
        _FakeAgent("b", alive_ts=NOW - 100),
        _FakeAgent("healthy", alive_ts=NOW - 5),
    ]
    s = _snap(agents, {"_dlq_size": 0})
    lh = s["loop_health"]
    assert lh["stalled_count"] == 2
    names = {x["name"] for x in lh["stalled"]}
    assert names == {"a", "b"}


def test_health_thresholds_in_defaults_and_hard_limits():
    from utils.config_loader import DEFAULTS, HARD_LIMITS
    assert DEFAULTS["agent_stall_timeout_sec"] == 60
    assert DEFAULTS["queue_backlog_warn_pending"] == 200
    assert DEFAULTS["data_stale_timeout_sec"] == 180
    assert HARD_LIMITS["agent_stall_timeout_sec"] == (10, 3600)
    assert HARD_LIMITS["queue_backlog_warn_pending"] == (50, 1000)
    assert HARD_LIMITS["data_stale_timeout_sec"] == (30, 3600)


def test_tick_stall_detected():
    agents = [_FakeAgent("stuck", tick_enter_ts=NOW - 200, tick_exit_ts=NOW - 260)]
    s = _snap(agents, {"_dlq_size": 0})
    lh = s["loop_health"]
    assert lh["tick_stalled_count"] == 1
    assert lh["tick_stalled"][0]["name"] == "stuck"
    assert lh["tick_stalled"][0]["tick_sec"] == 200


def test_tick_stall_exact_boundary_not_stalled():
    a = [_FakeAgent("edge", tick_enter_ts=NOW - 120, tick_exit_ts=NOW - 130)]
    assert _snap(a, {"_dlq_size": 0})["loop_health"]["tick_stalled_count"] == 0
    a2 = [_FakeAgent("over", tick_enter_ts=NOW - 121, tick_exit_ts=NOW - 130)]
    assert _snap(a2, {"_dlq_size": 0})["loop_health"]["tick_stalled_count"] == 1


def test_tick_mid_within_budget_not_stalled():
    a = [_FakeAgent("busy", tick_enter_ts=NOW - 30, tick_exit_ts=NOW - 90)]
    assert _snap(a, {"_dlq_size": 0})["loop_health"]["tick_stalled_count"] == 0


def test_tick_between_ticks_not_stalled():
    a = [_FakeAgent("idle", tick_enter_ts=NOW - 500, tick_exit_ts=NOW - 100)]
    assert _snap(a, {"_dlq_size": 0})["loop_health"]["tick_stalled_count"] == 0


def test_tick_unstarted_skipped():
    a = [_FakeAgent("fresh", tick_enter_ts=0.0, tick_exit_ts=0.0)]
    assert _snap(a, {"_dlq_size": 0})["loop_health"]["tick_stalled_count"] == 0
