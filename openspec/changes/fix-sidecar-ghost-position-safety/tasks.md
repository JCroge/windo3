## 1. Regression Coverage

- [x] 1.1 Add a Main migration regression proving manual OCO/conditional protection is preserved when a symbol is sidecar-owned and exchange exposure is present or unknown.
- [x] 1.2 Add sidecar admission regressions proving same-symbol sidecar opens are rejected in OKX `net_mode` when an active owner or present exchange exposure already exists.
- [x] 1.3 Add sidecar monitor regressions proving ghost exposure emits fail-closed audit/halt behavior and does not silently loop on `monitor_skipped_unproven`.
- [x] 1.4 Add a net-mode stacked-owner regression proving monitor does not close one owner row while leaving remaining same-symbol exposure unproven and unmanaged.
- [ ] 1.5 Add sidecar entry-drift regressions proving large stale drift rejects before `create_order` and accepted opens record drift metadata.

## 2. Main Migration Protection

- [x] 2.1 Extend Main sidecar-owner lookup or migration context so `_migrate_okx_algos_for_symbol()` can determine sidecar-owned present/unknown exchange exposure.
- [x] 2.2 Preserve ambiguous/manual OCO and conditional TP/SL algos for sidecar-owned present/unknown exposure instead of canceling them as orphan residuals.
- [x] 2.3 Keep existing exchange-flat or non-sidecar orphan cleanup behavior intact.

## 3. Sidecar Admission Safety

- [x] 3.1 Tighten `blocks_same_symbol_account_exposure()` or sidecar admission logic so active same-symbol sidecar owner rows block new sidecar opens in OKX `net_mode`.
- [x] 3.2 Ensure exchange-present same-symbol exposure blocks sidecar opens unless a future aggregate-position model is explicitly available.
- [x] 3.3 Add sidecar audit rejection reasons for same-symbol active owner and unmodeled exchange exposure.

## 4. Ghost Exposure Monitoring

- [x] 4.1 Detect ghost exposure in `monitor_sidecar_owned_exposure()` when owners are open, exchange exposure is present, and local sidecar position proof is missing.
- [x] 4.2 Emit fail-closed audit/halt metadata for ghost exposure while preserving the rule that unproven exchange exposure is not closed or reduced automatically.
- [x] 4.3 Detect ambiguous same-symbol net-mode owner stacks before applying close/reduce actions, and fail closed unless the whole net exposure is proven as one aggregate position.

## 5. Sidecar Entry Drift Guard

- [ ] 5.1 Derive sidecar drift anchors from `entry_ref`, `stop_loss`, and first `take_profit` when explicit `sl_pct`/`tp_pct` are absent.
- [ ] 5.2 Reject large stale sidecar opens before exchange order submission and record drift rejection attribution.
- [ ] 5.3 Persist drift admission metadata for accepted sidecar opens.

## 6. Verification And Operations

- [ ] 6.1 Run focused tests for sidecar core, sidecar executor, owner isolation, exit monitoring, entry drift, and algo migration.
- [ ] 6.2 Update or add verification report documenting the ADA failure-class reproduction, fixed behavior, and any remaining operational constraints.
- [ ] 6.3 Document rollout ordering: deploy Main migration preservation before resuming sidecar, then verify sidecar status has no ghost exposure.
