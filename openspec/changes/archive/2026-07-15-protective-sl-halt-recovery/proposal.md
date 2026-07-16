## Why

On 2026-07-14 the WLD Tactical live order filled, but the executor could not resolve the attached OKX stop-loss algo id. The system correctly treated a possibly unprotected live position as dangerous, but the resulting global halt stayed visible in Telegram until manual `/resume` even after the position was later closed, and `/status` did not make it clear that this was a protection halt rather than a Tactical circuit halt or a Tactical loss halt.

We need to keep the fail-closed safety posture for truly unprotected live positions while reducing avoidable sampling downtime and operator confusion.

## What Changes

- Add a bounded post-open protection verification window for OKX attached stop-loss resolution before classifying the position as terminal `protection_state=unknown`.
- During that verification window, block new risk so the system does not continue opening positions while protection is uncertain.
- When a protection-driven global halt is later proven resolved because the position is protected or no longer exists on exchange, auto-clear the matching per-symbol halt and global halt if no other blocking condition remains.
- Improve Telegram `/status` wording so global halt, per-symbol halt, and Tactical circuit state are visible as separate concepts.
- Add tests for the WLD-style sequence: attached SL unresolved, protection halt, local close, reconciliation showing no unresolved protected-risk, then automatic recovery.

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `position-sync-resilience`: extend protection-unknown handling to cover post-open `sl_algo_unresolved` and to self-heal protection-driven halts once exchange/local state proves the risk is gone.
- `tg-status-enhancement`: require `/status` to distinguish global halt, per-symbol halt, and Tactical circuit state instead of presenting a single ambiguous "熔断" signal.

## Impact

- **Code**:
  - `executor.py`: OKX attached SL resolution path, `_halt_symbol`, sync/migration protection-state recovery, and halt clearing conditions.
  - `utils/halt_state.py`: may need a narrowly scoped method or metadata convention for auto-clearing protection-resolved halt reasons without weakening manual/daily hard-stop halts.
  - `agents/trading/telegram_notifier.py`: `/status` formatting.
  - `agents/trading/portfolio_risk_guard.py` or `agents/trading/judge.py`: read-only Tactical circuit summary for status if the existing persisted state is sufficient.
- **Tests**:
  - Root executor tests for bounded attached-SL verification and protection halt self-heal.
  - Telegram status tests for global halt vs Tactical circuit wording.
  - Regression tests that manual halt and daily hard stop do not auto-clear.
- **Non-goals**:
  - No Tactical threshold tuning.
  - No weakening of fail-closed behavior while a live position might be unprotected.
  - No bypass of `/resume` for manual, daily hard-stop, or non-protection halt reasons.
  - No change to realized PnL attribution.
