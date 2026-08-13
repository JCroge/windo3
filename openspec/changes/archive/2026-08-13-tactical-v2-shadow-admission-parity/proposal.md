## Why

The 72-hour production replay showed that Tactical V2 and Shadow Tactical do not share a reliable admission contract. PUMP candidates passed the tactical gate but remained trapped behind a terminal episode because the renewal path required a directional bias that Shadow did not require; the controller also emitted no durable receipt explaining whether each candidate was accepted or rejected. This makes missed openings difficult to distinguish from intentional de-duplication or message loss, and prevents a defensible Shadow-to-V2 parity check.

## What Changes

- Allow a terminal Tactical episode to renew on a newer closed 15m bar or structure token when the candidate is available and not blocked, including a neutral bias; preserve de-duplication for the same episode and same structure evidence.
- Persist a durable candidate handling receipt containing candidate identity, source Shadow identity, message identity when available, admission result, rejection reason, episode ID, intent ID, and evaluation time.
- Define Shadow-to-V2 admission parity at the unique candidate/episode level rather than treating repeated Legacy Shadow ledger rows as independent V2 positions.
- Add deterministic replay coverage for the cloud-derived 22-candidate sequence, including repeated candidates, terminal episode renewal, neutral/unblocked candidates, and 100-run stability.
- Keep executable bid/ask entry checks, governor capacity, protection fail-closed behavior, Sidecar admission state, and live configuration unchanged by this change.

## Capabilities

### New Capabilities

- `tactical-shadow-admission-parity`: Defines the normalized candidate/episode parity contract and deterministic replay acceptance criteria between Shadow Tactical and Tactical V2.
- `tactical-candidate-receipts`: Defines durable accepted/rejected handling receipts for `tactical_candidate.v2` messages.

### Modified Capabilities

- `tactical-intent-lifecycle`: Terminal episodes may renew from fresh closed-bar or structure evidence when the side is not blocked, including neutral bias; repeated evidence remains de-duplicated.

## Impact

- Affected code: `utils/tactical_v2/episodes.py`, `utils/tactical_v2/controller.py`, `agents/trading/executor.py`, and associated replay/test modules.
- Affected persisted data: new append-only Tactical V2 candidate receipt events; existing event replay must remain backward compatible when receipts are absent.
- Affected operational reporting: parity summaries will distinguish accepted episode alignment, intentional duplicate rejection, and missing/unknown handling evidence.
- No production configuration, Sidecar admission setting, exchange protection behavior, or live order policy is changed as part of the admission fix.
