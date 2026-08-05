"""Pure Tactical V2 executable-price entry state machine."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Optional


ENTRY_MAX_WORSE_R = Decimal("0.10")
_ENTRY_ACTIVE_STATES = frozenset(
    {"pending_entry", "canceling_entry", "partial_fill", "filled_unverified"}
)


def _finite_positive(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return parsed


def _finite_nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return parsed


def _decimal(value: Any, name: str) -> Decimal:
    parsed = _finite_positive(value, name)
    return Decimal(str(parsed))


def _side(intent_or_side: Any) -> str:
    value = intent_or_side if isinstance(intent_or_side, str) else getattr(intent_or_side, "side", None)
    side = str(value or "").strip().lower()
    if side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    return side


@dataclass(frozen=True)
class ExecutableQuote:
    bid: float
    ask: float
    observed_at: float

    def __post_init__(self) -> None:
        bid = _finite_positive(self.bid, "bid")
        ask = _finite_positive(self.ask, "ask")
        observed_at = _finite_nonnegative(self.observed_at, "observed_at")
        if bid > ask:
            raise ValueError("bid must not exceed ask")
        object.__setattr__(self, "bid", bid)
        object.__setattr__(self, "ask", ask)
        object.__setattr__(self, "observed_at", observed_at)


@dataclass(frozen=True)
class EntryDecision:
    action: str
    reason: str
    executable_price: Optional[float] = None
    worse_r: Optional[float] = None
    limit_price: Optional[float] = None


@dataclass(frozen=True)
class OrderObservation:
    status: str
    filled_qty: float
    remaining_qty: float
    average_price: Optional[float] = None
    lane: str = "live"

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", str(self.status or "unknown").strip().lower())
        object.__setattr__(self, "filled_qty", _finite_nonnegative(self.filled_qty, "filled_qty"))
        object.__setattr__(
            self,
            "remaining_qty",
            _finite_nonnegative(self.remaining_qty, "remaining_qty"),
        )
        if self.average_price is not None:
            object.__setattr__(
                self,
                "average_price",
                _finite_positive(self.average_price, "average_price"),
            )
        lane = str(self.lane or "").strip().lower()
        if lane not in {"live", "shadow"}:
            raise ValueError("lane must be live or shadow")
        object.__setattr__(self, "lane", lane)


@dataclass(frozen=True)
class EntryState:
    intent: Any
    lane: str
    status: str
    requested_qty: float
    filled_qty: float
    remaining_qty: float
    entry_price: Optional[float] = None
    cancel_reason: Optional[str] = None
    terminal_reason: Optional[str] = None
    slot_held: bool = True


@dataclass(frozen=True)
class EntryTransition:
    next_state: EntryState
    command: Optional[str] = None
    terminal_reason: Optional[str] = None
    reason: Optional[str] = None

    @property
    def state(self) -> str:
        return self.next_state.status


def entry_executable_price(side: str, quote: ExecutableQuote) -> float:
    return quote.ask if _side(side) == "long" else quote.bid


def exit_executable_price(side: str, quote: ExecutableQuote) -> float:
    return quote.bid if _side(side) == "long" else quote.ask


def classify_entry(
    intent: Any,
    quote: ExecutableQuote,
    *,
    now: Optional[float] = None,
    max_tick_age_seconds: float = 5.0,
) -> EntryDecision:
    side = _side(intent)
    entry = _decimal(getattr(intent, "entry_ref", None), "entry_ref")
    stop = _decimal(getattr(intent, "stop_loss", None), "stop_loss")
    take_profit = _decimal(getattr(intent, "take_profit", None), "take_profit")
    risk = abs(entry - stop)
    if risk <= 0:
        return EntryDecision(action="reject", reason="invalid_r")
    if side == "long" and not stop < entry < take_profit:
        return EntryDecision(action="reject", reason="invalid_plan_side")
    if side == "short" and not take_profit < entry < stop:
        return EntryDecision(action="reject", reason="invalid_plan_side")

    evaluated_at = quote.observed_at if now is None else float(now)
    max_age = float(max_tick_age_seconds)
    if not math.isfinite(evaluated_at) or not math.isfinite(max_age) or max_age < 0:
        return EntryDecision(action="reject", reason="invalid_quote_clock")
    age = evaluated_at - quote.observed_at
    if age < 0:
        return EntryDecision(action="reject", reason="future_quote")
    if age > max_age:
        return EntryDecision(action="reject", reason="stale_quote")

    terminal_reason = _prefill_terminal_reason(intent, quote)
    if terminal_reason is not None:
        return EntryDecision(action="terminal", reason=terminal_reason)
    expires_at = float(getattr(intent, "expires_at", math.nan))
    if not math.isfinite(expires_at):
        return EntryDecision(action="reject", reason="invalid_expiry")
    if evaluated_at >= expires_at:
        return EntryDecision(action="terminal", reason="expired")

    executable = Decimal(str(entry_executable_price(side, quote)))
    worse = max(Decimal("0"), executable - entry) if side == "long" else max(
        Decimal("0"), entry - executable
    )
    worse_r = worse / risk
    if worse <= ENTRY_MAX_WORSE_R * risk:
        return EntryDecision(
            action="immediate",
            reason="within_entry_drift",
            executable_price=float(executable),
            worse_r=float(worse_r),
        )
    return EntryDecision(
        action="pending_limit",
        reason="wait_at_frozen_entry",
        executable_price=float(executable),
        worse_r=float(worse_r),
        limit_price=float(entry),
    )


def pending_entry(
    intent: Any,
    *,
    lane: str = "live",
    requested_qty: float = 1.0,
) -> EntryState:
    normalized_lane = str(lane or "").strip().lower()
    if normalized_lane not in {"live", "shadow"}:
        raise ValueError("lane must be live or shadow")
    quantity = _finite_positive(requested_qty, "requested_qty")
    return EntryState(
        intent=intent,
        lane=normalized_lane,
        status="pending_entry",
        requested_qty=quantity,
        filled_qty=0.0,
        remaining_qty=quantity,
        slot_held=True,
    )


def reduce_quote(
    state: EntryState,
    quote: ExecutableQuote,
    *,
    now: Optional[float] = None,
    structure_invalidated: bool = False,
    max_tick_age_seconds: float = 5.0,
) -> EntryTransition:
    if state.status != "pending_entry":
        return EntryTransition(next_state=state, reason="state_not_pending")

    evaluated_at = quote.observed_at if now is None else float(now)
    age = evaluated_at - quote.observed_at
    if not math.isfinite(evaluated_at) or age < 0 or age > max_tick_age_seconds:
        return EntryTransition(next_state=state, reason="stale_or_invalid_quote")

    cancel_reason = _prefill_terminal_reason(state.intent, quote)
    if cancel_reason is None and structure_invalidated:
        cancel_reason = "structure_invalidated"
    expires_at = float(getattr(state.intent, "expires_at", math.nan))
    if cancel_reason is None and (not math.isfinite(expires_at) or evaluated_at >= expires_at):
        cancel_reason = "expired" if math.isfinite(expires_at) else "invalid_expiry"

    if cancel_reason is None:
        return EntryTransition(next_state=state)
    return request_entry_cancel(state, cancel_reason)


def request_entry_cancel(state: EntryState, reason: str) -> EntryTransition:
    if state.status != "pending_entry":
        return EntryTransition(next_state=state, reason="state_not_pending")
    cancel_reason = str(reason or "entry_canceled").strip() or "entry_canceled"
    canceling = replace(
        state,
        status="canceling_entry",
        cancel_reason=cancel_reason,
        terminal_reason=None,
        slot_held=True,
    )
    return EntryTransition(
        next_state=canceling,
        command="cancel_entry",
        terminal_reason=cancel_reason,
        reason=cancel_reason,
    )


def reduce_order_observation(
    state: EntryState,
    observation: OrderObservation,
) -> EntryTransition:
    if observation.lane != state.lane:
        return _integrity_transition(state, "lane_mismatch")
    if state.status not in _ENTRY_ACTIVE_STATES:
        return EntryTransition(next_state=state, reason="state_not_entry_active")
    if observation.filled_qty > state.requested_qty:
        return _integrity_transition(state, "overfill")

    status = observation.status
    if status not in {"open", "pending", "partially_filled", "filled", "canceled", "rejected"}:
        return _integrity_transition(state, "unknown_order_state")

    if observation.filled_qty > 0:
        entry_price = observation.average_price or state.entry_price or float(
            getattr(state.intent, "entry_ref")
        )
        if observation.remaining_qty > 0:
            partial = replace(
                state,
                status="partial_fill",
                filled_qty=observation.filled_qty,
                remaining_qty=observation.remaining_qty,
                entry_price=entry_price,
                slot_held=True,
            )
            return EntryTransition(
                next_state=partial,
                command="cancel_remainder",
                reason="partial_fill",
            )
        filled = replace(
            state,
            status="filled_unverified",
            filled_qty=observation.filled_qty,
            remaining_qty=0.0,
            entry_price=entry_price,
            cancel_reason=None,
            terminal_reason=None,
            slot_held=True,
        )
        return EntryTransition(
            next_state=filled,
            command="verify_protection",
            reason="fill_confirmed",
        )

    if status == "canceled" and observation.remaining_qty == 0:
        terminal_reason = state.cancel_reason or "entry_canceled"
        terminal = replace(
            state,
            status="entry_terminal",
            filled_qty=0.0,
            remaining_qty=0.0,
            terminal_reason=terminal_reason,
            slot_held=False,
        )
        return EntryTransition(
            next_state=terminal,
            terminal_reason=terminal_reason,
            reason=terminal_reason,
        )
    if status == "rejected":
        terminal = replace(
            state,
            status="entry_terminal",
            remaining_qty=0.0,
            terminal_reason="entry_rejected",
            slot_held=False,
        )
        return EntryTransition(
            next_state=terminal,
            terminal_reason="entry_rejected",
            reason="entry_rejected",
        )
    if status == "filled":
        return _integrity_transition(state, "filled_without_quantity")
    return EntryTransition(next_state=state, reason="order_still_open")


def _prefill_terminal_reason(intent: Any, quote: ExecutableQuote) -> Optional[str]:
    side = _side(intent)
    exit_price = Decimal(str(exit_executable_price(side, quote)))
    stop = _decimal(getattr(intent, "stop_loss", None), "stop_loss")
    take_profit = _decimal(getattr(intent, "take_profit", None), "take_profit")
    if side == "long":
        if exit_price >= take_profit:
            return "missed_after_target"
        if exit_price <= stop:
            return "stopped_before_entry"
    else:
        if exit_price <= take_profit:
            return "missed_after_target"
        if exit_price >= stop:
            return "stopped_before_entry"
    return None


def _integrity_transition(state: EntryState, reason: str) -> EntryTransition:
    halted = replace(
        state,
        status="integrity_required",
        terminal_reason=None,
        slot_held=True,
    )
    return EntryTransition(
        next_state=halted,
        command="halt_integrity",
        reason=reason,
    )
