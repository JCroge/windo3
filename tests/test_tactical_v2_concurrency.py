import asyncio
import json
import multiprocessing as mp
import queue as queue_module
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts.replay_tactical_v2_admission import _seed_initial_episode, _validate_fixture
from utils.tactical_v2.store import TacticalStore


FIXTURE = Path(__file__).with_name("fixtures") / "tactical_v2_shadow_admission_window.json"


def _join_processes(processes, *, timeout):
    deadline = time.monotonic() + timeout
    for process in processes:
        process.join(timeout=max(0.0, deadline - time.monotonic()))

    timed_out = [process for process in processes if process.is_alive()]
    if timed_out:
        for process in timed_out:
            process.terminate()
        for process in timed_out:
            process.join(timeout=1)
        survivors = [process for process in timed_out if process.is_alive()]
        for process in survivors:
            process.kill()
            process.join(timeout=1)
        assert not any(process.is_alive() for process in survivors), (
            "failed to reap timed-out child processes"
        )
        raise AssertionError(f"{len(timed_out)} child process(es) timed out")

    exit_codes = [process.exitcode for process in processes]
    assert exit_codes == [0] * len(processes), (
        f"child process exit codes were {exit_codes}"
    )


def _collect_results(result_queue, *, count, timeout):
    deadline = time.monotonic() + timeout
    results = []
    while len(results) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            results.append(result_queue.get(timeout=remaining))
        except queue_module.Empty:
            break
    missing = count - len(results)
    assert missing == 0, f"missing {missing} child result(s)"
    return results


class _StuckProcess:
    def __init__(self):
        self.exitcode = None
        self.join_calls = []
        self.terminated = False

    def join(self, timeout=None):
        self.join_calls.append(timeout)

    def is_alive(self):
        return not self.terminated

    def terminate(self):
        self.terminated = True
        self.exitcode = -15


def test_join_timeout_terminates_and_reaps_child():
    process = _StuckProcess()

    with pytest.raises(AssertionError, match="timed out"):
        _join_processes([process], timeout=0)

    assert process.terminated is True
    assert len(process.join_calls) == 2
    assert process.is_alive() is False


def test_queue_collection_fails_bounded_when_child_result_is_missing():
    result_queue = queue_module.Queue()

    with pytest.raises(AssertionError, match="missing 1 child result"):
        _collect_results(result_queue, count=1, timeout=0.01)


def _paths(root):
    return SimpleNamespace(
        namespace="live",
        tactical_v2_events=str(root / "events.jsonl"),
        tactical_v2_state=str(root / "state.json"),
        tactical_v2_status=str(root / "status.json"),
    )


def _controller_worker(root_str, raw, message_id, now, barrier, queue):
    from utils.tactical_v2.controller import TacticalV2Controller

    root = Path(root_str)
    barrier.wait()
    controller = TacticalV2Controller(
        executor=SimpleNamespace(
            positions={},
            create_order=MagicMock(),
            cancel_order=MagicMock(),
            close_position=MagicMock(),
        ),
        config={"tactical_v2_mode": "shadow"},
        paths=_paths(root),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        publish=None,
        now_fn=lambda: now,
    )
    result = asyncio.run(
        controller.handle_candidate(raw, now=now, message_id=message_id)
    )
    queue.put({"accepted": result.accepted, "reason": result.reason})
    queue.close()


def test_same_candidate_concurrent_processes_have_one_authoritative_accept(tmp_path):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    initial_state, rows = _validate_fixture(fixture)
    paths = _paths(tmp_path)
    _seed_initial_episode(TacticalStore(paths), initial_state)
    row = rows[0]

    context = mp.get_context("fork")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(
            target=_controller_worker,
            args=(
                tmp_path.as_posix(),
                row.raw,
                row.msg_id,
                row.journal_timestamp,
                barrier,
                queue,
            ),
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        _join_processes(processes, timeout=10)
        outcomes = _collect_results(queue, count=len(processes), timeout=2)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
        queue.close()
        queue.join_thread()

    assert [outcome["accepted"] for outcome in outcomes] == [True, True]
    assert [outcome["reason"] for outcome in outcomes] == ["accepted", "accepted"]

    events = [
        json.loads(line)
        for line in Path(paths.tactical_v2_events).read_text(encoding="utf-8").splitlines()
    ]
    assert len([event for event in events if event["event_type"] == "intent_created"]) == 1
    assert len([event for event in events if event["event_type"] == "candidate_handled"]) == 1
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert TacticalStore(paths).rebuild()["integrity_failure"] is None
