---
change: sidecar-frozen-admission-risk-tiers
design-doc: docs/superpowers/specs/2026-08-17-sidecar-frozen-admission-risk-tiers-design.md
base-ref: c2ae752cd6d717ab23e959ea2106279e5b583f7c
---

# Sidecar Frozen Admission And Risk Tiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Judge freeze the Sidecar admission decision, make Sidecar verify and execute only fresh valid decisions at 100U/50U tiers, and remove Main's 30U cap from the Sidecar process without changing Main.

**Architecture:** A new pure `utils/shadow_sidecar_policy.py` module is the single versioned policy implementation. Judge stamps future Tactical Shadow plans before the counterfactual ledger append; Sidecar verifies the same deterministic policy and TTL before any exchange work. `ContractExecutor` receives an optional validated Sidecar-only maximum-trade-amount override, while all Main call sites retain current config behavior.

**Tech Stack:** Python 3.12, dataclasses, JSON/JSONL, pytest, unittest.mock, existing Judge/CounterfactualLedger/Sidecar/ContractExecutor paths.

---

### Task 1: Versioned Frozen Policy Module

**Files:**
- Create: `utils/shadow_sidecar_policy.py`
- Create: `tests/test_shadow_sidecar_policy.py`

- [x] **Step 1: Write the failing policy truth-table tests**

Create tests that import the new constants and functions and assert clean/full, warning/reduced, gate-fail, exhaustion, malformed evidence, stamp mismatch, unsupported version, exact five-second TTL, stale TTL, and future-skew behavior.

```python
import pytest

from utils.shadow_sidecar_policy import (
    SIDECAR_POLICY_VERSION,
    classify_sidecar_policy,
    stamp_sidecar_policy,
    verify_sidecar_policy,
)


def _plan(**overrides):
    plan = {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "tactical_track_gate": "pass",
        "tactical_trend_exhaustion_warning": False,
        "tactical_weak_volume_oi": False,
        "tactical_weak_provenance": False,
    }
    plan.update(overrides)
    return plan


@pytest.mark.parametrize(
    ("overrides", "eligible", "tier", "reason"),
    [
        ({}, True, "full", ""),
        ({"tactical_weak_volume_oi": True}, True, "reduced", ""),
        ({"tactical_weak_provenance": True}, True, "reduced", ""),
        ({"tactical_track_gate": "fail"}, False, "none", "tactical_track_gate_failed"),
        (
            {"tactical_trend_exhaustion_warning": True},
            False,
            "none",
            "trend_exhaustion_warning",
        ),
    ],
)
def test_policy_truth_table(overrides, eligible, tier, reason):
    decision = classify_sidecar_policy(_plan(**overrides))
    assert decision.eligible is eligible
    assert decision.risk_tier == tier
    assert decision.rejection_reason == reason


def test_stamp_and_verify_exact_ttl_boundary():
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)
    verified = verify_sidecar_policy(stamped, now=105.0)
    assert verified.valid is True
    assert verified.admissible is True
    assert verified.policy_version == SIDECAR_POLICY_VERSION
    assert verified.age_seconds == 5.0


def test_verify_rejects_stamp_evidence_mismatch():
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)
    stamped["tactical_weak_volume_oi"] = True
    verified = verify_sidecar_policy(stamped, now=101.0)
    assert verified.valid is False
    assert verified.rejection_reason == "sidecar_policy_evidence_mismatch"
```

- [x] **Step 2: Run the policy tests and confirm RED**

Run: `python3 -m pytest -q tests/test_shadow_sidecar_policy.py`

Expected: collection fails because `utils.shadow_sidecar_policy` does not exist.

- [x] **Step 3: Implement the minimal pure policy module**

Implement frozen result dataclasses, strict canonical evidence extraction, deterministic classification, stamp generation, and verification. Required constants are:

```python
SIDECAR_POLICY_VERSION = "shadow-sidecar-v1"
SIDECAR_POLICY_MAX_AGE_SECONDS = 5.0
SIDECAR_POLICY_FUTURE_TOLERANCE_SECONDS = 1.0
SIDECAR_MAX_ACTIVE_POSITIONS = 3
```

The implementation must reject non-boolean warning evidence rather than applying `bool(value)`, compare nested `sidecar_policy_evidence` with the canonical top-level raw fields, compare all frozen outcome fields with a freshly classified result, and return `sidecar_policy_stale` only after integrity passes.

- [x] **Step 4: Run policy tests and confirm GREEN**

Run: `python3 -m pytest -q tests/test_shadow_sidecar_policy.py`

Expected: all policy tests pass.

- [x] **Step 5: Commit the policy module**

```bash
git add utils/shadow_sidecar_policy.py tests/test_shadow_sidecar_policy.py
git commit -m "feat: define frozen Sidecar admission policy"
```

### Task 2: Freeze Judge Decisions And Persist Ledger Evidence

**Files:**
- Modify: `agents/trading/judge.py:3308-3360`
- Modify: `agents/trading/judge.py:4051-4075`
- Modify: `utils/counterfactual_ledger.py:43-84`
- Modify: `test_tactical_track_classifier.py`
- Create: `tests/test_shadow_sidecar_policy_judge.py`

- [x] **Step 1: Write failing Judge profile and ledger-stamp tests**

Extend `test_tactical_track_classifier.py` so `_apply_tactical_profile()` copies the three explicit policy booleans from `track_decision["quality_flags"]`:

```python
def test_tactical_profile_exports_explicit_sidecar_quality_flags():
    judge = make_judge()
    decision = {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "reason": "main_quality_failed:weak_volume_oi,weak_provenance",
        "quality_flags": {
            "trend_exhaustion_warning": False,
            "weak_volume_oi": True,
            "weak_provenance": True,
        },
    }
    profiled = judge._apply_tactical_profile(base_plan(), strong_short_tech(), decision)
    assert profiled["tactical_trend_exhaustion_warning"] is False
    assert profiled["tactical_weak_volume_oi"] is True
    assert profiled["tactical_weak_provenance"] is True
```

Create a partial Judge with a capturing ledger and assert `_record_rejected_plan()` stamps Tactical rows but leaves a Main reject unstamped. Also assert `CounterfactualLedger.record_rejection()` writes every stamp/evidence field into the created record.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `python3 -m pytest -q test_tactical_track_classifier.py tests/test_shadow_sidecar_policy_judge.py`

Expected: explicit policy fields and frozen stamp assertions fail.

- [x] **Step 3: Export quality flags in Tactical profiles**

In `_apply_tactical_profile()`, read a defensive copy of `track_decision.get("quality_flags") or {}` and add these exact fields to `plan.update()`:

```python
"tactical_trend_exhaustion_warning": quality_flags.get("trend_exhaustion_warning") is True,
"tactical_weak_volume_oi": quality_flags.get("weak_volume_oi") is True,
"tactical_weak_provenance": quality_flags.get("weak_provenance") is True,
```

- [x] **Step 4: Stamp at the final Judge-to-ledger boundary**

In `_record_rejected_plan()`, copy `plan`, identify Tactical rows using the same `track == "tactical" or exit_profile == "tactical_v1"` contract, call `stamp_sidecar_policy()` with one captured `time.time()`, and pass the stamped copy to both ledger and decision-tape price reads. Do not stamp non-Tactical records.

- [x] **Step 5: Persist fields verbatim in CounterfactualLedger**

Add these fields to the created record:

```python
"tactical_trend_exhaustion_warning": plan.get("tactical_trend_exhaustion_warning"),
"tactical_weak_volume_oi": plan.get("tactical_weak_volume_oi"),
"tactical_weak_provenance": plan.get("tactical_weak_provenance"),
"sidecar_live_eligible": plan.get("sidecar_live_eligible"),
"sidecar_policy_version": plan.get("sidecar_policy_version"),
"sidecar_risk_tier": plan.get("sidecar_risk_tier"),
"sidecar_rejection_reason": plan.get("sidecar_rejection_reason"),
"sidecar_decided_at": plan.get("sidecar_decided_at"),
"sidecar_policy_evidence": dict(plan.get("sidecar_policy_evidence") or {}),
```

- [x] **Step 6: Run focused tests and confirm GREEN**

Run: `python3 -m pytest -q test_tactical_track_classifier.py tests/test_shadow_sidecar_policy_judge.py tests/test_judge_decision_tape_wiring.py`

Expected: all pass and decision-tape behavior remains unchanged.

- [x] **Step 7: Commit Judge and ledger stamping**

```bash
git add agents/trading/judge.py utils/counterfactual_ledger.py test_tactical_track_classifier.py tests/test_shadow_sidecar_policy_judge.py
git commit -m "feat: freeze Sidecar policy in Judge shadow records"
```

### Task 3: Verify Policy Before Sidecar Admission

**Files:**
- Modify: `utils/shadow_tactical_live.py:175-207`
- Modify: `scripts/shadow_tactical_live_sidecar.py:739-822`
- Modify: `tests/test_shadow_tactical_live_core.py`
- Modify: `tests/test_shadow_tactical_live_cli.py`

- [ ] **Step 1: Add failing plan-mapping and pre-exchange rejection tests**

Update the common Tactical record fixtures to include a valid stamp produced by `stamp_sidecar_policy()`. Assert mapping carries every policy field and evidence field. Replace the old gate-fail dry-run expectation with rejection before `dry_run_plan`.

Add direct `_process_event()` tests for:

```text
valid full -> executor called with 100.0
valid reduced -> executor called with 50.0
gate failure -> executor not called
trend exhaustion -> executor not called
missing stamp -> executor not called
stamp mismatch -> executor not called
age 5.01 seconds -> executor not called
```

Each rejection test must assert no `_fetch_exchange_positions()` call and an audit event carrying exact reason, policy version, tier, and evidence.

- [ ] **Step 2: Run Sidecar core/CLI tests and confirm RED**

Run: `python3 -m pytest -q tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_cli.py`

Expected: mapping lacks stamp fields and invalid/stale records still reach dry-run or executor paths.

- [ ] **Step 3: Map frozen policy fields into Sidecar plans**

Extend `map_shadow_record_to_plan()` with top-level stamp/evidence values. Add the policy fields to `gate_metadata` so opened positions retain entry attribution. Keep required mechanical plan validation unchanged.

- [ ] **Step 4: Verify policy before dry-run and exchange work**

Immediately after the admission-enabled check in `_process_event()`:

```python
verification = verify_sidecar_policy(record, now=time.time())
if not verification.admissible:
    state["seen_shadow_ids"][shadow_id] = "rejected"
    append_audit_event(paths.audit, "rejected", verification.audit_payload(shadow_id))
    return
```

Then map the record, calculate the tier size from the validated full-tier base, and use that same value for dry-run audit and `executor.open_sidecar_plan()`.

- [ ] **Step 5: Persist tier and size in opened/rejected audits**

The `opened` payload must include `sidecar_policy_version`, `sidecar_risk_tier`, and `requested_size_usdt`. Executor rejection must include the same fields alongside `executor_rejected` and any drained drift events.

- [ ] **Step 6: Run Sidecar core/CLI tests and confirm GREEN**

Run: `python3 -m pytest -q tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_cli.py`

Expected: all pass, with every policy rejection occurring before exchange work.

- [ ] **Step 7: Commit Sidecar policy verification**

```bash
git add utils/shadow_tactical_live.py scripts/shadow_tactical_live_sidecar.py tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_cli.py
git commit -m "feat: verify frozen policy before Sidecar execution"
```

### Task 4: Dedicated 100U Risk Ceiling And Three-Position Limit

**Files:**
- Modify: `executor.py:65-157`
- Modify: `scripts/shadow_tactical_live_sidecar.py:84-110`
- Modify: `scripts/shadow_tactical_live_sidecar.py:1015-1049`
- Modify: `tests/test_shadow_tactical_live_executor.py`
- Modify: `tests/test_shadow_tactical_live_cli.py`

- [ ] **Step 1: Write failing executor override and startup-bound tests**

Add constructor-focused tests with patched exchange/config dependencies proving `max_trade_amount_override=100` constructs `RiskManager.max_trade_amount == 100`, omission preserves 30, and invalid values (`0`, `nan`, `10001`) raise `ValueError`.

Extend CLI tests to assert `_build_executor(paths, max_trade_amount=100)` passes `max_trade_amount_override=100`, `--max-active 4` fails before state/executor creation, and values one through three are accepted.

Update `test_open_sidecar_plan_enforces_hard_size_cap()` to cover both a 30U Main-like executor and a 100U Sidecar executor.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python3 -m pytest -q tests/test_shadow_tactical_live_executor.py tests/test_shadow_tactical_live_cli.py`

Expected: constructor rejects the unknown override argument and capacity above three is still accepted.

- [ ] **Step 3: Add the validated optional executor override**

Extend the constructor signature with `max_trade_amount_override: Optional[float] = None`. After config/fallback loading and before `RiskManager(...)`, normalize with `float()`, require `math.isfinite()`, and require the value within `HARD_LIMITS["max_trade_amount"]`; otherwise raise `ValueError`. Replace only `max_amount`.

- [ ] **Step 4: Validate Sidecar runtime arguments before state mutation**

Add `resolve_sidecar_base_size()` and `resolve_sidecar_max_active()` pure functions. Call them at the top of `cmd_run()`, write normalized values back to `args`, and only then create `SidecarStateStore`. The active resolver accepts integers `1 <= value <= 3` and rejects booleans, fractions, malformed strings, zero, and values above three.

- [ ] **Step 5: Pass the full-tier base as the executor override**

Change `_build_executor()` to require `max_trade_amount` and pass:

```python
max_trade_amount_override=max_trade_amount,
```

Construct it in `cmd_run()` with the validated base size. Keep Main call sites untouched.

- [ ] **Step 6: Run focused tests and confirm GREEN**

Run: `python3 -m pytest -q tests/test_shadow_tactical_live_executor.py tests/test_shadow_tactical_live_cli.py`

Expected: all pass; full tier persists 100U and reduced tier persists 50U when the Sidecar ceiling is 100U.

- [ ] **Step 7: Commit dedicated Sidecar risk controls**

```bash
git add executor.py scripts/shadow_tactical_live_sidecar.py tests/test_shadow_tactical_live_executor.py tests/test_shadow_tactical_live_cli.py
git commit -m "fix: give Sidecar a dedicated bounded risk ceiling"
```

### Task 5: Seal And Replay The 53-Trade Audit Cohort

**Files:**
- Create: `tests/fixtures/shadow_sidecar_policy_53_trade_window.json`
- Create: `tests/test_shadow_sidecar_policy_replay.py`

- [ ] **Step 1: Extract and join the audited cohort read-only**

Use the Sidecar owner registry as the authoritative population: select owners whose `opened_at` is within the exact inclusive epoch window `1786602333.548581..1786931035.0` (`2026-08-13 14:25:33.548581` through `2026-08-17 09:43:55` CST). This must produce 53 unique `shadow_id` and entry `order_id` values. For those ids, scan `/opt/crypto-arbitrage/data/rejected_signal_events.jsonl` in append order and retain the last `rejected_plan_created` row per id; do not apply a second event-time filter. Join audited actual PnL by owner entry order id, normalize gross, fees, and funding independently to 100U with decimal arithmetic, and use that net as `pnl_usdt_at_100u`. Shadow settlement rows are attribution evidence only and are not the actual-PnL source. Read cloud files only through stdout; do not write any cloud file, change `.env`, call a trading endpoint, or restart a process.

Validate extraction facts before adding the fixture:

```text
source Tactical trades: 53
policy-eligible trades: 9
all-100U net PnL: +4.47024185U
tiered 100U/50U net PnL: +9.086859325U
```

- [ ] **Step 2: Add a failing deterministic replay test**

The test loads the fixture, calls `classify_sidecar_policy()` for each row, scales `pnl_usdt_at_100u` by `1.0` for full and `0.5` for reduced, and asserts nine stable eligible ids and the fixture aggregate. Repeat the pure replay 100 times and compare serialized outputs byte-for-byte.

- [ ] **Step 3: Run replay test and confirm RED if any fixture/policy contract differs**

Run: `python3 -m pytest -q tests/test_shadow_sidecar_policy_replay.py`

Expected before fixture finalization: failure identifies count, tier, id, reason, or arithmetic drift. Correct the extraction or policy evidence, never the expected result without source evidence.

- [ ] **Step 4: Finalize fixture metadata and confirm GREEN**

Fixture metadata must include owner/event/actual-PnL source paths and hashes, the exact owner window start/end in epoch and CST, extraction timestamp, decimal normalization precision, SHA-256 of canonical trade rows, row count, eligible count, and counterfactual disclaimer. Record any raw-source completeness limitation instead of silently presenting a derived audit as independently regenerated exchange evidence.

Run: `python3 -m pytest -q tests/test_shadow_sidecar_policy_replay.py`

Expected: pass across 100 loops.

- [ ] **Step 5: Commit sealed replay evidence**

```bash
git add tests/fixtures/shadow_sidecar_policy_53_trade_window.json tests/test_shadow_sidecar_policy_replay.py
git commit -m "test: seal Sidecar frozen-policy replay cohort"
```

### Task 6: Documentation, Task Closure, And Regression

**Files:**
- Modify: `docs/runbook.md`
- Modify: `docs/to-do-list.md`
- Modify: `openspec/changes/sidecar-frozen-admission-risk-tiers/tasks.md`

- [ ] **Step 1: Document the operator contract**

Add the frozen policy fields, five-second TTL, full/reduced tier rules, startup command `python3 scripts/shadow_tactical_live_sidecar.py run --poll-seconds 2 --size-usdt 100 --max-active 3`, unsupported/historical stamp fail-closed behavior, and Main 30U isolation. State that replay PnL is counterfactual and does not authorize a live restart.

- [ ] **Step 2: Run the focused regression suite**

Run:

```bash
python3 -m pytest -q \
  tests/test_shadow_sidecar_policy.py \
  tests/test_shadow_sidecar_policy_judge.py \
  tests/test_shadow_sidecar_policy_replay.py \
  test_tactical_track_classifier.py \
  tests/test_judge_decision_tape_wiring.py \
  tests/test_shadow_tactical_live_core.py \
  tests/test_shadow_tactical_live_cli.py \
  tests/test_shadow_tactical_live_executor.py \
  tests/test_shadow_tactical_exit_monitoring.py
```

Expected: all pass.

- [ ] **Step 3: Run broader Tactical and repository regression**

Run: `python3 -m pytest -q tests/test_tactical_v2_candidate_bus.py tests/test_tactical_v2_shadow.py tests/test_tactical_v2_main_isolation.py tests/test_shadow_tactical_owner_isolation.py`

Then run: `python3 -m pytest -q`

Expected: all default non-network tests pass; report exact counts and warnings.

- [ ] **Step 4: Collect cloud pre-deployment facts read-only**

Read process commands, Sidecar status/admission state, owner registry, Sidecar local positions, Main local positions, exchange position/protection summaries through existing status/drain commands, and pending PnL. Do not stop/restart while an active owner exists unless restart recovery is proven.

- [ ] **Step 5: Check all completed OpenSpec tasks and commit docs**

Mark each implemented and evidenced task in `openspec/changes/sidecar-frozen-admission-risk-tiers/tasks.md` as complete. Leave deployment-specific work unchecked if cloud safety facts block it.

```bash
git add docs/runbook.md docs/to-do-list.md openspec/changes/sidecar-frozen-admission-risk-tiers/tasks.md
git commit -m "docs: record frozen Sidecar admission operations"
```

- [ ] **Step 6: Run build guard only after every required task is complete**

Run: `bash /Users/mac/.codex/skills/comet/scripts/comet-guard.sh sidecar-frozen-admission-risk-tiers build --apply`

Expected: build checks pass and Comet transitions to verify. If cloud deployment remains outside the approved build boundary, record the read-only pre-deployment result and keep actual restart as a separate rollout decision.
