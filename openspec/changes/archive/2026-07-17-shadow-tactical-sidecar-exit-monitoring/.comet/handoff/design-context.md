# Comet Design Handoff

- Change: shadow-tactical-sidecar-exit-monitoring
- Phase: design
- Mode: compact
- Context hash: a84a9cb8f8bfc51938a4746a105809229f62faaaaf8ffa46825e7b244b110715

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/shadow-tactical-sidecar-exit-monitoring/proposal.md

- Source: openspec/changes/shadow-tactical-sidecar-exit-monitoring/proposal.md
- Lines: 1-28
- SHA256: 3ee1f9ab9e8f75aa3f465054287bb28245372d675af4bb7b83c22029b5e8b2d8

```md
## Why

Shadow Tactical sidecar currently opens live positions, but once a position is open there is no periodic sidecar-owned exit evaluation. That means Tactical TP, invalidation, weakened-thesis, and max-hold exits can be missed until manual intervention, and the ONDO case showed the sidecar can also carry the wrong execution symbol through its open/close path.

## What Changes

- Add a sidecar poller that scans open sidecar-owned Tactical positions on a fixed cadence and applies the existing Tactical exit semantics.
- Canonicalize sidecar symbol handling so execution uses the exchange swap instrument while ownership/audit state keeps the internal symbol.
- Keep all behavior scoped to shadow Tactical sidecar state; main strategy, main live process, and symbol classification are unchanged.
- Preserve the current stop command as a shutdown path that only closes proven sidecar-owned positions.
- Emit audit and lifecycle records for monitoring, partial exits, closes, and skip/fail-safe cases.

## Capabilities

### New Capabilities
- `shadow-tactical-sidecar-exit-monitoring`: sidecar-owned Tactical position monitoring, symbol canonicalization, and exit execution.

### Modified Capabilities
- None.

## Impact

- `scripts/shadow_tactical_live_sidecar.py`
- `executor.py`
- `utils/shadow_tactical_live.py`
- sidecar state files under `data/shadow_tactical_live_*`
- tests covering shadow open, monitor, and stop paths

```

## openspec/changes/shadow-tactical-sidecar-exit-monitoring/design.md

- Source: openspec/changes/shadow-tactical-sidecar-exit-monitoring/design.md
- Lines: 1-128
- SHA256: 81628ecbd04d21d7a7f83ecaa80fba9aed52434db6ca3d26d0e7aa64d0fc7827

[TRUNCATED]

```md
## Context

Shadow Tactical sidecar currently does one thing well: it tails `rejected_plan_created` events, maps them into a Tactical shadow plan, and opens positions through `ContractExecutor.open_sidecar_plan()`. After that, the sidecar stops caring. There is no polling pass over open sidecar-owned positions, so TP1/TP2, invalidation, weakened-thesis, and max-hold exits can sit open until a human intervenes.

The Tactical exit rules already exist in `executor.py` and are consumed by the main executor path. The missing piece is a sidecar consumer of that same lifecycle. The ONDO incident also showed that the sidecar symbol path is not canonicalized cleanly, which makes ownership proof and cleanup brittle.

Target shape:

```text
shadow event stream
  -> open sidecar-owned tactical position
  -> persist ownership record
  -> poll open sidecar-owned positions
     -> evaluate tactical exit intent
     -> reduce_position / close_position
  -> stop drains proven open rows only
```

Constraints:
- keep this sidecar-only
- do not change main strategy classification or main execution semantics
- reuse the existing exit lock / residual protection lifecycle
- preserve old owner rows where possible

## Goals / Non-Goals

**Goals:**
- Make the sidecar actively manage open Tactical shadow positions after entry.
- Reuse the existing Tactical exit semantics rather than inventing a second rule set.
- Canonicalize ownership vs execution symbols so the sidecar can open, monitor, and stop the same position consistently.
- Keep sidecar shutdown and cleanup proven-only, so unrelated positions are untouched.

**Non-Goals:**
- No change to main-track vs tactical-track classification.
- No new risk governor or portfolio policy.
- No rewrite of the main executor's trade lifecycle.
- No new service process or message bus topic.

## Decisions

### 1. Extract a shared Tactical exit evaluator

The sidecar should not duplicate the Tactical exit conditions inline. Instead, `executor.py` should expose a small Tactical exit evaluator that takes the current position, price, and timestamp and returns an exit intent such as `partial_tp_1`, `partial_tp_2`, `tactical_invalidated`, `tactical_weakened_no_progress`, or `tactical_max_hold`.

Why this over duplicating logic in `scripts/shadow_tactical_live_sidecar.py`:
- one source of truth for Tactical behavior
- easier unit testing
- main loop and sidecar remain aligned when Tactical thresholds change

The current `check_stop_loss_take_profit()` entry point can stay as a wrapper so existing callers keep working.

### 2. Split identity from execution symbol

Sidecar ownership should be keyed by the internal symbol, while execution should use the exchange swap symbol. The existing executor `symbol` field should continue to mean the exchange execution symbol so the rest of `ContractExecutor` stays stable. For OKX this means resolving the plan symbol through `utils.symbol.to_okx_inst()` before order submission.

Why this over keeping the raw plan symbol:
- raw internal symbols can resolve to the wrong instrument family
- the ONDO case shows that symbol ambiguity leaks into both open and stop paths
- ownership proof becomes explicit when internal and exchange symbols are both persisted

Proposed sidecar state split:
- `internal_symbol`: sidecar identity and audit symbol
- `exchange_symbol`: order/market-data symbol used by `ContractExecutor`

Legacy owner rows can be read by deriving both fields from the stored symbol when one is missing.

### 3. Add the monitor pass to the existing sidecar loop

The sidecar already has a long-running `cmd_run()` loop. The monitor should be an extra pass in that loop, not a second daemon or thread.

Why this over a separate worker:
- no extra lifecycle or shutdown handling
- one poll cadence already exists
- fewer moving pieces to keep in sync with the owner registry

The loop should:
1. ingest new shadow events
2. scan open sidecar-owned rows
3. prove each live position
4. evaluate exit intent
```

Full source: openspec/changes/shadow-tactical-sidecar-exit-monitoring/design.md

## openspec/changes/shadow-tactical-sidecar-exit-monitoring/tasks.md

- Source: openspec/changes/shadow-tactical-sidecar-exit-monitoring/tasks.md
- Lines: 1-24
- SHA256: b10de6b7399afd3dc77f6c8cbe3739c6d71517b6e68775def557892a0b10aa66

```md
## 1. Symbol and ownership plumbing

- [ ] 1.1 Add internal and exchange symbol fields to sidecar owner records with backward-compatible loading for legacy rows.
- [ ] 1.2 Resolve sidecar opens to the exchange swap symbol before order submission while preserving the internal symbol for ownership and audit.
- [ ] 1.3 Update same-symbol exposure checks to compare canonical internal symbols so open and stop guards stay stable.

## 2. Shared Tactical exit evaluator

- [ ] 2.1 Extract a reusable Tactical exit decision helper from `executor.py` and keep `check_stop_loss_take_profit()` as a wrapper.
- [ ] 2.2 Route Tactical TP1, TP2, invalidation, weakened-no-progress, and max-hold through the shared helper.
- [ ] 2.3 Add unit tests for Tactical exit intent, partial-reduce sizing, and max-hold close reasons.

## 3. Sidecar monitor loop

- [ ] 3.1 Add a per-poll scan of open sidecar-owned positions in `scripts/shadow_tactical_live_sidecar.py`.
- [ ] 3.2 Prove ownership, fetch price, evaluate the Tactical exit intent, and call `reduce_position()` or `close_position()` accordingly.
- [ ] 3.3 Record audit events and update owner status for partial exits, closes, skips, and failures.

## 4. Stop, migration, and regression coverage

- [ ] 4.1 Refactor `cmd_stop` to reuse the same proven-owner drain path as the monitor.
- [ ] 4.2 Add regression tests for legacy owner rows and ONDO-style internal symbol opens.
- [ ] 4.3 Verify the change with sidecar idle-loop and stop-path integration tests.

```

## openspec/changes/shadow-tactical-sidecar-exit-monitoring/specs/shadow-tactical-sidecar-exit-monitoring/spec.md

- Source: openspec/changes/shadow-tactical-sidecar-exit-monitoring/specs/shadow-tactical-sidecar-exit-monitoring/spec.md
- Lines: 1-69
- SHA256: 482868cbfe2cf24829f303e91ba32b71fc3a5bf7659c8b7446d8490d033a3703

```md
## ADDED Requirements

### Requirement: Sidecar SHALL persist canonical ownership identity for Tactical shadow positions
The sidecar SHALL persist both the internal shadow symbol and the exchange execution symbol for each open Tactical shadow position. The exchange execution symbol SHALL be used for market data and order calls, while the internal symbol SHALL be used for ownership lookup, audit, and cross-process matching. The sidecar SHALL fail closed if it cannot resolve the execution symbol for a new open.

#### Scenario: Internal shadow symbol resolves to exchange execution symbol
- **WHEN** the sidecar receives a Tactical shadow plan whose symbol is the internal form
- **THEN** it SHALL resolve the exchange execution symbol before submitting the order
- **AND** it SHALL persist both symbols in the owner record and position state

#### Scenario: Unresolvable symbol is rejected
- **WHEN** the sidecar cannot resolve a plan symbol to a valid exchange execution symbol
- **THEN** it SHALL reject the open
- **AND** it SHALL record an audit event instead of falling back to spot execution

### Requirement: Sidecar SHALL monitor open Tactical shadow positions while running
The sidecar SHALL periodically scan open sidecar-owned Tactical positions and evaluate exit conditions even when no new shadow events arrive. Monitoring SHALL be independent from event ingestion so that open positions continue to be managed during quiet periods.

#### Scenario: Idle event stream still triggers monitoring
- **WHEN** at least one sidecar-owned Tactical position remains open
- **AND** no new shadow events arrive during the next poll window
- **THEN** the sidecar SHALL still evaluate the open position for exit conditions

#### Scenario: Closed positions are skipped
- **WHEN** a sidecar-owned position is already marked closed in owner state
- **THEN** the next monitor cycle SHALL NOT re-evaluate it

### Requirement: Sidecar Tactical exits SHALL reuse Tactical exit semantics
For each proven open sidecar-owned Tactical position, the sidecar SHALL apply the same Tactical exit semantics as the Tactical exit profile. TP1 SHALL reduce the position by 50 percent. TP2 SHALL reduce the remaining position by 25 percent. Positions marked invalidated, weakened without progress, or timed out by max hold SHALL close the remaining position. Protective stop handling SHALL remain authoritative and the sidecar SHALL use the shared reduce/close lifecycle for all exit actions.

#### Scenario: TP1 triggers partial reduce
- **WHEN** a proven open sidecar-owned Tactical position reaches TP1
- **THEN** the sidecar SHALL trigger a 50 percent reduce action
- **AND** it SHALL preserve the remaining position with updated protection state

#### Scenario: Invalidated thesis exits fast
- **WHEN** a proven open sidecar-owned Tactical position is marked invalidated
- **THEN** the sidecar SHALL request an immediate close
- **AND** it SHALL record the invalidation reason in the audit trail

#### Scenario: Max hold closes the remainder
- **WHEN** a proven open sidecar-owned Tactical position reaches its max-hold window
- **THEN** the sidecar SHALL close the remaining position
- **AND** it SHALL record `tactical_max_hold` as the close reason

### Requirement: Sidecar exit actions SHALL be ownership-bound and isolated
The sidecar SHALL only act on positions that it can prove are sidecar-owned. If a registry row cannot be matched to a live sidecar-owned position, the sidecar SHALL skip the exit action and record the skip. Sidecar exit actions SHALL not mutate main-process positions or any other non-sidecar state.

#### Scenario: Unproven position is skipped
- **WHEN** an owner record exists but the live position cannot be proven against the current sidecar-owned position
- **THEN** the sidecar SHALL skip the exit action
- **AND** it SHALL record a skip audit event

#### Scenario: Main process state is untouched
- **WHEN** a main-process position exists for the same symbol
- **THEN** the sidecar SHALL not reduce or close it unless it is separately proven sidecar-owned

### Requirement: Sidecar stop SHALL drain proven sidecar-owned exposure only
On shutdown or explicit stop, the sidecar SHALL close only proven open sidecar-owned Tactical positions, then mark those owner rows closed. Failure to confirm a close SHALL be recorded and SHALL not affect unrelated positions.

#### Scenario: Stop closes proven exposure
- **WHEN** stop is requested and an owner row is proven open
- **THEN** the sidecar SHALL close the position
- **AND** it SHALL mark the owner row closed

#### Scenario: Stop skips unproven exposure
- **WHEN** stop is requested and the position cannot be proven
- **THEN** the sidecar SHALL leave it untouched
- **AND** it SHALL record a skip audit event
```

