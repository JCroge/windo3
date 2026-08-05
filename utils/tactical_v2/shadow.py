"""Executable-price Shadow adapter over the shared Tactical entry reducer."""

from __future__ import annotations

from typing import Any, Iterable

from .entry import (
    EntryState,
    EntryTransition,
    ExecutableQuote,
    OrderObservation,
    classify_entry,
    pending_entry,
    request_entry_cancel,
    reduce_order_observation,
    reduce_quote,
)


class ShadowAdapter:
    """Produces simulated order observations and performs no exchange I/O."""

    def __init__(self):
        self.observations = []

    def start(
        self,
        intent: Any,
        quote: ExecutableQuote,
        *,
        requested_qty: float = 1.0,
        now: float | None = None,
    ) -> EntryTransition:
        state = pending_entry(intent, lane="shadow", requested_qty=requested_qty)
        decision = classify_entry(intent, quote, now=now)
        self._record("entry_classified", state, action=decision.action, reason=decision.reason)

        if decision.action == "immediate":
            return self._filled(
                state,
                price=decision.executable_price,
                quantity=requested_qty,
            )
        if decision.action == "pending_limit":
            return EntryTransition(next_state=state, reason=decision.reason)
        if decision.action == "terminal":
            canceling = EntryState(
                **{
                    **state.__dict__,
                    "status": "canceling_entry",
                    "cancel_reason": decision.reason,
                }
            )
            return self._confirm_zero_fill_cancel(canceling)

        halted = EntryState(
            **{
                **state.__dict__,
                "status": "integrity_required",
                "slot_held": True,
            }
        )
        return EntryTransition(
            next_state=halted,
            command="halt_integrity",
            reason=decision.reason,
        )

    def on_quote(
        self,
        state: EntryState,
        quote: ExecutableQuote,
        *,
        now: float | None = None,
        structure_invalidated: bool = False,
    ) -> EntryTransition:
        reduced = reduce_quote(
            state,
            quote,
            now=now,
            structure_invalidated=structure_invalidated,
        )
        if reduced.state == "canceling_entry":
            self._record(
                "cancel_requested",
                reduced.next_state,
                reason=reduced.terminal_reason,
            )
            return self._confirm_zero_fill_cancel(reduced.next_state)
        if reduced.state != "pending_entry":
            return reduced

        side = str(getattr(state.intent, "side")).lower()
        entry = float(getattr(state.intent, "entry_ref"))
        touched = quote.ask <= entry if side == "long" else quote.bid >= entry
        if not touched:
            self._record("limit_open", state)
            return reduced
        return self._filled(state, price=entry, quantity=state.requested_qty)

    def cancel_pending(self, state: EntryState, reason: str) -> EntryTransition:
        requested = request_entry_cancel(state, reason)
        if requested.state != "canceling_entry":
            return requested
        self._record(
            "cancel_requested",
            requested.next_state,
            reason=requested.terminal_reason,
        )
        return self._confirm_zero_fill_cancel(requested.next_state)

    def _filled(
        self,
        state: EntryState,
        *,
        price: float | None,
        quantity: float,
    ) -> EntryTransition:
        observation = OrderObservation(
            status="filled",
            filled_qty=quantity,
            remaining_qty=0,
            average_price=price,
            lane="shadow",
        )
        self._record("order_observation", state, observation=observation.__dict__)
        return reduce_order_observation(state, observation)

    def _confirm_zero_fill_cancel(self, state: EntryState) -> EntryTransition:
        observation = OrderObservation(
            status="canceled",
            filled_qty=0,
            remaining_qty=0,
            lane="shadow",
        )
        self._record("order_observation", state, observation=observation.__dict__)
        return reduce_order_observation(state, observation)

    def _record(self, event_type: str, state: EntryState, **data: Any) -> None:
        self.observations.append(
            {
                "lane": "shadow",
                "event_type": event_type,
                "intent_id": getattr(state.intent, "intent_id", None),
                "state": state.status,
                **data,
            }
        )


def summarize_shadow(states: Iterable[EntryState]) -> dict:
    filled = 0
    nonfilled_terminal = 0
    for state in states:
        if state.filled_qty > 0:
            filled += 1
        elif state.status == "entry_terminal":
            nonfilled_terminal += 1
    return {
        "filled_trade_count": filled,
        "nonfilled_terminal_count": nonfilled_terminal,
    }
