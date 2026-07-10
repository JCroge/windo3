## Implementation Tasks

- [ ] Add Tactical configuration flags, defaults, and metadata fields (`track`, `exit_profile`, Tactical source, Tactical R:R/EV, cost gate, risk state).
- [ ] Implement Judge Tactical classification before final R:R/EV gates, including Main strong-trend quality gate, downgrade handling, shadow-only handling, and hard veto handling.
- [ ] Implement Tactical plan math: structure stop, stop caps, sizing/leverage limits, TP profile, net EV, and cost coverage gate.
- [ ] Add Tactical slot/concurrency handling and risk governor: daily -10U hard stop, volatility-based concurrency, loss streak pause, quality breaker, and execution/protection failure pause.
- [ ] Extend Executor local lifecycle for Tactical positions: thesis-health checks, Tactical partial/protect exits, invalidation exit, max hold, and no-add enforcement while keeping exchange SL ownership.
- [ ] Propagate Tactical metadata through trade decisions, positions, execution results, PnL resolution events, Reviewer trade history, and Telegram/status surfaces where relevant.
- [ ] Extend counterfactual and replay tooling to resolve Tactical candidates with Tactical exit assumptions and separate Main/Tactical reporting.
- [ ] Add focused tests for classifier routing, hard vetoes, Tactical R:R isolation, cost gate, exit-state transitions, risk governor breakers, event attribution, and replay segmentation.
- [ ] Add WLD-like classifier and replay fixtures covering aligned-but-weak Main rejection, Tactical downgrade, Tactical shadow-only, Tactical TP1, and Tactical capped-stop outcomes.
- [ ] Run replay/shadow validation before enabling live Tactical opens; document rollout flags and rollback path.
