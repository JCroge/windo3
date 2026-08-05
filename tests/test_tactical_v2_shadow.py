import json
from pathlib import Path
from types import SimpleNamespace


FIXTURE = Path(__file__).with_name("fixtures") / "tactical_v2_wld_window.json"


def _intent(side="long", entry=1.0, stop=None, tp=None):
    return SimpleNamespace(
        intent_id=f"shadow-{side}",
        side=side,
        entry_ref=entry,
        stop_loss=stop if stop is not None else (0.95 if side == "long" else 1.05),
        take_profit=tp if tp is not None else (1.08 if side == "long" else 0.92),
        created_at=1000.0,
        expires_at=1900.0,
    )


def _quote(bid, ask, at=1000.0):
    from utils.tactical_v2.entry import ExecutableQuote

    return ExecutableQuote(bid=bid, ask=ask, observed_at=at)


def test_shadow_long_limit_requires_ask_touch_not_bid_or_last_proxy():
    from utils.tactical_v2.shadow import ShadowAdapter

    adapter = ShadowAdapter()
    pending = adapter.start(_intent(), _quote(1.019, 1.02)).next_state
    no_fill = adapter.on_quote(pending, _quote(0.999, 1.001, at=1010))
    fill = adapter.on_quote(no_fill.next_state, _quote(0.998, 1.0, at=1011))

    assert pending.status == "pending_entry"
    assert no_fill.state == "pending_entry"
    assert fill.state == "filled_unverified"
    assert fill.next_state.entry_price == 1.0
    assert fill.next_state.lane == "shadow"


def test_shadow_short_limit_requires_bid_touch():
    from utils.tactical_v2.shadow import ShadowAdapter

    adapter = ShadowAdapter()
    pending = adapter.start(_intent(side="short"), _quote(0.98, 0.981)).next_state
    no_fill = adapter.on_quote(pending, _quote(0.999, 1.001, at=1010))
    fill = adapter.on_quote(no_fill.next_state, _quote(1.0, 1.002, at=1011))

    assert no_fill.state == "pending_entry"
    assert fill.state == "filled_unverified"
    assert fill.next_state.entry_price == 1.0


def test_shadow_prefill_terminal_uses_same_cancel_confirmation_reducer():
    from utils.tactical_v2.shadow import ShadowAdapter

    adapter = ShadowAdapter()
    pending = adapter.start(_intent(), _quote(1.019, 1.02)).next_state
    terminal = adapter.on_quote(pending, _quote(1.08, 1.081, at=1010))

    assert terminal.state == "entry_terminal"
    assert terminal.terminal_reason == "missed_after_target"
    assert terminal.next_state.filled_qty == 0
    assert terminal.next_state.lane == "shadow"


def test_nonfilled_shadow_terminal_is_excluded_from_filled_statistics():
    from utils.tactical_v2.shadow import ShadowAdapter, summarize_shadow

    adapter = ShadowAdapter()
    filled = adapter.start(_intent(), _quote(1.0, 1.001)).next_state
    pending = adapter.start(_intent(), _quote(1.019, 1.02)).next_state
    terminal = adapter.on_quote(pending, _quote(1.08, 1.081, at=1010)).next_state

    summary = summarize_shadow([filled, terminal])

    assert summary["filled_trade_count"] == 1
    assert summary["nonfilled_terminal_count"] == 1


def test_wld_fixture_replays_no_chase_then_target_miss():
    from utils.tactical_v2.entry import ExecutableQuote, classify_entry
    from utils.tactical_v2.shadow import ShadowAdapter

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw = fixture["intent"]
    intent = SimpleNamespace(**raw)
    ticks = [ExecutableQuote(**row) for row in fixture["ticks"]]

    decision = classify_entry(intent, ticks[0])
    adapter = ShadowAdapter()
    pending = adapter.start(intent, ticks[0]).next_state
    terminal = adapter.on_quote(pending, ticks[1])

    assert decision.action == "pending_limit"
    assert terminal.state == fixture["expected_terminal"]
    assert terminal.terminal_reason == fixture["expected_reason"]


def test_entry_fixture_covers_all_first_cohort_crash_boundaries():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert {case["kind"] for case in fixture["boundary_cases"]} == {
        "immediate",
        "original_entry_wait",
        "tp_before_entry",
        "sl_before_entry",
        "expiry",
        "partial_fill",
    }
