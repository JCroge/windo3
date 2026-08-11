# Comet Design Handoff

- Change: tactical-v2-shadow-admission-parity
- Phase: design
- Mode: compact
- Context hash: d4a5088ce0eefe14d127b3cb087b47a223e4b40e79c8d9229ab676754c388cfa

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/tactical-v2-shadow-admission-parity/proposal.md

- Source: openspec/changes/tactical-v2-shadow-admission-parity/proposal.md
- Lines: 1-29
- SHA256: 4c51859c96d8b16644a65c63d79877f228e6d900fba5875abacac8735e2875a6

```md
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
```

## openspec/changes/tactical-v2-shadow-admission-parity/design.md

- Source: openspec/changes/tactical-v2-shadow-admission-parity/design.md
- Lines: 1-49
- SHA256: 16a6f29168085d9aef9aab37553d02780b0dd08e5f25afbbe41cd517575ba4d9

```md
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

1. **Fresh evidence gates neutral renewal.** A terminal episode may renew when the candidate is available, the side is not blocked, and either the closed 15m bar advances or the structure token changes. Neutral bias alone is insufficient.
2. **Episode de-duplication remains authoritative.** Repeated candidates on the same episode and structure evidence return a durable duplicate result. The normalized parity unit is the accepted episode, with source Shadow IDs retained for traceability.
3. **Receipts are append-only V2 events.** The handler records candidate ID, source Shadow ID, message ID when available, symbol, side, accepted flag, reason, episode ID, intent ID, evaluated time, and replay flag. Missing historical receipts remain explicitly unknown.
4. **Shadow parity stops at the correct boundary.** Admission parity can be proven from candidate and episode data. Fill and PnL parity still requires executable quote and exchange evidence; the implementation must not infer a live fill from a Legacy scalar-price Shadow result.
5. **Replay is the acceptance harness.** The 22-candidate cloud sequence is replayed repeatedly with pinned time and state. The expected normalized result is three BICO episodes plus two PUMP episodes, with repeated rows rejected explicitly.

## Risks / Trade-offs

- **[Risk]** Allowing neutral renewal can admit weak signals too often. **Mitigation:** require fresh closed-bar/structure evidence and no opposing block; retain the tactical track gate and governor.
- **[Risk]** Existing ledgers have no historical receipt events. **Mitigation:** report a separate `unknown_handling_evidence` state and do not backfill fabricated receipts.
- **[Risk]** Legacy Shadow and V2 may continue to disagree at executable entry. **Mitigation:** keep admission parity separate from fill parity and require quote capture before using the result for live rollout.
- **[Risk]** New receipt volume increases the Tactical ledger. **Mitigation:** use compact append-only records and preserve replay compatibility with absent receipts.

## Migration Plan

1. Add failing unit and replay tests for terminal neutral renewal and receipt coverage.
2. Implement the episode transition and receipt persistence behind the existing V2 controller path.
3. Replay the cloud-derived candidate fixture and the existing V2 regression suite.
4. Deploy with Tactical V2 live admission unchanged and inspect receipts, duplicate rates, and integrity state.
5. Do not enable Sidecar admission or expand V2 capacity until quote-level and protection-level evidence is separately verified.

## Open Questions

- None for the approved design. Receipts retain canonical identity/evidence fields plus a payload hash; a strictly newer closed 15m bar qualifies as fresh evidence even when the structure token is unchanged.
```

## openspec/changes/tactical-v2-shadow-admission-parity/tasks.md

- Source: openspec/changes/tactical-v2-shadow-admission-parity/tasks.md
- Lines: 1-27
- SHA256: 1bfee15b9d50ef70d5d8b1fb5d7eebda1fd0762d9a884d859d271d73860584ee

```md
## 1. Episode Renewal

- [ ] Add a regression test for terminal `neutral + unblocked + fresh closed bar` renewal.
- [ ] Add negative tests for neutral without fresh evidence and neutral while blocked.
- [ ] Implement the minimal EpisodeRegistry renewal rule using monotonic closed-bar or changed structure evidence.
- [ ] Verify episode replay and restart preserve the new epoch and terminal historical episodes.

## 2. Candidate Receipts

- [ ] Define the compact receipt event schema and backward-compatible replay behavior.
- [ ] Persist receipts for accepted, duplicate, validation, expiry, block, capacity, and other rejection paths.
- [ ] Preserve message ID/source Shadow ID mapping without assuming historical receipts exist.
- [ ] Add tests for receipt ordering, idempotent replay, and absent historical receipts.

## 3. Shadow Parity Replay

- [ ] Add a pinned 22-candidate replay fixture derived from the audited cloud sequence without live credentials or exchange I/O.
- [ ] Assert normalized output of three BICO episodes and two PUMP episodes, with repeated rows explicitly rejected.
- [ ] Run the replay 100 times and assert identical accepted identities and reasons.
- [ ] Keep executable quote, governor capacity, entry TTL, and protection checks in the parity boundary.

## 4. Verification and Operations

- [ ] Run the focused Tactical V2 regression suite and the full relevant test suite.
- [ ] Add an operator report showing accepted, duplicate, rejected, and unknown candidate counts.
- [ ] Update the runbook to prohibit Sidecar admission restoration until quote-level and protection evidence passes.
- [ ] Verify no production configuration or cloud data is modified by the implementation tests.
```

## openspec/changes/tactical-v2-shadow-admission-parity/specs/tactical-candidate-receipts/spec.md

- Source: openspec/changes/tactical-v2-shadow-admission-parity/specs/tactical-candidate-receipts/spec.md
- Lines: 1-28
- SHA256: 633acafa38bf6143a770138bace66bb5581fcec5c0e92099c6acc01c01942ead

```md
## ADDED Requirements

### Requirement: Tactical candidate handling SHALL persist a durable receipt
The V2 candidate consumer SHALL append one handling receipt for every consumed `tactical_candidate.v2` message. The receipt MUST contain candidate ID, source Shadow ID, message ID when available, normalized symbol, side, accepted flag, reason, episode ID when assigned, intent ID when created, evaluated timestamp, and replay flag. Receipt writes SHALL be append-only and replay-safe.

#### Scenario: Accepted candidate has a receipt
- **WHEN** a candidate passes validation, episode assignment, governor admission, and intent creation
- **THEN** the system SHALL persist an accepted receipt with the episode ID and intent ID

#### Scenario: Duplicate candidate has a receipt
- **WHEN** a candidate is rejected because its episode is already consumed
- **THEN** the system SHALL persist a rejected receipt with `reason=duplicate_episode`
- **AND** the receipt SHALL reference the existing episode ID

#### Scenario: Validation or admission rejection has a receipt
- **WHEN** a candidate is invalid, expired, blocked, over capacity, or otherwise rejected before intent creation
- **THEN** the system SHALL persist a rejected receipt with the exact reason
- **AND** it SHALL NOT fabricate an intent ID

#### Scenario: Historical absence remains unknown
- **WHEN** an old candidate has no persisted handling receipt
- **THEN** replay/reporting SHALL classify its handling evidence as unknown
- **AND** it SHALL NOT infer that the message was consumed or lost

#### Scenario: Receipt replay is idempotent
- **WHEN** the V2 event ledger is replayed or the process restarts
- **THEN** receipt history SHALL remain ordered and unchanged
- **AND** replay SHALL NOT create a second intent or a second handling decision
```

## openspec/changes/tactical-v2-shadow-admission-parity/specs/tactical-intent-lifecycle/spec.md

- Source: openspec/changes/tactical-v2-shadow-admission-parity/specs/tactical-intent-lifecycle/spec.md
- Lines: 1-37
- SHA256: 32302b4c88f4731b4949b0e1290c6e614516133f935d203f39ace44b4d3a19d4

```md
## MODIFIED Requirements

### Requirement: Tactical episodes SHALL deduplicate one structural market opportunity
The system SHALL assign a durable `episode_id` by symbol, direction, and active 15m structure epoch. Exact plan prices SHALL be represented by a separate `plan_hash` and MUST NOT define episode identity. An attempted, missed, invalidated, capacity-skipped, or closed episode MUST NOT become eligible for another live attempt until reset evidence creates a new episode. A terminal episode MAY renew when structure data is available, the candidate side is not blocked, and either the closed 15m bar is newer than the episode's recorded bar or the structure token has changed. A neutral bias SHALL be accepted for this fresh-evidence renewal; neutral bias without fresh evidence SHALL remain ineligible.

#### Scenario: Repeated plans remain one episode
- **WHEN** repeated Tactical rows have the same symbol, direction, and active 15m structure but slightly different entry, SL, or TP values
- **THEN** they SHALL share one episode id
- **AND** at most one live attempt SHALL occur

#### Scenario: Fresh neutral evidence creates a new episode
- **WHEN** a prior episode is terminal
- **AND** a later candidate has available 15m structure, the candidate side is not blocked, and the closed-bar timestamp or structure token is newer
- **AND** the 15m bias is neutral
- **THEN** the system SHALL persist reset evidence
- **AND** it SHALL create a new episode id for the candidate

#### Scenario: Neutral without fresh evidence remains a duplicate
- **WHEN** a terminal episode receives a neutral candidate with the same closed-bar timestamp and structure token
- **THEN** the system SHALL reject the candidate as `duplicate_episode`
- **AND** it SHALL NOT create a new intent

#### Scenario: Blocked neutral evidence cannot renew
- **WHEN** a terminal episode receives a neutral candidate while the candidate side is blocked
- **THEN** the system SHALL reject the candidate as `opposing_block`
- **AND** it SHALL NOT create a new episode

#### Scenario: Structure reset creates a new episode
- **WHEN** an opposing 15m block occurs, direction returns to neutral before reforming, or a new confirmed pivot/structure break appears after the prior episode terminates
- **THEN** the system SHALL create a new episode id for a later compatible signal
- **AND** the reset evidence SHALL be persisted

#### Scenario: Historical episode terminates after a newer epoch exists
- **WHEN** an in-flight intent belongs to an older episode and a reset has already made a newer episode current for the same symbol and direction
- **AND** the older intent later reaches TP, SL, max hold, or another terminal outcome
- **THEN** the older episode SHALL be consumed exactly once by its own episode id
- **AND** the newer current epoch SHALL remain unchanged across event replay and process restart
```

## openspec/changes/tactical-v2-shadow-admission-parity/specs/tactical-shadow-admission-parity/spec.md

- Source: openspec/changes/tactical-v2-shadow-admission-parity/specs/tactical-shadow-admission-parity/spec.md
- Lines: 1-23
- SHA256: fba68778ac47740bff3d13384d92d7d03f5c69042f239c2d98494bcbe8c686b4

```md
## ADDED Requirements

### Requirement: Tactical admission parity SHALL use normalized episodes
The system SHALL compare Legacy Shadow Tactical and Tactical V2 admission at the unique candidate/episode level. Repeated Shadow rows sharing the same candidate identity and active structure SHALL be reported as duplicate observations, not as separate required V2 positions. Every normalized candidate SHALL have an explicit V2 outcome of accepted, intentional duplicate, other rejection, or unknown historical handling evidence.

#### Scenario: PUMP neutral candidates renew after fresh evidence
- **WHEN** the replay contains the terminal PUMP episode followed by unblocked neutral candidates on two newer closed 15m bars
- **THEN** the normalized parity result SHALL contain two eligible PUMP episodes
- **AND** repeated rows on each bar SHALL be marked `duplicate_episode`

#### Scenario: Raw Shadow rows are not counted as independent V2 positions
- **WHEN** multiple Legacy Shadow rows map to one candidate identity and structure epoch
- **THEN** parity SHALL count one normalized opportunity
- **AND** it SHALL retain every source Shadow ID as supporting evidence

#### Scenario: Replay result is deterministic
- **WHEN** the same candidate sequence and initial episode state are replayed 100 times
- **THEN** accepted episode IDs and rejection reasons SHALL be identical in every run

#### Scenario: Executable entry remains a separate gate
- **WHEN** admission parity accepts a normalized candidate
- **THEN** V2 SHALL still evaluate executable bid/ask price, entry drift, TTL, capacity, and protection state before live exposure
- **AND** admission parity SHALL NOT claim that the candidate was exchange-filled
```

