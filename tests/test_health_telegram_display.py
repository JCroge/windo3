import pytest
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
