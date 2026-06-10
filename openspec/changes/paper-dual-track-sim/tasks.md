## 1. Book Abstraction (realistic-only, behavior-preserving)

- [x] 1.1 Introduce a per-book state container (positions + equity + locked margin) and route all access through it; default/single book = realistic.
- [x] 1.2 Parameterize `_open_paper_at_price`, `_close_paper`, `_check_sl_tp`, `_add_paper`, `_reduce_paper`, `_unrealized_pnl` by `book`; keep `_pending_limits` realistic-only.
- [x] 1.3 Tag every position record, `paper_trades.jsonl` close record, and `paper_execution_result` event with `book` (`'realistic'` default).
- [x] 1.4 Update existing paper tests to assert the realistic book's outcomes are unchanged (only the `book='realistic'` tag added).

## 2. State Persistence + Backward Compatibility

- [x] 2.1 Separate-file layout (per Design D5): realistic keeps existing flat `paper_positions.json`/`paper_equity.json`; idealized writes new `paper_positions_idealized.json`/`paper_equity_idealized.json`, written atomically.
- [x] 2.2 Legacy-load shim: a pre-change flat `paper_positions.json` loads wholesale into the realistic book; idealized starts empty; never deserialize pending limits.
- [x] 2.3 N/A by design — separate-file layout keeps realistic `paper_positions.json` a flat map unchanged, so `telegram_notifier.py` `/status` reader needs no change (verified via round-trip test asserting flat format).
- [x] 2.4 Round-trip + legacy-load tests.

## 3. Idealized Book

- [x] 3.1 Config flag `paper_dual_track_enabled` (DEFAULTS + env override; bool, not in HARD_LIMITS); disabled path is outcome-equivalent to today.
- [x] 3.2 On every accepted `open_*` decision, when enabled and tick is fresh, open an idealized market position at `_latest_price` with `entry_method='market'`, `book='idealized'`; skip on missing/stale tick (fail-safe).
- [x] 3.3 Mirror `close`/`add`/`reduce` lifecycle actions into the idealized book; run idealized SL/TP independently in `_check_sl_tp`.
- [x] 3.4 Tests: limit decision still fills idealized at market; unfilled realistic still leaves idealized open + closes on its own SL/TP; missing/stale tick skips idealized open; per-book independent SL.

## 4. Comparison Consumer

- [x] 4.1 Implemented the paper-only comparison reader as a pure-function helper (`agents/trading/paper_dual_track_report.py`) reading both books' closed trades over a configurable window.
- [x] 4.2 Compute per-book win% / avg net PnL / total net PnL / max drawdown + `limit_discipline_value = realistic_total − idealized_total`.
- [x] 4.3 Report low-sample explicitly when either book is under the configured minimum trade count.
- [x] 4.4 Surface to logs (periodic `tick()` log) + Telegram `/paper_gap [days]`; paper/live isolation guard test asserts the live Reviewer never consumes idealized/paper data.
- [x] 4.5 N/A by design — comparison layer is a pure-function helper (not an agent, per Design D4), so no `agents/orchestrator.py` registration is required.

## 5. Verification

- [x] 5.1 Run targeted dual-track suite (book abstraction, idealized open/close, unfilled-but-idealized, comparison math, isolation).
- [x] 5.2 Run full `python3 -m pytest -q`; confirm realistic-book regression-free and capture the new baseline count.
- [x] 5.3 Compileall check.
