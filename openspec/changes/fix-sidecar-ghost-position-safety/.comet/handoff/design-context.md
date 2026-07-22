# Comet Design Handoff

- Change: fix-sidecar-ghost-position-safety
- Phase: design
- Mode: compact
- Context hash: 99b03f9873efb0e288ec770f188bea5603e0b3952af6e419a31cc2e7b65faca2

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-sidecar-ghost-position-safety/proposal.md

- Source: openspec/changes/fix-sidecar-ghost-position-safety/proposal.md
- Lines: 1-44
- SHA256: 70335cc4b9d54ec5548dc41ede6278983a84d5c83033fe0c19584d6a64d8bea5

```md
## Why

The 2026-07-22 ADA sidecar incident exposed a split-brain failure between sidecar owner rows, sidecar local position metadata, OKX net-mode exposure, and Main algo migration. The system can currently leave sidecar exchange exposure unmanaged while Main cancels manual protection orders and the sidecar skips unproven owners.

This must be fixed before resuming sidecar scale-up because the failure mode is not ADA-specific: it follows from current same-symbol sidecar stacking, symbol-keyed local position state, and owner-proof gaps.

## What Changes

- Prevent Main OKX algo migration from canceling manual, foreign, or ambiguous TP/SL algos on a symbol that is currently sidecar-owned and still has exchange exposure.
- Prevent sidecar same-symbol stacking in OKX `net_mode` unless the implementation has an explicit aggregate-position model that can prove and manage the whole net exposure.
- Make sidecar monitoring fail closed when `owners.open > 0`, exchange position is present, but sidecar executable position metadata is missing or unproven.
- Add an operational guard/audit signal for sidecar ghost exposure: open owner rows plus exchange exposure plus no proven local sidecar position and no pending TP/SL protection.
- Add regression coverage for the ADA class:
  - manual OCO/conditional protection survives Main migration while the symbol is sidecar-owned,
  - repeated sidecar same-symbol opens are blocked or modeled as one aggregate position,
  - monitor cannot close only one owner row and leave remaining same-symbol net exposure unmanaged,
  - unproven present exposure produces a halt/alert instead of silent `monitor_skipped_unproven` loops.

## Capabilities

### New Capabilities

- None expected. This change tightens existing sidecar ownership and exit safety semantics rather than introducing a new trading capability.

### Modified Capabilities

- `shadow-tactical-sidecar-exit-monitoring`: tighten sidecar owner proof, ghost-exposure handling, same-symbol sidecar admission, and net-mode monitor semantics.
- `tactical-exit-track`: ensure Tactical hard veto and no-stacking requirements also apply to the live sidecar admission path.
- `protective-sl-owner-tag`: clarify Main migration must preserve ambiguous/manual protection on sidecar-owned symbols instead of treating it as orphan residual.
- `entry-drift-policy`: apply stale-entry protection to sidecar live opens instead of bypassing the drift guard entirely.

## Impact

- Affected code:
  - `scripts/shadow_tactical_live_sidecar.py`
  - `utils/shadow_tactical_live.py`
  - `executor.py`
- Affected tests:
  - `tests/test_shadow_tactical_exit_monitoring.py`
  - `tests/test_shadow_tactical_live_core.py`
  - `tests/test_shadow_tactical_owner_isolation.py`
  - likely a focused migration regression near existing partial-TP/algo migration tests.
- No dependency, public API, database schema, or cloud `.env` change is expected.
- Operationally, sidecar scale-up remains paused until this change is implemented and verified.
```

## openspec/changes/fix-sidecar-ghost-position-safety/design.md

- Source: openspec/changes/fix-sidecar-ghost-position-safety/design.md
- Lines: 1-77
- SHA256: 3246c9f8953f649061ee99ae0fd8da8d811e505ea9979ada5ffef32cbd3d75ae

```md
## Context

Shadow Tactical sidecar runs in the same OKX account as Main. In OKX `net_mode`, repeated same-symbol sidecar opens become one exchange-side net exposure, while sidecar ownership is stored as multiple owner rows and executor local position state is keyed by exchange symbol. The ADA incident showed the resulting split-brain:

- owner rows remained open,
- sidecar local position metadata disappeared or only represented one owner,
- exchange exposure remained present,
- sidecar monitor skipped unproven owners,
- Main skipped position backfill because the symbol was sidecar-owned,
- Main algo migration still canceled manual TP/SL protection because it had no local Main position.

The existing 2026-07-20 flat-reconcile hotfix only handles the exchange-flat case. This change targets the exchange-present ghost exposure case and the admission paths that create it.

## Goals / Non-Goals

**Goals:**

- Prevent Main from canceling ambiguous/manual protection for sidecar-owned symbols that still have exchange exposure.
- Prevent new same-symbol sidecar stacking in OKX `net_mode` unless a future aggregate-position model is explicitly introduced.
- Make sidecar monitor detect ghost exposure and fail closed with halt/audit signals instead of silently looping on `monitor_skipped_unproven`.
- Add sidecar live-entry drift protection so stale shadow prices cannot be market-opened without a bounded check.
- Add regression tests that reproduce the ADA failure class.

**Non-Goals:**

- Do not introduce a full per-lot or aggregate sidecar position model in this change.
- Do not change Main Judge strategy logic.
- Do not alter cloud `.env`, OKX account mode, or exchange credentials.
- Do not make sidecar close or reduce unproven exchange exposure automatically.

## Decisions

### 1. Preserve Ambiguous Protection On Sidecar-Owned Present Exposure

Main migration will treat a sidecar-owned symbol with exchange exposure as an ownership boundary. When there is no local Main position, pending SL/OCO/conditional algos for that symbol must be preserved unless they are proven Main-owned and safe to mutate. This includes manual OKX UI protection that has no owner tag.

Alternative considered: keep canceling unowned algos to avoid stale orders. Rejected because in the sidecar-owned present-exposure case, preserving protection is safer than creating a naked position. Stale protection can be inspected manually; canceled protection immediately removes risk coverage.

### 2. Block Same-Symbol Sidecar Stacking In Net Mode

The sidecar will reject a new open for a symbol/side when an active sidecar owner already exists or exchange exposure for that symbol is present in `net_mode`. Existing `--max-active` remains a portfolio cap, not a same-symbol stacking permission.

Alternative considered: aggregate owners into a single net position. Rejected for this change because it requires owner-row allocation, weighted entry/SL/TP recomputation, ledger allocation, and close distribution semantics. Blocking new stacks removes the unsafe path with lower blast radius.

### 3. Ghost Exposure Is A Fail-Closed State

When sidecar owner rows are open, exchange position state is present, and no local sidecar position can be proven, the sidecar will record a ghost-exposure audit event and halt/block further sidecar opens for that symbol. If pending TP/SL protection is absent or unknown, the event must be critical enough for operator action. The sidecar still must not close or reduce unproven exposure.

Alternative considered: reconstruct local positions from owners plus exchange state. Rejected as the default hot path because net-mode allocation is ambiguous after stacked opens and partial exits. Reconstruction can be a future explicit repair tool, not an automatic monitor action.

### 4. Sidecar Opens Need Entry Drift Protection

`open_sidecar_plan()` currently uses market orders and bypasses Main's entry drift gate. The sidecar will add a bounded stale-entry check using `entry_ref` and derived stop/TP percentages when explicit `sl_pct`/`tp_pct` are absent. Large drift rejects before order submission and records a sidecar audit reason.

Alternative considered: reuse `open_position_with_plan()` directly. Rejected because that path includes Main strategy accounting and other gates the sidecar intentionally bypasses. Reusing the drift classifier or equivalent helper keeps the admission fix narrow.

## Risks / Trade-offs

- [Risk] Blocking same-symbol sidecar stacking reduces shadow-to-live coverage. -> Mitigation: record explicit `same_symbol_sidecar_active` rejection for later analysis.
- [Risk] Preserving manual algos may leave stale protection after the exchange is flat. -> Mitigation: existing exchange-flat reconcile can still close owner metadata; Main should only preserve in sidecar-owned present/unknown exposure cases.
- [Risk] Halting on ghost exposure may require manual intervention. -> Mitigation: this is intentional fail-closed behavior for unmanaged live exposure.
- [Risk] Drift thresholds may reject some profitable sidecar entries. -> Mitigation: use existing entry-drift policy thresholds where possible and audit rejects for future tuning.

## Migration Plan

1. Add regression tests for the ADA failure class before implementation.
2. Deploy code with sidecar still stopped or at reduced operational scale.
3. Run focused sidecar, migration, and drift tests locally.
4. On cloud, restart Main first so migration preservation is active before sidecar is resumed.
5. Resume sidecar only after `status` shows no ghost exposure and existing owner rows are reconciled or manually closed.

Rollback is to stop sidecar, leave OKX manual protection intact, and revert this code change. The Main migration preservation behavior is conservative and should not require an emergency rollback unless it blocks known-safe cleanup.

## Open Questions

- Should a future change introduce an explicit operator repair command to reconstruct sidecar local metadata from owner rows plus exchange position state?
- Should sidecar same-symbol stacking remain permanently forbidden, or be reintroduced only with a dedicated subaccount or aggregate-position model?
```

## openspec/changes/fix-sidecar-ghost-position-safety/tasks.md

- Source: openspec/changes/fix-sidecar-ghost-position-safety/tasks.md
- Lines: 1-37
- SHA256: 1bfff6df076adcaf3d48165ae747a6586bd4355cc677abc614d04967b315e8b4

```md
## 1. Regression Coverage

- [ ] 1.1 Add a Main migration regression proving manual OCO/conditional protection is preserved when a symbol is sidecar-owned and exchange exposure is present or unknown.
- [ ] 1.2 Add sidecar admission regressions proving same-symbol sidecar opens are rejected in OKX `net_mode` when an active owner or present exchange exposure already exists.
- [ ] 1.3 Add sidecar monitor regressions proving ghost exposure emits fail-closed audit/halt behavior and does not silently loop on `monitor_skipped_unproven`.
- [ ] 1.4 Add a net-mode stacked-owner regression proving monitor does not close one owner row while leaving remaining same-symbol exposure unproven and unmanaged.
- [ ] 1.5 Add sidecar entry-drift regressions proving large stale drift rejects before `create_order` and accepted opens record drift metadata.

## 2. Main Migration Protection

- [ ] 2.1 Extend Main sidecar-owner lookup or migration context so `_migrate_okx_algos_for_symbol()` can determine sidecar-owned present/unknown exchange exposure.
- [ ] 2.2 Preserve ambiguous/manual OCO and conditional TP/SL algos for sidecar-owned present/unknown exposure instead of canceling them as orphan residuals.
- [ ] 2.3 Keep existing exchange-flat or non-sidecar orphan cleanup behavior intact.

## 3. Sidecar Admission Safety

- [ ] 3.1 Tighten `blocks_same_symbol_account_exposure()` or sidecar admission logic so active same-symbol sidecar owner rows block new sidecar opens in OKX `net_mode`.
- [ ] 3.2 Ensure exchange-present same-symbol exposure blocks sidecar opens unless a future aggregate-position model is explicitly available.
- [ ] 3.3 Add sidecar audit rejection reasons for same-symbol active owner and unmodeled exchange exposure.

## 4. Ghost Exposure Monitoring

- [ ] 4.1 Detect ghost exposure in `monitor_sidecar_owned_exposure()` when owners are open, exchange exposure is present, and local sidecar position proof is missing.
- [ ] 4.2 Emit fail-closed audit/halt metadata for ghost exposure while preserving the rule that unproven exchange exposure is not closed or reduced automatically.
- [ ] 4.3 Detect ambiguous same-symbol net-mode owner stacks before applying close/reduce actions, and fail closed unless the whole net exposure is proven as one aggregate position.

## 5. Sidecar Entry Drift Guard

- [ ] 5.1 Derive sidecar drift anchors from `entry_ref`, `stop_loss`, and first `take_profit` when explicit `sl_pct`/`tp_pct` are absent.
- [ ] 5.2 Reject large stale sidecar opens before exchange order submission and record drift rejection attribution.
- [ ] 5.3 Persist drift admission metadata for accepted sidecar opens.

## 6. Verification And Operations

- [ ] 6.1 Run focused tests for sidecar core, sidecar executor, owner isolation, exit monitoring, entry drift, and algo migration.
- [ ] 6.2 Update or add verification report documenting the ADA failure-class reproduction, fixed behavior, and any remaining operational constraints.
- [ ] 6.3 Document rollout ordering: deploy Main migration preservation before resuming sidecar, then verify sidecar status has no ghost exposure.
```

## openspec/changes/fix-sidecar-ghost-position-safety/specs/entry-drift-policy/spec.md

- Source: openspec/changes/fix-sidecar-ghost-position-safety/specs/entry-drift-policy/spec.md
- Lines: 1-20
- SHA256: effc3e1272014eff9f604906d61383cb4f695ab9e4e92e9a7c5fabe07b208037

```md
## ADDED Requirements

### Requirement: Sidecar live opens SHALL enforce stale-entry drift protection
The sidecar live open path SHALL evaluate live price drift against the Tactical shadow plan entry reference before submitting a market order. If explicit drift anchors are missing, the sidecar SHALL derive stop and TP percentages from `entry_ref`, `stop_loss`, and the first `take_profit` level when possible. A stale sidecar plan beyond the configured hard drift bound SHALL be rejected before order submission.

#### Scenario: Large sidecar entry drift rejects before order
- **WHEN** a sidecar Tactical plan has `entry_ref`
- **AND** the current market price drifts beyond the configured hard drift bound from that entry reference
- **THEN** `open_sidecar_plan` SHALL reject the open before calling `create_order`
- **AND** the sidecar SHALL record a drift rejection audit event

#### Scenario: Sidecar drift decision is recorded on accepted open
- **WHEN** a sidecar Tactical plan passes stale-entry drift protection
- **THEN** the sidecar SHALL persist enough drift metadata on the position or audit stream to explain the admission decision
- **AND** the open SHALL still satisfy existing SL-side, slippage, precheck, min-size, and protective-SL verification checks

#### Scenario: Missing drift anchors fail safely
- **WHEN** a sidecar Tactical plan cannot provide or derive enough information for stale-entry drift protection
- **THEN** the sidecar SHALL reject the open or emit an explicit fail-safe audit reason before order submission
- **AND** it SHALL NOT silently bypass drift protection
```

## openspec/changes/fix-sidecar-ghost-position-safety/specs/protective-sl-owner-tag/spec.md

- Source: openspec/changes/fix-sidecar-ghost-position-safety/specs/protective-sl-owner-tag/spec.md
- Lines: 1-23
- SHA256: fb3d88d1c256b746742d85c45596d99f4a01bb6f967230898bc64b3e633e03ca

```md
## ADDED Requirements

### Requirement: Main migration SHALL preserve protection on sidecar-owned present exposure
Main OKX algo migration SHALL preserve pending TP/SL protection for symbols that are currently sidecar-owned and have present or unknown exchange exposure, even when Main has no local position for that symbol. Manual or ambiguous OCO/conditional algos SHALL NOT be canceled as orphan residuals in this state.

#### Scenario: Manual OCO survives sidecar-owned migration
- **WHEN** Main OKX algo migration scans a symbol with no local Main position
- **AND** the sidecar owner registry has an open owner row matching that symbol and side
- **AND** exchange position state for that symbol is present or unknown
- **AND** a pending manual OCO algo exists without a sidecar owner tag
- **THEN** Main SHALL preserve the algo
- **AND** it SHALL record the preservation or ambiguity in the migration summary

#### Scenario: Manual conditional SL survives sidecar-owned migration
- **WHEN** Main OKX algo migration scans a sidecar-owned symbol with present or unknown exchange exposure
- **AND** a pending conditional SL algo exists without a recognized Main owner tag
- **THEN** Main SHALL preserve the algo
- **AND** it SHALL NOT count the algo as an orphan SL cancellation

#### Scenario: Exchange-flat orphan cleanup is not weakened
- **WHEN** Main OKX algo migration scans a symbol with no local Main position
- **AND** there is no active sidecar owner row for the symbol or exchange state is confirmed flat
- **THEN** existing orphan cleanup behavior MAY still cancel residual Main-owned or unowned algos according to the migration policy
```

## openspec/changes/fix-sidecar-ghost-position-safety/specs/shadow-tactical-sidecar-exit-monitoring/spec.md

- Source: openspec/changes/fix-sidecar-ghost-position-safety/specs/shadow-tactical-sidecar-exit-monitoring/spec.md
- Lines: 1-47
- SHA256: ccd79ee4765d6c3cbc1dc92c656dd0043ac4bb01b8ecfc59d2c16e8f40c23372

```md
## ADDED Requirements

### Requirement: Sidecar SHALL block unsafe same-symbol net-mode stacking
The sidecar SHALL NOT open a new live Tactical sidecar position for a symbol when OKX `net_mode` cannot represent that new open as a separately provable sidecar position. An existing open sidecar owner row or present exchange exposure for the same normalized symbol and side SHALL reject the new open unless an explicit aggregate-position model proves and manages the whole net exposure.

#### Scenario: Existing sidecar owner blocks same-symbol open
- **WHEN** a sidecar Tactical shadow record targets a symbol and side that already has an owner row with `status=open`
- **AND** the executor is operating in OKX `net_mode`
- **THEN** the sidecar SHALL reject the new open before submitting an exchange order
- **AND** it SHALL write a sidecar audit event with reason `same_symbol_sidecar_active`

#### Scenario: Existing exchange exposure blocks unmodeled stack
- **WHEN** a sidecar Tactical shadow record targets a symbol whose exchange position is present
- **AND** the exposure is not represented by a single proven aggregate sidecar position model
- **THEN** the sidecar SHALL reject the new open before submitting an exchange order
- **AND** it SHALL NOT rely on `--max-active` to permit same-symbol stacking

### Requirement: Sidecar SHALL detect ghost exposure and fail closed
The sidecar SHALL treat open owner rows plus present exchange exposure plus missing or unproven local sidecar position metadata as ghost exposure. Ghost exposure SHALL block further sidecar opens for that symbol, produce an audit event, and require operator or later repair flow intervention. The sidecar SHALL NOT close or reduce unproven exchange exposure automatically.

#### Scenario: Unproven owner with present exposure triggers ghost guard
- **WHEN** an owner record exists with `status=open`
- **AND** the live sidecar position cannot be proven against current sidecar-owned position state
- **AND** the exchange position check for the owner symbol returns present
- **THEN** the sidecar SHALL record a ghost-exposure audit event
- **AND** it SHALL halt or block further sidecar opens for that symbol
- **AND** it SHALL NOT submit a close or reduce order

#### Scenario: Missing protection escalates ghost exposure
- **WHEN** ghost exposure is detected for a sidecar-owned symbol
- **AND** pending exchange TP/SL protection for that symbol is absent or cannot be verified
- **THEN** the sidecar SHALL mark the audit event as requiring operator action
- **AND** repeated monitor passes SHALL NOT silently emit only `monitor_skipped_unproven`

### Requirement: Sidecar monitor SHALL not partially close ambiguous net-mode owner stacks
The sidecar monitor SHALL NOT close or reduce one proven owner row for a same-symbol net-mode stack while other open owner rows for the same exchange symbol remain unproven. It SHALL either manage one proven aggregate position consistently or fail closed with ghost-exposure audit.

#### Scenario: Multiple owner rows with one local symbol position are ambiguous
- **WHEN** multiple sidecar owner rows are open for the same exchange symbol and side
- **AND** executor local position state contains only one symbol-keyed sidecar position
- **THEN** the sidecar monitor SHALL NOT close only the matching row and leave the remaining owner rows open against present exchange exposure
- **AND** it SHALL record an ambiguous net-mode stack or ghost-exposure audit event

#### Scenario: Exchange flat reconciliation remains allowed
- **WHEN** multiple open owner rows exist for a symbol
- **AND** the exchange position check confirms the symbol is flat
- **THEN** the sidecar MAY reconcile those owner rows closed using the existing exchange-flat pending-ledger path
```

## openspec/changes/fix-sidecar-ghost-position-safety/specs/tactical-exit-track/spec.md

- Source: openspec/changes/fix-sidecar-ghost-position-safety/specs/tactical-exit-track/spec.md
- Lines: 1-14
- SHA256: 2fb34bf19ecdcce9e38534801c2c4977715779135e30428ec72c186e9c932128

```md
## ADDED Requirements

### Requirement: Live sidecar admission SHALL enforce Tactical hard vetoes
The live sidecar admission path SHALL enforce Tactical hard vetoes that protect against same-symbol stacking and unbounded duplicate exposure. A Tactical shadow event that would create inseparable same-symbol exposure in the live sidecar SHALL be rejected before order submission and recorded with attribution.

#### Scenario: Sidecar active owner is a hard veto
- **WHEN** a Tactical shadow event targets a symbol and side with an already open sidecar owner row
- **THEN** live sidecar admission SHALL reject the event before order submission
- **AND** the rejection SHALL preserve attribution identifying same-symbol sidecar activity

#### Scenario: Main or unknown same-symbol exposure remains blocked
- **WHEN** a Tactical shadow event targets a symbol that already has Main, manual, unknown, or otherwise non-sidecar account exposure
- **THEN** live sidecar admission SHALL reject the event with same-symbol exposure attribution
- **AND** it SHALL NOT convert the candidate into a sidecar add-to-position action
```

