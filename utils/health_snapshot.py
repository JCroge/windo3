"""Agent Health Supervisor — 健康快照纯函数 builder (#95)。

observability-only：把 agents 实例字段 + bus 指标聚合成 snapshot dict。
无 IO、无副作用、不改 agent 状态、不调用 bus；外部状态由调用方取好传入，
故可用假 stub 单测。严禁任何 gate/veto/halt 读取本快照（与 provenance 红线一致）。
"""

COLLECTOR_NAME = "multi_data_collector"


def _loop_health(agents, now, stall_timeout_sec):
    stalled = []
    for a in agents:
        ts = getattr(a, "_last_alive_ts", 0.0) or 0.0
        if ts <= 0.0:
            continue
        idle = now - ts
        if idle > stall_timeout_sec:
            stalled.append({"name": a.name, "idle_sec": int(idle)})
    return {"stalled_count": len(stalled), "stalled": stalled}


def _queue_health(bus_metrics, backlog_warn_pending):
    backlogged = []
    max_pending = 0
    for name, m in bus_metrics.items():
        if name == "_dlq_size" or not isinstance(m, dict):
            continue
        pending = int(m.get("pending", 0) or 0)
        if pending > max_pending:
            max_pending = pending
        if pending > backlog_warn_pending:
            backlogged.append({"name": name, "pending": pending})
    return {"backlogged_count": len(backlogged),
            "max_pending": max_pending,
            "backlogged": backlogged}


def _llm_health(agents):
    degraded_agents = []
    for a in agents:
        llm = getattr(a, "llm", None)
        if llm is not None and getattr(llm, "degraded", False):
            degraded_agents.append({
                "name": a.name,
                "consecutive_failures": getattr(llm, "consecutive_failures", None),
            })
    return {"degraded": len(degraded_agents) > 0,
            "degraded_agents": degraded_agents}


def _data_health(agents, now, data_stale_timeout_sec):
    collector = next((a for a in agents if a.name == COLLECTOR_NAME), None)
    if collector is None or not hasattr(collector, "_latest_data_health"):
        return {"degraded": False, "stale": False,
                "last_collect_ago_sec": None, "degraded_symbols": [],
                "present": False}
    h = collector._latest_data_health or {}
    last_ts = h.get("last_collect_ts")
    if last_ts is None:
        ago = None
        stale = False
    else:
        ago = int(now - last_ts)
        stale = ago > data_stale_timeout_sec
    return {"degraded": bool(h.get("any_degraded", False)),
            "stale": stale,
            "last_collect_ago_sec": ago,
            "degraded_symbols": list(h.get("degraded_symbols", [])),
            "present": True}


def build_health_snapshot(agents, bus_metrics, now, *,
                          stall_timeout_sec, backlog_warn_pending,
                          data_stale_timeout_sec, base_stats):
    """聚合健康快照。

    agents: 可迭代 BaseAgent（research + trading），只读其实例字段。
    bus_metrics: MessageBus.get_metrics() 返回的 dict（含 '_dlq_size' 键）。
    now: float 时间戳（调用方传入，便于测试）。
    base_stats: Orchestrator 现成统计（agents_registered/tasks_alive/tasks_failed/
                halted_symbols/bus_dlq_size），原样透传保持向后兼容。
    """
    agents = list(agents)
    snapshot = dict(base_stats)
    snapshot["ts"] = now
    snapshot["loop_health"] = _loop_health(agents, now, stall_timeout_sec)
    snapshot["queue_health"] = _queue_health(bus_metrics, backlog_warn_pending)
    snapshot["llm_health"] = _llm_health(agents)
    snapshot["data_health"] = _data_health(agents, now, data_stale_timeout_sec)
    return snapshot
