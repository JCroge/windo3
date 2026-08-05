---
change: promote-shadow-tactical-v2-live
design-doc: docs/superpowers/specs/2026-07-28-promote-shadow-tactical-v2-live-design.md
base-ref: 2e2d1871631bd8a36b8f0775cfe3c1bafd3a9b15
---

# Shadow Tactical V2 Live Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Promote the existing Shadow Tactical plan population into a crash-safe Main-process Tactical V2 live lifecycle with fixed 100U sizing, three slots, non-chasing entries, isolated exits, persistent circuit state, Telegram visibility, and a gated sidecar retirement.

**Architecture:** MultiExecutor owns a TacticalV2Controller that shares the one ContractExecutor and common OKX position truth. Pure modules under utils/tactical_v2 implement intent/episode identity, entry transitions, durable storage, risk state, lane parity, and status; exchange I/O is isolated behind an adapter and every external command is preceded by a durable transition.

**Tech Stack:** Python 3.12, asyncio, dataclasses, JSONL plus atomic JSON snapshots, the existing MessageBus/EventJournal/ContractExecutor/LiveLedger, ccxt-compatible OKX APIs, pytest, and existing cloud process scripts.

**Commit policy:** The user explicitly requested that the pre-existing sidecar resident-run changes be preserved and submitted with this Comet change. Do not create intermediate commits. Use tests and task checkboxes as checkpoints, then create one reviewed final commit after Comet verification includes scripts/shadow_tactical_live_sidecar.py and tests/test_shadow_tactical_live_cli.py.

---

## File Structure

- Create utils/tactical_v2/models.py for immutable schemas, enums, canonical hashes, and deterministic ids.
- Create utils/tactical_v2/episodes.py for persisted 15m structural epoch and one-attempt rules.
- Create utils/tactical_v2/entry.py for pure bid/ask, 0.10R, TTL, fill, and cancellation transitions.
- Create utils/tactical_v2/store.py for fsynced JSONL events, replay, and atomic snapshots.
- Create utils/tactical_v2/governor.py for slots, rolling final PnL, streak cooldown, and integrity halt.
- Create utils/tactical_v2/shadow.py for executable-price shadow lane transitions.
- Create utils/tactical_v2/exchange.py for the narrow ContractExecutor live adapter.
- Create utils/tactical_v2/status.py for the atomic Telegram read model.
- Create utils/tactical_v2/controller.py for serialized orchestration and recovery.
- Create utils/tactical_v2/cutover.py for sidecar drain reports and retirement proof validation.
- Create scripts/replay_tactical_v2.py for deterministic historical replay and parity reports.
- Modify utils/state_paths.py, utils/config_loader.py, .env.example, and run_agents.py for V2 paths, validated configuration, and startup banner.
- Modify agents/message_bus.py and utils/event_journal.py for durable high-priority tactical_candidate.v2 delivery.
- Modify agents/trading/tech_analyst.py and agents/trading/judge.py for structural metadata and exact frozen candidate publication.
- Modify agents/trading/executor.py and executor.py for controller ownership, entry/protection primitives, position metadata, exits, and PnL routing.
- Modify agents/trading/position_analyst.py and agents/trading/portfolio_risk_guard.py to isolate V2 policy while retaining global safety.
- Modify agents/trading/telegram_notifier.py for freshness-aware Tactical V2 status.
- Modify scripts/shadow_tactical_live_sidecar.py and utils/shadow_tactical_live.py for admission stop, drain, report, and archive.
- Add focused tests under tests/test_tactical_v2_*.py and extend existing Tactical, owner-isolation, sidecar, PnL, and Telegram tests.
- Update docs/runbook.md, README.md, OpenSpec tasks, and a verification report with rollout evidence.

## Task 1: Configuration, Paths, And Immutable Models

**Files:**
- Create: utils/tactical_v2/__init__.py
- Create: utils/tactical_v2/models.py
- Modify: utils/state_paths.py
- Modify: utils/config_loader.py
- Modify: .env.example
- Modify: run_agents.py
- Create: tests/test_tactical_v2_models.py
- Create: tests/test_tactical_v2_config.py

- [x] **Step 1: Write failing model and state-path tests**

```python
def test_intent_freezes_exact_shadow_plan():
    raw = {
        "candidate_id": "cand-1", "symbol": "WLD-USDT", "side": "long",
        "entry_ref": 1.0, "stop_loss": 0.95, "take_profit": 1.08,
        "leverage": 5, "source_shadow_id": "shadow-7",
        "tactical_source": "rr_below_floor", "created_at": 1000.0,
    }
    intent = TacticalIntent.from_candidate(raw, episode_id="ep-1")
    assert intent.entry_ref == 1.0
    assert intent.stop_loss == 0.95
    assert intent.take_profit == 1.08
    assert intent.margin_usdt == 100.0
    assert intent.max_hold_seconds == 5400
    with pytest.raises(FrozenInstanceError):
        intent.entry_ref = 1.01


def test_tactical_paths_follow_namespace():
    paths = StatePaths.for_namespace("testnet")
    assert paths.tactical_v2_events == "data/testnet_tactical_v2_events.jsonl"
    assert paths.tactical_v2_state == "data/testnet_tactical_v2_state.json"
    assert paths.tactical_v2_status == "data/testnet_tactical_v2_status.json"
    assert paths.sidecar_retirement == "data/testnet_sidecar_retirement.json"
```

- [x] **Step 2: Run the tests and confirm the missing API**

Run:

```bash
pytest tests/test_tactical_v2_models.py tests/test_tactical_v2_config.py -q
```

Expected: FAIL because utils.tactical_v2 and the four StatePaths fields do not exist.

- [x] **Step 3: Add explicit validated configuration**

Add defaults and environment mappings with these exact values:

```python
"tactical_v2_mode": "off",
"tactical_v2_margin_usdt": 100.0,
"tactical_v2_max_concurrent": 3,
"tactical_v2_max_leverage": 5,
"tactical_v2_entry_max_worse_r": 0.10,
"tactical_v2_entry_ttl_seconds": 900,
"tactical_v2_max_hold_minutes": 90,
"tactical_v2_rolling_loss_limit_usdt": -15.0,
"tactical_v2_loss_streak_count": 3,
"tactical_v2_loss_streak_pause_minutes": 60,
"tactical_v2_status_stale_seconds": 90,
```

Validate mode against off, shadow, and live; reject non-finite numeric values and values outside the fixed safety constraints. Add matching TACTICAL_V2_* entries to .env.example and display resolved V2 mode, 100U x 3, -15U/24h, and owner identity in the startup banner.

- [x] **Step 4: Implement immutable schemas and deterministic identity**

Define frozen dataclasses TacticalCandidate, TacticalIntent, TacticalEvent, LaneState, ProtectionIdentity, and FinalResolution. Canonicalize symbols with utils.symbol.to_internal. Use decimal-string canonicalization before SHA-256 hashing so 1 and 1.0 produce one plan hash. Derive intent id from candidate id plus episode id and derive client ids later from intent id plus purpose.

- [x] **Step 5: Run focused tests and update OpenSpec tasks 2.1 and 2.4 only when their assertions pass**

Run:

```bash
pytest tests/test_tactical_v2_models.py tests/test_tactical_v2_config.py -q
```

Expected: PASS with no changes to existing live/testnet/paper path expectations.

## Task 2: Durable Store And Structural Episodes

**Files:**
- Create: utils/tactical_v2/store.py
- Create: utils/tactical_v2/episodes.py
- Modify: agents/trading/tech_analyst.py
- Create: tests/test_tactical_v2_store.py
- Create: tests/test_tactical_v2_episodes.py
- Create: tests/test_tactical_v2_structure.py

- [x] **Step 1: Add failing replay, corruption, and episode tests**

```python
def test_store_replay_ignores_snapshot_after_last_sequence(tmp_path):
    store = TacticalStore(paths_for(tmp_path))
    store.append("intent_created", {"intent_id": "i1"})
    store.write_snapshot({"last_seq": 1, "intents": {"i1": "ready"}})
    store.append("episode_terminal", {"intent_id": "i1", "reason": "expired"})
    state = store.rebuild()
    assert state["intents"]["i1"]["reason"] == "expired"
    assert state["last_seq"] == 2


def test_same_structure_repeated_prices_share_episode(registry):
    first = registry.assign(candidate("WLD", "long", 1.0), structure("bull", 900))
    second = registry.assign(candidate("WLD", "long", 1.01), structure("bull", 900))
    assert first.episode_id == second.episode_id
    assert second.eligible is False
```

- [x] **Step 2: Run tests and confirm failure**

Run:

```bash
pytest tests/test_tactical_v2_store.py tests/test_tactical_v2_episodes.py tests/test_tactical_v2_structure.py -q
```

Expected: FAIL because the store, episode registry, closed-bar timestamp, and structure token are absent.

- [x] **Step 3: Implement one fsynced writer and snapshot replay**

Each JSONL row must contain schema_version, seq, event_id, event_type, emitted_at, and data. Under a threading lock, append one line, flush, and fsync. Rebuild starts from a valid atomic snapshot then applies events with seq greater than snapshot.last_seq. A malformed tail row is reported and ignored only when it is the final partial line; malformed committed history activates integrity failure.

- [x] **Step 4: Add stable 15m observational structure metadata**

Extend the result of _analyze_15m_timing with tf_15m_closed_bar_ts and tf_15m_structure_token. Derive the token only from closed candles: last confirmed swing high/low, direction of a close beyond that pivot, and the closed-bar timestamp. Preserve all existing confirm/block thresholds and tests.

- [x] **Step 5: Implement persisted epoch transitions**

Maintain symbol/side epoch_seq, current bias, neutral_seen, last block edge, and last structure token. Advance only for opposing block, neutral then renewed direction, or a new confirmed token after terminal state. Persist the reset evidence before assigning a new episode. Missing/stale structure can keep the current episode but cannot reset it.

- [x] **Step 6: Verify deterministic replay**

Run:

```bash
pytest tests/test_tactical_v2_store.py tests/test_tactical_v2_episodes.py tests/test_tactical_v2_structure.py -q
```

Expected: PASS including restart producing identical episode ids and terminality.

## Task 3: Pure Entry State Machine And Shadow Lane

**Files:**
- Create: utils/tactical_v2/entry.py
- Create: utils/tactical_v2/shadow.py
- Test: tests/test_tactical_v2_entry.py
- Test: tests/test_tactical_v2_shadow.py
- Add fixture: tests/fixtures/tactical_v2_wld_window.json

- [x] **Step 1: Add table-driven 0.10R and terminal tests**

```python
@pytest.mark.parametrize(
    "side,bid,ask,expected",
    [
        ("long", 1.004, 1.005, "immediate"),
        ("long", 1.005, 1.0050001, "pending_limit"),
        ("short", 0.995, 0.996, "immediate"),
        ("short", 0.9949999, 0.996, "pending_limit"),
    ],
)
def test_point_one_r_boundary(side, bid, ask, expected):
    plan = intent(side=side, entry=1.0, stop=0.95 if side == "long" else 1.05)
    assert classify_entry(plan, quote(bid, ask, at=1000)).action == expected


def test_tp_before_fill_consumes_episode():
    state = pending_long(entry=1.0, stop=0.95, tp=1.08)
    out = reduce_quote(state, quote(bid=1.08, ask=1.081, at=1010))
    assert out.command == "cancel_entry"
    assert out.terminal_reason == "missed_after_target"
```

- [x] **Step 2: Confirm failing tests**

Run:

```bash
pytest tests/test_tactical_v2_entry.py tests/test_tactical_v2_shadow.py -q
```

Expected: FAIL because entry and shadow reducers do not exist.

- [x] **Step 3: Implement side-aware executable pricing and TTL**

Require finite positive bid/ask and a freshness age no greater than the configured tick limit. For longs use ask on entry and bid on exit; reverse them for shorts. Calculate worse drift in absolute price and compare with 0.10 times abs(entry-stop). Expiry is created_at plus 900 seconds and never extends on restart.

- [x] **Step 4: Implement cancel/fill race states**

Cancellation produces canceling_entry and keeps the slot. Only confirmed canceled plus zero fill becomes the requested terminal outcome. Any confirmed fill moves to partial_fill or filled_unverified. Unknown order state emits integrity_required. A partial fill always commands cancel_remainder before protection verification.

- [x] **Step 5: Implement shadow execution using the same reducer**

ShadowAdapter supplies simulated order observations but cannot submit exchange work. A long limit fills only at ask <= entry; a short fills only at bid >= entry. Store lane=shadow on all adapter observations and keep non-filled outcomes out of filled PnL statistics.

- [x] **Step 6: Verify entry and shadow behavior**

Run:

```bash
pytest tests/test_tactical_v2_entry.py tests/test_tactical_v2_shadow.py -q
```

Expected: PASS for favorable price, exact boundary, worse drift, TP/SL-before-fill, structure invalidation, expiry, partial fill, and restart TTL.

## Task 4: Persistent Governor And Corrected Final PnL

**Files:**
- Create: utils/tactical_v2/governor.py
- Test: tests/test_tactical_v2_governor.py
- Modify: agents/trading/portfolio_risk_guard.py
- Extend: tests/test_tactical_circuit_breaker.py

- [x] **Step 1: Add failing rolling, correction, streak, and integrity tests**

```python
def test_correction_applies_delta_not_second_trade():
    g = governor(now=100000)
    g.apply_final(resolution("r1", "p1", -9.0, at=90000))
    g.apply_final(resolution("r2", "p1", -4.0, at=90000))
    assert g.rolling_pnl == -4.0
    assert g.final_episode_count == 1


def test_three_losses_pause_and_consume_streak():
    g = governor(now=1000)
    for idx in range(3):
        g.apply_final(resolution(f"r{idx}", f"p{idx}", -1.0, at=1000 + idx))
    assert g.pause_until == 4602
    assert g.loss_streak == 0
    assert g.can_open(now=1003).reason == "loss_streak_pause"
```

- [x] **Step 2: Confirm failure**

Run:

```bash
pytest tests/test_tactical_v2_governor.py tests/test_tactical_circuit_breaker.py -q
```

Expected: FAIL because legacy code uses natural-day additive PnL and volatility-dependent slots.

- [x] **Step 3: Implement reconstructed final-truth accounting**

Deduplicate by resolution_id. Group corrections by position_id or entry_request_id and retain the latest final value. Recompute rolling 24-hour PnL from close timestamps after every final event or correction. Reject pending, estimated, mismatch, missing-id, and non-finite inputs.

- [x] **Step 4: Implement all admission reasons in one API**

Governor.can_open receives now, active_count, pending_count, same_symbol_state, account_gate, and integrity state. Return a typed AdmissionDecision for rolling_loss_pause, loss_streak_pause, integrity_halt, capacity_full, same_symbol_exposure, account_reject, or admitted. Existing position management never calls can_open.

Do not add a volatility or correlation gate to the first V2 cohort. Extreme/news and shared account-integrity vetoes remain external account gates, while the Tactical slot cap stays three in every normal volatility regime.

- [x] **Step 5: Disable legacy V2 authority in PortfolioRiskGuard**

Keep global drawdown and emergency behavior. For exit_profile=tactical_v2, do not update legacy tactical_daily_pnl, quality windows, volatility concurrency, or pause state. Legacy Tactical V1 remains readable during drain.

- [x] **Step 6: Verify governor and correction behavior**

Run:

```bash
pytest tests/test_tactical_v2_governor.py tests/test_tactical_circuit_breaker.py -q
```

Expected: PASS for -15U inclusive threshold, time-window eviction, correction delta, three-loss consumption, zero reset, timed pause expiry, and proof-only integrity clearing.

## Task 5: Candidate Publication And Controller Shadow Integration

**Files:**
- Create: utils/tactical_v2/controller.py
- Modify: agents/message_bus.py
- Modify: utils/event_journal.py
- Modify: agents/trading/judge.py
- Modify: agents/trading/executor.py
- Test: tests/test_tactical_v2_candidate_bus.py
- Test: tests/test_tactical_v2_controller.py
- Extend: tests/test_tactical_wld_replay.py

- [x] **Step 1: Add failing exact-plan and no-legacy-open integration tests**

```python
async def test_judge_publishes_candidate_and_main_hold(judge, capture_bus):
    decision = await run_qualifying_shadow_case(judge)
    candidate = capture_bus.one("tactical_candidate.v2")
    assert decision["action"] == "hold"
    assert candidate["entry_ref"] == qualifying_shadow_entry
    assert candidate["stop_loss"] == qualifying_shadow_sl
    assert candidate["take_profit"] == qualifying_shadow_tp1
    assert capture_bus.count("trade_decision", action="open_long") == 0
```

- [x] **Step 2: Confirm failing bus/controller tests**

Run:

```bash
pytest tests/test_tactical_v2_candidate_bus.py tests/test_tactical_v2_controller.py tests/test_tactical_wld_replay.py -q
```

Expected: FAIL because Judge routes Tactical V1 through trade_decision and MultiExecutor has no controller.

- [x] **Step 3: Make tactical_candidate.v2 durable and high priority**

Add the topic to MessageBus PRIORITY_HIGH mapping, _IMPORTANT_TOPICS, and EventJournal.CRITICAL_TOPICS. Preserve journal msg_id in the delivered envelope. Every candidate carries StatePaths.namespace; restart replay filters to the current namespace and created_at within the 900-second intent window before import. Add EventJournal replay tests proving unimported same-namespace candidate messages can be read after restart and cross-namespace rows cannot be imported.

- [x] **Step 4: Publish frozen candidates at the Tactical classification point**

Use the exact _apply_tactical_shadow_profile output. Include source technical structure metadata and deterministic candidate_id. Never call the Main entry drift or V1 open path for a V2 candidate. Keep existing rejected-signal logging during shadow comparison.

- [x] **Step 5: Preserve Tactical full-TP1 economics**

Calculate tactical_rr, tactical_ev, and tactical_cost_gate from frozen entry, frozen SL, full-position TP1, configured fees, slippage, and funding approximation. Add a regression where Main TP2/TP3 ladder R:R passes but full-TP1 net EV fails; assert no candidate is published and tactical_cost_gate or Tactical EV is the recorded rejection.

- [x] **Step 6: Wire the controller into the one MultiExecutor**

Construct TacticalV2Controller in setup with the existing ContractExecutor, config, StatePaths, logger, and async publish callback. Subscribe to tactical_candidate.v2, price_tick:*, tech_analysis:*, pnl_resolved, and pnl_mismatch. Route candidate, quote, structure, and external final events to typed controller methods. Call controller.tick before generic position checks.

- [x] **Step 7: Prevent pending-symbol cross-strategy opens**

Before a Main open, ask controller.blocks_main_symbol(norm_symbol). Before Tactical admission, inspect ContractExecutor positions plus active sidecar ownership. Mark a blocked Tactical episode terminal; reject a Main open as tactical_pending_symbol without mutating Tactical state.

- [x] **Step 8: Verify shadow-only cannot reach exchange methods**

Run:

```bash
pytest tests/test_tactical_v2_candidate_bus.py tests/test_tactical_v2_controller.py tests/test_tactical_wld_replay.py -q
```

Expected: PASS with controller mode=shadow and create_order, cancel_order, and close_position call counts all zero.

## Task 6: Live Entry, Deterministic Orders, And Full Protection

**Files:**
- Create: utils/tactical_v2/exchange.py
- Modify: executor.py
- Test: tests/test_tactical_v2_exchange.py
- Test: tests/test_tactical_v2_protection.py
- Extend: tests/test_shadow_tactical_owner_isolation.py

- [x] **Step 1: Add failing deterministic-id and protection tests**

```python
def test_tactical_ids_are_stable_and_owner_tagged(executor):
    first = executor.make_tactical_clord_id("intent-abc", "entry")
    second = executor.make_tactical_clord_id("intent-abc", "entry")
    assert first == second
    assert executor._is_owner_clord_id(first)
    assert len(first) <= 32
    assert executor.make_tactical_clord_id("intent-abc", "tp") != first


async def test_fill_requires_full_tp_and_sl(exchange_adapter):
    exchange_adapter.fake_fill(quantity=4)
    exchange_adapter.fake_algos(tp_qty=4, sl_qty=4)
    proof = await exchange_adapter.verify_protection(intent(), filled_qty=4)
    assert proof.complete is True


async def test_live_entry_requests_fixed_one_hundred_margin(exchange_adapter):
    await exchange_adapter.submit_entry(intent(margin_usdt=100.0))
    assert exchange_adapter.requested_margin_usdt == 100.0
    assert exchange_adapter.main_max_trade_amount_writes == []
```

- [x] **Step 2: Confirm failure before live adapter implementation**

Run:

```bash
pytest tests/test_tactical_v2_exchange.py tests/test_tactical_v2_protection.py tests/test_shadow_tactical_owner_isolation.py -q
```

Expected: FAIL on missing deterministic Tactical ids and TP+SL proof.

- [x] **Step 3: Add narrow ContractExecutor Tactical primitives**

Add deterministic client-id generation, submit_tactical_entry, query_tactical_entry, cancel_tactical_entry, verify_tactical_protection, cancel_tactical_protection, and close_tactical_position. These methods normalize exchange response shapes but contain no episode, governor, or retry policy.

- [x] **Step 4: Submit attached protection for market and limit entry**

Build OKX attachAlgoOrds with frozen TP and SL plus distinct deterministic client identities when the API shape permits. Persist entry client id before create_order. Accept one combined OCO id or separate algo ids only after both trigger and quantity proofs pass.

- [x] **Step 5: Fail closed on partial or unknown protection**

On any fill, cancel unfilled entry remainder and prove cancellation. Verify exact filled quantity protection. If either leg is absent, wrong-sized, wrong-priced, foreign, or unknown, persist integrity failure, cancel only proven Tactical orders, and attempt an owner-bound reduce-only close.

- [x] **Step 6: Verify exchange behavior**

Run:

```bash
pytest tests/test_tactical_v2_exchange.py tests/test_tactical_v2_protection.py tests/test_shadow_tactical_owner_isolation.py -q
```

Expected: PASS for market, limit, partial fill, cancel/fill race, combined OCO, separate algos, response loss, foreign/manual preservation, and safe-close fallback.

## Task 7: V2 Exit Isolation And Idempotent Reconciliation

**Files:**
- Modify: utils/tactical_v2/controller.py
- Modify: agents/trading/executor.py
- Modify: agents/trading/position_analyst.py
- Modify: executor.py
- Test: tests/test_tactical_v2_exit.py
- Test: tests/test_tactical_v2_main_isolation.py
- Extend: test_partial_tp_lifecycle.py

- [x] **Step 1: Add failing Main-interference tests**

```python
@pytest.mark.parametrize(
    "action",
    ["position_analyst_close", "position_analyst_reduce", "position_analyst_add",
     "main_break_even", "main_profit_trailing", "tactical_invalidated",
     "tactical_weakened_no_progress"],
)
async def test_main_action_never_mutates_v2(action, v2_position, harness):
    before = deepcopy(v2_position)
    await harness.deliver(action, v2_position["symbol"])
    assert harness.exchange_commands == []
    assert v2_position["stop_loss"] == before["stop_loss"]
    assert v2_position["take_profit"] == before["take_profit"]
```

- [x] **Step 2: Confirm failures**

Run:

```bash
pytest tests/test_tactical_v2_exit.py tests/test_tactical_v2_main_isolation.py test_partial_tp_lifecycle.py -q
```

Expected: FAIL because track=tactical currently enters legacy invalidation and 50 percent TP paths.

- [x] **Step 3: Guard by strategy_owner=tactical_v2**

PositionAnalyst must emit no command for V2. MultiExecutor skips early review and legacy partial TP for V2. ContractExecutor._update_trailing returns no Main/legacy strategy trigger for V2. Add/reduce methods reject V2 unless invoked through an explicit owner-bound global safety close.

- [x] **Step 4: Implement full TP1, full SL, and max hold**

Exchange OCO handles TP/SL. Controller tick handles 90-minute max hold from immutable fill/open time. Local close acquires the existing normalized-symbol exit lock, re-fetches remaining quantity, cancels only proven Tactical protection, and closes the full remaining quantity reduce-only.

- [x] **Step 5: Handle exchange/local close races**

When sync detects flat, controller records exchange_closed_pending_pnl and does not submit another close. A waiting max-hold command rechecks quantity after acquiring the lock. Repeated sync or restart observations converge on one position close identity.

- [x] **Step 6: Preserve global safety authority and attribution**

Route daily/global hard stop, flash move, protection-integrity safe close, and manual emergency through close_tactical_position with reason risk_forced:<source>. Do not route Position Analyst or thesis events through this authority.

- [x] **Step 7: Run exit and isolation suites**

Run:

```bash
pytest tests/test_tactical_v2_exit.py tests/test_tactical_v2_main_isolation.py test_partial_tp_lifecycle.py tests/test_low_rr_early_trailing.py -q
```

Expected: PASS with full close quantities and zero V2 Main-strategy commands.

## Task 8: Final PnL Routing, Status Snapshot, And Telegram

**Files:**
- Create: utils/tactical_v2/status.py
- Modify: utils/tactical_v2/controller.py
- Modify: agents/trading/executor.py
- Modify: agents/trading/reviewer.py
- Modify: agents/trading/telegram_notifier.py
- Test: tests/test_tactical_v2_pnl.py
- Test: tests/test_tactical_v2_status.py
- Extend: tests/test_health_telegram_display.py

- [x] **Step 1: Add failing final/correction and status tests**

```python
def test_status_marks_old_snapshot_stale(tmp_path, monkeypatch):
    write_status(tmp_path, {"updated_at": 1000.0, "mode": "live"})
    monkeypatch.setattr(time, "time", lambda: 1091.0)
    text = format_tactical_v2_status(read_status(tmp_path), stale_seconds=90)
    assert "STALE" in text
    assert "circuit clear" not in text.lower()


def test_status_rejects_nan_pnl():
    text = format_tactical_v2_status({"updated_at": time.time(), "rolling_pnl": float("nan")})
    assert "PnL: ?" in text
```

- [x] **Step 2: Confirm failure**

Run:

```bash
pytest tests/test_tactical_v2_pnl.py tests/test_tactical_v2_status.py tests/test_health_telegram_display.py -q
```

Expected: FAIL because Telegram reads the legacy riskguard tactical circuit and no V2 snapshot exists.

- [x] **Step 3: Route final resolutions exactly once**

When MultiExecutor creates an external-close pending record or obtains a final resolution, call controller.on_pnl_resolution before publishing to other agents. Subscribe for final corrections published by other sources. Propagate strategy_owner, intent_id, episode_id, plan_hash, protection ids, and close reason through execution_result, pnl_resolved, and Reviewer history.

- [x] **Step 4: Build the atomic read model**

Write status after each material transition and at least every 30 seconds. Include mode/version, fixed sizing, active/pending/free slots and symbols, rolling final PnL, streak, timed pause, integrity halt, episode counts, protection/reconciliation state, lane mismatch counts, and updated_at.

- [x] **Step 5: Replace only the Telegram Tactical source**

Keep global halt, per-symbol halt, agent, and DLQ reads unchanged. Read Tactical fields only from StatePaths.tactical_v2_status. Apply a 90-second default freshness check, finite-number validation, compact symbol truncation, and explicit new-admission wording for rolling/timed pause.

- [x] **Step 6: Verify PnL propagation and Telegram degradation**

Run:

```bash
pytest tests/test_tactical_v2_pnl.py tests/test_tactical_v2_status.py tests/test_health_telegram_display.py tests/test_reviewer_symbol_canonical.py -q
```

Expected: PASS for healthy, paused, integrity, missing, malformed, stale, and non-finite snapshots while other status lines continue rendering.

## Task 9: Sidecar Admission Stop, Drain, Archive, And Cutover Gate

**Files:**
- Create: utils/tactical_v2/cutover.py
- Modify: scripts/shadow_tactical_live_sidecar.py
- Modify: utils/shadow_tactical_live.py
- Modify: utils/tactical_v2/controller.py
- Extend: tests/test_shadow_tactical_live_cli.py
- Extend: tests/test_shadow_tactical_exit_monitoring.py
- Create: tests/test_tactical_v2_cutover.py

- [x] **Step 1: Preserve and run the pre-existing resident CLI tests**

Run before editing either existing dirty file:

```bash
pytest tests/test_shadow_tactical_live_cli.py -q
```

Expected: PASS, proving deprecated duration does not terminate the resident monitor and explicit stop behavior remains covered.

- [x] **Step 2: Add failing drain barrier tests**

```python
def test_live_cutover_rejects_unknown_exchange_owner(tmp_path):
    report = drain_report(owner_status="open", exchange_state="unknown")
    proof = archive_drain_report(report, tmp_path)
    decision = validate_live_cutover(proof.path)
    assert decision.allowed is False
    assert decision.reason == "sidecar_drain_unresolved"


def test_v2_never_adopts_sidecar_position(controller, sidecar_owner):
    controller.recover(sidecar_owner=sidecar_owner)
    assert controller.active_positions == {}
    assert controller.integrity_halt is True
```

- [x] **Step 3: Add persistent admission-disabled behavior**

Add a sidecar command or state transition that persists admission_enabled=false before returning success. The run loop continues polling and monitoring but does not call open_sidecar_plan for new events. Record skipped post-stop candidates for audit without reopening them on later slot release.

- [x] **Step 4: Build a complete drain report**

Report pending entries, owner rows, local sidecar positions, exchange positions, TP/SL objects, ownership proof, exchange state, pending/final PnL, documented exceptions, generated_at, and a content hash. Unknown/unsupported exchange state, open owner, pending entry, protection ambiguity, or undocumented pending PnL keeps complete=false.

- [x] **Step 5: Archive and gate requested live mode**

Only a complete report can be atomically archived to StatePaths.sidecar_retirement. Tactical controller requested mode=live must validate namespace, bot owner id, archive schema/hash, complete=true, and no unresolved objects. Failure keeps live admission blocked while shadow/status/reconciliation remain active.

- [x] **Step 6: Verify rollback semantics**

Disabling V2 cancels only proven pending V2 entry orders, preserves protected filled V2 management until flat, and leaves sidecar admission disabled. Add tests proving no old owner row becomes a V2 slot or position.

- [x] **Step 7: Run sidecar and cutover suites**

Run:

```bash
pytest tests/test_shadow_tactical_live_cli.py tests/test_shadow_tactical_exit_monitoring.py tests/test_shadow_tactical_live_core.py tests/test_tactical_v2_cutover.py -q
```

Expected: PASS with the original dirty resident-run behavior preserved.

## Task 10: Historical Replay And Failure Injection

**Files:**
- Create: scripts/replay_tactical_v2.py
- Create: tests/test_tactical_v2_replay.py
- Create: tests/test_tactical_v2_crash_recovery.py
- Add fixture: tests/fixtures/tactical_v2_reproduced_window.json
- Modify: openspec/changes/promote-shadow-tactical-v2-live/tasks.md

- [x] **Step 1: Encode the reproduced evidence as deterministic fixtures**

Represent the seven historical live closes and fourteen deduplicated shadow clusters with source timestamps, candidate plans, 15m structure tokens, bid/ask ticks, capacity state, observed fills, exit reason, and final PnL. Keep the 143 raw rows only as input duplication evidence.

- [x] **Step 2: Add replay acceptance assertions**

```python
def test_reproduced_window_has_one_attempt_per_episode(report):
    assert report.raw_candidates == 143
    assert report.episodes == 14
    assert report.duplicate_live_attempts == 0
    assert report.stale_chase_fills == 0
    assert report.tp_before_entry_fills == 0
    assert report.unclassified_mismatches == 0
```

- [x] **Step 3: Add a crash matrix around every external boundary**

Parameterize crash injection at before_entry_io, after_entry_accept, after_partial_fill, before_cancel_remainder, after_cancel, before_protection_verify, after_exchange_tp, before_local_close_persist, after_local_close, before_pending_pnl, and after_final_pnl. Restart from disk with the same fake exchange state.

- [x] **Step 4: Assert recovery invariants for every crash point**

For each case assert entry submissions <= 1, reduce-only closes <= 1, no terminal episode re-entry, no slot release before exchange proof, and either full verified TP+SL or active integrity halt plus owner-bound safe-close attempt.

- [x] **Step 5: Run replay and crash suites**

Run:

```bash
pytest tests/test_tactical_v2_replay.py tests/test_tactical_v2_crash_recovery.py tests/test_tactical_wld_replay.py -q
python scripts/replay_tactical_v2.py --fixture tests/fixtures/tactical_v2_reproduced_window.json
```

Expected: tests PASS and report exits zero for duplicate, stale chase, TP-before-entry fill, Main strategy exit, unprotected fill, and unclassified mismatch.

## Task 11: Full Local Verification And Operational Documentation

**Files:**
- Modify: README.md
- Modify: docs/runbook.md
- Create: docs/superpowers/reports/2026-07-28-promote-shadow-tactical-v2-live-verify.md
- Modify: openspec/changes/promote-shadow-tactical-v2-live/tasks.md

- [x] **Step 1: Run focused Tactical V2 verification**

```bash
pytest tests/test_tactical_v2_models.py tests/test_tactical_v2_config.py tests/test_tactical_v2_store.py tests/test_tactical_v2_episodes.py tests/test_tactical_v2_structure.py tests/test_tactical_v2_entry.py tests/test_tactical_v2_shadow.py tests/test_tactical_v2_governor.py tests/test_tactical_v2_candidate_bus.py tests/test_tactical_v2_controller.py tests/test_tactical_v2_exchange.py tests/test_tactical_v2_protection.py tests/test_tactical_v2_exit.py tests/test_tactical_v2_main_isolation.py tests/test_tactical_v2_pnl.py tests/test_tactical_v2_status.py tests/test_tactical_v2_cutover.py tests/test_tactical_v2_replay.py tests/test_tactical_v2_crash_recovery.py -q
```

Expected: PASS.

- [x] **Step 2: Run affected legacy suites**

```bash
pytest tests/test_tactical_circuit_breaker.py tests/test_tactical_wld_replay.py tests/test_shadow_tactical_live_cli.py tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_executor.py tests/test_shadow_tactical_owner_isolation.py tests/test_shadow_tactical_exit_monitoring.py tests/test_health_telegram_display.py tests/test_entry_drift_hybrid_policy.py test_partial_tp_lifecycle.py -q
```

Expected: PASS with V1/sidecar drain compatibility and Main behavior unchanged.

- [x] **Step 3: Run the repository regression suite**

Run:

```bash
pytest -q
```

Expected: PASS. Record total count, duration, and any deliberately skipped cloud-only tests.

- [x] **Step 4: Update runbook and verification report**

Document V2 modes, exact risk settings, status interpretation, integrity halt response, sidecar admission stop, drain inspection, archive validation, rollback, and the prohibition on manual force-resume of unresolved owner/protection ambiguity. Record commands and actual results in the verification report.

- [x] **Step 5: Reconcile OpenSpec tasks**

Check each completed item in tasks.md only when its corresponding test or operational evidence exists. Keep cloud observation, drain, live cohort, and final submission items unchecked until completed.

## Task 12: Cloud Shadow, Sidecar Drain, Live Cohort, And Final Submission

**Files:**
- Remote modify: /opt/crypto-arbitrage/.env
- Remote execute: /opt/crypto-arbitrage/run_agents.py
- Remote execute: /opt/crypto-arbitrage/scripts/shadow_tactical_live_sidecar.py
- Modify: docs/superpowers/reports/2026-07-28-promote-shadow-tactical-v2-live-verify.md
- Modify: openspec/changes/promote-shadow-tactical-v2-live/tasks.md
- Include preserved: scripts/shadow_tactical_live_sidecar.py
- Include preserved: tests/test_shadow_tactical_live_cli.py

- [x] **Step 1: Deploy shadow-only with live exchange commands mechanically disabled**

Set TACTICAL_V2_MODE=shadow, retain sidecar admission during observation, restart the Main service, and verify startup banner, fresh TG status, candidate flow, executable bid/ask ticks, zero Tactical V2 exchange order calls, and persistent restart recovery.

- [x] **Step 2: Collect at least 24 hours of shadow evidence**

Record observation start/end, service restarts, intent count, episode count, filled/non-filled outcomes, stale/invalid tick count, mismatch categories, snapshot freshness, and integrity events. Do not treat raw row count or fewer than 30 final episodes as performance proof.

- [x] **Step 3: Stop sidecar admission and complete the drain**

Persist admission_enabled=false, keep its monitor resident, cancel only proven pending orders, reconcile every owner and exchange position, verify TP/SL ownership, resolve or document pending final PnL, and generate complete=true retirement proof. Do not enable V2 live while any field is unknown.

- [x] **Step 4: Enable fixed 100U x 3 live mode**

Set TACTICAL_V2_MODE=live only after archive validation. Verify TG displays live V2, 100U x 3, current slots, rolling PnL, streak/circuit, protection, parity, and fresh updated_at. Confirm Main MAX_TRADE_AMOUNT is unchanged.

- [x] **Step 5: Monitor the first live cohort**

For every intent compare shadow and live transitions and verify no duplicate entry, no chase beyond 0.10R, no slot-release backfill, no Main strategy exit, full TP1/SL behavior, verified protection or immediate integrity handling, final resolution deduplication, and classified mismatches.

- [x] **Step 6: Run final verification and strict OpenSpec validation**

```bash
pytest -q
openspec validate promote-shadow-tactical-v2-live --strict
git diff --check
```

Expected: all commands PASS.

- [x] **Step 7: Review the complete dirty worktree and create the single final commit**

Confirm every changed file belongs to this Comet change or the explicitly preserved resident sidecar work. Review scripts/shadow_tactical_live_sidecar.py and tests/test_shadow_tactical_live_cli.py against their passing tests. Then stage the complete verified change and commit:

```bash
git add .env.example README.md docs agents executor.py run_agents.py scripts tests utils openspec/changes/promote-shadow-tactical-v2-live
git commit -m "feat: promote shadow tactical v2 live"
```

- [x] **Step 8: Record final commit and prepare Comet verification**

Write the commit id, cloud evidence, drain archive hash, live cohort evidence, full test count, and strict OpenSpec result into the verification report. Leave branch handling and archive transitions to comet-verify and comet-archive.
