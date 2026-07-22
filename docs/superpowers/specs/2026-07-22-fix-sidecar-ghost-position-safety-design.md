---
comet_change: fix-sidecar-ghost-position-safety
role: technical-design
canonical_spec: openspec
---

# Sidecar Ghost Position Safety Technical Design

## Context

OpenSpec is the canonical requirement source for this change. This document defines the implementation approach for `fix-sidecar-ghost-position-safety`.

The ADA incident exposed a split-brain between four state owners:

- OKX account state: one live `ADA-USDT-SWAP` net-mode long position.
- Sidecar owner registry: multiple open owner rows for the same symbol.
- Sidecar executor positions file: empty or containing only the last symbol-keyed position.
- Main executor migration: no local Main position, but account-level pending algo cleanup still active.

The dangerous chain is:

```text
sidecar opens same symbol more than once
  -> OKX net_mode merges exposure
  -> sidecar local position state keeps only one symbol-keyed row
  -> monitor can prove at most one owner row
  -> remaining owners become unproven while exchange exposure is present
  -> Main skips position backfill as sidecar-owned
  -> Main still cancels manual OCO/conditional protection as orphan algo
  -> exchange exposure is left unmanaged
```

The 2026-07-20 flat-reconcile hotfix only closes stale owner metadata after the exchange confirms flat. This change handles the harder exchange-present case and blocks the admission path that creates unmanageable net-mode stacks.

## Design Summary

Use a conservative safety model:

1. Main preserves ambiguous/manual protection for sidecar-owned symbols when exchange exposure is present or unknown.
2. Sidecar blocks same-symbol stacking in OKX `net_mode`.
3. Sidecar monitor treats unproven present exposure as ghost exposure and fails closed.
4. Sidecar live opens enforce stale-entry drift protection before market order submission.

This change does not introduce aggregate sidecar positions. If multiple same-symbol owner rows already exist, they are treated as an ambiguous legacy state unless exchange state is flat.

## Components

### Main Migration Protection

Primary code path: `ContractExecutor._migrate_okx_algos_for_symbol()`.

Add a read-only helper that answers:

```text
sidecar_symbol_state(symbol) -> none | flat | present | unknown
```

The helper combines:

- active sidecar owner lookup from `ShadowTacticalOwnerRegistry`,
- exchange position state when available,
- normalized internal/exchange symbol matching.

Migration behavior:

- If Main has a local position, existing Main ownership logic continues.
- If Main has no local position and sidecar state is `present` or `unknown`, preserve pending SL/OCO/conditional algos for that symbol unless they are proven Main-owned and explicitly safe to mutate.
- If sidecar state is `none` or confirmed `flat`, existing orphan cleanup behavior remains available.

The migration summary should expose preserved ambiguous protection separately from `foreign_algos`, for example `sidecar_protected_algos` or `ambiguous_sidecar_algos`. Tests should assert behavior, not depend on the exact field name unless implementation chooses one.

### Sidecar Admission Guard

Primary code paths:

- `utils.shadow_tactical_live.ShadowTacticalOwnerRegistry`
- `utils.shadow_tactical_live.blocks_same_symbol_account_exposure()`
- `scripts.shadow_tactical_live_sidecar._process_event()`

Current behavior treats sidecar-owned same-symbol exposure as safe and allows another open. That is the bug source in OKX `net_mode`.

New behavior:

- If registry has an open owner row for the same normalized symbol and side, reject the new sidecar open with `same_symbol_sidecar_active`.
- If exchange has present same-symbol exposure and no explicit aggregate model exists, reject with `same_symbol_account_exposure` or a more specific sidecar reason.
- Keep `--max-active` as portfolio-level cap only.

This should run before `executor.open_sidecar_plan()` so no exchange order is submitted.

### Ghost Exposure Monitor

Primary code path: `scripts.shadow_tactical_live_sidecar.monitor_sidecar_owned_exposure()`.

Add a ghost exposure branch before the current `monitor_skipped_unproven` path:

```text
for each open owner row:
  local = prove row against executor.positions
  exchange_state = present | flat | unknown | unsupported

  if local is missing and exchange_state == flat:
      existing exchange-flat reconcile path
  if local is missing and exchange_state in present | unknown:
      record ghost exposure
      block/halt sidecar opens for symbol
      do not close/reduce
      continue
```

For `present`, the audit event should include:

- `event_type`: `monitor_ghost_exposure` or equivalent,
- `shadow_id`,
- `symbol`,
- `exchange_state`,
- `unproven_owner: true`,
- whether pending TP/SL protection could be observed,
- `operator_action_required: true` when protection is absent or unknown.

The monitor must also guard against ambiguous owner stacks:

```text
if there are multiple open owners for the same symbol/side
and only one symbol-keyed local position exists
and exchange state is present:
  do not close/reduce any one row
  record ambiguous net-mode stack / ghost exposure
```

Exchange-flat reconciliation remains allowed because no exchange exposure is being mutated.

### Entry Drift Guard For Sidecar Opens

Primary code path: `ContractExecutor.open_sidecar_plan()`.

Sidecar should not call `open_position_with_plan()` because that would pull in Main strategy accounting. Instead, add a narrow sidecar drift precheck before slippage, precheck, amount calculation, and `create_order()`.

Preferred implementation:

1. Build a temporary drift plan:
   - `entry_ref`: existing `plan.entry_ref` or `plan.entry_price`,
   - `sl_pct`: existing `plan.sl_pct` or derived from `abs(entry_ref - stop_loss) / entry_ref`,
   - `tp_pct`: existing `plan.tp_pct` or derived from first take-profit level,
   - `side`: plan side.
2. Call existing `_classify_entry_drift()` when enough anchors exist.
3. For `abandon` or `recalc_fail`, reject before order submission.
4. For `recalc_pass`, either use recomputed SL/TP or choose a stricter sidecar policy that rejects recomputation and records a stale-entry reason. The recommended first implementation is strict reject for non-accept bands unless tests show recompute is already safe for sidecar TP/SL attach flow.
5. Persist drift metadata on accepted positions under `gate_metadata["entry_drift"]` or a top-level sidecar-specific field.

Missing anchors should fail closed. The sidecar input normally has `entry_ref`, `stop_loss`, and `take_profit`; if any are absent, existing mapper validation or new drift validation should reject before exchange order submission.

## Data Flow

### Admission

```text
shadow event
  -> map_shadow_record_to_plan()
  -> active-owner same-symbol guard
  -> exchange same-symbol guard
  -> open_sidecar_plan()
       -> sidecar drift precheck
       -> existing SL side validation
       -> existing slippage/depth/precheck/min-size checks
       -> market open with attached owner-tag SL
       -> SL verification
  -> record owner row only after verified open
```

### Monitoring

```text
owner registry load
  -> group open owners by exchange_symbol/side
  -> prove owner against executor.positions
  -> check exchange position state
  -> flat: reconcile metadata
  -> present + unproven/ambiguous: ghost audit + halt/block
  -> present + single proven local position: evaluate tactical exits
```

### Main Migration

```text
sync_positions()
  -> Main skips sidecar-owned position backfill
  -> _migrate_all_symbols_algos()
  -> _migrate_okx_algos_for_symbol(symbol)
       -> classify sidecar symbol state
       -> preserve ambiguous protection if sidecar-owned present/unknown
       -> otherwise keep existing Main migration rules
```

## Error Handling

- Exchange position fetch failure should produce `unknown`, not `flat`.
- `unknown` sidecar state preserves protection and blocks unsafe sidecar opens.
- Ghost exposure detection must not submit close/reduce orders.
- Failed audit writes should not cause exposure mutation; log and keep fail-closed behavior.
- Existing exchange-flat reconcile remains the only automatic unproven-owner close path.

## Test Strategy

### Regression Tests First

1. Main migration:
   - no local Main position,
   - active sidecar owner,
   - exchange state present/unknown,
   - manual OCO and conditional SL without owner tag,
   - assert no cancel and preservation summary/audit.

2. Sidecar admission:
   - active same-symbol sidecar owner rejects new same-symbol open before `create_order`,
   - present exchange exposure rejects unmodeled sidecar stack,
   - non-sidecar exposure remains blocked.

3. Ghost monitor:
   - open owner + no local position + exchange present -> ghost audit, no close/reduce,
   - no pending protection -> `operator_action_required`,
   - repeated monitor does not only emit old unproven skip.

4. Ambiguous stack:
   - three open owners + one local symbol-keyed position,
   - trigger close condition,
   - assert no single-row close and ghost/ambiguous audit.

5. Entry drift:
   - large drift rejects before `create_order`,
   - accepted drift stores metadata,
   - missing anchors reject before order.

### Focused Verification Command

The implementation plan should include a focused command similar to:

```bash
pytest \
  tests/test_shadow_tactical_live_core.py \
  tests/test_shadow_tactical_live_executor.py \
  tests/test_shadow_tactical_owner_isolation.py \
  tests/test_shadow_tactical_exit_monitoring.py \
  tests/test_entry_drift_hybrid_policy.py \
  test_partial_tp_lifecycle.py -q
```

The exact set can be narrowed if the new migration tests live in a more focused file.

## Rollout

1. Keep sidecar scale-up paused while this change is in build/verify.
2. Deploy Main migration preservation before resuming sidecar.
3. Run sidecar `status` and inspect owner rows for pre-existing ghost/stacked exposure.
4. Manually reconcile any existing exchange-present ghost exposure; this change intentionally does not auto-close it.
5. Resume sidecar with same-symbol stacking blocked.

## Spec Patch Notes

No additional OpenSpec delta spec patch is required beyond the current change artifacts. If implementation discovers that a repair command is needed to reconstruct local sidecar metadata, that should be a separate change because it introduces a new operational capability.
