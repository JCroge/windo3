from dataclasses import FrozenInstanceError

import pytest


def _candidate(**overrides):
    raw = {
        "candidate_id": "cand-1",
        "namespace": "testnet",
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "entry_ref": 1.0,
        "stop_loss": 0.95,
        "take_profit": [1.08, 1.12],
        "leverage": 5,
        "source_shadow_id": "shadow-7",
        "tactical_source": "rr_below_floor",
        "created_at": 1000.0,
    }
    raw.update(overrides)
    return raw


def test_intent_freezes_exact_shadow_plan():
    from utils.tactical_v2.models import TacticalIntent

    intent = TacticalIntent.from_candidate(_candidate(), episode_id="ep-1")

    assert intent.symbol == "WLD-USDT"
    assert intent.entry_ref == 1.0
    assert intent.stop_loss == 0.95
    assert intent.take_profit == 1.08
    assert intent.leverage == 5
    assert intent.margin_usdt == 100.0
    assert intent.max_hold_seconds == 5400
    assert intent.expires_at == 1900.0
    with pytest.raises(FrozenInstanceError):
        intent.entry_ref = 1.01


def test_plan_hash_is_numeric_representation_stable():
    from utils.tactical_v2.models import TacticalIntent

    integer_form = TacticalIntent.from_candidate(
        _candidate(entry_ref=1, stop_loss=0.95, take_profit=1.08),
        episode_id="ep-1",
    )
    float_form = TacticalIntent.from_candidate(
        _candidate(entry_ref=1.0, stop_loss=0.9500, take_profit=[1.0800]),
        episode_id="ep-1",
    )

    assert integer_form.plan_hash == float_form.plan_hash
    assert integer_form.intent_id == float_form.intent_id


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"leverage": 6}, "leverage"),
        ({"entry_ref": float("nan")}, "entry_ref"),
        ({"stop_loss": 1.01}, "stop_loss"),
        ({"take_profit": 0.99}, "take_profit"),
        ({"side": "flat"}, "side"),
    ],
)
def test_invalid_or_mutated_shadow_plan_fails_closed(overrides, match):
    from utils.tactical_v2.models import TacticalIntent

    with pytest.raises(ValueError, match=match):
        TacticalIntent.from_candidate(_candidate(**overrides), episode_id="ep-1")


def test_same_candidate_in_different_episode_has_distinct_intent():
    from utils.tactical_v2.models import TacticalIntent

    first = TacticalIntent.from_candidate(_candidate(), episode_id="ep-1")
    second = TacticalIntent.from_candidate(_candidate(), episode_id="ep-2")

    assert first.plan_hash == second.plan_hash
    assert first.intent_id != second.intent_id
