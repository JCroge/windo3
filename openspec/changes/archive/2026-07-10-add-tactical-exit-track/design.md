## Context

The live system already has a Main Trend-oriented open and exit path:

- `Judge` builds a plan, calculates TP/SL, effective R:R, EV, slot metadata, and publishes `trade_decision`.
- `executor.py` stores `slot_type`, `attribution`, local TP levels, and exchange-side protective SL in the position record.
- Partial TP is local: TP1 reduces 50%, TP2 reduces 25%, and the remainder uses trailing protection.
- Low-R:R handling is currently an extra slot and early trailing overlay, not a separate trading thesis.
- Reviewer, counterfactual ledger, and PnL resolution events already carry enough attribution hooks to extend with a track/profile label.

The problem is that weak or mixed-environment opportunities are currently evaluated and managed through a trend-runner TP/SL model. That can overstate R:R, delay profit realization, and blur metrics when a position should be judged as a short-horizon tactical trade rather than a clean trend continuation.

## Goals / Non-Goals

**Goals:**

- Add an explicit `tactical` track alongside the existing Main Trend path.
- Preserve Main Trend behavior for strong aligned setups.
- Downgrade eligible weak/mixed candidates into Tactical instead of forcing them through Main TP/SL assumptions.
- Calculate Tactical R:R and EV from its own exit profile, cost gate, and structure stop.
- Manage Tactical exits through local TP, exchange protective SL, thesis-health checks, and max-hold rules.
- Keep Tactical risk, concurrency, circuit breakers, and metrics independent from Main.
- Make replay and counterfactual reporting compare Main vs Tactical honestly.

**Non-Goals:**

- No exchange TP owner migration in v1; OKX remains exchange protective SL only, with TP owned locally.
- No same-symbol stacking between Main and Tactical.
- No change to Main Trend TP/SL semantics except explicit metadata and isolation.
- No manual discretionary mode; the design targets full automation behind feature flags and circuit breakers.
- No assumption that Tactical is profitable until segmented live/replay evidence supports it.

## Decisions

### Decision 1: Represent Tactical as a first-class track, not a low-R:R variant

Add `track` and `exit_profile` fields to plans, positions, execution payloads, and review records:

- Main Trend: `track=main`, `exit_profile=trend_runner`
- Tactical: `track=tactical`, `exit_profile=tactical_v1`

`slot_type` remains useful for concurrency buckets, but it is not enough to describe exit semantics. Tactical may use `slot_type=tactical` or a compatible new slot value, while `track` is the canonical performance and exit-contract field.

Alternatives considered:

- Reuse `low_rr_extra`: rejected because low-R:R is a sizing/protection overlay, while Tactical changes candidate classification, R:R math, TP/SL lifecycle, hold time, and risk governor.
- Add a completely separate executor: rejected for v1 because the current position lifecycle already supports local partial TP plus exchange SL, and duplicating order ownership increases failure surface.

### Decision 2: Classify before final R:R and EV gates

Judge should classify candidate intent before applying final R:R/EV acceptance:

1. Strong trend candidates stay Main only when HTF and daily bias align with the trade, 15m is not opposing, and a Main Trend quality gate passes.
2. Directionally valid candidates that fail Main trend protection can be considered for Tactical.
3. A narrow subset of hold/reject candidates can be reconsidered for Tactical only when rejection reason is compatible with Tactical, such as R:R below Main floor or confidence 40-60 with strong structure.
4. Hard vetoes remain hard vetoes: regime flat with no thesis, 15m opposing block, explicit reversal thesis, short structural blocks, liquidity/execution failure, and extreme/news pause.

The quality gate exists because directional alignment alone can still be a poor Main Runner setup. A WLD-like short can have HTF, daily, and 15m bearish alignment while also showing mixed regime, weak volume/OI confirmation, LLM reversal risk, trend-exhaustion warnings, and weak provenance. That setup must not remain Main solely because it is directionally aligned; it should either be downgraded to Tactical with Tactical R:R/EV or rejected live and recorded for shadow/replay.

Alternatives considered:

- Classify after Main rejection only: too late, because Main R:R/EV may already have used the wrong target assumptions.
- Classify only by confidence: too weak, because Tactical eligibility is structure and environment dependent.

### Decision 3: Tactical has its own plan math

Tactical MUST compute:

- structure-bounded stop,
- stop cap relative to Main stop (`<= 0.6R_main`) plus ATR/percentage cap,
- very-near-stop flag (`<= 0.4R_main`) for possible full Main-sized margin,
- default size at 70% of Main, max 100% only when stop is very near and conditions are clean,
- max leverage 5x,
- TP1 around 0.6R or nearest structure,
- net EV greater than 0,
- TP1 net return covering fee plus slippage by at least 4x.

The current `effective_risk_reward_ratio` for Main can remain ladder-weighted. Tactical needs a separate `tactical_effective_rr` and must not pass a Main gate by borrowing trend-runner TP2/TP3 assumptions.

### Decision 4: Tactical exit lifecycle is state-machine driven

Tactical positions use local TP and exchange protective SL like Main, but the local lifecycle has different states:

```
opened
  -> healthy       -> staged_tp / protect / continue
  -> weakened      -> tighten / partial_or_full_exit
  -> invalidated   -> immediate_exit
  -> timed_out     -> close
  -> closed
```

Health is evaluated every minute, on relevant events, and with heavier weight on each 15m close. A healthy thesis can continue toward staged exits up to 90 minutes. A weakened thesis with no progress exits after 30-45 minutes. An invalidated thesis exits immediately, using market execution only when spread and depth are acceptable.

### Decision 5: Tactical risk governor is independent

Tactical gets an independent governor:

- daily realized+resolved loss hard stop: -10U,
- dynamic concurrency: calm market max 2, high volatility max 1, extreme/news pause,
- 3 consecutive Tactical losses pauses Tactical for 1 hour,
- 20-trade quality failure pauses Tactical,
- execution/protection failure pauses Tactical immediately,
- no add-to-position policy.

Main should not inherit Tactical pauses except when the underlying failure is system-wide protection or execution integrity.

### Decision 6: Observability is part of the contract

Every accepted, rejected, resolved, replayed, and reviewed Tactical decision must carry enough metadata to answer:

- Was this Main or Tactical?
- Which exit profile priced the trade?
- Which source created the Tactical candidate?
- Which vetoes passed or blocked it?
- Which close reason ended the position?
- Did Tactical add incremental value during Main idle periods?

## Risks / Trade-offs

- R:R overstatement -> Mitigation: separate Tactical R:R/EV fields and tests that fail when Tactical uses Main ladder assumptions.
- Higher trade frequency increases cost drag -> Mitigation: net EV gate and TP1 cost coverage >= 4x fee+slippage.
- Full-auto Tactical can compound losses in chop -> Mitigation: daily -10U hard stop, dynamic concurrency, loss streak pause, and quality breaker.
- Local TP ownership can miss exits during process failure -> Mitigation: keep exchange protective SL, persist Tactical exit state, and pause Tactical on protection failure.
- Sparse samples can mislead optimization -> Mitigation: independent metrics and counterfactual honesty gates before expanding size/frequency.
- Tactical may cannibalize Main winners -> Mitigation: strong trend protection keeps aligned HTF/daily setups on Main and forbids same-symbol stacking.
- Weak aligned trades remain misclassified as Main -> Mitigation: Main Trend quality gate requires regime/confirmation/provenance health, not just directional alignment.

## Migration Plan

1. Add feature flags with Tactical disabled by default until replay and dry-run evidence is reviewed.
2. Add `track`/`exit_profile` metadata propagation without behavior change.
3. Add classifier, Main Trend quality gate, and Tactical plan math behind flags.
4. Add Tactical executor lifecycle behind flags.
5. Add risk governor and circuit breakers.
6. Add segmented reviewer/counterfactual reports.
7. Enable paper or shadow mode first, then small live rollout only if Tactical meets the success standard.

Rollback is flag-based: disabling Tactical classification returns all eligible candidates to current Main/hold behavior while preserving historical metadata.

## Open Questions

- Exact first-live flags and sizing defaults should be confirmed before implementation.
- Whether Tactical rejected/hold promotion starts in shadow-only mode or immediately live-open mode remains a build-phase decision.
- The final set of "strong structure" features for hold/reject promotion needs to be mapped to the current `tech_analysis` fields during implementation.
