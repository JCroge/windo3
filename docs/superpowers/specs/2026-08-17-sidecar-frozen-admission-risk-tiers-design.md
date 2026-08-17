---
comet_change: sidecar-frozen-admission-risk-tiers
role: technical-design
canonical_spec: openspec
---

# Sidecar Frozen Admission And Risk Tiers

## Context

Judge creates the Shadow Tactical plan, `CounterfactualLedger` persists it, and the legacy Sidecar tails the ledger to open real positions. The current Sidecar accepts any broad Tactical row, including `tactical_track_gate=fail` and `trend_exhaustion_warning`, because `is_tactical_shadow_event()` only checks the track/profile marker. It also passes `--size-usdt 100` into `open_sidecar_plan()`, where the requested size is clamped to the executor's Main-derived 30U `RiskManager.max_trade_amount`.

The approved correction is a producer-owned strategy decision plus a consumer-owned execution check. Judge freezes the decision. Sidecar verifies and executes it. Sidecar must not fetch indicators, parse LLM output, derive provenance confidence, or recalculate Tactical RR/EV.

## Architecture

```text
Judge quality + Tactical profile
             |
             v
  shadow_sidecar_policy.py
  classify + stamp (v1)
             |
             v
CounterfactualLedger append all rows
             |
             v
Sidecar tail -> verify same v1 stamp -> TTL <= 5s
             |
             +-- ineligible/mismatch/stale -> audit reject
             |
             v
full=100U / reduced=50U
             |
             v
capacity<=3 -> exposure -> drift<=0.5% -> exchange/protection
```

The canonical OpenSpec requirements remain under `openspec/changes/sidecar-frozen-admission-risk-tiers/specs/`. This document defines implementation ownership and test structure only.

## Policy Module

Create `utils/shadow_sidecar_policy.py` as the single policy implementation. It should expose immutable result objects or plain frozen dataclasses with these responsibilities:

```python
SIDECAR_POLICY_VERSION = "shadow-sidecar-v1"
SIDECAR_POLICY_MAX_AGE_SECONDS = 5.0
SIDECAR_MAX_ACTIVE_POSITIONS = 3

def canonical_policy_evidence(plan: dict) -> dict:
    ...

def classify_sidecar_policy(evidence: dict) -> SidecarPolicyDecision:
    ...

def stamp_sidecar_policy(plan: dict, *, decided_at: float) -> dict:
    ...

def verify_sidecar_policy(record: dict, *, now: float) -> SidecarPolicyVerification:
    ...
```

Canonical evidence is explicit and typed:

```text
tactical_track_gate: pass|fail
trend_exhaustion_warning: bool
weak_volume_oi: bool
weak_provenance: bool
```

Policy v1 is deterministic:

```text
gate != pass                  -> reject: tactical_track_gate_failed
trend_exhaustion_warning      -> reject: trend_exhaustion_warning
weak_volume_oi OR weak_provenance
                              -> eligible: reduced
otherwise                     -> eligible: full
```

Unknown values do not coerce to safe-looking defaults. Missing evidence, malformed booleans, unsupported versions, non-finite timestamps, outcome mismatches, and tier mismatches return explicit fail-closed verification results. Verification recomputes only this four-field policy; it does not recompute the trading strategy.

## Judge Integration

`Judge._classify_track()` already produces `quality_flags`. `_apply_tactical_profile()` will copy the three policy-relevant booleans into explicit plan fields instead of forcing Sidecar to parse `tactical_source`. Clean rows write false values. Gate status remains the existing `tactical_track_gate` field.

`Judge._record_rejected_plan()` is the final common boundary before every rejected plan enters the counterfactual ledger. For Tactical rows, it will call `stamp_sidecar_policy(..., decided_at=time.time())` on a copied plan and pass that copy to `CounterfactualLedger.record_rejection()`. Non-Tactical rows remain unchanged. This captures a single decision time immediately before persistence and avoids putting strategy behavior into the ledger class.

`CounterfactualLedger.record_rejection()` will persist the stamp and canonical evidence fields verbatim. Every row remains in `_active` and `rejected_signal_events.jsonl`, including rows ineligible for Sidecar live execution.

## Sidecar Integration

`map_shadow_record_to_plan()` will carry the complete stamp and evidence into the execution plan and gate metadata. `_process_event()` will verify the policy immediately after duplicate/admission-enabled checks and before dry-run, active-count, exchange-position, or executor work.

Rejected verification outcomes will:

- mark the shadow id `rejected`;
- append a `rejected` audit event with the exact policy reason, policy version, age when available, frozen tier, and evidence;
- make no balance, position, ticker, or order call.

Eligible outcomes map sizing from the command's full-tier base:

```python
requested_size = base_size if tier == "full" else base_size * 0.5
```

The opened/rejected audit includes `sidecar_policy_version`, `sidecar_risk_tier`, and `requested_size_usdt`. The position already persists `amount_usdt` and gate metadata; the mapped policy fields remain in `gate_metadata` for post-trade attribution.

Dry-run uses the same verification and tier mapping. It is a simulation of live admission, not a bypass.

## Capacity And Startup Validation

Add pure argument resolvers in the Sidecar script so tests can validate startup without exchange I/O:

```python
def resolve_sidecar_base_size(value) -> float:
    ...  # finite, positive, existing hard limit

def resolve_sidecar_max_active(value) -> int:
    ...  # 1..3 only
```

`cmd_run()` validates both before creating state or an executor. Values above three fail with a clear error. Values one or two remain valid for cautious operation. Production remains `--size-usdt 100 --max-active 3`.

## Executor Risk Override

Extend `ContractExecutor.__init__()` with:

```python
max_trade_amount_override: Optional[float] = None
```

After `load_config()` or its existing fallback establishes Main risk values, validate the optional override against `HARD_LIMITS["max_trade_amount"]`. Invalid or non-finite input raises before `RiskManager` construction. A valid override replaces only `max_amount`; drawdown, daily loss, effective balance cap, baseline mode, state files, and all Main call sites remain unchanged.

`_build_executor(paths, *, max_trade_amount)` passes the already validated Sidecar base size as the override. This makes the existing `min(requested_size, risk_manager.max_trade_amount)` clamp a true Sidecar safety ceiling of 100U instead of the unrelated Main 30U ceiling.

## Replay Fixture

Create `tests/fixtures/shadow_sidecar_policy_53_trade_window.json` from a read-only cloud extraction. Store only:

```text
shadow_id
tactical_track_gate
trend_exhaustion_warning
weak_volume_oi
weak_provenance
resolved_pnl_pct or audited pnl_usdt_at_100u
```

The fixture metadata records source path, UTC/CST window, row count, extraction hash, and the approved aggregate. It contains no credentials, account balances, order ids, or mutable production state.

The replay helper remains local and pure. It classifies all 53 rows, asserts exactly nine eligible, scales full rows by 1.0 and reduced rows by 0.5 against the sealed 100U PnL, and repeats classification enough times to prove stable identities, reasons, tiers, and aggregate arithmetic. The expected tiered result is `+9.09U` within fixture precision. It is labeled counterfactual, not realized PnL.

## Error Handling

Policy errors are pre-exchange fail-closed errors. They do not halt unrelated symbols or mutate global halt state. Existing exchange errors retain current behavior: unknown exposure rejects, ghost exposure halts the symbol, drift rejection audits, and unverified attached SL halts the symbol.

Producer/consumer version skew is expected during deployment. New Sidecar code rejects old unstamped rows and unsupported future rows; it never falls back to broad Tactical admission. Deployment starts from new events after both code paths are synchronized.

## Testing Strategy

1. `tests/test_shadow_sidecar_policy.py` covers the complete classifier/stamp/verify truth table, strict typing, version mismatch, timestamp boundaries, future skew, and stamp/evidence mismatch.
2. Judge/Tactical tests prove quality flags become explicit fields and every Tactical ledger record is stamped while Main records remain unchanged.
3. `tests/test_shadow_tactical_live_core.py` proves mapping preserves all policy fields.
4. `tests/test_shadow_tactical_live_cli.py` proves rejection occurs before executor/exchange calls, dry-run parity, 100U/50U requests, exact audit evidence, and capacity startup bounds.
5. `tests/test_shadow_tactical_live_executor.py` proves a 100U risk ceiling does not clamp full-tier orders and existing 30U behavior remains when no override is present.
6. The sealed 53-row replay proves nine eligible rows, tier assignment, `+9.09U` arithmetic, and loop determinism.
7. Focused tests run before the relevant repository suite. No test connects to cloud or an exchange.

## Rollout Boundary

Code completion does not authorize live restart. Before deployment, collect current Sidecar/Main process commands, admission state, owner registry, local positions, exchange positions, protective orders, pending entries, and pending PnL. An active Sidecar owner requires verified restart recovery or a flat/drained state before restart.

Deployment does not modify Main `.env`. Synchronize producer and Sidecar files, validate hashes, stop old Sidecar admission, confirm old PID exit, then start one Sidecar with `--size-usdt 100 --max-active 3`. Verify the constructed risk ceiling, policy version, no historical backfill, and first audit outcomes before treating the rollout as active.
