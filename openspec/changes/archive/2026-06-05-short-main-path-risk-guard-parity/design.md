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
