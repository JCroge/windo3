import json
import pytest
from agents.message_bus import MessageBus


@pytest.fixture(autouse=True)
def _reset_bus():
    MessageBus._instance = None
    yield
    MessageBus._instance = None


def _make_orch(tmp_path, monkeypatch):
    """构造一个最小可测的 Orchestrator，agent_health 路径指向 tmp。

    Orchestrator.__init__ 只赋字段，不连交易所不起 agent，可直接 Orchestrator(config={})。
    _write_agent_health 内部做 'from utils.state_paths import get_state_paths'，
    monkeypatch 需 patch 模块属性 utils.state_paths.get_state_paths 即可生效。
    """
    from utils import state_paths
    from agents.orchestrator import Orchestrator

    class _P:
        agent_health = str(tmp_path / "agent_health.json")
    monkeypatch.setattr(state_paths, "get_state_paths", lambda *a, **k: _P())

    # config={} 是 falsy，会触发 _default_config() → load_config() → 凭证校验失败。
    # 传一个带占位键的 dict 让 `config or self._default_config()` 走左分支。
    orch = Orchestrator(config={"_test": True})
    return orch


def test_write_agent_health_includes_four_dimensions(tmp_path, monkeypatch):
    orch = _make_orch(tmp_path, monkeypatch)
    orch._write_agent_health()
    with open(tmp_path / "agent_health.json") as f:
        snap = json.load(f)
    for k in ("ts", "agents_registered", "tasks_alive", "tasks_failed",
              "halted_symbols", "bus_dlq_size"):
        assert k in snap
    assert "loop_health" in snap
    assert "queue_health" in snap
    assert "llm_health" in snap
    assert "data_health" in snap
    assert snap["llm_health"]["degraded"] is False


class _CapturingBus:
    def __init__(self):
        self.published = []

    async def publish(self, sender, topic, payload, to, **kwargs):
        self.published.append((topic, payload))


def _snap_with(loop=False, queue=False, llm=False, data=False):
    return {
        "loop_health": {"stalled_count": 1 if loop else 0,
                        "stalled": [{"name": "judge", "idle_sec": 99}] if loop else []},
        "queue_health": {"backlogged_count": 1 if queue else 0,
                         "max_pending": 300 if queue else 0,
                         "backlogged": [{"name": "reviewer", "pending": 300}] if queue else []},
        "llm_health": {"degraded": llm,
                       "degraded_agents": [{"name": "tech", "consecutive_failures": 4}] if llm else []},
        "data_health": {"degraded": data, "stale": False,
                        "degraded_symbols": ["ETH-USDT"] if data else [], "present": True},
    }


@pytest.mark.asyncio
async def test_alert_fires_once_on_edge_then_silent(tmp_path, monkeypatch):
    orch = _make_orch(tmp_path, monkeypatch)
    bus = _CapturingBus()
    orch.bus = bus
    await orch._maybe_alert_health_transitions(_snap_with(llm=True))
    await orch._maybe_alert_health_transitions(_snap_with(llm=True))
    types = [p["type"] for _, p in bus.published]
    assert types.count("health_llm") == 1
    assert "health_llm_recovered" not in types


@pytest.mark.asyncio
async def test_alert_recovery_fires_once(tmp_path, monkeypatch):
    orch = _make_orch(tmp_path, monkeypatch)
    bus = _CapturingBus()
    orch.bus = bus
    await orch._maybe_alert_health_transitions(_snap_with(llm=True))
    await orch._maybe_alert_health_transitions(_snap_with(llm=False))
    types = [p["type"] for _, p in bus.published]
    assert types == ["health_llm", "health_llm_recovered"]


@pytest.mark.asyncio
async def test_dimensions_independent(tmp_path, monkeypatch):
    orch = _make_orch(tmp_path, monkeypatch)
    bus = _CapturingBus()
    orch.bus = bus
    await orch._maybe_alert_health_transitions(_snap_with(loop=True, data=True))
    types = sorted(p["type"] for _, p in bus.published)
    assert types == ["health_data", "health_loop"]
