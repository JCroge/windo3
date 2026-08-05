# Verification Report: promote-shadow-tactical-v2-live

Date: 2026-08-05
Branch: `promote-shadow-tactical-v2-live`
Change: `promote-shadow-tactical-v2-live`
Workflow: full
Verify mode: full

## Current Result

BUILD COMPLETE / FINAL COMET VERIFY PENDING. The invalid first shadow window remains excluded. The repaired deployment completed a fresh 32-hour 24-minute shadow gate, the sidecar drain is archived and admission remains disabled, and Tactical V2 is live at fixed `100U x 3`. The first live cohort and both entry-reconciliation incidents have now been reconciled through final exchange evidence; the self-heal hardening is deployed and under observation.

## Local Checks

| Check | Result | Evidence |
| --- | --- | --- |
| Tactical V2/PnL/executor concurrency matrix | PASS | `pytest -q tests/test_tactical_v2_*.py test_pnl_resolved_event_contract.py test_exchange_realized_pnl_resolver.py` -> `276 passed, 2 warnings in 7.79s` |
| PnL/parity/crash/episode combination | PASS | Focused combination -> `45 passed in 3.07s` |
| Affected legacy suite | PASS | Plan Task 11 legacy command -> `177 passed in 9.43s` |
| Extra V1/classifier/Reviewer checks | PASS | `19 passed in 1.90s` |
| Cross-process JSONL serialization | PASS | Two independent Python processes contended on the same `.lock`; the waiter wrote only after the holder released `flock` |
| Historical replay | PASS | 143 raw rows -> 14 episodes; all 14 evidence gaps classified |
| Repository regression | PASS | `pytest -q` -> `1869 passed, 4 deselected, 576 warnings in 247.51s` |
| Diff whitespace | PASS | `git diff --check` |
| OpenSpec strict validation | PASS | `openspec validate promote-shadow-tactical-v2-live --strict` |

The first repository run produced `1808 passed, 7 failed, 4 deselected` in 248.11s. All seven failures were the same obsolete Telegram fixture group writing V1 `riskguard_state.tactical_circuit` while production now intentionally reads only `tactical_v2_status.json`. The matrix was migrated to the V2 snapshot contract and passed `12/12`; no V1 fallback was added.

## Replay Evidence

`python scripts/replay_tactical_v2.py --fixture tests/fixtures/tactical_v2_reproduced_window.json` reported:

| Metric | Value |
| --- | ---: |
| Raw Shadow rows | 143 |
| Deduplicated episodes | 14 |
| Historical live closes | 7 |
| Historical live PnL | -1.4437U |
| Historical `tactical_invalidated` PnL | -3.2773U |
| Other historical live PnL | +1.8336U |
| Duplicate live attempts | 0 |
| Stale chase fills | 0 |
| TP-before-entry fills | 0 |
| Main strategy exits | 0 |
| Unprotected fills | 0 |
| Full-TP1 violations | 0 |
| Unclassified mismatches | 0 |

All 14 historical comparisons are `legacy_executable_quote_unavailable`. The old ledger did not contain executable bid/ask or stable 15m structure tokens, so the replay records the evidence gap instead of fabricating prices.

## Safety Findings Resolved

- Final PnL can arrive before exchange-flat polling. Recovery now consumes the episode before `closed_final`, while duplicate final delivery uses the governor's durable canonical resolution without double-counting PnL.
- Shadow and live now share full TP1, SL, max-hold, expiry, executable-price fill, and 15m pre-fill structure invalidation semantics. Structure invalidation before the first quote also consumes both lanes.
- Fixed-100U full-TP1 routing now enforces configured Tactical RR as well as EV and cost coverage. A legacy small-size fixture that rounded to RR 0.75 recalculates to 0.6476 under the fixed-100U cost model and is correctly rejected.
- Snapshot audit rows expose terminal and close reasons for shadow-only and live projection comparison.
- Sidecar admission stop and resident event processing now share one cross-process state lock. Each event reloads persisted admission state while holding the lock through processing and offset/seen-id persistence, so a successful `stop-admission` cannot be ignored or overwritten by a stale resident state.
- Main and sidecar now retain distinct exchange owner tags in their shared environment: Main uses `BOT_INSTANCE_ID=main01`, while the sidecar executor forces `SIDECAR_BOT_INSTANCE_ID=stlive`. This prevents sidecar ownership from collapsing into Main after the owner id is configured.

## Crash Matrix

Failure injection covers `before_entry_io`, `after_entry_accept`, `after_partial_fill`, `before_cancel_remainder`, `after_cancel`, `before_protection_verify`, `after_exchange_tp`, `before_local_close_persist`, `after_local_close`, `before_pending_pnl`, and `after_final_pnl`.

For every point, tests assert at most one entry submission, at most one reduce-only close, no terminal episode re-entry, no premature slot release, and either complete TP+SL protection or integrity halt plus owner-bound safe close.

## Cloud Gates

| Gate | Status |
| --- | --- |
| Deploy `TACTICAL_V2_MODE=shadow` with zero V2 exchange commands | PASS; repaired Main started `2026-07-29T20:52:44Z` |
| At least 24 hours executable bid/ask lifecycle and parity evidence | PASS; 389 five-minute samples from `2026-07-29T20:57:34Z` through `2026-07-31T05:21:43Z` |
| Sidecar `stop-admission` persisted while monitor remains resident | PASS; PID `1773370`, active `0`, `admission_enabled=false` |
| Complete owner/exchange/protection/PnL drain archived | PASS; `complete=true`, `retired=true`, all unresolved counters zero |
| V2 `live` cutover at fixed `100U x 3` | ACTIVE from `2026-07-31T05:27:01Z`, Main PID `2013149` |
| First live cohort duplicate/chase/protection/Main-exit review | PASS; five final live intents, five unique single submits, no chase or Main strategy exits, complete TP/SL proof, all seven mismatches classified |

No cloud gate may be inferred from local tests. Unknown exchange truth, owner ambiguity, protection ambiguity, stale status, or undocumented pending PnL keeps live cutover blocked.

## Cloud Shadow Observation

Observation start: `2026-07-28T17:05:55Z` (`2026-07-29 01:05:55 +08:00`). Earliest valid 24-hour review: `2026-07-29T17:05:55Z` (`2026-07-30 01:05:55 +08:00`). This gate remains in progress until elapsed-time and lifecycle evidence are both reviewed.

| Evidence | Initial value |
| --- | --- |
| Cloud source baseline | `/opt/crypto-arbitrage`, original `main@2e2d187` plus this uncommitted change deployment |
| Pre-deploy backup | `backups/pre_tactical_v2_shadow_20260728T165716Z.tgz`, SHA-256 `de0f459fb8110727eb4b8741652d0316a183452a1917ccda3bc64f969033ef9a` |
| Cloud focused verification | `228 passed in 6.84s`; owner/cutover/sidecar follow-up `41 passed in 4.89s` |
| Main process/log | PID `1773371`; `logs/launcher_20260728T170555Z_v2_shadow_observation.log` |
| Sidecar process/log | PID `1773370`; `logs/shadow_tactical_live_sidecar_20260728T170555Z_v2_shadow_observation.log` |
| Read-only observer | PID `1774288`; five-minute samples for 25 hours in `logs/tactical_v2_shadow_monitor_20260728T170555Z.log` |
| Resolved V2 settings | `SHADOW`, fixed `100U x 3`, max 5x, `0.10R`, 900s, 90m, `-15U/24h` |
| Owner identities | Main/V2 `main01`; legacy sidecar `stlive` |
| Initial account state | Main local positions 0; sidecar local positions 0; sidecar active owners 0; admission remains enabled |
| Initial sidecar state | offset `51880405`; 33 opened, 459 rejected, 0 active |
| Initial V2 state | 0 active, 0 pending, 3 free; PnL `0.00U`; streak 0; no integrity halt |
| Baseline ledgers | V2 events 0 rows; live order ledger 141 rows; rejected signal events 46,755 rows |
| Status/TG proof | Snapshot advanced from `1785258420.494603` to `1785258450.7961557`; rendered `Tactical V2 SHADOW | 100U x 3`, circuit clear, protection/reconciliation verified, 0 mismatch |
| Initial V2 exchange proof | No Tactical V2 event rows and no Tactical V2 submit/order audit rows after final restart |

The initial startup and periodic refresh are healthy. The natural Judge pipeline processed KAITO, SHIB, PUMP, WLD, and HYPE after restart, but those decisions were hold or below the eligible quality threshold, so no Tactical V2 candidate had arrived at the baseline checkpoint. Candidate, executable quote, fill/non-fill, structure invalidation, restart recovery with durable intent state, and parity-category evidence remain to be collected from the live observation window. Synthetic candidates will not be injected into the production process.

### Failed Observation Finding: 2026-07-29

The observation gate failed and the window starting `2026-07-28T17:05:55Z` is permanently invalid for live authorization. Main and sidecar remained resident and V2 issued no exchange orders, but an older KAITO shadow episode was replaced in the registry when a newer structural epoch was assigned. When the older filled shadow intent later reached a terminal exit, `EpisodeRegistry.mark_terminal()` raised `KeyError: unknown episode_id: 4020d298...` before the intent transition could be persisted. Every later KAITO quote retried the same transition and exception, leaving one false active shadow slot.

At the `2026-07-29T20:46Z` checkpoint, Main had no account position, sidecar reported zero active positions with admission still enabled, and Tactical V2 remained `shadow` at fixed `100U x 3`. The V2 status showed three shadow fills, one non-fill, one stale KAITO active slot, and no integrity halt. This is a logical lifecycle failure even though no real-money V2 exposure existed. The fix must preserve historical episodes by id, terminate an older episode exactly once without replacing the current epoch, reconcile the stale shadow lifecycle through durable events, and restart a fresh 24-hour observation window.

### Episode-History Fix Deployment: 2026-07-29

The registry now retains every assigned state by `episode_id` while separately tracking the highest monotonic `epoch_seq` for each current symbol/side key. A terminal transition for an older in-flight episode updates its history without replacing the newer current epoch. Store rebuild applies the same monotonic selection, and restart tests preserve both histories. Three new regressions reproduce the cloud sequence at the registry, controller, and store aggregation layers.

Before deployment, `drain-report` proved `exchange_positions=0`, `local_positions=0`, `open_owners=0`, and `protection_ambiguities=0`. It correctly remained incomplete because sidecar admission is enabled and 21 historical PnL rows remain pending. The pre-fix source and V2 ledgers are backed up at `backups/pre_tactical_v2_episode_fix_20260729T205139Z.tgz`, SHA-256 `d2c12fffb8dc1f8f6f252ac2bb84625c1de4766ca6dd075f2e57be1a4233c3c6`.

Cloud focused verification passed `31/31`. Only Main was gracefully restarted; sidecar PID `1773370` remained resident and flat. Main PID `1880348` started with `Tactical V2: SHADOW | 100.0U x 3 | -15.0U/24h`. Natural KAITO executable-price delivery then appended exactly one historical `episode_terminal` at sequence 189 with `tactical_tp1`, followed by the closing intent transition at sequence 190. The current KAITO epoch remained sequence 4 with a different episode id and no terminal reason. Status recovered to zero active, three free slots, no integrity halt, and zero V2 exchange-order rows without deleting or rewriting ledger history.

Fresh observation uses PID `1880945` and `logs/tactical_v2_shadow_monitor_20260729T205734Z_episode_fix.log`. Its first two five-minute samples show Main, sidecar, and observer resident; V2 still shadow-only; Main and sidecar flat; zero Tactical V2 exchange-order rows; and zero `unknown episode_id` or Main error lines in the new launcher log. The earliest eligible 24-hour review is `2026-07-30T20:57:34Z` (`2026-07-31 04:57:34 +08:00`).

### Fresh Shadow Gate Completion: 2026-07-31

The repaired observation completed 389 five-minute samples over 32 hours 24 minutes. Main and the resident sidecar remained up, Tactical V2 stayed `shadow`, `data/positions.json` stayed flat at the final checkpoint, and `data/live_order_events.jsonl` contained zero `tactical_v2` exchange-order rows.

Post-fix durable evidence from sequence 191 through 439 contains seven natural intents. Three filled from executable quotes, installed simulated protection, and reached `closed_final` by `tactical_max_hold`; four reached terminal expiry without a fill. There were no duplicate episode terminals, stale-chase market fallbacks, unclassified integrity events, protection failures, or `unknown episode_id` errors. The three filled projections produce approximately `+2.756U` gross before fees at the fixed `100U`, 5x notional model. This is lifecycle evidence, not a performance claim; the sample contains only three filled final episodes.

The launcher recorded 42 Main errors: 21 provider-balance failures, 16 upstream failures, two timeouts, two provider-concurrency limits, and one non-Tactical error. None was Tactical, episode-registry, protection, ownership, or PnL reconciliation related.

### Sidecar Retirement And Live Cutover: 2026-07-31

Sidecar admission was persisted off while PID `1773370` remained resident and active count stayed zero. The final clean drain proved exchange flat, local positions zero, open owners zero, pending entries zero, protection ambiguities zero, all four ownership proof fields true, and all unresolved counters zero.

Historical PnL reconciliation added six final owner corrections totaling `+7.0169172U` plus one non-attributable PUMP net-mode aggregate correction of `+5.0264986U`. Six remaining owner rows are preserved as accepted documented exceptions because assigning them would double-count an already booked OKX order or fabricate owner-level allocation across a shared net-mode stack. The exception file records the entry/close orders, contract arithmetic, existing ledger identities, and disposition for every row.

The final clean archive is `data/sidecar_retirement.json`, runtime SHA-256 `ab80f436f048c19ef0eda18337469748394d4ac5c29e8c34bcc2b8cd5b24c2a5`; `validate_live_cutover()` returned `sidecar_retirement_verified`. The PnL reconciliation rollback backup is `backups/pre_sidecar_final_pnl_reconcile_20260731T052004Z.tgz`, SHA-256 `21c637aa6eecc6bd6f1480475f9ec90688ec5b0dc90d1198a654bea96a5dbd38`.

Main was then restarted from `logs/launcher_20260731T052657Z_v2_live_cutover.log` as PID `2013149`. Startup resolved `Tactical V2: LIVE | 100.0U x 3 | -15.0U/24h`; status reported cutover allowed, three free slots, no integrity halt, verified protection/reconciliation, and zero rolling PnL. The prior `.env` is backed up at `backups/pre_tactical_v2_live_20260731T052657Z.env`, SHA-256 `2f949c92bad71279b0a929e15cea8516a393deadb9cd62f2780e65d3e4dcf992`. First-cohort monitoring writes to `logs/tactical_v2_live_monitor_20260731T052657Z.log`.

### Interim Checkpoint: 2026-07-28T18:15:41Z

After approximately 1 hour 10 minutes, Main, sidecar, and the read-only observer were each still resident. Tactical V2 status remained fresh with 0 active, 0 pending, 3 free, no integrity halt, no mismatch, and zero V2 exchange/audit events. The monitor reported zero Tactical errors and zero Main positions.

Four new legacy Tactical-shaped WLD short rows appeared after the baseline. All four were explicitly `track=shadow_only`, `tactical_track_gate=fail`, and `tactical_gate_failed=cost_gate,min_rr`, with effective RR `0.4963`, expected value `-0.010545`, and no eligible 15m structure metadata. Tactical V2 correctly published no candidate for them. The legacy sidecar evaluated the same broad `tactical_v1` population and rejected all four as `drift_rr_floor_fail`; it opened no position. Therefore sidecar rejected increasing from 459 to 463 does not indicate a missing V2 order or a V2 candidate-bus failure.

### First Live Cohort Review: 2026-08-05

The durable V2 ledger contains five final live entries: PUMP `-7.1702U`, three KAITO outcomes `+17.0969U`, `+4.9711U`, and `-6.2686U`, plus ADA `+7.5871395U`, totaling `+16.2163395U` after final corrections. This is an execution cohort, not enough observations for a strategy-performance conclusion.

- Each filled intent contains exactly one `submitting_entry` transition and one unique deterministic entry client id. The five identities and exchange entry-order proofs are distinct; the governor has five finals, five unique `resolution_id` values, and no duplicates.
- Two PUMP intents whose executable price had already crossed TP terminated as `missed_after_target` before submission. The one quote beyond the immediate `0.10R` boundary used a limit at frozen entry and filled there; there was no drifted market fallback.
- Three ordinary fills transitioned through `filled_unverified -> protected` with distinct TP and SL algo ids. PUMP and ADA entered integrity halt under the old visibility bug, but later reconciliation proved the exact entry, full TP/SL identities, close, zero open orders/algos, and exchange-flat state. No cohort fill remained unprotected or unresolved.
- Close attribution contains Tactical SL, exchange TP/SL, Tactical max hold, and one reconciled external close. No Position Analyst close/reduce/add event matched a Tactical V2 owner.
- Status reports 14 compared live intents and seven mismatches, all classified as `exchange_fill`; there are no unclassified parity rows. The integrity-halted candidates submitted no order.

### Entry Reconciliation Self-Heal Deployment: 2026-08-05

The first PUMP and ADA submissions exposed an OKX exact-order visibility gap: exchange entry and attached protection existed, but the old one-shot lookup advanced each intent from `submitting_entry` to `entry_reconciliation_unknown` and left admission halted. Both incidents were reconstructed by exact client identity and exchange order/bill evidence, closed final, applied once to the governor, and cleared with complete flat/protection proof.

The controller now persists an entry-visibility deadline, serializes submit versus reconciliation, and rechecks eligible entry integrity halts every 30 seconds without resubmitting. Deferred cancellation retains its original terminal reason. Exact order lookup is tri-state, final PnL uses a durable retry outbox, JSONL append/update operations share thread, instance, and cross-process `flock` serialization, and durable `integrity_required` remains visible in `/status` even if the governor halt slot is transiently empty.

Cloud deployment replaced only the six runtime files whose hashes differed. The rollback backup is `backups/pre_v2_entry_self_heal_followup_20260805T085543Z`. Only Main restarted: PID `2564205`, log `logs/launcher_20260805T085721Z_v2_entry_self_heal_followup.log`; Sidecar PID `1773370` remained resident with `admission_enabled=false` and zero active positions. At `2026-08-05T09:05:41Z`, V2 was fresh and `LIVE`, fixed `100U x 3`, `0 active / 0 pending / 3 free`, no integrity halt, and verified protection/reconciliation. The new launcher contained zero error, traceback, or critical lines. All three durable Tactical correction outbox records were acknowledged, pending external closes were zero, and governor finals remained five unique resolutions with no duplicates.

Residual delivery contract: a process crash after the bus/TG receives a final but before the durable outbox acknowledgement remains at-least-once and can repeat one TG final notification after restart. Governor, Reviewer, and Judge are protected by `resolution_id`; cross-restart exactly-once Telegram consumer delivery is not part of this change.
