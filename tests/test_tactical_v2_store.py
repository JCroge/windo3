import json
import multiprocessing as mp
from pathlib import Path
from types import SimpleNamespace

import pytest


def _paths(tmp_path):
    return SimpleNamespace(
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
    )


def _append_from_process(events_path, state_path, barrier, count, queue):
    from utils.tactical_v2.store import TacticalStore

    paths = SimpleNamespace(
        tactical_v2_events=events_path,
        tactical_v2_state=state_path,
    )
    barrier.wait()
    store = TacticalStore(paths)
    for index in range(count):
        store.append("concurrent_event", {"worker": index})
    queue.put("ok")


def test_store_serializes_sequence_allocation_across_processes(tmp_path):
    paths = _paths(tmp_path)
    context = mp.get_context("fork")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(
            target=_append_from_process,
            args=(paths.tactical_v2_events, paths.tactical_v2_state, barrier, 3, queue),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    assert [queue.get(timeout=1) for _ in processes] == ["ok", "ok"]
    events = json.loads("[" + ",".join(
        line for line in Path(paths.tactical_v2_events).read_text().splitlines()
    ) + "]")
    assert [event["seq"] for event in events] == list(range(1, 7))
    assert __import__("utils.tactical_v2.store", fromlist=["TacticalStore"]).TacticalStore(
        paths
    ).rebuild()["integrity_failure"] is None


def test_post_write_fsync_failure_rolls_back_unconfirmed_event(tmp_path, monkeypatch):
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    store = TacticalStore(paths)
    failed_once = False

    def fail_after_write_once(handle):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("injected fsync failure")

    monkeypatch.setattr(store, "_sync_event_handle", fail_after_write_once)
    try:
        store.append("one", {}, emitted_at=1)
    except OSError as exc:
        assert str(exc) == "injected fsync failure"
    else:
        raise AssertionError("fsync failure did not propagate")

    assert store.append("two", {}, emitted_at=2)["seq"] == 1
    assert [event["seq"] for event in store.read_events()] == [1]
    assert TacticalStore(paths).rebuild()["integrity_failure"] is None


def test_append_rollback_failure_halts_further_writes(tmp_path, monkeypatch):
    from utils.tactical_v2.store import TacticalStore, TacticalStoreIntegrityError

    store = TacticalStore(_paths(tmp_path))
    monkeypatch.setattr(
        store,
        "_sync_event_handle",
        lambda handle: (_ for _ in ()).throw(OSError("injected fsync failure 1")),
    )
    monkeypatch.setattr(
        store,
        "_sync_rollback_handle",
        lambda handle: (_ for _ in ()).throw(OSError("injected fsync failure 2")),
    )

    with pytest.raises(OSError, match="injected fsync failure 1"):
        store.append("one", {}, emitted_at=1)
    with pytest.raises(TacticalStoreIntegrityError):
        store.append("two", {}, emitted_at=2)


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


def test_candidate_handled_does_not_pollute_rebuilt_intent_state(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    store = TacticalStore(_paths(tmp_path))
    created = {
        "intent_id": "intent-1",
        "episode_id": "episode-1",
        "intent": {"candidate_id": "candidate-1"},
        "state": "ready_for_quote",
        "lane": "shadow",
    }
    store.append("intent_created", created, emitted_at=1)
    store.append(
        "candidate_handled",
        {
            "candidate_id": "candidate-1",
            "source_shadow_id": "shadow-1",
            "message_id": "message-1",
            "symbol": "WLD-USDT",
            "side": "long",
            "accepted": True,
            "reason": "accepted",
            "episode_id": "episode-1",
            "intent_id": "intent-1",
            "evaluated_at": 2.0,
            "replayed": False,
            "payload_hash": "a" * 64,
        },
        emitted_at=2,
    )

    state = store.rebuild()

    assert state["intents"]["intent-1"] == created


def test_candidate_handled_without_prior_intent_does_not_synthesize_intent(tmp_path):
    from utils.tactical_v2.store import TacticalStore

    store = TacticalStore(_paths(tmp_path))
    store.append(
        "candidate_handled",
        {
            "candidate_id": "candidate-unknown",
            "source_shadow_id": "shadow-unknown",
            "message_id": "message-unknown",
            "symbol": "WLD-USDT",
            "side": "long",
            "accepted": True,
            "reason": "accepted",
            "episode_id": "episode-unknown",
            "intent_id": "intent-unknown",
            "evaluated_at": 1.0,
            "replayed": False,
            "payload_hash": "b" * 64,
        },
        emitted_at=1,
    )

    state = store.rebuild()

    assert state["intents"] == {}


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
