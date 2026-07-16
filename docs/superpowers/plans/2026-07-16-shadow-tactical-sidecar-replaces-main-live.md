# Shadow Tactical Sidecar Replaces Main Live Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disable main-process live Tactical execution while keeping Tactical classification active, then run the Shadow Tactical sidecar on only true-open Tactical shadow records.

**Architecture:** Main `run_agents.py` keeps `TACTICAL_TRACK_ENABLED=true` but switches to `TACTICAL_SHADOW_ONLY=true`, so Judge writes executable Tactical counterfactuals instead of direct live orders. The sidecar consumes only `rejected_plan_created` records with `track=tactical`, `exit_profile=tactical_v1`, and `tactical_track_gate=pass`, and blocks same-symbol exchange exposure before opening orders.

**Tech Stack:** Python, pytest, OKX via ccxt, existing `utils.shadow_tactical_live`, `scripts/shadow_tactical_live_sidecar.py`, and `executor.ContractExecutor`.

---

### Task 1: Tighten Sidecar Event Filter

**Files:**
- Modify: `utils/shadow_tactical_live.py`
- Test: `tests/test_shadow_tactical_live_core.py`

- [ ] **Step 1: Write failing tests**

Add tests proving the sidecar ignores diagnostic Tactical-profile records and only accepts true-open Tactical records:

```python
def test_tactical_shadow_event_requires_true_open_track_and_gate_pass():
    assert is_tactical_shadow_event(
        _event(_tactical_record(track="tactical", tactical_track_gate="pass"))
    )

    assert not is_tactical_shadow_event(
        _event(_tactical_record(track="shadow_only", tactical_track_gate="fail"))
    )

    assert not is_tactical_shadow_event(
        _event(_tactical_record(track="tactical", tactical_track_gate="fail"))
    )
```

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m pytest tests/test_shadow_tactical_live_core.py::test_tactical_shadow_event_requires_true_open_track_and_gate_pass -q
```

Expected: FAIL because the current filter accepts `exit_profile=tactical_v1` even when `track=shadow_only` or `tactical_track_gate=fail`.

- [ ] **Step 3: Implement minimal filter**

Change `is_tactical_shadow_event()` to return true only when:

```python
event.get("event_type") == "rejected_plan_created"
record.get("track") == "tactical"
record.get("exit_profile") == "tactical_v1"
record.get("tactical_track_gate") == "pass"
```

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m pytest tests/test_shadow_tactical_live_core.py -q
```

Expected: PASS.

### Task 2: Block Same-Symbol Main Exposure

**Files:**
- Modify: `utils/shadow_tactical_live.py`
- Test: `tests/test_shadow_tactical_live_core.py`

- [ ] **Step 1: Write failing test**

Add a test proving an opposite-side main exposure on the same symbol blocks sidecar opens:

```python
def test_same_symbol_guard_blocks_opposite_side_non_sidecar_exposure(tmp_path):
    reg = ShadowTacticalOwnerRegistry(str(tmp_path / "owners.json"))
    blocked, reason = blocks_same_symbol_account_exposure(
        [{"symbol": "WLD-USDT-SWAP", "side": "short", "contracts": 1}],
        "WLD-USDT-SWAP",
        "long",
        reg,
    )
    assert blocked is True
    assert reason == "same_symbol_account_exposure"
```

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m pytest tests/test_shadow_tactical_live_core.py::test_same_symbol_guard_blocks_opposite_side_non_sidecar_exposure -q
```

Expected: FAIL because the current guard only blocks matching symbol and matching side.

- [ ] **Step 3: Implement minimal guard**

In `blocks_same_symbol_account_exposure()`, block any active exchange position whose normalized symbol matches the target unless that exact exchange position is known sidecar-owned by `owners.matches_position(pos_symbol, pos_side)`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m pytest tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_cli.py tests/test_shadow_tactical_owner_isolation.py tests/test_shadow_tactical_live_executor.py -q
```

Expected: PASS.

### Task 3: Deploy And Switch Runtime

**Files:**
- Remote modify: `/opt/crypto-arbitrage/.env`
- Remote run: `/opt/crypto-arbitrage/run_agents.py`
- Remote run: `/opt/crypto-arbitrage/scripts/shadow_tactical_live_sidecar.py`

- [ ] **Step 1: Sync changed files to cloud**

Use rsync/scp for modified code and tests only. Do not push GitHub.

- [ ] **Step 2: Run remote focused tests**

Run:

```bash
cd /opt/crypto-arbitrage
python3 -m pytest tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_cli.py tests/test_shadow_tactical_owner_isolation.py tests/test_shadow_tactical_live_executor.py -q
```

Expected: PASS.

- [ ] **Step 3: Switch main Tactical to shadow-only**

Update remote `.env`:

```env
TACTICAL_TRACK_ENABLED=true
TACTICAL_SHADOW_ONLY=true
```

- [ ] **Step 4: Restart main process**

Stop old `python3 /opt/crypto-arbitrage/run_agents.py`, start a new one with a dated log, and confirm one main process.

- [ ] **Step 5: Start sidecar from end**

Start sidecar without `--backfill-from-start`:

```bash
nohup python3 scripts/shadow_tactical_live_sidecar.py run \
  --duration-hours 24 \
  --poll-seconds 2 \
  --size-usdt 30 \
  --max-active 1 \
  > logs/shadow_tactical_live_sidecar_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```

- [ ] **Step 6: Monitor**

Check main process count, sidecar process count, `scripts/shadow_tactical_live_sidecar.py status`, sidecar audit log, main run log errors, `data/positions.json`, and `data/shadow_tactical_live_owners.json`.

Self-review: This plan covers the two required code safety changes, the runtime config switch, no historical backfill, remote verification, and monitoring. It intentionally does not change main strategy logic beyond runtime `.env`.
