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
