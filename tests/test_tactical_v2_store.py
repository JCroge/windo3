import json
from pathlib import Path
from types import SimpleNamespace


def _paths(tmp_path):
    return SimpleNamespace(
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
    )


def test_store_replays_events_after_snapshot(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    store = TacticalStore(_paths(tmp_path))
    store.append("intent_created", {"intent_id": "i1", "state": "ready"}, emitted_at=1)
    store.write_snapshot({"last_seq": 1, "intents": {"i1": "ready"}})
    store.append(
        "episode_terminal",
        {"intent_id": "i1", "reason": "expired"},
        emitted_at=2,
    )

    state = store.rebuild()

    assert state["intents"]["i1"]["reason"] == "expired"
    assert state["last_seq"] == 2
    assert state["integrity_failure"] is None


def test_store_rebuild_keeps_newest_epoch_after_historical_terminal(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    store = TacticalStore(_paths(tmp_path))
    key = "WLD-USDT|long"
    first = {"episode_id": "ep-1", "epoch_seq": 1, "terminal": False}
    renewed = {"episode_id": "ep-2", "epoch_seq": 2, "terminal": False}
    terminal_first = {
        "episode_id": "ep-1",
        "epoch_seq": 1,
        "terminal": True,
        "terminal_reason": "tactical_tp1",
    }
    for event_type, episode in (
        ("episode_assigned", first),
        ("episode_assigned", renewed),
        ("episode_terminal", terminal_first),
    ):
        store.append(
            event_type,
            {
                "episode_id": episode["episode_id"],
                "registry_key": key,
                "registry_state": episode,
            },
        )

    state = store.rebuild()

    assert state["episodes"][key] == renewed


def test_restart_continues_monotonic_event_sequence(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    first = TacticalStore(paths)
    assert first.append("one", {}, emitted_at=1)["seq"] == 1

    restarted = TacticalStore(paths)

    assert restarted.append("two", {}, emitted_at=2)["seq"] == 2


def test_final_partial_json_line_is_reported_and_ignored(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    store = TacticalStore(paths)
    store.append("one", {}, emitted_at=1)
    with open(paths.tactical_v2_events, "ab") as handle:
        handle.write(b'{"schema_version":2')

    state = TacticalStore(paths).rebuild()

    assert state["last_seq"] == 1
    assert state["integrity_failure"] is None
    assert state["recovery_warnings"]


def test_first_append_after_partial_tail_repairs_uncommitted_bytes(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    TacticalStore(paths).append("one", {}, emitted_at=1)
    with open(paths.tactical_v2_events, "ab") as handle:
        handle.write(b'{"schema_version":2')

    restarted = TacticalStore(paths)
    restarted.append("two", {}, emitted_at=2)
    state = restarted.rebuild()

    assert state["last_seq"] == 2
    assert state["integrity_failure"] is None
    assert [row["event_type"] for row in restarted.read_events()] == ["one", "two"]


def test_malformed_committed_history_activates_integrity_failure(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    store = TacticalStore(paths)
    store.append("one", {}, emitted_at=1)
    with open(paths.tactical_v2_events, "ab") as handle:
        handle.write(b"not-json\n")

    state = TacticalStore(paths).rebuild()

    assert state["last_seq"] == 1
    assert state["integrity_failure"]["reason"] == "malformed_committed_event"


def test_invalid_snapshot_falls_back_to_authoritative_ledger(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    store = TacticalStore(paths)
    store.append("intent_created", {"intent_id": "i1"}, emitted_at=1)
    Path(paths.tactical_v2_state).write_text("not-json", encoding="utf-8")

    state = TacticalStore(paths).rebuild()

    assert state["last_seq"] == 1
    assert "i1" in state["intents"]
    assert state["recovery_warnings"]


def test_event_rows_have_required_durable_envelope(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    event = TacticalStore(paths).append("intent_created", {"intent_id": "i1"}, emitted_at=7)
    persisted = json.loads(Path(paths.tactical_v2_events).read_text(encoding="utf-8"))

    assert persisted == event
    assert set(persisted) == {
        "schema_version",
        "seq",
        "event_id",
        "event_type",
        "emitted_at",
        "data",
    }
