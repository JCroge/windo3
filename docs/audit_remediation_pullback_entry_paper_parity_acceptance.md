# Pullback Entry Paper Parity — Acceptance Report

**Change**: `pullback-entry-paper-parity`
**Branch**: `pullback-entry-paper-parity`
**Verify date**: 2026-06-03
**Verify mode**: full
**Base ref**: `f512d1a4c13ec3954fb5c8aed6c2d86acb3ba2a1` (pre-change HEAD on `main`)
**Final HEAD**: 14 commits on top of base
**Test baseline**: `993 passed / 4 deselected / 1 warning` (from 954, net +39 cases)

## TL;DR

OpenSpec change `pullback-entry-paper-parity` is implemented end-to-end. All 28 spec scenarios across two new capabilities (`paper-executor`, `risk-alert-routing`) are covered by passing tests. Build (`compileall`) and verify (`pytest -q`) commands both green. No CRITICAL findings. Two non-blocking notes recorded for ongoing observation; both are explicitly out of this change's scope and tracked in `docs/to-do-list.md`.

## Source-of-truth Inputs

- `openspec/changes/pullback-entry-paper-parity/proposal.md`
- `openspec/changes/pullback-entry-paper-parity/design.md`
- `openspec/changes/pullback-entry-paper-parity/specs/paper-executor/spec.md` (8 Requirements / 22 Scenarios)
- `openspec/changes/pullback-entry-paper-parity/specs/risk-alert-routing/spec.md` (2 Requirements / 6 Scenarios)
- `openspec/changes/pullback-entry-paper-parity/tasks.md` (59 checkboxes / 11 task groups, all marked done)
- `docs/superpowers/specs/2026-06-03-pullback-entry-paper-parity-design.md` (technical-design role; TD-1..TD-7)
- `docs/superpowers/plans/2026-06-03-pullback-entry-paper-parity.md` (1353-line implementation plan, 8 phases)

## Commit Trail (base..HEAD)

```
f56478f docs: sync CLAUDE.md + to-do-list + tasks.md after pullback-entry-paper-parity build
2082935 fix(test): isolate paper_executor module constants in test fixture
f8cfad3 test(paper): cover Req8 empty pending no-op scenario
9d9ff7b test(paper): cover limit fill paths + tick staleness gating
c79e155 test(tg): cover pullback_unfilled / paper_unfilled routing
43a74b5 fix(tg): render limit_price for live pullback_unfilled
ed0d99c feat(executor): tag drift alerts with source + agent-layer pullback log
409f2de fix(paper): pop pending limit before await to prevent zombie entries
144020a fix(paper): close two spec compliance gaps from P2+P3 review
afb61c6 feat(paper): timeout decision tree + cleanup loop
41d9920 feat(paper): limit-order pending state + entry_method field
72e7770 feat(config): add paper_limit_tick_staleness_sec + freezegun dev dep
d452525 chore(openspec): scaffold pullback-entry-paper-parity change
```

(Plus `chore(comet): set build/verify commands + close last build task` for state plumbing.)

## Files Touched (against base)

| Path | LoC delta | Role |
|---|---|---|
| `agents/trading/paper_executor.py` | (large refactor) | core implementation |
| `agents/trading/telegram_notifier.py` | +42 | critical_types + branch handler |
| `agents/trading/executor.py` | +7 | agent-layer pullback log + drain alert |
| `executor.py` | ±6 | source default in `_enqueue_drift_alert` |
| `utils/config_loader.py` | +6 | DEFAULTS + ENV map + HARD_LIMITS |
| `requirements.txt` / `requirements.lock` | +1 each | `freezegun==1.5.1` |
| `.env.example` | +3 | `PAPER_LIMIT_TICK_STALENESS_SEC` doc |
| `tests/test_paper_limit_fill.py` | +431 | 17 cases |
| `tests/test_telegram_pullback_alerts.py` | +124 | 6 cases |
| `CLAUDE.md` | +2 | baseline + red-line |
| `docs/to-do-list.md` | +13 / -1 | open follow-ups |
| `docs/superpowers/specs/2026-06-03-...-design.md` | +228 | technical design |
| `docs/superpowers/plans/2026-06-03-...md` | +1353 | implementation plan |
| `openspec/changes/pullback-entry-paper-parity/**` | — | OpenSpec artifacts |

## Verification Commands Run

```bash
$ env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
(silent — no errors)

$ python3 -m pytest -q
993 passed, 4 deselected, 1 warning in 174.53s (0:02:54)

$ python3 -m pytest -q tests/test_paper_limit_fill.py tests/test_telegram_pullback_alerts.py
23 passed in 1.45s
```

Build guard `bash $COMET_GUARD pullback-entry-paper-parity build --apply` → ALL PASS.

## Spec Coverage Matrix

### Capability: `paper-executor` (8 Requirements / 22 Scenarios)

| Req | Scenario | Test File:Func | Status |
|---|---|---|---|
| 1 | Limit plan defers to wait_paper_limit_fill | `test_paper_limit_fill.py:test_limit_plan_enters_pending_not_position` | ✅ |
| 1 | Market plan keeps legacy immediate fill | `test_paper_limit_fill.py:test_market_plan_immediate_fill` | ✅ |
| 1 | Limit plan with missing entry_zone falls back to market | `test_paper_limit_fill.py:test_limit_plan_missing_entry_zone_falls_back_to_market` | ✅ |
| 2 | Tick price inside entry_zone triggers fill | `test_paper_limit_fill.py:test_tick_inside_zone_fills_at_midpoint` | ✅ |
| 2 | Tick price crosses entry_zone instantaneously | `test_paper_limit_fill.py:test_tick_crossing_zone_fills` | ✅ |
| 2 | Tick price never enters entry_zone | `test_paper_limit_fill.py:test_timeout_no_fallback_paper_unfilled` (covers the no-fill setup) | ✅ |
| 3 | Pullback policy timeout (no_fallback=True) | `test_paper_limit_fill.py:test_timeout_no_fallback_paper_unfilled` | ✅ |
| 3 | Non-pullback limit timeout (no_fallback=False), fresh tick | `test_paper_limit_fill.py:test_timeout_fallback_market_with_fresh_tick` | ✅ |
| 3 | Non-pullback limit timeout with no tick available | `test_paper_limit_fill.py:test_timeout_fallback_no_tick` | ✅ |
| 4 | Market open writes entry_method=market | `test_paper_limit_fill.py:test_market_plan_immediate_fill` | ✅ |
| 4 | Limit fill writes entry_method=limit_filled | `test_paper_limit_fill.py:test_close_record_carries_entry_method_and_legacy_default` (Path A) | ✅ |
| 4 | Limit unfilled rejection writes entry_method=limit_unfilled | `test_paper_limit_fill.py:test_timeout_no_fallback_paper_unfilled` (asserts `_rejected_log` field) | ✅ |
| 4 | Legacy record without entry_method | `test_paper_limit_fill.py:test_close_record_carries_entry_method_and_legacy_default` (Path B) | ✅ |
| 5 | Duplicate open_short during pending limit | `test_paper_limit_fill.py:test_duplicate_open_during_pending_skipped` | ✅ |
| 5 | Close arrives during pending limit | `test_paper_limit_fill.py:test_close_cancels_pending` | ✅ |
| 6 | Restart drops pending limits | `test_paper_limit_fill.py:test_restart_drops_pending_limits` | ✅ |
| 6 | save_state does not serialize pending limits | `test_paper_limit_fill.py:test_save_state_does_not_serialize_pending` | ✅ |
| 7 | Stale tick blocks fallback | `test_paper_limit_fill.py:test_timeout_fallback_stale_tick` | ✅ |
| 7 | Fresh tick allows fallback | `test_paper_limit_fill.py:test_timeout_fallback_market_with_fresh_tick` + `test_custom_staleness_threshold` | ✅ |
| 7 | Custom staleness threshold honored | `test_paper_limit_fill.py:test_custom_staleness_threshold` (asserts `_tick_staleness_sec == 120.0` and 101s tick still fresh against 120s threshold) | ✅ |
| 8 | Cleanup runs each tick cycle | `test_paper_limit_fill.py:test_cleanup_loop_runs_each_tick` | ✅ |
| 8 | Empty pending limits is no-op | `test_paper_limit_fill.py:test_cleanup_loop_empty_pending_no_op` | ✅ |

**Coverage**: 22/22 scenarios. ✅

### Capability: `risk-alert-routing` (2 Requirements / 6 Scenarios)

| Req | Scenario | Test File:Func | Status |
|---|---|---|---|
| 1 | Live pullback_unfilled triggers TG message | `test_telegram_pullback_alerts.py:test_pullback_unfilled_live_prefix` | ✅ |
| 1 | Paper paper_unfilled triggers TG message | `test_telegram_pullback_alerts.py:test_paper_unfilled_paper_prefix` | ✅ |
| 1 | Other alert types unaffected | `test_telegram_pullback_alerts.py:test_unknown_type_does_not_send` | ✅ |
| 2 | Paper alert has source=paper_executor | `test_telegram_pullback_alerts.py:test_paper_unfilled_paper_prefix` (payload + `[模拟]` prefix) | ✅ |
| 2 | Live alert has live source | `test_telegram_pullback_alerts.py:test_pullback_unfilled_live_prefix` + `test_paper_and_live_unfilled_distinguished` | ✅ |
| 2 | TG message prefix reflects source | `test_telegram_pullback_alerts.py:test_paper_and_live_unfilled_distinguished` | ✅ |

Bonus regression coverage (not strictly enumerated as scenarios but reinforces Req2 prose):

- `test_telegram_pullback_alerts.py:test_paper_unfilled_no_tick_variant` — `subtype='no_tick'` renders 行情失联 variant
- `test_telegram_pullback_alerts.py:test_pullback_unfilled_missing_source_fail_safe` — missing source → `[实盘]` default + `logger.warning`

**Coverage**: 6/6 scenarios. ✅

## Two-Stage Review History

Each phase passed both **spec compliance** and **code quality** review by an isolated subagent before being merged into the branch:

| Phase | Initial finding | Resolution commit |
|---|---|---|
| P1 | ✅ APPROVED first time | (no fix needed) |
| P2+P3 | Spec gaps: Req1 Sc3 missing warning; Req3 Sc2 log lacks `paper_limit_fallback_used` keyword | `144020a` fix(paper): close two spec compliance gaps |
| P2+P3 (quality) | MUST-FIX: zombie pending if `_open_paper_at_price` raises after pop is post-await | `409f2de` fix(paper): pop pending limit before await |
| P4+P5 | MUST-FIX: live `pullback_unfilled` rendered `区间: []` instead of `limit_price` | `43a74b5` fix(tg): render limit_price for live pullback_unfilled |
| P6+P7 | Spec gap: Req8 Scenario 2 (empty pending no-op) uncovered | `f8cfad3` test(paper): cover Req8 empty pending no-op |
| Full pytest suite | Tests fail when run after `test_paper_executor.py` (root-level) due to module-constant pollution | `2082935` fix(test): isolate paper_executor module constants |

All findings closed in-cycle; none deferred.

## Non-blocking Notes (for record)

These observations were raised during code-quality review but explicitly accepted as non-blocking for this change. Each is now tracked in `docs/to-do-list.md`:

1. **Pre-existing publish-after-mutate pattern**: `_open_paper_at_price` and `_close_paper` mutate `_positions` / `_equity` *before* awaiting `publish(...)`. If the bus publish raises, state is mutated without an event. This pre-dates the change and was already present in `_close_paper`. Not introduced by this work; not fixed by this work; flagged for future paper-executor refactor.

2. **`30` second default `limit_timeout_sec` magic literal**: in `_enqueue_pending_limit`, `plan.get('limit_timeout_sec', 30)` is unnamed. The 1800s pullback timeout overrides this; the 30s is only a defensive default for non-pullback limits. A `DEFAULT_PAPER_LIMIT_TIMEOUT_SEC = 30` constant would mirror the staleness constant pattern. YAGNI for now.

3. **Bundled scaffold commit `d452525`**: The OpenSpec scaffolding commit accidentally bundled the design doc + plan markdown along with the change artifacts. Substantively correct; commit hygiene cosmetic only. Not split because rewriting branch history adds risk for no functional benefit.

## Out-of-Scope Follow-ups

These were explicitly declared non-goals (proposal §Non-goals + design §Goals/Non-Goals) and remain open in `docs/to-do-list.md`:

| Open | Tracked in to-do-list | Trigger for future change |
|---|---|---|
| Paper 双轨 (idealized + realistic) 模拟 | Yes | After Paper 独立复盘 reviewer is built |
| `ma_aligned` 收窄 PULLBACK_ATR_ENTRY_TYPES (issue #2) | Yes | Data-driven decision after collecting paper realistic data |
| `PULLBACK_LIMIT_TIMEOUT_SEC` 数值调参 (issue #4) | Yes | Same — observe unfilled rate first |
| `paper_limit_tick_staleness_sec` 阈值调参 | Yes | Observe `paper_unfilled_no_tick` ratio in production |

## CLAUDE.md / Red-line Updates

- New current-fact entry recording 993 baseline + paper limit 撮合 contract (`_wait_paper_limit_fill` single funnel + `_scan_pending_limits` cleanup loop + `entry_method` field + critical_types extension + source-based prefix)
- New red-line entry: "Paper limit 撮合单一入口 (2026-06-03)" mandating `_pending_limits` queue path, `_open_paper_at_price` single-funnel, no `_pending_limits` persistence, `source` field discipline

## Branch Status

- Local branch `pullback-entry-paper-parity` clean except `.comet.yaml` (verify-phase product) and a single stash entry (`stash@{0}: model-downgrade-4.7-to-4.6` — unrelated, parked)
- Branch is **ready** for merge / PR / keep — pending user decision in finishing-a-development-branch step

## Verdict

✅ **PASS** — All 28 spec scenarios covered by green tests. All review findings resolved. No CRITICAL items. No spec drift. Ready for archive.

## Implementation ↔ Design Doc Alignment (TD-1..TD-7)

| Decision | Implementation | Verified |
|---|---|---|
| TD-1 — tick + cleanup dual-driver | `paper_executor.on_message[price_tick]` invokes `_wait_paper_limit_fill` (tick path); `paper_executor.tick()` invokes `_scan_pending_limits` every 30s (cleanup path) | ✅ test_tick_inside_zone_fills_at_midpoint + test_cleanup_loop_runs_each_tick |
| TD-2 — freezegun, no `_now()` helper | `tests/test_paper_limit_fill.py` uses `freeze_time` + `frozen.tick(delta=...)`; production code uses bare `time.time()` | ✅ tests pass; no `_now()` indirection added |
| TD-3 — configurable staleness threshold | `paper_executor.DEFAULT_PAPER_LIMIT_TICK_STALENESS_SEC = 60` + `__init__` reads `config['paper_limit_tick_staleness_sec']`; `utils/config_loader.py` DEFAULTS / ENV map / HARD_LIMITS all updated | ✅ test_custom_staleness_threshold (120s override) |
| TD-4 — pending dict shape | `_pending_limits[symbol] = {created_at, deadline, side, action, plan, decision, entry_zone, limit_no_fallback}`; `_latest_tick_ts` separate dict | ✅ test_limit_plan_enters_pending_not_position asserts shape |
| TD-5 — timeout decision tree | `_resolve_pending_timeout` single function: no_fallback=True → reject; fresh tick → market; stale tick → no_tick reject | ✅ Req3 + Req7 scenarios |
| TD-6 — TG critical_types + source prefix | `telegram_notifier._handle_risk_alert` extends critical_types tuple + adds branch with `[实盘]/[模拟]` prefix | ✅ all 6 risk-alert-routing scenarios |
| TD-7 — root → agent logger passthrough | `agents/trading/executor._drain_drift_alerts` logs `[Pullback]` line for `pullback_unfilled` alerts at agent layer | ✅ code present at `agents/trading/executor.py:432-436` (manually verified — runtime check would need a live executor stub) |

