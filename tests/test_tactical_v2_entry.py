from types import SimpleNamespace

import pytest


def _intent(side="long", entry=1.0, stop=None, tp=None, created_at=1000.0):
    stop = stop if stop is not None else (0.95 if side == "long" else 1.05)
    tp = tp if tp is not None else (1.08 if side == "long" else 0.92)
    return SimpleNamespace(
        intent_id=f"i-{side}",
        side=side,
        entry_ref=entry,
        stop_loss=stop,
        take_profit=tp,
        created_at=created_at,
        expires_at=created_at + 900,
    )


def _quote(bid, ask, at=1000.0):
    from utils.tactical_v2.entry import ExecutableQuote

    return ExecutableQuote(bid=bid, ask=ask, observed_at=at)


@pytest.mark.parametrize(
    "side,bid,ask,expected",
    [
        ("long", 1.004, 1.005, "immediate"),
        ("long", 1.005, 1.0050001, "pending_limit"),
        ("short", 0.995, 0.996, "immediate"),
        ("short", 0.9949999, 0.996, "pending_limit"),
    ],
)
def test_point_one_r_boundary_uses_executable_entry_side(side, bid, ask, expected):
    from utils.tactical_v2.entry import classify_entry

    result = classify_entry(_intent(side=side), _quote(bid, ask))

    assert result.action == expected


@pytest.mark.parametrize(
    "side,bid,ask,expected_price",
    [
        ("long", 0.979, 0.98, 0.98),
        ("short", 1.02, 1.021, 1.02),
    ],
)
def test_favorable_price_is_immediate(side, bid, ask, expected_price):
    from utils.tactical_v2.entry import classify_entry

    result = classify_entry(_intent(side=side), _quote(bid, ask))

    assert result.action == "immediate"
    assert result.executable_price == expected_price
    assert result.worse_r == 0.0


def test_stale_quote_fails_closed():
    from utils.tactical_v2.entry import classify_entry

    result = classify_entry(
        _intent(),
        _quote(1.0, 1.001, at=1000),
        now=1006,
        max_tick_age_seconds=5,
    )

    assert result.action == "reject"
    assert result.reason == "stale_quote"


@pytest.mark.parametrize(
    "bid,ask",
    [(float("nan"), 1.0), (1.0, float("inf")), (0.0, 1.0), (1.1, 1.0)],
)
def test_invalid_executable_quote_is_rejected(bid, ask):
    from utils.tactical_v2.entry import ExecutableQuote

    with pytest.raises(ValueError):
        ExecutableQuote(bid=bid, ask=ask, observed_at=1000)


@pytest.mark.parametrize(
    "side,bid,ask,reason",
    [
        ("long", 1.08, 1.081, "missed_after_target"),
        ("long", 0.949, 0.951, "stopped_before_entry"),
        ("short", 0.919, 0.92, "missed_after_target"),
        ("short", 1.049, 1.05, "stopped_before_entry"),
    ],
)
def test_tp_or_sl_before_fill_starts_cancel_and_holds_slot(side, bid, ask, reason):
    from utils.tactical_v2.entry import pending_entry, reduce_quote

    transition = reduce_quote(pending_entry(_intent(side=side)), _quote(bid, ask, at=1010))

    assert transition.state == "canceling_entry"
    assert transition.command == "cancel_entry"
    assert transition.terminal_reason == reason
    assert transition.next_state.slot_held is True


def test_structure_invalidation_and_absolute_expiry_start_cancel():
    from utils.tactical_v2.entry import pending_entry, reduce_quote

    state = pending_entry(_intent())
    invalidated = reduce_quote(state, _quote(1.01, 1.011, at=1100), structure_invalidated=True)
    expired = reduce_quote(state, _quote(1.01, 1.011, at=1900), now=1900)

    assert invalidated.terminal_reason == "structure_invalidated"
    assert expired.terminal_reason == "expired"
    assert expired.state == "canceling_entry"


def test_restart_does_not_extend_frozen_entry_ttl():
    from utils.tactical_v2.entry import pending_entry, reduce_quote

    intent = _intent(created_at=1000)
    restarted_state = pending_entry(intent)

    before = reduce_quote(restarted_state, _quote(1.01, 1.011, at=1899), now=1899)
    at_expiry = reduce_quote(restarted_state, _quote(1.01, 1.011, at=1900), now=1900)

    assert before.state == "pending_entry"
    assert at_expiry.terminal_reason == "expired"


def test_zero_fill_cancel_confirmation_is_only_then_terminal():
    from utils.tactical_v2.entry import (
        OrderObservation,
        pending_entry,
        reduce_order_observation,
        reduce_quote,
    )

    canceling = reduce_quote(
        pending_entry(_intent()),
        _quote(1.08, 1.081, at=1010),
    ).next_state
    terminal = reduce_order_observation(
        canceling,
        OrderObservation(status="canceled", filled_qty=0, remaining_qty=0),
    )

    assert terminal.state == "entry_terminal"
    assert terminal.terminal_reason == "missed_after_target"
    assert terminal.next_state.slot_held is False


def test_cancel_fill_race_prefers_fill_and_partial_cancels_remainder():
    from utils.tactical_v2.entry import (
        OrderObservation,
        pending_entry,
        reduce_order_observation,
        reduce_quote,
    )

    canceling = reduce_quote(
        pending_entry(_intent(), requested_qty=10),
        _quote(1.08, 1.081, at=1010),
    ).next_state
    partial = reduce_order_observation(
        canceling,
        OrderObservation(status="canceled", filled_qty=4, remaining_qty=6),
    )

    assert partial.state == "partial_fill"
    assert partial.command == "cancel_remainder"
    assert partial.next_state.filled_qty == 4
    assert partial.next_state.slot_held is True

    protected_next = reduce_order_observation(
        partial.next_state,
        OrderObservation(status="canceled", filled_qty=4, remaining_qty=0),
    )
    assert protected_next.state == "filled_unverified"
    assert protected_next.command == "verify_protection"


def test_full_fill_during_cancel_goes_to_protection_not_terminal():
    from utils.tactical_v2.entry import (
        OrderObservation,
        pending_entry,
        reduce_order_observation,
        reduce_quote,
    )

    canceling = reduce_quote(
        pending_entry(_intent(), requested_qty=10),
        _quote(1.08, 1.081, at=1010),
    ).next_state
    filled = reduce_order_observation(
        canceling,
        OrderObservation(status="filled", filled_qty=10, remaining_qty=0),
    )

    assert filled.state == "filled_unverified"
    assert filled.command == "verify_protection"
    assert filled.terminal_reason is None


def test_unknown_order_state_requires_integrity_halt():
    from utils.tactical_v2.entry import (
        OrderObservation,
        pending_entry,
        reduce_order_observation,
    )

    result = reduce_order_observation(
        pending_entry(_intent()),
        OrderObservation(status="unknown", filled_qty=0, remaining_qty=1),
    )

    assert result.state == "integrity_required"
    assert result.command == "halt_integrity"
    assert result.next_state.slot_held is True


def test_exit_executable_side_is_bid_for_long_and_ask_for_short():
    from utils.tactical_v2.entry import exit_executable_price

    quote = _quote(0.999, 1.001)

    assert exit_executable_price("long", quote) == 0.999
    assert exit_executable_price("short", quote) == 1.001
