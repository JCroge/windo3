## 1. Protection Verification

- [x] 1.1 Add tests for OKX attached SL first-lookup miss followed by successful bounded verification.
- [x] 1.2 Add tests for attached SL verification exhaustion triggering existing fail-closed protection halt.
- [x] 1.3 Implement bounded attached SL verification without allowing additional live opens while protection is pending.

## 2. Protection Halt Recovery

- [x] 2.1 Add tests for `okx_sl_algo_unresolved:<symbol>` auto-clear after exchange confirms the symbol is closed.
- [x] 2.2 Add tests proving manual halt, daily hard stop, and non-allowlisted halt reasons do not auto-clear.
- [x] 2.3 Implement allowlisted protection-halt recovery with audit logging and per-symbol halt cleanup.

## 3. Telegram Status

- [x] 3.1 Add `/status` tests for global protection halt with Tactical circuit not paused.
- [x] 3.2 Add `/status` tests for Tactical circuit paused with global halt clear.
- [x] 3.3 Update `/status` formatting to show global halt, per-symbol halt, and Tactical circuit as distinct lines.

## 4. Verification

- [x] 4.1 Run focused executor and Telegram status tests.
- [x] 4.2 Run the project test suite or the agreed equivalent verification subset.
- [x] 4.3 Sync to cloud only after local verification and restart/validate cloud status output.
