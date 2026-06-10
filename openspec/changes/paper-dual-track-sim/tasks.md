## 1. Book Abstraction (realistic-only, behavior-preserving)

- [ ] 1.1 Introduce a per-book state container (positions + equity + locked margin) and route all access through it; default/single book = realistic.
- [ ] 1.2 Parameterize `_open_paper_at_price`, `_close_paper`, `_check_sl_tp`, `_add_paper`, `_reduce_paper`, `_unrealized_pnl` by `book`; keep `_pending_limits` realistic-only.
- [ ] 1.3 Tag every position record, `paper_trades.jsonl` close record, and `paper_execution_result` event with `book` (`'realistic'` default).
- [ ] 1.4 Update existing paper tests to assert the realistic book's outcomes are unchanged (only the `book='realistic'` tag added).

## 2. State Persistence + Backward Compatibility

- [ ] 2.1 Move `paper_positions.json` / `paper_equity.json` to a book-keyed layout, written atomically.
- [ ] 2.2 Legacy-load shim: a pre-change flat `paper_positions.json` loads wholesale into the realistic book; idealized starts empty; never deserialize pending limits.
- [ ] 2.3 Update `telegram_notifier.py` `/status` reader to the new layout while keeping legacy files readable.
- [ ] 2.4 Round-trip + legacy-load tests.

## 3. Idealized Book

- [ ] 3.1 Config flag `paper_dual_track_enabled` (DEFAULTS + HARD_LIMITS where numeric + env override); disabled path must be outcome-equivalent to today.
- [ ] 3.2 On every `open_*` decision, when enabled and tick is fresh, open an idealized market position at `_latest_price` with `entry_method='market'`, `book='idealized'`; skip on missing tick (fail-safe).
- [ ] 3.3 Mirror `close`/`add`/`reduce` lifecycle actions into the idealized book; run idealized SL/TP independently in `_check_sl_tp`.
- [ ] 3.4 Tests: limit decision still fills idealized at market; unfilled realistic still leaves idealized open + closes on its own SL/TP; missing tick skips idealized open; divergent entry → divergent PnL.

## 4. Comparison Consumer

- [ ] 4.1 Implement the paper-only comparison consumer (dedicated `PaperDualTrackReviewer` agent or timer/command helper) reading both books' closed trades over a configurable window.
- [ ] 4.2 Compute per-book win% / avg net PnL / total net PnL / max drawdown + `limit_discipline_value = realistic_total − idealized_total`.
- [ ] 4.3 Report low-sample / wide-error-bars explicitly when either book is under the configured minimum trade count.
- [ ] 4.4 Surface to logs + Telegram-readable summary; assert no idealized data reaches any live Reviewer metric (paper/live isolation test).
- [ ] 4.5 Register the consumer in `agents/orchestrator.py` if agent-based.

## 5. Verification

- [ ] 5.1 Run targeted dual-track suite (book abstraction, idealized open/close, unfilled-but-idealized, comparison math, isolation).
- [ ] 5.2 Run full `python3 -m pytest -q`; confirm realistic-book regression-free and capture the new baseline count.
- [ ] 5.3 Compileall check.
