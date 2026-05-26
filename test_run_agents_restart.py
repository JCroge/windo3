import os

import run_agents


def test_main_exec_relaunches_when_restart_flag_present(monkeypatch):
    starts = []
    relaunches = []

    class DummyOrchestrator:
        def start(self):
            starts.append("started")

    monkeypatch.setattr(run_agents, "Orchestrator", DummyOrchestrator)
    monkeypatch.setattr(run_agents, "_restart_process", lambda argv=None, delay_seconds=3: relaunches.append((argv, delay_seconds)))

    os.makedirs("data", exist_ok=True)
    with open(run_agents.RESTART_FLAG_FILE, "w", encoding="utf-8") as fh:
        fh.write("restart")

    run_agents.main(argv=["run_agents.py", "--paper"])

    assert starts == ["started"]
    assert relaunches == [(["run_agents.py", "--paper"], 3)]
    assert not os.path.exists(run_agents.RESTART_FLAG_FILE)


def test_main_exits_without_restart_when_no_flag(monkeypatch):
    starts = []
    relaunches = []

    class DummyOrchestrator:
        def start(self):
            starts.append("started")

    monkeypatch.setattr(run_agents, "Orchestrator", DummyOrchestrator)
    monkeypatch.setattr(run_agents, "_restart_process", lambda argv=None, delay_seconds=3: relaunches.append((argv, delay_seconds)))

    run_agents.main(argv=["run_agents.py"])

    assert starts == ["started"]
    assert relaunches == []


def test_restart_process_execs_current_python_with_script_path(monkeypatch):
    exec_calls = []
    sleeps = []
    shutdowns = []

    monkeypatch.setattr(run_agents.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(run_agents.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(run_agents.logging, "shutdown", lambda: shutdowns.append(True))

    def fake_execv(path, argv):
        exec_calls.append((path, argv))
        raise SystemExit(0)

    monkeypatch.setattr(run_agents.os, "execv", fake_execv)

    argv = ["scripts/run_agents.py", "--paper", "--verbose"]
    try:
        run_agents._restart_process(argv=argv, delay_seconds=5)
    except SystemExit:
        pass

    assert sleeps == [5]
    assert shutdowns == [True]
    assert exec_calls == [
        (
            "/usr/bin/python3",
            ["/usr/bin/python3", os.path.abspath("scripts/run_agents.py"), "--paper", "--verbose"],
        )
    ]
