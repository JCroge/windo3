## Context

Tactical V2 consumes `tactical_candidate.v2` messages after Judge has recorded Legacy Shadow Tactical plans. The production replay found 22 tactical candidate rows but only three V2 intents, all BICO. Four PUMP candidates passed the tactical gate while the persisted PUMP episode was terminal; because the candidate bias was neutral, the reset function returned no renewal reason and the controller returned `duplicate_episode` before intent creation.

The same replay also showed that Legacy Shadow rows are repeated price-counterfactual records, while V2 deduplicates by structural episode and later requires executable bid/ask quotes. The system currently has no durable candidate handling receipt, so a rejected candidate, a duplicate, and a lost message cannot be distinguished from the event ledger.

## Goals / Non-Goals

**Goals:**

- Make fresh, unblocked neutral candidates eligible to renew a terminal episode when closed-bar or structure evidence is newer.
- Preserve one-attempt-per-episode and same-symbol/capacity safety rules.
- Persist accepted and rejected candidate handling outcomes with stable identities.
- Define parity against normalized candidate/episode identities, not raw repeated Shadow rows.
- Make the cloud-derived replay deterministic and regression-testable.

**Non-Goals:**

- Changing the Shadow Tactical scoring or track gate.
- Making every repeated Legacy Shadow row a separate V2 position.
- Bypassing executable bid/ask entry checks, governor capacity, entry TTL, or fail-closed protection.
- Repairing OKX attached-algo price semantics or reopening Sidecar admission.

## Decisions

1. **Fresh evidence gates compatible neutral renewal.** A terminal episode may renew when the candidate is available, the side is not blocked, the bias is aligned or neutral, and either the closed 15m bar advances or the structure token changes. Neutral bias alone is insufficient; opposing or unavailable bias remains ineligible.
2. **Episode de-duplication remains authoritative.** Repeated candidates on the same episode and structure evidence return a durable duplicate result. The normalized parity unit is the accepted episode, with source Shadow IDs retained for traceability.
3. **Receipts are append-only V2 events.** The handler records candidate ID, source Shadow ID, message ID when available, symbol, side, accepted flag, reason, episode ID, intent ID, evaluated time, and replay flag. Missing historical receipts remain explicitly unknown.
4. **Shadow parity has two explicit admission layers.** Normalized structural eligibility can be proven from candidate, episode, and audited opportunity-boundary data. It is a research projection, not the real Controller population. Real Controller admission is replayed separately without synthetic lifecycle release and retains `same_symbol_exposure`; fill and PnL parity still require executable quote and exchange evidence.
5. **Replay is the acceptance harness.** The 22-candidate cloud sequence is replayed repeatedly with pinned time and state. The expected normalized result is three BICO episodes plus two PUMP episodes, while the real Controller result is two intents, 22 receipts, and three `same_symbol_exposure` outcomes because terminal lifecycle evidence is absent. Both projections run independently in every one of 100 iterations.
6. **Single Main is a live correctness precondition.** The cross-process lock protects ledger sequence and candidate admission/receipt transactions only. Until a live lifecycle lease/fencing token exists, one namespace and exchange account may have exactly one active Main; controlled restart is stop-then-start.

## Risks / Trade-offs

- **[Risk]** Allowing neutral renewal can admit weak signals too often. **Mitigation:** require fresh closed-bar/structure evidence and no opposing block; retain the tactical track gate and governor.
- **[Risk]** Existing ledgers have no historical receipt events. **Mitigation:** report a separate `unknown_handling_evidence` state and do not backfill fabricated receipts.
- **[Risk]** Legacy Shadow and V2 may continue to disagree at executable entry. **Mitigation:** keep admission parity separate from fill parity and require quote capture before using the result for live rollout.
- **[Risk]** New receipt volume increases the Tactical ledger. **Mitigation:** use compact append-only records and preserve replay compatibility with absent receipts.
- **[Risk]** Operators mistake candidate locking for complete multi-process live safety. **Mitigation:** make one active Main a normative precondition and explicitly keep overlapping Main execution NO-GO.

## Migration Plan

1. Add failing unit and replay tests for terminal neutral renewal and receipt coverage.
2. Implement the episode transition and receipt persistence behind the existing V2 controller path.
3. Replay the cloud-derived candidate fixture and the existing V2 regression suite.
4. Deploy with Tactical V2 live admission unchanged and inspect receipts, duplicate rates, and integrity state.
5. Do not enable Sidecar admission or expand V2 capacity until quote-level and protection-level evidence is separately verified.

## Open Questions

- None for the approved design. Receipts retain canonical identity/evidence fields plus a payload hash; a strictly newer closed 15m bar qualifies as fresh evidence even when the structure token is unchanged.
