from types import SimpleNamespace

import pytest


def _paths(tmp_path):
    return SimpleNamespace(
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
    )


def _resolution(resolution_id, position_id, pnl, at):
    from utils.tactical_v2.models import FinalResolution

    return FinalResolution(
        resolution_id=resolution_id,
        position_id=position_id,
        intent_id=f"intent-{position_id}",
        episode_id=f"episode-{position_id}",
        pnl_usdt=pnl,
        resolved_at=at,
        close_reason="tactical_sl" if pnl < 0 else "tactical_tp1",
    )


def _governor(now=1000, store=None):
    from utils.tactical_v2.governor import TacticalGovernor

    clock = [float(now)]
    governor = TacticalGovernor(store=store, now_fn=lambda: clock[0])
    governor.test_clock = clock
    return governor


def test_correction_applies_latest_truth_not_second_trade():
    governor = _governor(now=100000)

    first = governor.apply_final(_resolution("r1", "p1", -9.0, 90000))
    correction = governor.apply_final(_resolution("r2", "p1", -4.0, 90000))

    assert first.delta_pnl == -9.0
    assert correction.delta_pnl == 5.0
    assert governor.rolling_pnl == -4.0
    assert governor.final_episode_count == 1


def test_resolution_id_redelivery_is_deduplicated():
    governor = _governor(now=1000)
    resolution = _resolution("r1", "p1", -2.0, 1000)

    assert governor.apply_final(resolution).accepted is True
    duplicate = governor.apply_final(resolution)

    assert duplicate.accepted is False
    assert duplicate.reason == "duplicate_resolution"
    assert governor.rolling_pnl == -2.0
    assert governor.final_episode_count == 1


def test_entry_request_id_is_valid_correction_identity_fallback():
    governor = _governor(now=1000)

    result = governor.apply_final({
        "resolution_id": "r1",
        "entry_request_id": "request-1",
        "pnl_usdt": 2.0,
        "resolved_at": 1000,
        "status": "final",
    })

    assert result.accepted is True
    assert governor.rolling_pnl == 2.0
    assert governor.final_episode_count == 1


def test_rolling_loss_threshold_is_inclusive_and_evicts_by_24h():
    governor = _governor(now=1000)
    governor.apply_final(_resolution("r1", "p1", -15.0, 1000))

    blocked = governor.can_open(now=1001)
    after_window = governor.can_open(now=1000 + 86401)

    assert blocked.allowed is False
    assert blocked.reason == "rolling_loss_pause"
    assert after_window.allowed is True
    assert governor.rolling_pnl == 0.0


def test_three_losses_pause_for_60_minutes_and_consume_streak():
    governor = _governor(now=1000)
    for index in range(3):
        governor.apply_final(
            _resolution(f"r{index}", f"p{index}", -1.0, 1000 + index)
        )

    assert governor.pause_until == 4602
    assert governor.loss_streak == 0
    assert governor.can_open(now=1003).reason == "loss_streak_pause"
    assert governor.can_open(now=4603).allowed is True


def test_zero_or_profit_resets_unconsumed_loss_streak():
    governor = _governor(now=1000)
    governor.apply_final(_resolution("r1", "p1", -1.0, 1000))
    governor.apply_final(_resolution("r2", "p2", 0.0, 1001))
    governor.apply_final(_resolution("r3", "p3", -1.0, 1002))

    assert governor.loss_streak == 1


def test_correction_does_not_revoke_or_reconsume_issued_cooldown():
    governor = _governor(now=1000)
    for index in range(3):
        governor.apply_final(
            _resolution(f"r{index}", f"p{index}", -1.0, 1000 + index)
        )
    pause_until = governor.pause_until

    governor.apply_final(_resolution("r-correction", "p0", 2.0, 1000))

    assert governor.pause_until == pause_until
    assert governor.loss_streak == 0
    assert governor.rolling_pnl == 0.0


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"position_id": "p1", "pnl_usdt": 1.0, "resolved_at": 1}, "missing_resolution_id"),
        ({"resolution_id": "r1", "pnl_usdt": 1.0, "resolved_at": 1}, "missing_close_identity"),
        ({"resolution_id": "r1", "position_id": "p1", "pnl_usdt": float("nan"), "resolved_at": 1}, "non_finite_final"),
        ({"resolution_id": "r1", "position_id": "p1", "pnl_usdt": 1, "resolved_at": 1, "status": "pending"}, "not_final"),
        ({"resolution_id": "r1", "position_id": "p1", "pnl_usdt": 1, "resolved_at": 1, "estimated": True}, "estimated_final"),
        ({"resolution_id": "r1", "position_id": "p1", "pnl_usdt": 1, "resolved_at": 1, "mismatch": True}, "mismatched_final"),
    ],
)
def test_nonfinal_or_ambiguous_pnl_is_rejected(payload, reason):
    result = _governor().apply_final(payload)

    assert result.accepted is False
    assert result.reason == reason


def test_admission_reasons_are_centralized_and_slot_cap_stays_three():
    governor = _governor(now=1000)

    assert governor.can_open(now=1000, active_count=2, pending_count=1).reason == "capacity_full"
    assert governor.can_open(now=1000, same_symbol_state=True).reason == "same_symbol_exposure"
    assert governor.can_open(now=1000, account_gate=False).reason == "account_reject"
    assert governor.can_open(now=1000, active_count=1, pending_count=1).reason == "admitted"


def test_integrity_halt_clears_only_with_persisted_complete_proof(tmp_path):
    from utils.tactical_v2.governor import TacticalGovernor
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    store = TacticalStore(paths)
    governor = _governor(store=store)
    governor.activate_integrity_halt("ambiguous_owner", evidence={"symbol": "WLD-USDT"})

    assert governor.can_open(now=1000).reason == "integrity_halt"
    assert governor.clear_integrity_halt("recon-1", {"ownership": True}) is False
    assert governor.can_open(now=1000).reason == "integrity_halt"
    assert governor.clear_integrity_halt(
        "recon-2",
        {"ownership": True, "orders": True, "positions": True, "protection": True},
    ) is True

    restarted = TacticalGovernor(store=TacticalStore(paths), now_fn=lambda: 1000)
    assert restarted.can_open(now=1000).reason == "admitted"


def test_final_truth_and_cooldown_survive_restart(tmp_path):
    from utils.tactical_v2.governor import TacticalGovernor
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    governor = _governor(store=TacticalStore(paths))
    for index in range(3):
        governor.apply_final(
            _resolution(f"r{index}", f"p{index}", -1.0, 1000 + index)
        )

    restarted = TacticalGovernor(store=TacticalStore(paths), now_fn=lambda: 1003)

    assert restarted.rolling_pnl == -3.0
    assert restarted.final_episode_count == 3
    assert restarted.pause_until == 4602
    assert restarted.loss_streak == 0
