import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_candidate_topic_is_high_priority_important_and_journaled():
    from agents.message_bus import (
        PRIORITY_HIGH,
        _IMPORTANT_TOPICS,
        get_priority,
    )
    from utils.event_journal import CRITICAL_TOPICS

    assert get_priority("tactical_candidate.v2") == PRIORITY_HIGH
    assert "tactical_candidate.v2" in _IMPORTANT_TOPICS
    assert "tactical_candidate.v2" in CRITICAL_TOPICS


@pytest.mark.asyncio
async def test_delivered_candidate_preserves_journal_message_id(monkeypatch):
    from agents.message_bus import MessageBus

    journal = SimpleNamespace(
        should_record=lambda topic: True,
        append=MagicMock(),
    )
    monkeypatch.setattr("agents.message_bus.get_event_journal", lambda: journal)
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register("executor", ["tactical_candidate.v2"])

    await bus.publish(
        "judge",
        "tactical_candidate.v2",
        {"candidate_id": "cand-1", "namespace": "testnet"},
    )
    delivered = await bus.receive("executor", timeout=0.1)

    assert delivered["msg_id"]
    assert journal.append.call_args.kwargs["msg_id"] == delivered["msg_id"]


def test_journal_replay_filters_namespace_and_original_ttl(tmp_path):
    from utils.event_journal import EventJournal

    journal = EventJournal(str(tmp_path))
    now = time.time()
    journal.append(
        "tactical_candidate.v2",
        {"candidate_id": "fresh", "namespace": "testnet", "created_at": now - 30},
        msg_id="msg-fresh",
    )
    journal.append(
        "tactical_candidate.v2",
        {"candidate_id": "cross", "namespace": "live", "created_at": now - 30},
        msg_id="msg-cross",
    )
    journal.append(
        "tactical_candidate.v2",
        {"candidate_id": "stale", "namespace": "testnet", "created_at": now - 901},
        msg_id="msg-stale",
    )
    journal.close()

    replayed = journal.replay_messages(
        "tactical_candidate.v2",
        namespace="testnet",
        now=now,
        max_age_seconds=900,
    )

    assert [row["payload"]["candidate_id"] for row in replayed] == ["fresh"]
    assert replayed[0]["msg_id"] == "msg-fresh"
    assert replayed[0]["type"] == "tactical_candidate.v2"


def test_journal_replay_scans_past_non_matching_rows_before_limit(tmp_path):
    from utils.event_journal import EventJournal

    journal = EventJournal(str(tmp_path))
    now = time.time()
    for index in range(1000):
        journal.append(
            "tactical_candidate.v2",
            {
                "candidate_id": f"old-{index}",
                "namespace": "other",
                "created_at": now - 30,
            },
            msg_id=f"old-{index}",
        )
    journal.append(
        "tactical_candidate.v2",
        {"candidate_id": "fresh", "namespace": "testnet", "created_at": now - 30},
        msg_id="msg-fresh",
    )
    journal.close()

    replayed = journal.replay_messages(
        "tactical_candidate.v2",
        namespace="testnet",
        now=now,
        max_age_seconds=900,
    )

    assert [row["payload"]["candidate_id"] for row in replayed] == ["fresh"]


def test_journal_replay_stops_reading_once_message_limit_is_satisfied(
    tmp_path,
    monkeypatch,
):
    import utils.event_journal as event_journal

    journal = event_journal.EventJournal(str(tmp_path))
    now = time.time()
    for index in range(2):
        journal.append(
            "tactical_candidate.v2",
            {
                "candidate_id": f"fresh-{index}",
                "namespace": "testnet",
                "created_at": now - 30,
            },
            msg_id=f"msg-fresh-{index}",
        )
    journal.close()
    real_loads = event_journal.json.loads
    parsed_rows = 0

    def counting_loads(value):
        nonlocal parsed_rows
        parsed_rows += 1
        return real_loads(value)

    monkeypatch.setattr(event_journal.json, "loads", counting_loads)

    replayed = journal.replay_messages(
        "tactical_candidate.v2",
        namespace="testnet",
        now=now,
        max_age_seconds=900,
        limit=1,
    )

    assert [row["payload"]["candidate_id"] for row in replayed] == ["fresh-0"]
    assert parsed_rows == 1


def test_journal_replay_does_not_parse_files_older_than_message_ttl(
    tmp_path,
    monkeypatch,
):
    import utils.event_journal as event_journal

    journal = event_journal.EventJournal(str(tmp_path))
    now = time.time()
    old_entry = {
        "topic": "tactical_candidate.v2",
        "msg_id": "msg-old",
        "timestamp": now - 86400,
        "payload": {
            "candidate_id": "old",
            "namespace": "testnet",
            "created_at": now - 86400,
        },
    }
    (tmp_path / "events_20000101.jsonl").write_text(
        event_journal.json.dumps(old_entry) + "\n",
        encoding="utf-8",
    )
    journal.append(
        "tactical_candidate.v2",
        {"candidate_id": "fresh", "namespace": "testnet", "created_at": now - 30},
        msg_id="msg-fresh",
    )
    journal.close()
    real_loads = event_journal.json.loads
    parsed_rows = 0

    def counting_loads(value):
        nonlocal parsed_rows
        parsed_rows += 1
        return real_loads(value)

    monkeypatch.setattr(event_journal.json, "loads", counting_loads)

    replayed = journal.replay_messages(
        "tactical_candidate.v2",
        namespace="testnet",
        now=now,
        max_age_seconds=900,
    )

    assert [row["payload"]["candidate_id"] for row in replayed] == ["fresh"]
    assert parsed_rows == 1


@pytest.mark.asyncio
async def test_judge_routes_exact_shadow_profile_to_candidate_and_main_hold(monkeypatch):
    from test_tactical_track_classifier import base_plan, make_judge, strong_short_tech

    judge = make_judge()
    judge._tactical_v2_mode = "shadow"
    judge.config = {"tactical_v2_mode": "shadow"}
    judge._record_rejected_plan = lambda *args, **kwargs: "shadow-7"
    published = []

    async def capture(topic, payload, **kwargs):
        published.append((topic, payload, kwargs))

    judge.publish = capture
    tech = strong_short_tech()
    tech["entry_timing"].update({
        "tf_15m_closed_bar_ts": 123456,
        "tf_15m_structure_token": "break_down:abc",
    })
    shadow_decision = {
        "track": "shadow_only",
        "exit_profile": "none",
        "reason": "main_quality_failed:weak_volume_oi:tactical_shadow_only",
    }
    shadow_plan = base_plan()
    shadow_plan["size_usdt"] = 8.57
    shadow_plan["stop_loss"] = 0.4
    exact_profile = judge._apply_tactical_shadow_profile(
        shadow_plan,
        tech,
        shadow_decision,
    )

    candidate = await judge._route_tactical_v2_candidate(
        "WLD-USDT",
        "open_short",
        exact_profile,
        tech,
        shadow_decision,
        score=-70,
        confidence=70,
        attribution={},
        created_at=1000.0,
    )

    candidate_messages = [payload for topic, payload, _ in published if topic == "tactical_candidate.v2"]
    decisions = [payload for topic, payload, _ in published if topic == "trade_decision"]
    assert candidate == candidate_messages[0]
    assert candidate["entry_ref"] == exact_profile["entry_ref"]
    assert candidate["stop_loss"] == exact_profile["stop_loss"]
    assert candidate["take_profit"] == exact_profile["take_profit"][0]
    assert candidate["source_shadow_id"] == "shadow-7"
    assert candidate["tf_15m_structure_token"] == "break_down:abc"
    assert candidate["margin_usdt"] == 100.0
    assert decisions[-1]["action"] == "hold"
    assert not any(decision.get("action") == "open_short" for decision in decisions)


@pytest.mark.asyncio
async def test_full_tp1_economics_rejects_plan_that_only_ladder_rr_makes_attractive():
    from test_tactical_track_classifier import base_plan, make_judge, strong_short_tech

    judge = make_judge()
    judge._tactical_v2_mode = "shadow"
    judge.config = {"tactical_v2_mode": "shadow"}
    judge._record_rejected_plan = lambda *args, **kwargs: "shadow-cost-fail"
    published = []

    async def capture(topic, payload, **kwargs):
        published.append((topic, payload))

    judge.publish = capture
    plan = base_plan()
    plan.update({
        "effective_risk_reward_ratio": 2.2,
        "effective_rr_ladder": 2.2,
        "take_profit": [0.3847, 0.36, 0.35],
    })
    track_decision = {
        "track": "shadow_only",
        "exit_profile": "none",
        "reason": "main_quality_failed:weak_volume_oi:tactical_shadow_only",
    }
    exact_profile = judge._apply_tactical_shadow_profile(
        plan,
        strong_short_tech(),
        track_decision,
    )

    candidate = await judge._route_tactical_v2_candidate(
        "WLD-USDT",
        "open_short",
        exact_profile,
        strong_short_tech(),
        track_decision,
        score=-70,
        confidence=70,
        attribution={},
        created_at=1000.0,
    )

    assert candidate is None
    assert not any(topic == "tactical_candidate.v2" for topic, _ in published)
    hold = next(payload for topic, payload in published if topic == "trade_decision")
    assert hold["action"] == "hold"
    assert "tactical" in hold["reasoning"]


@pytest.mark.asyncio
async def test_full_tp1_economics_requires_configured_tactical_rr():
    from test_tactical_track_classifier import base_plan, make_judge, strong_short_tech

    judge = make_judge()
    judge._tactical_v2_mode = "shadow"
    judge._tactical_min_rr_for_track = 0.95
    judge._tactical_min_ev_for_track = -1.0
    judge.config = {"tactical_v2_mode": "shadow"}
    recorded = []

    def record_rejected(*args, **kwargs):
        recorded.append(args[2])
        return "shadow-low-rr"

    judge._record_rejected_plan = record_rejected
    published = []

    async def capture(topic, payload, **kwargs):
        published.append((topic, payload))

    judge.publish = capture
    plan = base_plan()
    plan["size_usdt"] = 8.57
    track_decision = {
        "track": "shadow_only",
        "exit_profile": "none",
        "reason": "main_quality_failed:weak_volume_oi:tactical_shadow_only",
    }
    exact_profile = judge._apply_tactical_shadow_profile(
        plan,
        strong_short_tech(),
        track_decision,
    )

    candidate = await judge._route_tactical_v2_candidate(
        "WLD-USDT",
        "open_short",
        exact_profile,
        strong_short_tech(),
        track_decision,
        score=-70,
        confidence=70,
        attribution={},
        created_at=1000.0,
    )

    assert candidate is None
    assert not any(topic == "tactical_candidate.v2" for topic, _ in published)
    assert recorded[-1]["tactical_rr_gate"] == "fail"
    assert recorded[-1]["tactical_track_gate"] == "fail"
    assert "min_rr" in recorded[-1]["tactical_gate_failed"]
