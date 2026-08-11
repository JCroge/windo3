# Tactical V2 Shadow Admission Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Tactical V2 admit the same normalized structural opportunities as Shadow Tactical, while preserving episode de-duplication and producing durable candidate handling evidence.

**Architecture:** Keep `EpisodeRegistry` as the sole owner of structural renewal and de-duplication. Pass the MessageBus message ID into `TacticalV2Controller`, which writes an append-only `candidate_handled` event for every consumed candidate. Add a fixture-driven admission replay that compares normalized candidate/episode outcomes and keeps executable bid/ask entry as a separate gate.

**Tech Stack:** Python 3, asyncio, pytest, existing `TacticalStore` JSONL event ledger, existing Tactical V2 entry reducer, JSON fixtures, Comet/OpenSpec artifacts.

**Design Doc:** `docs/superpowers/specs/2026-08-11-tactical-v2-shadow-admission-parity-design.md`

**Base Ref:** `cd3e2342c469c0212da2833ee67110dac46d01f9`

---

### Task 1: Implement Fresh-Evidence Episode Renewal

**Files:**
- Modify: `utils/tactical_v2/episodes.py:183-211`
- Test: `tests/test_tactical_v2_episodes.py`

- [ ] **Step 1: Add the failing neutral renewal test**

Add a test using the existing `_registry`, `_candidate`, and `_structure` helpers:

```python
def test_terminal_neutral_candidate_with_new_closed_bar_advances_epoch(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")

    renewed = registry.assign(
        _candidate(),
        {
            **_structure(bias="neutral", token="break-up-1"),
            "tf_15m_closed_bar_ts": 915.0,
        },
    )

    assert renewed.eligible is True
    assert renewed.reason == "new_confirmed_structure"
    assert renewed.episode_id != first.episode_id
    assert renewed.epoch_seq == first.epoch_seq + 1
```

- [ ] **Step 2: Add the negative cases before implementation**

Add tests that assert a terminal neutral candidate with the same bar/token returns `duplicate_episode`, and a terminal neutral candidate with `block_long=True` returns `opposing_block` without creating a new episode.

- [ ] **Step 3: Run the focused tests and confirm the bug is red**

Run:

```bash
pytest -q tests/test_tactical_v2_episodes.py
```

Expected before implementation: the fresh-neutral test fails because the current method returns no reset reason and `duplicate_episode`.

- [ ] **Step 4: Implement the minimal reset rule**

In `_reset_reason`, after checking `tf_15m_available` and `_is_blocked`, evaluate terminal fresh evidence before the directional-bias checks:

```python
if state.get("terminal"):
    token = structure.get("tf_15m_structure_token")
    closed_bar = structure.get("tf_15m_closed_bar_ts")
    previous_bar = state.get("last_closed_bar_ts")
    newer_bar = (
        closed_bar is not None
        and previous_bar is not None
        and float(closed_bar) > float(previous_bar)
    )
    changed_token = bool(
        token
        and token != state.get("last_structure_token")
    )
    if newer_bar or changed_token:
        return "new_confirmed_structure"
```

Leave the existing bullish/bearish `neutral_then_renewed` and `opposing_block_then_renewed` behavior after this branch. Do not update the persisted baseline before the comparison.

- [ ] **Step 5: Run the episode tests and restart coverage**

Run:

```bash
pytest -q tests/test_tactical_v2_episodes.py tests/test_tactical_v2_crash_recovery.py
```

Expected: all tests pass, including persisted reset evidence and historical episode terminality.

- [ ] **Step 6: Commit the episode change**

```bash
git add utils/tactical_v2/episodes.py tests/test_tactical_v2_episodes.py
git commit -m "fix: renew tactical episodes on fresh neutral structure"
```

### Task 2: Persist Candidate Handling Receipts

**Files:**
- Modify: `agents/trading/executor.py:223-227`
- Modify: `utils/tactical_v2/controller.py:134-237`
- Test: `tests/test_tactical_v2_candidate_receipts.py`
- Test: `tests/test_tactical_v2_controller.py`

- [ ] **Step 1: Add receipt tests before implementation**

Create controller tests that call `handle_candidate` for an accepted candidate, a repeated candidate, an expired candidate, and a capacity-skipped candidate. Read the generated Tactical V2 JSONL and assert exactly one `candidate_handled` event per call with these fields:

```python
required = {
    "candidate_id",
    "source_shadow_id",
    "message_id",
    "symbol",
    "side",
    "accepted",
    "reason",
    "episode_id",
    "intent_id",
    "evaluated_at",
    "replayed",
    "payload_hash",
}
assert required <= receipt.keys()
```

The accepted receipt must reference the created intent. The duplicate receipt must reference the existing episode and have `intent_id is None`. The expired and capacity receipts must be rejected without fabricating an intent ID.

- [ ] **Step 2: Run the new tests and confirm they fail**

Run:

```bash
pytest -q tests/test_tactical_v2_candidate_receipts.py
```

Expected before implementation: no `candidate_handled` event is present.

- [ ] **Step 3: Extend the handler boundary with message identity**

Change `TacticalV2Controller.handle_candidate` to accept `message_id: Optional[str] = None`. Add a private receipt helper that accepts the raw payload, `CandidateHandlingResult`, evaluation time, message ID, and replay flag, computes a stable JSON payload hash, and appends one `candidate_handled` event through the existing store.

Route every existing early return through the helper, including namespace mismatch, invalid candidate, future candidate, expired candidate, disabled mode, episode rejection, governor rejection, and accepted intent creation. For accepted results, append the receipt only after `_register_intent` has appended `intent_created` so `intent_id` is available.

- [ ] **Step 4: Pass `msg_id` from Executor and replay paths**

In `MultiExecutor.handle_message`, call:

```python
await controller.handle_candidate(
    msg.get("payload") or {},
    message_id=msg.get("msg_id"),
)
```

Keep startup journal replay behavior explicit by passing `replayed=True` and the replayed message ID from `setup()`.

- [ ] **Step 5: Verify receipt replay behavior**

Add a restart test that constructs a controller from the same TacticalStore, replays the same candidate, and asserts no second intent is created and the prior receipt remains unchanged. Missing receipt events in a legacy ledger must not be synthesized during restore; expose them only as unknown reporting evidence.

Run:

```bash
pytest -q tests/test_tactical_v2_candidate_receipts.py tests/test_tactical_v2_controller.py tests/test_tactical_v2_candidate_bus.py
```

- [ ] **Step 6: Commit the receipt change**

```bash
git add agents/trading/executor.py utils/tactical_v2/controller.py tests/test_tactical_v2_candidate_receipts.py tests/test_tactical_v2_controller.py
git commit -m "feat: persist Tactical V2 candidate handling receipts"
```

### Task 3: Add Cloud-Window Normalized Parity Replay

**Files:**
- Create: `tests/fixtures/tactical_v2_shadow_admission_window.json`
- Create: `scripts/replay_tactical_v2_admission.py`
- Create: `tests/test_tactical_v2_shadow_admission_parity.py`
- Modify: `openspec/changes/tactical-v2-shadow-admission-parity/tasks.md`

- [ ] **Step 1: Create a sanitized 22-candidate fixture**

Store only candidate payload fields needed by `TacticalCandidate.from_raw` and the EpisodeRegistry structure fields. Include the initial terminal PUMP state separately. Do not include exchange credentials, raw account data, or production paths. Preserve the audited source Shadow IDs and candidate IDs so the result is traceable.

- [ ] **Step 2: Add the failing replay assertions**

The replay test must assert:

```python
assert report.raw_candidates == 22
assert report.accepted_by_symbol == {"BICO-USDT": 3, "PUMP-USDT": 2}
assert report.accepted == 5
assert report.reasons["duplicate_episode"] == 17
assert report.stable_iterations == 100
```

It must also assert that accepted candidates passed the shared entry reducer with an at-entry executable quote, while the report explicitly labels this as an entry-decision check rather than an exchange fill.

- [ ] **Step 3: Run the replay test before implementation integration**

Run:

```bash
pytest -q tests/test_tactical_v2_shadow_admission_parity.py
```

Expected before the new episode rule: PUMP accepted count is `0`, so the test fails on normalized parity.

- [ ] **Step 4: Implement the replay driver**

Create a network-free CLI that loads the fixture, instantiates an in-memory TacticalStore and EpisodeRegistry, replays candidates in `created_at` order, emits JSON containing raw count, normalized accepted count, per-symbol accepted count, rejection reasons, accepted identity list, and stability iterations, and exits non-zero when the expected safety/parity assertions fail.

- [ ] **Step 5: Run the 100-loop replay and targeted suite**

Run:

```bash
python3 scripts/replay_tactical_v2_admission.py --fixture tests/fixtures/tactical_v2_shadow_admission_window.json
pytest -q tests/test_tactical_v2_shadow_admission_parity.py tests/test_tactical_v2_episodes.py tests/test_tactical_v2_entry.py tests/test_tactical_v2_replay.py
```

Expected replay output includes `accepted=5`, `BICO-USDT=3`, `PUMP-USDT=2`, `duplicate_episode=17`, and `stable_iterations=100`.

- [ ] **Step 6: Commit the replay change**

```bash
git add scripts/replay_tactical_v2_admission.py tests/fixtures/tactical_v2_shadow_admission_window.json tests/test_tactical_v2_shadow_admission_parity.py openspec/changes/tactical-v2-shadow-admission-parity/tasks.md
git commit -m "test: lock Tactical V2 Shadow admission parity replay"
```

### Task 4: Verification and Operational Reporting

**Files:**
- Modify: `docs/to-do-list.md`
- Modify: `openspec/changes/tactical-v2-shadow-admission-parity/tasks.md`
- Test: existing Tactical V2 regression suite

- [ ] **Step 1: Add the operator-facing distinction**

Document that raw Legacy Shadow rows, normalized V2 admission outcomes, executable entry decisions, and exchange fills are separate metrics. Record that historical candidates without receipts are unknown, not presumed lost or consumed. Keep the Sidecar admission-disabled rule explicit.

- [ ] **Step 2: Run the focused Tactical V2 suite**

Run:

```bash
pytest -q tests/test_tactical_v2_protection.py tests/test_tactical_v2_exchange.py tests/test_tactical_v2_entry.py tests/test_tactical_v2_exit.py tests/test_tactical_v2_episodes.py tests/test_tactical_v2_parity.py tests/test_tactical_v2_candidate_bus.py tests/test_tactical_v2_replay.py tests/test_tactical_v2_shadow.py tests/test_tactical_v2_controller.py tests/test_tactical_v2_crash_recovery.py tests/test_tactical_v2_store.py tests/test_tactical_v2_config.py tests/test_tactical_v2_models.py tests/test_tactical_v2_governor.py tests/test_tactical_v2_main_isolation.py tests/test_tactical_v2_cutover.py tests/test_tactical_v2_structure.py tests/test_tactical_v2_pnl.py tests/test_tactical_v2_candidate_receipts.py tests/test_tactical_v2_shadow_admission_parity.py
```

Expected: all focused tests pass and no test accesses the cloud or changes production data.

- [ ] **Step 3: Run repository verification and static checks**

Run:

```bash
python3 -m compileall -q utils agents scripts tests
git diff --check
git status --short
```

Expected: compile succeeds, diff check is clean, and only planned files are modified.

- [ ] **Step 4: Commit operational documentation and mark tasks complete**

```bash
git add docs/to-do-list.md openspec/changes/tactical-v2-shadow-admission-parity/tasks.md
git commit -m "docs: document Tactical V2 admission parity operations"
```

After the commit, mark every task checkbox complete and run the Comet build guard. Do not enable Sidecar admission or change live V2 capacity as part of verification.
