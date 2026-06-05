# Comet Design Handoff

- Change: short-main-path-risk-guard-parity
- Phase: design
- Mode: compact
- Context hash: d0ebf63892203f263466127008d28a52925875852fa23b50a74ea201bd13519d

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/short-main-path-risk-guard-parity/proposal.md

- Source: openspec/changes/short-main-path-risk-guard-parity/proposal.md
- Lines: 1-30
- SHA256: bd3456ebcdc54b01a5a5ebdd55da7e5f7e56554bbd3fadab0ba2dc4da01ac315

```md
## Why

Recent NEAR short trades exposed a main-path risk drift: `ma_aligned_short` with LLM hold / "do not short" reasoning, bullish daily bias, low-range entry, deep prior move, and bullish divergence could still publish `open_short` on the main path. Deferred short entry already rejects similar cases through regime policy, so the same candidate can be blocked or allowed depending on route.

This change makes short entry risk decisions route-consistent without changing the existing `RSI <= 30` hard no-short threshold.

## What Changes

- Route main-path short candidates through the same side-aware short gate semantics used by deferred/regime policy.
- Ensure daily non-bearish bias, low 24h range position, deep 12h pre-move, insufficient score, and low RSI soft guard are evaluated before `main_direct` short publication.
- Preserve the existing `RSI <= 30` hard pullback/no-short behavior unchanged.
- Treat LLM hold / "do not short" / bullish-divergence text as a tightening factor only when independent market-structure risk is also present; it is not a standalone veto.
- Add attribution for short gate version, rejection reason, and LLM-risk text signals so Reviewer/backtests can separate pre-change and post-change behavior.
- Add regression tests and event replay coverage for NEAR 2026-06-05 09:01 / 09:23 style candidates.

## Capabilities

### New Capabilities
- `short-main-path-risk-guard`: Route-consistent short entry risk gating for Judge main and deferred open paths.

### Modified Capabilities

None.

## Impact

- Affected code: `agents/trading/judge.py`, Judge attribution helpers, rejected-plan lifecycle/event replay tests, and related Judge unit tests.
- Affected behavior: fewer `ma_aligned_short` main-path entries when daily bias is non-bearish or entry is already in low/deep-move reversal-risk territory.
- Affected metrics: historical `ma_aligned_short/main_direct` slices need versioned attribution to avoid mixing pre-change and post-change distributions.
- No external dependencies, exchange API changes, database schema changes, or changes to the `RSI <= 30` hard no-short threshold.
```

## openspec/changes/short-main-path-risk-guard-parity/design.md

- Source: openspec/changes/short-main-path-risk-guard-parity/design.md
- Lines: 1-79
- SHA256: cbc25b09316f6caa506a450b47f43c49338f2c92572075705c6d6eb1a0be3e68

```md
## Context

NEAR 2026-06-05 demonstrated that the Judge main open path can publish `open_short` when several reversal-risk signals are present: `ma_aligned_short`, `llm_relation=hold`, bullish daily bias, low 24h range position, deep 12h pre-move, support proximity, and bullish RSI divergence. The same class of short candidate can later be blocked by deferred/regime policy with `daily_bearish_required`, proving a route-consistency gap rather than a missing concept.

The user constraint is explicit: the existing `RSI <= 30` hard no-short threshold must remain unchanged. The solution therefore addresses route parity and combined short-side risk gates, not the hard RSI threshold.

## Goals / Non-Goals

**Goals:**

- Make main-path and deferred-path short decisions use the same side-aware short gate semantics.
- Reject or downgrade reversal-risk shorts before `main_direct` publication when market-structure gates fail.
- Keep `RSI <= 30` as the existing hard no-short/pending-pullback threshold.
- Use LLM hold / "do not short" text as a tightening factor only when independent market-structure risk is also present.
- Preserve observability through versioned attribution and rejection reasons.

**Non-Goals:**

- Do not change long entry position guard behavior.
- Do not make LLM text a standalone veto over all rule-signal shorts.
- Do not alter executor drift gates, OKX order semantics, or protective order lifecycle.
- Do not change the `RSI <= 30` hard no-short threshold or its existing pending-pullback behavior.

## Decisions

### D1: Reuse a single short gate before main-path publication

Main-path short candidates will run through the same side-aware short gate semantics currently represented by `_apply_regime_policy`: daily bearish requirement, 24h range position, 12h pre-move, minimum RSI soft guard, minimum score, and HTF vote checks. The gate must execute before ranking/main publication can emit `open_short`.

Alternative A, adding only a daily-bearish check to the main path, would have blocked NEAR but leaves the range/pre-move/score drift intact. Alternative B, changing only the `RSI <= 30` threshold, violates the user constraint and collapses separate hard-threshold and soft-gate semantics. Alternative C, using LLM text as an unconditional veto, is too brittle because parse failures and wording variance are common.

### D2: Stop silent daily-bias pass-through in short position policy

`_check_entry_position_policy()` currently returns allowed when `daily_bias != bearish` for short candidates, leaving rejection to `_apply_regime_policy()`. That is safe only if every caller also runs `_apply_regime_policy()`. The design removes this split-brain by either delegating short rejection to one gate or ensuring the position-policy short branch returns a machine-readable non-allowed result for non-bearish daily bias.

The implementation should avoid duplicating short guard logic at call sites. If a helper remains, it should report metrics and call a single classifier rather than re-encoding conditions.

### D3: Preserve two RSI layers

`RSI <= 30` remains the hard no-short threshold that creates/uses pullback behavior. `short_live_min_rsi` (default 40) remains a short-side soft/hard gate inside the short risk classifier. This allows RSI 31.5 / 34 to be handled as reversal-risk context when combined with daily/range/pre-move/score failures without changing the legacy hard boundary.

### D4: LLM risk text tightens only combined-risk cases

The gate will extract a boolean signal such as `llm_short_reversal_risk=true` when LLM action is hold and reasoning/key factors/risk warnings contain terms like "禁止做空", "超卖", "看涨背离", "支撑", or "追空风险". This signal can strengthen attribution and can break ties when independent market-structure risk is present, but it must not independently veto a rule-signal short that otherwise passes daily/range/pre-move/score gates.

This handles both NEAR forms: 09:23 has explicit parsed "禁止做空" text; 09:01 still fails structural gates even though Judge reasoning is empty after JSON parse failure.

### D5: Version attribution for downstream slicing

Accepted and rejected short candidates will include a short gate version and reason fields, for example:

- `short_gate_version: short_main_path_parity_v1`
- `short_gate_decision: pass | reject | probe`
- `short_gate_reason: daily_bearish_required | range_position_too_low | pre_move_too_deep | rsi_too_low_for_short | short_score_too_low | htf_votes_insufficient`
- `llm_short_reversal_risk: true | false`

This prevents Reviewer/backtest slices from mixing old and new `ma_aligned_short/main_direct` distributions.

## Risks / Trade-offs

- [Reduced short frequency] → Expected. Track rejected-plan counts and compare post-change `short_gate_reason` distribution before expanding live size.
- [Missed breakdown shorts during daily bullish pullbacks] → Route eligible exceptional cases through existing `probe_short` rather than main slot when probe criteria pass.
- [Duplicate gate behavior if helpers diverge again] → Keep one classifier/helper as the single source of truth and add route-parity tests for main and deferred paths.
- [LLM text matching false positives] → Do not let text alone veto; require independent structural risk or use text only for attribution.
- [Historical metrics discontinuity] → Emit `short_gate_version` and update tests/replay reports to slice by version.

## Migration Plan

1. Add regression fixtures for NEAR 09:01 and 09:23 decision inputs before changing logic.
2. Introduce/normalize the single short risk gate and route main/deferred callers through it.
3. Add attribution fields and rejected-plan reason consistency.
4. Run targeted Judge tests and event replay for recent risk-text `open_short` samples.
5. Run the project pytest suite baseline.

Rollback is simple: revert the Judge gate change and tests. No persisted state migration is required.

## Open Questions

None blocking. The user has confirmed the hard `RSI <= 30` threshold remains unchanged.
```

## openspec/changes/short-main-path-risk-guard-parity/tasks.md

- Source: openspec/changes/short-main-path-risk-guard-parity/tasks.md
- Lines: 1-24
- SHA256: 813edf05f1234764171476529b60ca5823397737835f2439698b052d0cc03fd1

```md
## 1. Regression Fixtures

- [ ] 1.1 Add NEAR 2026-06-05 09:01 main-path fixture with `daily_bias=bullish`, low range position, deep pre-move, LLM parse-failure/default hold, and expected structural short rejection.
- [ ] 1.2 Add NEAR 2026-06-05 09:23 main-path fixture with parsed LLM `hold` / `禁止做空` reasoning and expected structural short rejection plus LLM risk attribution.
- [ ] 1.3 Add route-parity fixture proving the same short candidate rejects consistently through main and deferred entry routes.

## 2. Short Gate Implementation

- [ ] 2.1 Introduce or normalize a single Judge short risk gate helper covering daily bearish requirement, 24h range position, 12h pre-move, `short_live_min_rsi`, minimum score, and HTF votes.
- [ ] 2.2 Route main-path `open_short` candidates through the short gate before ranking/main publication.
- [ ] 2.3 Route deferred short candidates through the same gate semantics without duplicating call-site if/else branches.
- [ ] 2.4 Preserve existing `RSI <= 30` hard no-short/pending-pullback behavior unchanged.

## 3. Attribution and Observability

- [ ] 3.1 Add short gate metadata (`short_gate_version`, `short_gate_decision`, `short_gate_reason`) to accepted and rejected short attribution.
- [ ] 3.2 Add LLM short reversal-risk detection for parsed hold reasoning/key factors/risk warnings and expose `llm_short_reversal_risk`.
- [ ] 3.3 Ensure LLM parse failure cannot hide structural gate failures.

## 4. Verification

- [ ] 4.1 Run targeted Judge unit tests for route parity, NEAR regressions, RSI hard-threshold preservation, and LLM reversal-risk attribution.
- [ ] 4.2 Run event replay or equivalent fixture scan for recent risk-text `open_short` decisions to confirm executed NEAR/HYPE-style cases no longer enter `main_direct` when structural gates fail.
- [ ] 4.3 Run the full pytest baseline or document any environment-limited exclusions.
```

## openspec/changes/short-main-path-risk-guard-parity/specs/short-main-path-risk-guard/spec.md

- Source: openspec/changes/short-main-path-risk-guard-parity/specs/short-main-path-risk-guard/spec.md
- Lines: 1-56
- SHA256: 9b0c4a9631efda21916d09fb42bd41a5d20ce3e34bf5f0881c70c38e85d38a30

```md
## ADDED Requirements

### Requirement: Route-Consistent Short Risk Gate

The Judge SHALL evaluate main-path and deferred-path `open_short` candidates with the same side-aware short risk gate before publishing an executable short decision. A candidate that fails the gate SHALL NOT be published as `main_direct` `open_short`.

#### Scenario: Main path rejects bullish daily short
- **WHEN** a main-path `ma_aligned_short` candidate has `symbol_daily_bias=bullish` and is not eligible for `probe_short`
- **THEN** Judge SHALL publish `hold` instead of `open_short`
- **AND** the rejection reason SHALL include `daily_bearish_required`

#### Scenario: Deferred path matches main path rejection
- **WHEN** the same `open_short` candidate is evaluated through the deferred entry route
- **THEN** Judge SHALL produce the same short gate rejection class as the main path
- **AND** no route SHALL bypass the daily/range/pre-move/score short gate semantics

### Requirement: Hard RSI Threshold Preservation

The Judge SHALL preserve the existing hard no-short behavior for `open_short` when `RSI <= 30`. This threshold SHALL NOT be changed by the short main path parity gate.

#### Scenario: RSI hard threshold remains unchanged
- **WHEN** an `open_short` candidate has `RSI <= 30`
- **THEN** Judge SHALL apply the existing hard no-short/pending-pullback behavior
- **AND** this behavior SHALL remain distinct from the `short_live_min_rsi` gate

#### Scenario: RSI above hard threshold can still fail structural gate
- **WHEN** an `open_short` candidate has `RSI=31.5` or `RSI=34` and fails daily/range/pre-move/score short gate conditions
- **THEN** Judge SHALL reject the candidate through the structural short gate
- **AND** the rejection SHALL NOT be reported as a change to the `RSI <= 30` hard threshold

### Requirement: LLM Reversal Risk Tightening

The Judge SHALL detect LLM short reversal-risk text from parsed reasoning, key factors, and risk warnings when LLM action is `hold`. This signal SHALL be used for attribution and MAY tighten decisions only when independent market-structure short risk is present. It SHALL NOT be a standalone veto for all rule-signal shorts.

#### Scenario: Parsed do-not-short text is attributed
- **WHEN** LLM action is `hold` and reasoning contains text such as `禁止做空`, `超卖`, `看涨背离`, `支撑`, or `追空风险`
- **THEN** Judge SHALL set `llm_short_reversal_risk=true` in attribution
- **AND** the final decision SHALL still be based on the structural short gate outcome

#### Scenario: LLM parse failure does not allow structural-risk short
- **WHEN** LLM parsing yields default `hold` with empty reasoning
- **AND** the candidate fails daily/range/pre-move/score short gate conditions
- **THEN** Judge SHALL reject the candidate through structural short gate reasons
- **AND** the missing LLM reasoning SHALL NOT allow a `main_direct` short

### Requirement: Short Gate Attribution Versioning

The Judge SHALL include versioned short gate attribution on accepted and rejected short candidates so downstream metrics can separate pre-change and post-change behavior.

#### Scenario: Rejected short includes gate metadata
- **WHEN** Judge rejects an `open_short` candidate through the parity gate
- **THEN** the decision/rejected-plan attribution SHALL include `short_gate_version`, `short_gate_decision`, and `short_gate_reason`

#### Scenario: Accepted short includes pass metadata
- **WHEN** Judge accepts an `open_short` candidate after the parity gate
- **THEN** the executable decision attribution SHALL include `short_gate_version` and `short_gate_decision=pass`
```

