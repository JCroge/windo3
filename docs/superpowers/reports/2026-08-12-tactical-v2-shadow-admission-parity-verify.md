# Verification Report: tactical-v2-shadow-admission-parity

Date: 2026-08-13 (final rerun; post-deploy history retained below)
Branch: `tactical-v2-shadow-admission-parity`
Change: `tactical-v2-shadow-admission-parity`
Workflow: full
Verify mode: full

## Final Assessment

PASS for the scoped admission-parity and candidate-admission concurrency-hardening change. The
audited 22-candidate sequence normalizes to three BICO episodes and two PUMP
episodes with deterministic rejection of repeats. The real Controller replay is
reported separately: it creates two intents because same-symbol exposure blocks
three later opportunities until lifecycle evidence releases the symbol; the
fixture contains no terminal/close evidence to simulate that release.

This is an admission result, not fill or settlement parity. Historical executable
quotes and fill-bound protection evidence are absent, so Sidecar admission remains
NO-GO and `live_rollout_ready=false`. This change does not authorize production
configuration changes, V2 capacity expansion, or Sidecar admission restoration.

## Scorecard

| Dimension | Status | Evidence |
| --- | --- | --- |
| Completeness | PASS | OpenSpec reports 17/17 tasks complete; 3/3 requirements and 15/15 scenarios are mapped to implementation and tests |
| Correctness | PASS | Focused Tactical V2 matrix: `508 passed`; full repository regression: `2160 passed, 4 deselected`; no failures |
| Coherence | PASS | Proposal, OpenSpec design, technical Design Doc, and delta specs agree on the admission boundary and NO-GO rollout gate |

## Root Cause And Fix

The production replay showed 22 candidate messages but only three V2 intents, all
BICO. The missing PUMP admissions were not caused by the tactical gate. The
persisted PUMP episode was terminal, and `EpisodeRegistry._reset_reason()` required
a directional 15m bias before renewing it. Shadow supplied fresh, available,
unblocked structure with a neutral bias, so V2 returned `duplicate_episode` before
intent creation.

`utils/tactical_v2/episodes.py` now permits terminal renewal for aligned or neutral
bias only when the closed 15m bar advances monotonically or a non-empty structure
token changes. Same evidence remains a duplicate; opposing/unavailable bias and
blocked sides still fail closed. Tests cover newer-bar renewal, changed-token
renewal, stale/same evidence, opposing block, failed append retry, historical
episode termination, replay, and restart.

`utils/tactical_v2/controller.py` now persists append-only `candidate_handled`
receipts with candidate, source Shadow, message, episode, intent, decision, time,
replay, and payload-hash evidence. `agents/trading/executor.py` forwards `msg_id`
on normal delivery and startup replay. Missing legacy receipts remain
`unknown_handling_evidence`; conflicts and receipt gaps fail closed rather than
inventing authority.

The candidate-admission concurrency audit found two independent-process races in the previous
implementation. Two stores could allocate the same ledger sequence, producing
the observed `[1, 2, 3, 3, 4, 4, 5, 5, 6]` pattern and restart-time integrity
failure. Two Controllers could also both return `accepted` for one candidate.
The fix uses a per-ledger `fcntl.flock` plus in-process reentrant locking across
the full admission transaction, resynchronizes sequence state from the ledger,
reloads stale Controller read models, and binds receipts to deterministic event
identity. The regression replay now proves one `intent_created`, one
`candidate_handled`, contiguous sequence numbers, and no integrity failure for
the concurrent duplicate-candidate case.

This guarantee stops at the admission transaction. The current implementation
does not provide a cross-process lease or fencing token for quote handling,
entry submit/reconcile/cancel, protection, close, PnL, or status snapshot writes.
Live operation therefore requires exactly one active Main for a namespace and
exchange account. A controlled restart must confirm the old Main has exited
before starting the replacement; overlapping Main processes are unsupported.

## Replay Evidence

Command:

```bash
/usr/local/anaconda3/bin/python3.12 scripts/replay_tactical_v2_admission.py \
  --fixture tests/fixtures/tactical_v2_shadow_admission_window.json \
  --iterations 100
```

| Metric | Result |
| --- | ---: |
| Raw candidates | 22 |
| Accepted normalized episodes | 5 |
| BICO accepted | 3 |
| PUMP accepted | 2 |
| Explicit `duplicate_episode` rows | 17 |
| Other rejected / unknown replay outcomes | 0 / 0 |
| Real Controller intents | 2 |
| Real Controller receipts | 22 |
| Real Controller result split | `accepted=2`, `duplicate_episode=17`, `same_symbol_exposure=3` |
| Real Controller lifecycle evidence | absent from fixture |
| Normalized reducer stable iterations | 100 |
| Normalized reducer fingerprint | `73175bcdd2435db7c7be81f242be5264390acc318655c8c7cd758dd293ca2ab0` |
| Real Controller stable iterations | 100 |
| Real Controller fingerprint | `e9c38ea01eab7df332ea9f5855090decf65df9a9521937219553a02c8c7e42cc` |
| Fixture fingerprint | `65dd6e2f3cd21dd1aaa9d163126c818f0a0db8f92997d80f24e548f44e72fa5f` |

The CLI exited 0 with `admission_replay_passed=true`,
`parity_expected_values_passed=true`, `replay_integrity_passed=true`,
`stability_requirement_passed=true`, and
`controller_replay_stability_requirement_passed=true`. Every iteration runs a
fresh real Controller in an independent temporary root; Controller stability is
not inferred from the normalized reducer.

The same output deliberately reports `historical_executable_quote_available=false`,
`exchange_fill=false`, `protection_evidence_proven=false`,
`protection_check_status=not_run_no_fill`, and `live_rollout_ready=false`.
Synthetic `bid=ask=entry_ref` checks prove only the shared reducer boundary,
900-second TTL, entry drift, and governor capacity behavior.

The normalized `accepted=5` metric is therefore an opportunity metric, not a
claim that five production orders were created. The three `same_symbol_exposure`
results are intentional safety rejections and must not be removed to force
five Controller intents. A settlement or realized-PnL conclusion requires a
fixture with executable bid/ask history, exchange fills, protection evidence,
and terminal lifecycle events.

## Concurrency And Failure Replay

| Scenario | Before fix | After fix |
| --- | --- | --- |
| Two processes append to one ledger | Duplicate sequences and restart integrity failure; reproduced 10/10 | `tests/test_tactical_v2_store.py`: contiguous `1..6`, rebuild integrity clear |
| Two processes handle one candidate | Both returned `accepted`; could create two admission side effects | `tests/test_tactical_v2_concurrency.py`: one intent, one receipt, both calls return the same idempotent accepted result |
| Multiprocessing test child hangs or exits abnormally | Test could block on queue reads or leave a child running | Bounded total join, terminate/reap on timeout, explicit exit-code assertion, bounded queue collection, and queue cleanup |
| Post-write `fsync` failure | An unconfirmed row could be treated as committed | Record pre-append size, truncate and `fsync` rollback, then surface a durable handling gap/unknown result; rollback failure poisons later writes fail-closed |
| Journal replay limit | Fresh rows after the raw 1000-row cutoff could be missed; removing the cutoff materialized all topic history | Lazy scan applies namespace/TTL before the result limit and stops reading once enough valid messages are found |
| Concurrent atomic/status writers | Shared `<target>.tmp` could be renamed by another writer | Same-directory PID+UUID temporary paths, atomic replace, directory `fsync`, cleanup, and direct concurrent regressions for both the shared JSON writer and Tactical status writer |

Before each ledger append, Store durably writes an `events.jsonl.append-pending`
marker containing the event identity, sequence, and pre-append file size. A
confirmed append or confirmed rollback removes and directory-fsyncs that marker.
If event sync and rollback both fail, the marker survives process restart and
both current and new Store instances refuse ledger authority. A parseable but
unconfirmed tail can therefore no longer be rediscovered as an accepted receipt.

The post-write `fsync` behavior is covered by store and receipt regressions. The
system remains fail-closed when a write cannot be confirmed; it does not invent
an accepted outcome from an unproven durable side effect.

The multiprocessing regression proves ledger/admission serialization only. It
does not prove full live lifecycle fencing and must not be used to authorize two
overlapping Main processes.

## Verification Commands

| Check | Result |
| --- | --- |
| Focused Tactical V2 matrix | `508 passed in 63.05s` |
| Focused persistence/receipt/bus/concurrency/status matrix | `195 passed in 4.02s` |
| Bounded multiprocessing follow-up (2026-08-13) | `tests/test_tactical_v2_concurrency.py`: `3 passed in 0.21s` |
| Repository regression | `2160 passed, 4 deselected, 580 warnings in 279.65s`; no failures |
| Network-denied/temp-root isolation | `2 passed` |
| Replay CLI, 100 iterations | exit 0; stable normalized identities, episode IDs, and all 22 row reasons |
| Python compilation | `python3.12 -m compileall -q utils agents scripts tests` -> exit 0 |
| OpenSpec artifacts | `openspec status --change tactical-v2-shadow-admission-parity --json` -> complete |
| Credential/path scan | no added secret-like value and no `.env`, secret, or credential path in the change |
| Production mutation | none; replay uses a sanitized fixture, denies socket creation, writes only under temporary roots, and removes default temporary storage |
| Independent review | implementation and spec reviewers rechecked all prior critical/important findings; none remain |

The repository warning baseline is pre-existing pytest deprecation and
multiprocessing warnings. The fresh full-suite result is green.

## Requirement Mapping

| Requirement | Implementation | Test evidence |
| --- | --- | --- |
| Durable candidate receipts | `utils/tactical_v2/controller.py`, `utils/tactical_v2/store.py`, `agents/trading/executor.py` | `tests/test_tactical_v2_candidate_receipts.py`, controller/store regressions |
| Terminal episode renewal from fresh compatible evidence | `utils/tactical_v2/episodes.py` | `tests/test_tactical_v2_episodes.py`, replay/controller/store regressions |
| Normalized Shadow admission parity | `scripts/replay_tactical_v2_admission.py`, pinned fixture | `tests/test_tactical_v2_shadow_admission_parity.py`, parity/entry/governor/protection tests |

All five receipt scenarios, six lifecycle scenarios, and four admission-parity
scenarios have direct test coverage. The implementation keeps existing executable
quote, capacity, TTL, protection, ownership, and production-admission boundaries.

## Issues

- CRITICAL: none within the ledger/candidate-admission concurrency scope after the concurrency and fsync hardening regressions.
- WARNING: one active Main per namespace/account is an operational precondition; overlapping Main live lifecycle is not fenced.
- WARNING: historical fixture lacks executable quote, fill, protection, and terminal lifecycle evidence; this keeps Sidecar admission NO-GO.
- SUGGESTION: run a second replay fixture containing complete close/terminal evidence before interpreting normalized opportunities as Controller-level open counts.

## Operational Decision

Keep Sidecar `admission_enabled=false`. The 2026-08-12 NO-GO gate remains active.
Keep exactly one active Main for the live namespace/account and use stop-then-start
deployment. The ledger/admission lock is not authorization for process overlap.
Only a separate review with real quote-level executable evidence and fill-bound
protection evidence may reconsider restoration. Historical Shadow returns remain
scalar-price counterfactual results and must not be reported as exchange fills or
realized USDT PnL.

## Post-Deploy Live Follow-Up

The reviewed runtime files were deployed and Main restarted on 2026-08-12 while
the Sidecar process remained resident with `admission_enabled=false`. Local and
cloud SHA-256 values match for the executor, event journal, Tactical controller,
episode registry, and store. At the 2026-08-12 13:49 CST snapshot Main was PID
`3163368`, Sidecar was PID `1773370`, and Tactical V2 remained `LIVE 100U x 3`
with `0 active / 0 pending / 3 free`, no integrity halt, and verified protection
and reconciliation.

A natural observation crossed one new 15-minute close and then waited another
120 seconds. No qualified `tactical_candidate.v2` was emitted, so there was no
new receipt, intent, order, fill, or protection lifecycle to validate. Since
restart, six Tactical-shaped counterfactual rows were produced and all six had
`tactical_track_gate=fail`; there were zero true-open Tactical rows. The zero
candidate result is therefore expected at the Judge gate, not evidence of a
Controller message loss. Runtime health remained 19/19 tasks alive, zero failed,
zero DLQ, zero stalled loops, and zero backlogged queues.

The audit also corrected an important terminology error. Generic `[Shadow]
... recorded` log lines describe every CounterfactualLedger rejection; they are
not all Shadow Tactical entries. Three-day `quality_gate` rows remained
`track=main / exit_profile=trend_runner` and are outside this change's admission
contract. A temporary local experiment that routed them toward Tactical V2 was
replayed against 1,348 real decision tapes and produced zero candidates after
direction, 15m hard-veto, short-regime, and RSI gates. That experiment was
rejected and fully removed; `agents/trading/judge.py` is unchanged locally and
on the cloud.

An earlier post-deploy repository rerun on 2026-08-12 produced `2149 passed, 4
deselected, 1 failed`. The sole failure was the environment-dependent historical-tape guard
`tests/test_decision_replay.py::test_no_unclassified_missing_snapshot_keys`,
which read the ignored local 324MB decision tape and found one sparse 2026-06-17
v3 config snapshot. Tactical focused tests remained green (`488 passed`; the
receipt/episode/store/concurrency/bus/parity subset was `294 passed`), compileall
passed, and the 100-loop admission replay remained stable. The ignored historical
tape was not changed to force a green result. The final 2026-08-13 rerun retained
that row in fidelity replay but limited the completeness audit to snapshots with
production sentinels; the full suite then passed with the fresh result recorded
above.
