# Restore Shadow Tactical Sidecar Mirror Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the Shadow Tactical live sidecar to mirror Tactical shadow records instead of requiring `tactical_track_gate=pass`.

**Architecture:** Keep the sidecar as a separate process that tails `data/rejected_signal_events.jsonl`. The event filter should identify Tactical shadow records by Tactical identity (`track=tactical` or `exit_profile=tactical_v1`) and retain gate fields as audit metadata only. Mechanical safety remains unchanged: active cap, same-symbol account exposure guard, OKX posMode checks, entry drift, balance/min-size/orderbook checks, attached SL verification, and owner isolation.

**Tech Stack:** Python, pytest, existing `utils.shadow_tactical_live`, `scripts/shadow_tactical_live_sidecar.py`, OKX via existing executor.

---

### Task 1: Lock the Mirror Semantics With Tests

**Files:**
- Modify: `tests/test_shadow_tactical_live_core.py`
- Modify: `tests/test_shadow_tactical_live_cli.py`

- [ ] **Step 1: Write failing core test**

Replace the current strict-gate test with assertions that `gate=fail` does not block Tactical identity:

```python
def test_tactical_shadow_event_accepts_shadow_mirror_records_without_gate_pass():
    assert is_tactical_shadow_event(
        _event(_tactical_record(track="tactical", tactical_track_gate="pass"))
    )
    assert is_tactical_shadow_event(
        _event(_tactical_record(track="tactical", tactical_track_gate="fail"))
    )
    assert is_tactical_shadow_event(
        _event(_tactical_record(track="shadow_only", tactical_track_gate="fail"))
    )
    assert is_tactical_shadow_event(
        _event(
            _tactical_record(
                track="main",
                exit_profile="tactical_v1",
                tactical_track_gate="fail",
            )
        )
    )
    assert not is_tactical_shadow_event(
        _event(_tactical_record(track="main", exit_profile="trend_runner"))
    )
    assert not is_tactical_shadow_event(
        {"event_type": "shadow_tp", "record": _tactical_record()}
    )
```

- [ ] **Step 2: Write failing CLI dry-run test**

Add a dry-run test proving the sidecar processes `track=shadow_only`, `exit_profile=tactical_v1`, `tactical_track_gate=fail`:

```python
def test_run_dry_run_processes_shadow_only_tactical_gate_fail_event(tmp_path):
    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    rec = {
        "id": "shadow-gate-fail",
        "symbol": "DOGE-USDT-SWAP",
        "side": "short",
        "entry_price": 0.072,
        "stop_loss": 0.073,
        "take_profit": [0.071],
        "leverage": 20,
        "track": "shadow_only",
        "exit_profile": "tactical_v1",
        "tactical_track_gate": "fail",
        "reject_reason": "main_quality_failed:tactical_shadow_only",
    }
    events.write_text(json.dumps({"event_type": "rejected_plan_created", "record": rec}) + "\n")

    subprocess.check_call(
        [
            sys.executable,
            SCRIPT,
            "run",
            "--dry-run",
            "--once",
            "--backfill-from-start",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
            "--duration-hours",
            "24",
        ],
        cwd=str(ROOT),
    )

    row = json.loads(audit.read_text().splitlines()[0])
    assert row["event_type"] == "dry_run_plan"
    assert row["shadow_id"] == "shadow-gate-fail"
    assert row["plan"]["gate_metadata"]["tactical_track_gate"] == "fail"
```

- [ ] **Step 3: Verify RED**

Run:

```bash
python3 -m pytest \
  tests/test_shadow_tactical_live_core.py::test_tactical_shadow_event_accepts_shadow_mirror_records_without_gate_pass \
  tests/test_shadow_tactical_live_cli.py::test_run_dry_run_processes_shadow_only_tactical_gate_fail_event \
  -q
```

Expected: FAIL because current code requires `track=tactical`, `exit_profile=tactical_v1`, and `tactical_track_gate=pass`.

### Task 2: Restore the Filter

**Files:**
- Modify: `utils/shadow_tactical_live.py`
- Test: `tests/test_shadow_tactical_live_core.py`
- Test: `tests/test_shadow_tactical_live_cli.py`

- [ ] **Step 1: Implement minimal filter change**

Change `is_tactical_shadow_event()` to:

```python
def is_tactical_shadow_event(event: dict) -> bool:
    if event.get("event_type") != "rejected_plan_created":
        return False
    record = event.get("record") or {}
    return (
        record.get("track") == "tactical"
        or record.get("exit_profile") == "tactical_v1"
    )
```

- [ ] **Step 2: Verify GREEN locally**

Run:

```bash
python3 -m pytest \
  tests/test_shadow_tactical_live_core.py \
  tests/test_shadow_tactical_live_cli.py \
  tests/test_shadow_tactical_live_executor.py \
  tests/test_shadow_tactical_owner_isolation.py \
  tests/test_shadow_tactical_exit_monitoring.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Verify sidecar safety impact**

Run:

```bash
python3 -m pytest \
  tests/test_shadow_tactical_live_core.py \
  tests/test_shadow_tactical_live_cli.py \
  tests/test_shadow_tactical_live_executor.py \
  tests/test_shadow_tactical_owner_isolation.py \
  tests/test_shadow_tactical_exit_monitoring.py \
  tests/test_entry_drift_hybrid_policy.py \
  test_partial_tp_lifecycle.py \
  -q
```

Expected: PASS.

### Task 3: Deploy and Restart Sidecar Only

**Files:**
- Sync: `utils/shadow_tactical_live.py`
- Sync: `tests/test_shadow_tactical_live_core.py`
- Sync: `tests/test_shadow_tactical_live_cli.py`
- Sync: `docs/superpowers/plans/2026-07-23-restore-shadow-tactical-sidecar-mirror.md`

- [ ] **Step 1: Sync changed files to `/opt/crypto-arbitrage`**

Use `scp`/`rsync` for the files above only.

- [ ] **Step 2: Verify remote tests**

Run the focused and safety-impact pytest commands on the cloud server.

- [ ] **Step 3: Confirm safe restart conditions**

Check:

```bash
python3 scripts/shadow_tactical_live_sidecar.py status
python3 - <<'PY'
import json
from pathlib import Path
for p in ["data/shadow_tactical_live_positions.json", "data/positions.json"]:
    data=json.loads(Path(p).read_text()) if Path(p).exists() else {}
    print(p, len(data), list(data.keys()))
PY
```

Expected: sidecar active count is safe to restart; no unowned sidecar exposure is present.

- [ ] **Step 4: Restart sidecar only**

Stop the existing `shadow_tactical_live_sidecar.py run` process, then start a new 24h run from the current end of `data/rejected_signal_events.jsonl`:

```bash
cd /opt/crypto-arbitrage
kill -TERM <sidecar_pid>
nohup python3 scripts/shadow_tactical_live_sidecar.py run \
  --duration-hours 24 \
  --poll-seconds 2 \
  --size-usdt 100 \
  --max-active 3 \
  > logs/shadow_tactical_live_sidecar_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```

Do not restart Main. Do not backfill already skipped events.
