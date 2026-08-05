# Tactical V2 Entry Reconciliation Self-Heal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent OKX order-visibility delay and concurrent controller ticks from permanently halting Tactical V2, while preserving fail-closed behavior for ambiguous exchange truth.

**Architecture:** `ContractExecutor` returns a tri-state exact-entry lookup result: found, confirmed absent, or query error. `TacticalV2Controller` owns a durable visibility deadline, suppresses reconciliation while the local submit call is in flight, retries temporarily absent orders from its existing five-second tick, and only enters integrity halt after the deadline or on genuinely ambiguous evidence. Recovery never resubmits an entry command.

**Tech Stack:** Python 3.10, asyncio, ccxt/OKX private order API, pytest/pytest-asyncio, Tactical V2 append-only event store.

---

### Task 1: Make Entry Lookup Evidence Explicit

**Files:**
- Modify: `executor.py`
- Test: `tests/test_tactical_v2_exchange.py`

- [x] Add failing tests proving an exact found order returns `found`, a successful empty query returns `not_found`, and total API failure returns `query_error`.
- [x] Run the three focused tests and confirm they fail against the current optional-dict contract.
- [x] Add an explicit tri-state entry-query result contract and update `query_tactical_entry` plus its submit/cancel callers.
- [x] Run the focused exchange tests and confirm they pass.

### Task 2: Serialize Submit and Reconciliation

**Files:**
- Modify: `utils/tactical_v2/controller.py`
- Test: `tests/test_tactical_v2_crash_recovery.py`

- [x] Add a failing concurrency test that blocks submit I/O, calls `tick()`, and proves no reconciliation or halt occurs while the submit is in flight.
- [x] Run the focused test and confirm the existing controller reproduces the race.
- [x] Track process-local in-flight submit intent IDs with `try/finally`; have `tick()` skip those IDs without changing durable state.
- [x] Run the focused test and existing crash-boundary tests.

### Task 3: Add Durable Visibility Grace and Periodic Self-Heal

**Files:**
- Modify: `utils/tactical_v2/controller.py`
- Test: `tests/test_tactical_v2_crash_recovery.py`
- Test: `tests/test_tactical_v2_controller.py`

- [x] Add failing tests for not-found within grace, visibility after retry, query error, restart during grace, and deadline exhaustion.
- [x] Persist `entry_visibility_deadline` with `submitting_entry`; use that timestamp after restart rather than resetting the clock.
- [x] Within grace, keep `reconciling_entry` and retry on later ticks without resubmission; query errors remain fail-closed and never clear an existing halt.
- [x] At deadline, transition to `integrity_required` and activate `entry_reconciliation_unknown`.
- [x] Allow periodic recheck of only this halt reason; clear it only after controller-derived ownership/orders/positions/protection proof is complete.
- [x] Run all Tactical V2 tests.

### Task 4: Regression and Deployment Verification

**Files:**
- Modify only if a regression exposes a scoped issue.

- [x] Run `pytest -q tests/test_tactical_v2_*.py`.
- [x] Run the broader executor and lifecycle regression set.
- [x] Review the diff for changes to sizing, strategy gates, exit ownership, or governor thresholds; none are permitted.
- [x] Deploy the tested files to the server with backups, restart Main, and verify `LIVE / 100U x 3`, no integrity halt, verified reconciliation/protection, and Sidecar admission disabled.
- [x] Observe at least one full status-refresh interval and inspect logs for new errors.

### Post-Plan Hardening

- [x] Make pending external-close ledger writes idempotent by stable close match key across restart.
- [x] Recover exact unfilled orders without clearing newer integrity halts; cancel expired/deferred orders with flat proof.
- [x] Handle cancel/fill races without recursive cancellation.
- [x] Replay the real ADA anonymous close shape and current OKX bill subtypes with exact quantity and identity proof.
- [x] Preserve the original cancel reason across `entry_cancel_unproven` rechecks so invalid orders cannot return to `pending_entry`.
- [x] Serialize JSONL read-modify-replace against every append across ledger instances and Main/Sidecar processes.
- [x] Add a durable final-PnL outbox, legacy ack migration, and in-process route serialization without double-applying governor PnL.
- [x] Project durable `integrity_required` intent state into local and Telegram status even when the governor halt slot is transiently clear.
- [x] Verify `1869 passed, 4 deselected`, deploy only the six changed runtime files, restart Main only, and observe a complete status/recheck interval.
- [x] Run the complete repository suite and deploy only after it passes.

Residual delivery contract: the final-PnL bus remains at-least-once across a
process crash between successful publish and durable outbox ack. In-process
concurrent routes are serialized and suppressed by the durable ack; making the
Telegram consumer exactly-once across that crash window requires a separate
durable subscriber-offset change.
