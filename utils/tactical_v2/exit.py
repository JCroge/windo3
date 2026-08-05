"""Pure Tactical V2 full-position exit decisions shared by both lanes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from .entry import ExecutableQuote, exit_executable_price


@dataclass(frozen=True)
class ExitDecision:
    action: str
    reason: str
    close_fraction: float = 0.0
    executable_price: Optional[float] = None
    pnl_pct: Optional[float] = None


def max_hold_due(intent: Any, *, opened_at: float, now: float) -> bool:
    try:
        opened = float(opened_at)
        evaluated_at = float(now)
        max_hold = float(getattr(intent, "max_hold_seconds"))
    except (TypeError, ValueError, AttributeError):
        return False
    return (
        math.isfinite(opened)
        and math.isfinite(evaluated_at)
        and math.isfinite(max_hold)
        and max_hold > 0
        and evaluated_at >= opened
        and evaluated_at - opened >= max_hold
    )


def classify_exit(
    intent: Any,
    *,
    entry_price: float,
    opened_at: float,
    quote: ExecutableQuote,
    now: Optional[float] = None,
    max_tick_age_seconds: float = 5.0,
) -> ExitDecision:
    """Classify full TP1, full SL, or max hold at an executable exit price."""
    evaluated_at = quote.observed_at if now is None else float(now)
    age = evaluated_at - quote.observed_at
    if (
        not math.isfinite(evaluated_at)
        or not math.isfinite(max_tick_age_seconds)
        or max_tick_age_seconds < 0
        or age < 0
        or age > max_tick_age_seconds
    ):
        return ExitDecision("reject", "stale_or_invalid_quote")

    side = str(getattr(intent, "side", "")).strip().lower()
    if side not in {"long", "short"}:
        return ExitDecision("reject", "invalid_side")
    try:
        entry = float(entry_price)
        stop = float(getattr(intent, "stop_loss"))
        take_profit = float(getattr(intent, "take_profit"))
    except (TypeError, ValueError, AttributeError):
        return ExitDecision("reject", "invalid_plan")
    if not all(math.isfinite(value) and value > 0 for value in (entry, stop, take_profit)):
        return ExitDecision("reject", "invalid_plan")

    executable = exit_executable_price(side, quote)
    if side == "long":
        reason = (
            "tactical_sl" if executable <= stop
            else "tactical_tp1" if executable >= take_profit
            else None
        )
        pnl_pct = (executable - entry) / entry * 100.0
    else:
        reason = (
            "tactical_sl" if executable >= stop
            else "tactical_tp1" if executable <= take_profit
            else None
        )
        pnl_pct = (entry - executable) / entry * 100.0

    if reason is None and max_hold_due(intent, opened_at=opened_at, now=evaluated_at):
        reason = "tactical_max_hold"
    if reason is None:
        return ExitDecision("hold", "no_exit")
    return ExitDecision(
        "close",
        reason,
        close_fraction=1.0,
        executable_price=executable,
        pnl_pct=pnl_pct,
    )
