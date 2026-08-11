---
comet_change: tactical-v2-shadow-admission-parity
role: technical-design
canonical_spec: openspec
---

# Tactical V2 Shadow Admission Parity

## Context

The audited production window contained 22 tactical candidate messages and 22 Legacy Shadow Tactical rows, but only three V2 intents, all for BICO. Four PUMP rows passed the tactical gate while the persisted PUMP episode was terminal. The candidate structure was available and unblocked but neutral, and `EpisodeRegistry._reset_reason()` rejected it before intent creation because a long side required a bullish bias.

The Legacy Shadow ledger also records repeated price-counterfactual plans independently, while V2 deduplicates by symbol/side/structure episode and requires executable bid/ask entry. The message bus journals publication but the Executor currently does not persist a durable handling outcome. The implementation therefore needs two explicit boundaries: admission parity and executable/exchange parity.

## Goals

- Permit a terminal episode to renew from fresh, unblocked structure evidence even when the 15m bias is neutral.
- Preserve de-duplication, same-symbol exposure protection, governor capacity, entry TTL, and fail-closed protection behavior.
- Make every newly consumed candidate auditable as accepted, rejected, or unknown for historical data.
- Compare Shadow and V2 at normalized candidate/episode granularity.
- Lock the cloud-derived replay expectation into deterministic tests.

## Non-Goals

- Rewriting Shadow Tactical scoring, track gating, or Legacy Shadow price settlement.
- Treating every raw Shadow row as a separate live V2 position.
- Bypassing the V2 executable quote reducer or converting scalar Kline touches into fills.
- Fixing OKX attached-algo rounding/asynchronous visibility in this change.
- Enabling the Sidecar or changing live configuration.

## Design

### 1. Episode renewal

`EpisodeRegistry._reset_reason()` will evaluate fresh evidence before applying the existing directional-bias renewal rules:

```text
structure available
AND candidate side not blocked
AND state terminal
AND (
  candidate closed_bar_ts > state.last_closed_bar_ts
  OR candidate structure_token != state.last_structure_token
)
=> new_confirmed_structure
```

The comparison is numeric and monotonic for `closed_bar_ts`; missing timestamps do not qualify. A changed structure token can qualify when the token is present and differs from the persisted token. Neutral bias is accepted only through this fresh-evidence branch. A neutral candidate with identical structure evidence remains `duplicate_episode`.

The existing `_observe_locked()` behavior that preserves the last confirmed structure baseline remains intentional: the baseline is needed to compare a later candidate against the terminal episode. Reset evidence is persisted before the new `episode_assigned` event.

### 2. Candidate handling receipts

The Executor will pass the MessageBus `msg_id` into the controller candidate handler. The controller will append a compact `candidate_handled` event after it has a final `CandidateHandlingResult` and, for accepted candidates, after `intent_created` has been persisted.

The receipt contains:

```text
candidate_id
source_shadow_id
message_id
symbol
side
accepted
reason
episode_id
intent_id
evaluated_at
replayed
payload_hash
```

The canonical fields avoid duplicating the full message journal. `payload_hash` allows later evidence matching. Legacy candidates without a receipt remain `unknown_handling_evidence`; replay never fabricates a historical receipt.

Every controller return path must pass through one receipt-writing helper. If the process crashes between intent creation and receipt append, replay reports the intent and missing receipt as an observability inconsistency rather than inventing an outcome.

### 3. Normalized admission parity

Parity reporting will maintain two counts:

```text
raw_shadow_rows       # Legacy Shadow observations
normalized_opportunities  # candidate/episode identities
```

For the audited fixture, repeated source Shadow IDs remain visible, but the expected admission result is:

```text
3 BICO episodes
2 PUMP episodes
all other repeated rows: duplicate_episode
```

Parity does not mark an exchange fill. After admission, V2 still runs executable bid/ask, entry drift, TTL, governor, and protection checks. Quote-level and exchange-level PnL comparisons remain separate evidence classes.

### 4. Replay harness

The replay fixture will contain the 22 candidate payloads and the initial terminal PUMP episode state. It will use an in-memory TacticalStore, pinned timestamps, and no exchange or network access. The harness will assert the accepted identities, rejection reasons, episode sequence, and executable entry decision for an at-entry quote. It will repeat the same sequence 100 times and require identical serialized results.

## Data Flow

```text
Judge
  -> MessageBus publish(msg_id)
  -> Executor receives candidate(msg_id)
  -> Controller validates candidate
  -> EpisodeRegistry assigns/renews episode
  -> candidate_handled receipt
  -> intent_created (accepted path)
  -> V2 executable bid/ask reducer
  -> live exchange or shared V2 ShadowAdapter
```

The receipt records admission decisions; entry and settlement events record later lifecycle decisions. This separation prevents a price-only Legacy Shadow TP from being mistaken for an executable V2 fill.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Neutral renewal admits too many weak signals | Require fresh bar/token, available structure, no block, and retain tactical gate/governor. |
| Receipt append gap after intent creation | Report missing receipt as an explicit integrity/observability inconsistency; add restart replay tests. |
| Same-bar structure token churn | Prefer strictly newer closed bars; only accept token changes when a non-empty token differs. |
| Legacy Shadow remains non-executable | Keep admission parity separate and prohibit live rollout claims without L1 quote evidence. |
| Existing ledgers lack receipt history | Preserve `unknown_handling_evidence`; never backfill inferred consumption. |

## Verification Strategy

1. Episode unit tests cover fresh neutral renewal, same-evidence duplicate, blocked neutral, missing structure, restart, and historical terminal episodes.
2. Receipt tests cover accepted, duplicate, validation, expiry, capacity, ordering, replay, and absent historical receipt behavior.
3. The cloud-derived replay asserts 3 BICO plus 2 PUMP normalized episodes and 100-loop stability.
4. The focused Tactical V2 suite and all related replay/shadow tests must pass.
5. No test may require cloud credentials, exchange I/O, process restart, Sidecar admission, or mutation of production data.

## Rollout and Rollback

The change is deployed with existing V2 mode and Sidecar admission settings unchanged. First inspect candidate receipts, duplicate rates, unknown handling count, episode outcomes, and integrity state in shadow/replay. Rollback is a code-version rollback; append-only receipt events remain replay-compatible and do not require data migration. Sidecar admission remains disabled until separate quote-level and protection-level verification passes.
