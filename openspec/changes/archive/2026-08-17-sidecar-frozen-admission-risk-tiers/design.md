## Context

Shadow Tactical rows are created by Judge and persisted through `CounterfactualLedger`, while the legacy Sidecar tails the resulting JSONL and turns broad Tactical rows into exchange orders. The current consumer checks shape, capacity, same-symbol exposure, entry drift, and exchange protection, but it does not enforce the strategy gate or quality warnings that existed when Judge created the row. It also accepts `--size-usdt 100` while `ContractExecutor.open_sidecar_plan()` clamps that request to the executor's Main-derived `RiskManager.max_trade_amount`, which is currently 30U in production.

The audited 53-trade cohort showed that `tactical_track_gate=pass` with no `trend_exhaustion_warning` yields nine eligible trades. At 100U for clean rows and 50U for rows marked `weak_volume_oi` or `weak_provenance`, the replay produced `+9.09U` net with `6.52U` maximum drawdown, versus `+4.47U` and `13.05U` maximum drawdown when every eligible row used 100U. Event-to-open latency was 1.79 seconds median, 2.53 seconds maximum, and fill drift remained within the existing 0.5 percent acceptance boundary.

Constraints include preserving every Shadow row for counterfactual research, keeping Sidecar latency under the five-second freshness boundary, not changing Main's risk configuration, retaining owner/protection/same-symbol fail-closed behavior, and not treating local replay as exchange-fill or live-PnL proof.

## Goals / Non-Goals

**Goals:**

- Make Judge the single strategy decision owner for Sidecar admission.
- Persist a versioned, auditable admission stamp on every future Tactical Shadow row.
- Make Sidecar execute only valid, fresh, eligible stamps at the frozen full/reduced risk tier.
- Give the Sidecar executor a dedicated 100U risk ceiling without changing Main.
- Lock the approved 53-row replay projection and policy integrity behavior into deterministic tests.

**Non-Goals:**

- Recomputing indicators, LLM warnings, Tactical economics, or strategy gates in Sidecar.
- Removing rejected Shadow rows or changing their counterfactual settlement.
- Changing Tactical exit mathematics, leverage, TP/SL ownership, entry drift, or same-symbol exposure handling.
- Enabling admission, deploying, or restarting cloud processes before local verification and owner-state checks.
- Claiming that counterfactual PnL is realized exchange PnL.

## Decisions

1. **Use a pure versioned policy classifier at the Judge boundary.** Judge will pass explicit Tactical quality flags into the profiled plan and derive a `sidecar_live_eligible`, `sidecar_policy_version`, `sidecar_risk_tier`, `sidecar_rejection_reason`, and `sidecar_decided_at` stamp before the ledger append. The ledger only persists those fields. This keeps strategy ownership in Judge instead of turning the observability ledger or Sidecar into a second strategy engine.

2. **Persist canonical policy evidence and verify it in Sidecar.** The stamp includes canonical booleans for the Tactical gate, trend exhaustion, weak volume/OI, and weak provenance. Sidecar re-runs only the pure policy classifier over these persisted booleans and compares the result with the stamp. It does not fetch market data or provenance inputs. Missing fields, unsupported versions, or any mismatch reject fail-closed with a specific audit reason.

3. **Apply the approved two-tier sizing after verification.** A valid eligible `full` row requests the configured base size of 100U; a valid eligible `reduced` row requests 50 percent of that base. `trend_exhaustion_warning` and gate failure always reject. Reduced tier is selected when `weak_volume_oi` or `weak_provenance` is frozen true; other non-exhaustion diagnostic labels do not independently alter the tier in this policy version.

4. **Keep freshness and execution safety separate.** Sidecar rejects a policy decision older than five seconds before capacity or exchange calls. A fresh eligible row must still pass the existing maximum-three-active, same-symbol account exposure, symbol halt, balance, 0.5 percent entry drift, slippage, order capability, geometry, and attached-SL verification checks. A policy pass is not a fill guarantee.

5. **Use an explicit constructor override for Sidecar risk only.** `ContractExecutor` gains an optional `max_trade_amount_override` validated against existing hard limits. `_build_executor()` passes the Sidecar base size as this override. Main call sites omit it and retain `load_config()` values. This is preferred over mutating `.env` because Main and Sidecar share the host and because command-line sizing alone is currently clamped.

6. **Make three positions a hard Sidecar ceiling.** Runtime values below three remain valid for cautious operation; values above three fail startup rather than silently expanding risk. The production command remains `--size-usdt 100 --max-active 3`.

7. **Replay policy, not future exchange behavior.** A sealed local fixture uses the Sidecar owner registry's exact `opened_at` window to identify the 53-trade population, joins policy evidence by `shadow_id`, and joins audited actual PnL by the owner entry order id. It will contain only fields needed to classify the row and its audited 100U-normalized PnL. The replay asserts nine eligible rows, stable full/reduced assignments, and the approved 100U/50U arithmetic over repeated loops. Live latency, drift, protection, and future realized PnL remain separate rollout evidence.

**Alternatives considered:**

- Recompute the strategy in Sidecar: rejected because duplicated indicator/LLM state introduces timing drift and recreates the parity failure.
- Filter only in Sidecar from `tactical_source`: rejected because string parsing becomes a second undocumented strategy contract and cannot prove producer/consumer agreement.
- Execute directly from Tactical V2 candidates: rejected for this change because it would replace the Sidecar lifecycle and ownership path rather than repair the approved Shadow Tactical strategy application.

## Risks / Trade-offs

- **[Risk] Five-second TTL rejects valid signals during host stalls.** Mitigation: audit `sidecar_policy_stale` separately and preserve the current two-second poll; do not extend TTL without latency evidence.
- **[Risk] Historical unstamped rows can no longer be backfilled live.** Mitigation: keep them readable for counterfactual analysis but fail closed for admission; start from new events after deployment.
- **[Risk] Producer and consumer code deploy at different revisions.** Mitigation: unsupported/missing policy versions reject and audit instead of falling back to broad Tactical detection.
- **[Risk] 100U raises order and drawdown exposure.** Mitigation: three-position ceiling, 50U warning tier, existing balance/daily-loss/drawdown checks, same-symbol guard, drift gate, and mandatory attached-SL verification remain authoritative.
- **[Risk] The 53-row result is in-sample.** Mitigation: use it as a deterministic correctness fixture, not a profitability guarantee; require live observation for realized performance.
- **[Risk] `weak_provenance` is confused with Sidecar reading observability metadata.** Mitigation: Sidecar consumes only the frozen Judge policy boolean and never reads or derives raw provenance confidence.

## Migration Plan

1. Add failing policy, ledger-stamp, Sidecar admission, risk-override, and replay tests.
2. Implement the pure classifier and stamp future Tactical Shadow plans at the Judge boundary.
3. Persist and map all stamp/evidence fields through the ledger and Sidecar plan.
4. Enforce version, integrity, TTL, tier sizing, and the three-position ceiling before exchange calls.
5. Add the dedicated executor risk override and prove Main defaults remain unchanged.
6. Run focused tests, the sealed replay repeatedly, and the relevant repository regression suite.
7. Before cloud deployment, collect read-only process, owner, position, protection, and admission state. Do not restart while an active owner cannot be recovered safely.
8. Deploy code without changing Main `.env`; restart Sidecar only through a controlled stop/start after owner recovery proof, then verify startup risk values and audit events.
9. Roll back by disabling Sidecar admission first, draining/protecting proven Sidecar exposure, and restoring the prior code revision. Historical stamped rows remain backward-compatible data.

## Open Questions

- None for implementation. Live enablement and restart remain separate operational gates based on current owner/protection truth.
