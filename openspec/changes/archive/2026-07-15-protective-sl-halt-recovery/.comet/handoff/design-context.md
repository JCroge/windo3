# Comet Design Handoff

- Change: protective-sl-halt-recovery
- Phase: design
- Mode: compact
- Context hash: 40576741fa366c19903fee36674523b406293cece22fab15fd088ae786fc4811

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/protective-sl-halt-recovery/proposal.md

- Source: openspec/changes/protective-sl-halt-recovery/proposal.md
- Lines: 1-41
- SHA256: b48aca365dabaac217f49b2f282ab046376feae36af73bcca7129513da65d183

```md
## Why

On 2026-07-14 the WLD Tactical live order filled, but the executor could not resolve the attached OKX stop-loss algo id. The system correctly treated a possibly unprotected live position as dangerous, but the resulting global halt stayed visible in Telegram until manual `/resume` even after the position was later closed, and `/status` did not make it clear that this was a protection halt rather than a Tactical circuit halt or a Tactical loss halt.

We need to keep the fail-closed safety posture for truly unprotected live positions while reducing avoidable sampling downtime and operator confusion.

## What Changes

- Add a bounded post-open protection verification window for OKX attached stop-loss resolution before classifying the position as terminal `protection_state=unknown`.
- During that verification window, block new risk so the system does not continue opening positions while protection is uncertain.
- When a protection-driven global halt is later proven resolved because the position is protected or no longer exists on exchange, auto-clear the matching per-symbol halt and global halt if no other blocking condition remains.
- Improve Telegram `/status` wording so global halt, per-symbol halt, and Tactical circuit state are visible as separate concepts.
- Add tests for the WLD-style sequence: attached SL unresolved, protection halt, local close, reconciliation showing no unresolved protected-risk, then automatic recovery.

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `position-sync-resilience`: extend protection-unknown handling to cover post-open `sl_algo_unresolved` and to self-heal protection-driven halts once exchange/local state proves the risk is gone.
- `tg-status-enhancement`: require `/status` to distinguish global halt, per-symbol halt, and Tactical circuit state instead of presenting a single ambiguous "熔断" signal.

## Impact

- **Code**:
  - `executor.py`: OKX attached SL resolution path, `_halt_symbol`, sync/migration protection-state recovery, and halt clearing conditions.
  - `utils/halt_state.py`: may need a narrowly scoped method or metadata convention for auto-clearing protection-resolved halt reasons without weakening manual/daily hard-stop halts.
  - `agents/trading/telegram_notifier.py`: `/status` formatting.
  - `agents/trading/portfolio_risk_guard.py` or `agents/trading/judge.py`: read-only Tactical circuit summary for status if the existing persisted state is sufficient.
- **Tests**:
  - Root executor tests for bounded attached-SL verification and protection halt self-heal.
  - Telegram status tests for global halt vs Tactical circuit wording.
  - Regression tests that manual halt and daily hard stop do not auto-clear.
- **Non-goals**:
  - No Tactical threshold tuning.
  - No weakening of fail-closed behavior while a live position might be unprotected.
  - No bypass of `/resume` for manual, daily hard-stop, or non-protection halt reasons.
  - No change to realized PnL attribution.
```

## openspec/changes/protective-sl-halt-recovery/design.md

- Source: openspec/changes/protective-sl-halt-recovery/design.md
- Lines: 1-91
- SHA256: 438440b04663a8e557762e3cd190bc7c427fe0f75e6cce9354e160d5cbe93422

[TRUNCATED]

```md
# High-Level Design: Protective SL Halt Recovery

## Context

The current executor treats several protection failures as fail-closed events by calling `_halt_symbol(...)`, which also writes the persistent global `halt_state`. This is safe when a live position may be without exchange-side stop loss, but it creates an availability problem when the risky condition later disappears: the per-symbol halt can be cleared on `/resume`, but the global halt remains until manual action even if the position has already closed and no unprotected position remains.

The WLD event showed this chain:

1. Tactical WLD short filled.
2. Attached OKX SL algo id could not be resolved.
3. `_halt_symbol(reason='sl_algo_unresolved')` wrote global halt `okx_sl_algo_unresolved:WLD-USDT-SWAP`.
4. WLD later closed via `tactical_invalidated`.
5. `/status` still showed a generic "熔断", which was easy to confuse with Tactical circuit loss control.

## Goals / Non-Goals

**Goals:**

- Keep fail-closed behavior when a live position might be unprotected.
- Avoid converting short-lived OKX attached-algo visibility delays into long manual outages when the protection condition resolves.
- Automatically recover protection-driven global halts only after exchange/local state proves there is no unresolved unprotected position.
- Make Telegram status distinguish global halt, per-symbol halt, and Tactical circuit state.

**Non-Goals:**

- Do not change Tactical entry parameters, Tactical PnL accounting, or Tactical circuit thresholds.
- Do not auto-clear manual halts, daily hard stops, reconciliation mismatches, or unknown halt reasons.
- Do not keep trading normally while protection state is pending.

## Decisions

### Decision 1: Treat attached SL lookup failure as a bounded pending state first

When `open_position_with_plan` submits an OKX order with `attachAlgoOrds`, the first lookup of `attachAlgoClOrdId` can be stale. Instead of immediately classifying the position as terminal `unknown`, the executor will perform a bounded verification loop:

- retry by `attachAlgoClOrdId`;
- fetch open algo orders for the symbol;
- optionally run the existing migration/owner-tag matching path.

During this bounded window, new risk is blocked. If the SL is found, the position becomes `protected`. If the bounded window is exhausted, the existing fail-closed halt path remains.

Downstream impact: transient OKX visibility delay should not create a manual outage, but true missing-SL risk still prevents new positions.

### Decision 2: Auto-clear only allowlisted protection halt reasons

The auto-clear path is limited to halt reasons that encode protection uncertainty, such as:

- `okx_sl_algo_unresolved:<symbol>`;
- `okx_migrate_missing_sl:<symbol>` if existing migrate-missing-SL recovery rules apply.

Auto-clear requires all of these:

- the halted symbol is no longer present on exchange, or it is present and has a verified owner-matched protective SL;
- local positions contain no `protection_state in {'unknown', 'pending'}` for that symbol;
- there are no other halted symbols with unresolved protection reasons;
- the global halt reason still matches the same allowlisted protection reason.

Manual, daily hard-stop, force-close, and mismatch reasons remain manual/reconciliation controlled.

Downstream impact: a WLD-style closed position can recover without waiting for `/resume`; genuine global risk halts remain sticky.

### Decision 3: Telegram status becomes a status matrix, not one ambiguous flag

`/status` should still show the global halt state, but label it as global. It should also show:

- per-symbol halt count/list from `agent_health.json`;
- Tactical circuit state from persisted `riskguard_state.tactical_circuit`;
- reconciliation status if present.

This is a formatting and observability change. It must not change trading behavior.

## Risks / Trade-offs

- **Risk: auto-clear could hide a true unprotected position.** Mitigation: only clear after exchange/local sync proves the symbol is closed or protected, and only for allowlisted protection reasons.
- **Risk: retry window delays open confirmation.** Mitigation: keep the retry bounded and block new risk during the pending window.
- **Risk: `/status` grows noisy.** Mitigation: keep it compact: one line each for global halt, per-symbol halt, Tactical circuit.

## Migration Plan

1. Implement test coverage for pending SL verification and protection-halt auto-clear.
```

Full source: openspec/changes/protective-sl-halt-recovery/design.md

## openspec/changes/protective-sl-halt-recovery/tasks.md

- Source: openspec/changes/protective-sl-halt-recovery/tasks.md
- Lines: 1-23
- SHA256: cb75a9bf3b09b589865dbf61478e29a03fb0717b4e2fc1b1d89f7fc540d9f364

```md
## 1. Protection Verification

- [ ] 1.1 Add tests for OKX attached SL first-lookup miss followed by successful bounded verification.
- [ ] 1.2 Add tests for attached SL verification exhaustion triggering existing fail-closed protection halt.
- [ ] 1.3 Implement bounded attached SL verification without allowing additional live opens while protection is pending.

## 2. Protection Halt Recovery

- [ ] 2.1 Add tests for `okx_sl_algo_unresolved:<symbol>` auto-clear after exchange confirms the symbol is closed.
- [ ] 2.2 Add tests proving manual halt, daily hard stop, and non-allowlisted halt reasons do not auto-clear.
- [ ] 2.3 Implement allowlisted protection-halt recovery with audit logging and per-symbol halt cleanup.

## 3. Telegram Status

- [ ] 3.1 Add `/status` tests for global protection halt with Tactical circuit not paused.
- [ ] 3.2 Add `/status` tests for Tactical circuit paused with global halt clear.
- [ ] 3.3 Update `/status` formatting to show global halt, per-symbol halt, and Tactical circuit as distinct lines.

## 4. Verification

- [ ] 4.1 Run focused executor and Telegram status tests.
- [ ] 4.2 Run the project test suite or the agreed equivalent verification subset.
- [ ] 4.3 Sync to cloud only after local verification and restart/validate cloud status output.
```

## openspec/changes/protective-sl-halt-recovery/specs/position-sync-resilience/spec.md

- Source: openspec/changes/protective-sl-halt-recovery/specs/position-sync-resilience/spec.md
- Lines: 1-41
- SHA256: 07dabd7590ceb75cdb4f5bee877e11c1b43241d6e10c40e2766c3e927f0a07cb

```md
## ADDED Requirements

### Requirement: OKX attached SL resolution SHALL use bounded verification before terminal protection halt

When an OKX open order is submitted with an attached protective stop loss and the first lookup of the attached SL `algoId` by client order id fails, the executor SHALL enter a bounded protection-verification state before treating the position as terminally unprotected. During this bounded state the system MUST NOT open additional live risk. If verification finds the attached SL, the position SHALL be marked `protection_state="protected"` and no protection halt SHALL be triggered. If verification is exhausted without finding a valid protective SL, the executor SHALL retain the existing fail-closed behavior and trigger a protection halt.

#### Scenario: attached SL appears during bounded verification
- **WHEN** an OKX open fills and the first attached SL lookup by `attachAlgoClOrdId` returns no `algoId`
- **AND** a later bounded verification attempt finds an owner-matched protective SL for the position
- **THEN** the position MUST be saved with `protection_state="protected"`
- **AND** the system MUST NOT write global halt reason `okx_sl_algo_unresolved:<symbol>`

#### Scenario: attached SL remains missing after bounded verification
- **WHEN** an OKX open fills and all bounded verification attempts fail to find a valid protective SL
- **THEN** the position MUST be saved with `protection_state="unknown"`
- **AND** the executor MUST trigger the existing fail-closed protection halt for that symbol
- **AND** new live opens MUST remain blocked until the halt is resolved

### Requirement: protection-driven global halt SHALL self-heal after protection risk is proven gone

For allowlisted protection halt reasons caused by missing or unresolved protective stop loss, the system SHALL automatically clear the matching per-symbol halt and global halt only after exchange/local state proves that the protection risk is gone. The allowlist SHALL include `okx_sl_algo_unresolved:<symbol>` and MAY include existing migrate-missing-SL reasons that already have symbol-level self-heal semantics. Manual halts, daily hard stops, reconciliation mismatches, and unknown halt reasons MUST NOT auto-clear through this path.

#### Scenario: halted symbol is closed on exchange
- **WHEN** global halt reason is `okx_sl_algo_unresolved:WLD-USDT-SWAP`
- **AND** sync/reconciliation confirms WLD is no longer open on exchange
- **AND** local state has no WLD position with `protection_state` of `unknown` or `pending`
- **THEN** the WLD per-symbol halt MUST be cleared
- **AND** the global halt MUST be cleared if no other unresolved protection halt remains
- **AND** an audit log MUST record the automatic protection halt recovery

#### Scenario: halted symbol becomes protected
- **WHEN** global halt reason is `okx_sl_algo_unresolved:<symbol>`
- **AND** sync/reconciliation later finds a valid owner-matched protective SL for that symbol
- **THEN** the position MUST become `protection_state="protected"`
- **AND** the matching protection halt MAY be auto-cleared if no other unresolved protection halt remains

#### Scenario: non-protection halt remains sticky
- **WHEN** global halt reason is manual, daily hard-stop, reconciliation mismatch, or an unknown non-allowlisted reason
- **AND** positions are flat or protected
- **THEN** the system MUST NOT auto-clear the global halt
- **AND** recovery MUST still require the existing `/resume` or `/force_resume` path as appropriate
```

## openspec/changes/protective-sl-halt-recovery/specs/tg-status-enhancement/spec.md

- Source: openspec/changes/protective-sl-halt-recovery/specs/tg-status-enhancement/spec.md
- Lines: 1-23
- SHA256: c721fb70d498190fbe384dfc289c939b75654f5e1faf1999deb65c1eff467b82

```md
## ADDED Requirements

### Requirement: `/status` SHALL distinguish global halt, per-symbol halt, and Tactical circuit

Telegram `/status` SHALL display global halt state, per-symbol halt state, and Tactical circuit state as separate status lines. A global protection halt MUST NOT be presented in a way that implies the Tactical circuit is paused. Tactical circuit state SHALL be read from the persisted risk guard tactical circuit state when available.

#### Scenario: global protection halt while Tactical circuit is not paused
- **WHEN** `halt_state.halted == true` with reason `okx_sl_algo_unresolved:WLD-USDT-SWAP`
- **AND** `riskguard_state.tactical_circuit.pause_until == 0`
- **THEN** `/status` MUST show global halt as active with the OKX protection reason
- **AND** `/status` MUST show Tactical circuit as not paused
- **AND** the message MUST NOT imply Tactical loss circuit caused the halt

#### Scenario: Tactical circuit paused while global halt is clear
- **WHEN** `halt_state.halted == false`
- **AND** `riskguard_state.tactical_circuit.pause_until` is in the future
- **THEN** `/status` MUST show global halt as inactive
- **AND** `/status` MUST show Tactical circuit as paused with its pause reason

#### Scenario: status data missing degrades safely
- **WHEN** `riskguard_state.tactical_circuit` is missing or unreadable
- **THEN** `/status` MUST still show global halt and per-symbol halt state
- **AND** Tactical circuit line MUST degrade to an unknown/unavailable marker rather than failing the command
```

