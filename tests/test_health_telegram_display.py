from agents.trading.telegram_notifier import TelegramNotifier


def _summary(health):
    return TelegramNotifier._format_health_summary(health)


def test_summary_all_green():
    health = {
        "loop_health": {"stalled_count": 0},
        "queue_health": {"backlogged_count": 0},
        "llm_health": {"degraded": False},
        "data_health": {"degraded": False, "stale": False},
    }
    assert _summary(health) == "─ 健康: ✓"


def test_summary_lists_only_bad_dims():
    health = {
        "loop_health": {"stalled_count": 1},
        "queue_health": {"backlogged_count": 2},
        "llm_health": {"degraded": True},
        "data_health": {"degraded": False, "stale": False},
    }
    s = _summary(health)
    assert s.startswith("─ 健康: ⚠")
    assert "1 stall" in s
    assert "2 backlog" in s
    assert "LLM降级" in s
    assert "data" not in s


def test_summary_missing_snapshot():
    assert _summary(None) == "─ 健康: ?（快照缺失）"


def test_summary_data_stale_counts():
    health = {
        "loop_health": {"stalled_count": 0},
        "queue_health": {"backlogged_count": 0},
        "llm_health": {"degraded": False},
        "data_health": {"degraded": False, "stale": True},
    }
    assert "data降级" in _summary(health)


def _detail(health):
    return TelegramNotifier._format_health_detail(health, now=2000.0)


def test_detail_all_green():
    health = {
        "ts": 1990.0,
        "loop_health": {"stalled_count": 0, "stalled": []},
        "queue_health": {"backlogged_count": 0, "max_pending": 12, "backlogged": []},
        "llm_health": {"degraded": False, "degraded_agents": []},
        "data_health": {"degraded": False, "stale": False, "last_collect_ago_sec": 23,
                        "degraded_symbols": [], "present": True},
    }
    s = _detail(health)
    assert "🩺 Agent 健康明细" in s
    assert "Loop:  ✓" in s
    assert "Queue: ✓" in s
    assert "LLM:   ✓" in s
    assert "Data:  ✓" in s


def test_detail_shows_offenders():
    health = {
        "ts": 1990.0,
        "loop_health": {"stalled_count": 1, "stalled": [{"name": "judge", "idle_sec": 73}]},
        "queue_health": {"backlogged_count": 0, "max_pending": 5, "backlogged": []},
        "llm_health": {"degraded": True, "degraded_agents": [{"name": "tech", "consecutive_failures": 4}]},
        "data_health": {"degraded": False, "stale": False, "last_collect_ago_sec": 9,
                        "degraded_symbols": [], "present": True},
    }
    s = _detail(health)
    assert "judge message-loop 空闲 73s" in s
    assert "tech 连续失败 4" in s


def test_detail_missing_snapshot():
    s = TelegramNotifier._format_health_detail(None, now=2000.0)
    assert "健康快照缺失" in s


def test_detail_shows_queue_offenders():
    health = {
        "ts": 1990.0,
        "loop_health": {"stalled_count": 0, "stalled": []},
        "queue_health": {"backlogged_count": 1, "max_pending": 300,
                         "backlogged": [{"name": "reviewer", "pending": 300}]},
        "llm_health": {"degraded": False, "degraded_agents": []},
        "data_health": {"degraded": False, "stale": False, "last_collect_ago_sec": 5,
                        "degraded_symbols": [], "present": True},
    }
    s = TelegramNotifier._format_health_detail(health, now=2000.0)
    assert "Queue: ⚠ 1 backlog" in s
    assert "reviewer pending 300" in s


def test_detail_tolerates_missing_offender_fields():
    # schema 漂移：offender 缺字段不应 crash，用 '?' 兜底
    health = {
        "ts": 1990.0,
        "loop_health": {"stalled_count": 1, "stalled": [{}]},
        "queue_health": {"backlogged_count": 0, "max_pending": 0, "backlogged": []},
        "llm_health": {"degraded": False, "degraded_agents": []},
        "data_health": {"degraded": False, "stale": False, "last_collect_ago_sec": 1,
                        "degraded_symbols": [], "present": True},
    }
    s = TelegramNotifier._format_health_detail(health, now=2000.0)
    assert "?" in s   # 缺 name/idle_sec 用 '?' 兜底，不抛


def test_detail_shows_tick_stalled():
    health = {
        "ts": 1990.0,
        "loop_health": {"stalled_count": 0, "stalled": [],
                        "tick_stalled_count": 1,
                        "tick_stalled": [{"name": "reviewer", "tick_sec": 200}]},
        "queue_health": {"backlogged_count": 0, "max_pending": 5, "backlogged": []},
        "llm_health": {"degraded": False, "degraded_agents": []},
        "data_health": {"degraded": False, "stale": False, "last_collect_ago_sec": 9,
                        "degraded_symbols": [], "present": True},
    }
    s = TelegramNotifier._format_health_detail(health, now=2000.0)
    assert "reviewer tick 卡死 200s" in s
    assert "Loop:  ⚠" in s


def test_summary_counts_tick_into_loop():
    health = {
        "loop_health": {"stalled_count": 1, "tick_stalled_count": 2},
        "queue_health": {"backlogged_count": 0},
        "llm_health": {"degraded": False},
        "data_health": {"degraded": False, "stale": False},
    }
    s = TelegramNotifier._format_health_summary(health)
    assert "3 stall" in s
