## Why

The paper executor's limit-fill discipline drops roughly half of intended entries (`limit_unfilled`), but the system cannot currently measure whether missing those entries helps or hurts. The closed-trade ledger structurally cannot hold unfilled events (an unfilled limit never opens, so it never closes and never lands in `paper_trades.jsonl`), and the historical price data needed to reconstruct the counterfactual does not exist locally. A dual-track shadow account — running the real limit-based book alongside an idealized market-immediate baseline — is the only instrument that can generate this missing data going forward.

### Evidence from historical exploration (2026-06-04 → 06-10, limit-fill era)

- **Unfilled is high-frequency:** 7 `limit_filled` vs 8 `limit_unfilled` vs 4 `market` → limit unfilled rate ≈ 53%. About half of all limit entries never become positions.
- **Filled limits are the only profitable bucket:** `limit_filled` n=7, sum +9.04, win 43%; `market` entries n=5, sum −33.30, avg −6.66, win 40%. This weakly suggests the limit-wait discipline is *filtering out bad entries*, not merely costing missed upside.
- **The counterfactual is unrecoverable retroactively:** all 8 unfilled decisions were on low-cap symbols (XLM/NEAR/WLD/H/HYPE) with no local kline history and no per-symbol persisted tick stream. "Would the missed trades have won or lost?" cannot be answered from existing data — only generated prospectively.
- **Sample is tiny (n≈19 over one week):** any gap estimate has wide error bars, so the value is in *accumulating* the comparison continuously, not in a one-shot report.

These findings drive two settled decisions: the idealized baseline is **market-immediate** (answers "is our limit discipline net-positive vs naive market entry?", the open question the data cannot close), and a **minimal comparison consumer is in-scope** (the current paper stream is consumed by no Reviewer — emitting more unread data would repeat that trap).

## What Changes

- Add an **idealized book** to `PaperExecutor`: for every `open_*` decision, in addition to the existing realistic (limit/market) path, immediately open a market-fill position at the latest tick price. Both books run independent position/SL-TP/close/equity lifecycles.
- Introduce a **`book ∈ {realistic, idealized}` dimension** across paper positions, equity, and trade records. The realistic book preserves today's exact behavior (limit queue, `limit_filled`/`limit_unfilled`, market fallback); the idealized book always fills.
- Tag every `paper_execution_result` and trade-ledger record with `book`, so the two streams are separable downstream.
- Add a **minimal comparison consumer** (`PaperDualTrackReviewer` agent or reporting function) that joins the two books and computes the idealized-vs-realistic gap: win%, EV, max drawdown, and signed `realistic − idealized` net effect (the "limit discipline value"), surfaced to logs and Telegram `/status` / a dedicated command.
- Add config to enable/disable the idealized book and tune the comparison window, so the dual-track overhead can be turned off in production if undesired.
- Preserve backward compatibility: existing `paper_trades.jsonl` / `paper_positions.json` records without a `book` field are treated as `realistic`.

## Capabilities

### New Capabilities
- `paper-dual-track`: Dual-book (realistic + idealized) paper simulation and the comparison/reporting layer that quantifies the limit-discipline gap.

### Modified Capabilities
- `paper-executor`: Open/close/SL-TP/persistence and the `paper_execution_result` contract gain a `book` dimension; the realistic book's externally observable behavior is unchanged.

## Impact

- **Code:** `agents/trading/paper_executor.py` (book dimension across `_open_paper`, `_open_paper_at_price`, `_close_paper`, `_check_sl_tp`, `_add_paper`, `_reduce_paper`, `_persist_state`, `_load_state`, `_unrealized_pnl`, `tick`); new comparison consumer under `agents/trading/`; `agents/orchestrator.py` (register new consumer if agent-based); `utils/config_loader.py` (new config keys + env overrides); `agents/trading/telegram_notifier.py` (surface gap; it currently reads `paper_positions.json` directly).
- **State files:** `paper_positions.json` / `paper_equity.json` / `paper_trades.jsonl` schema extended with `book`; may add an idealized-book state file or namespace within existing files (design decision).
- **Tests:** new dual-track open/close/comparison tests; existing paper tests updated for the `book` field while asserting realistic-book behavior is unchanged.
- **Out of scope:** cross-source data provenance (`source`/`freshness_sec`/`confidence`) is a separate planned change; live executor and live Reviewer are untouched; paper/live isolation is preserved (idealized data must never reach live Reviewer metrics).
