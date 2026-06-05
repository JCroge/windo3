---
comet_change: short-main-path-risk-guard-parity
role: technical-design
canonical_spec: openspec
---

# Short Main Path Risk Guard Parity Design

## Problem

NEAR 2026-06-05 exposed a route-consistency bug in Judge short entry handling. Main-path `ma_aligned_short` candidates could publish `open_short` even when LLM returned hold / "do not short", daily bias was bullish, entry was low in the 24h range, prior 12h move was already deep, and RSI divergence/support risk was present. Deferred short entry later rejected the same class of signal with `daily_bearish_required`, proving the guard existed but was not consistently applied.

The hard constraint is that `RSI <= 30` remains the existing hard no-short/pending-pullback threshold. This design does not move that threshold.

## Recommended Approach

Use a single short-side structural risk gate before any executable `open_short` is published. Main and deferred routes must share the same semantics:

```text
open_short candidate
  ├─ existing RSI<=30 hard no-short gate remains unchanged
  └─ short structural gate
       ├─ daily_bias must be bearish or route to eligible probe_short
       ├─ 24h range position must not be too low
       ├─ 12h pre-move must not be too deep
       ├─ RSI must satisfy short_live_min_rsi soft/structural gate
       ├─ score must satisfy short_live_min_score
       └─ HTF votes must satisfy short_live_min_htf_votes
```

LLM hold / "禁止做空" / bullish-divergence text is not a standalone veto. It is attribution and a tightening signal only when independent market-structure risk is present.

## Decisions

### D1: Unify main and deferred short gates

Main path must not duplicate part of `_apply_regime_policy()` while deferred uses the full version. The implementation should introduce or normalize one Judge helper that returns a structured short gate result and is called by both routes.

### D2: Keep RSI hard and structural semantics separate

`RSI <= 30` remains a hard no-short threshold. `short_live_min_rsi` remains a structural short gate, default 40. RSI 31.5/34 can therefore fail as reversal-risk context without changing the hard threshold.

### D3: Do not let LLM text alone veto

LLM parsing is imperfect and wording varies. The gate should record `llm_short_reversal_risk=true` for terms such as `禁止做空`, `超卖`, `看涨背离`, `支撑`, and `追空风险`, but the final rejection should come from structural reasons such as `daily_bearish_required`, `range_position_too_low`, `pre_move_too_deep`, `rsi_too_low_for_short`, `short_score_too_low`, or `htf_votes_insufficient`.

### D4: Version attribution

Accepted and rejected short candidates should include:

- `short_gate_version=short_main_path_parity_v1`
- `short_gate_decision=pass|reject|probe`
- `short_gate_reason=<machine-readable reason>`
- `llm_short_reversal_risk=true|false`

This lets Reviewer/backtest metrics avoid mixing pre-change and post-change distributions.

## Expected Chain Effects

1. Short trade count decreases for daily-bullish or low-range/deep-move setups.
2. Some continuation breakdown shorts may be missed; eligible exceptions should go through `probe_short`, not main slot.
3. Rejected-plan counts increase while executor-side accidental blocks decrease.
4. Main and deferred route behavior becomes easier to explain and test.
5. Historical `ma_aligned_short/main_direct` metrics need versioned slicing.
6. LLM JSON parse failures no longer turn structural-risk shorts into executable candidates.

## Testing Strategy

- Add NEAR 09:01 fixture: LLM parse-failure/default hold + bullish daily + low range + deep pre-move must reject structurally.
- Add NEAR 09:23 fixture: parsed hold/`禁止做空` must set LLM risk attribution and reject structurally.
- Add route-parity test for main vs deferred short evaluation.
- Add preservation test for `RSI <= 30` hard no-short behavior.
- Add event replay or fixture scan for recent risk-text open_short decisions.
- Run full pytest baseline after targeted tests.

## Scope Boundaries

Do not change executor drift gates, OKX order semantics, protective SL lifecycle, long entry guard behavior, or the hard `RSI <= 30` threshold.
