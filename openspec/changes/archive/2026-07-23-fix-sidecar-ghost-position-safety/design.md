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
