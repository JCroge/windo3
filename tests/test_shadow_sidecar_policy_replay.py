import json
from decimal import Decimal
from pathlib import Path

from utils.shadow_sidecar_policy import classify_sidecar_policy


FIXTURE = Path(__file__).parent / "fixtures" / "shadow_sidecar_policy_53_trade_window.json"
EXPECTED_ELIGIBLE_TIERS = {
    "23d14fa4": "reduced",
    "888acefb": "full",
    "b22722aa": "full",
    "7dd0993e": "full",
    "b1573ab2": "reduced",
    "72430dbe": "reduced",
    "117a66fd": "reduced",
    "70346318": "reduced",
    "7cdce539": "reduced",
}
EXPECTED_REASON_COUNTS = {
    "": 9,
    "tactical_track_gate_failed": 33,
    "trend_exhaustion_warning": 11,
}


def _load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _replay_once(fixture):
    eligible_tiers = {}
    reason_counts = {}
    all_100u_net = Decimal("0")
    tiered_net = Decimal("0")
    decisions = []

    for row in fixture["trades"]:
        evidence = {
            "tactical_track_gate": row["tactical_track_gate"],
            "tactical_trend_exhaustion_warning": row[
                "tactical_trend_exhaustion_warning"
            ],
            "tactical_weak_volume_oi": row["tactical_weak_volume_oi"],
            "tactical_weak_provenance": row["tactical_weak_provenance"],
        }
        decision = classify_sidecar_policy(evidence)
        reason_counts[decision.rejection_reason] = (
            reason_counts.get(decision.rejection_reason, 0) + 1
        )
        decisions.append(
            {
                "shadow_id": row["shadow_id"],
                "eligible": decision.eligible,
                "risk_tier": decision.risk_tier,
                "rejection_reason": decision.rejection_reason,
            }
        )
        if not decision.eligible:
            continue

        eligible_tiers[row["shadow_id"]] = decision.risk_tier
        pnl_at_100u = Decimal(row["pnl_usdt_at_100u"])
        all_100u_net += pnl_at_100u
        tiered_net += pnl_at_100u * (
            Decimal("0.5") if decision.risk_tier == "reduced" else Decimal("1")
        )

    return {
        "row_count": len(fixture["trades"]),
        "eligible_count": len(eligible_tiers),
        "eligible_tiers": eligible_tiers,
        "reason_counts": reason_counts,
        "all_100u_net": str(all_100u_net),
        "tiered_net": str(tiered_net),
        "decisions": decisions,
    }


def test_fixture_schema_contains_only_policy_and_pnl_trade_fields():
    fixture = _load_fixture()

    assert set(fixture) == {"schema_version", "metadata", "trades"}
    assert fixture["schema_version"] == "shadow_sidecar_policy_53_trade_window.v1"
    assert fixture["metadata"]["row_count"] == 53
    assert fixture["metadata"]["eligible_count"] == 9
    assert fixture["metadata"]["counterfactual_disclaimer"]
    for row in fixture["trades"]:
        assert set(row) == {
            "shadow_id",
            "symbol",
            "side",
            "tactical_track_gate",
            "tactical_trend_exhaustion_warning",
            "tactical_weak_volume_oi",
            "tactical_weak_provenance",
            "pnl_usdt_at_100u",
        }


def test_replay_locks_eligible_tiers_reasons_and_tiered_pnl():
    fixture = _load_fixture()

    replay = _replay_once(fixture)

    assert replay["row_count"] == 53
    assert replay["eligible_count"] == 9
    assert replay["eligible_tiers"] == EXPECTED_ELIGIBLE_TIERS
    assert replay["reason_counts"] == EXPECTED_REASON_COUNTS
    assert replay["all_100u_net"] == "4.47024185"
    assert replay["tiered_net"] == "9.086859325"
    assert fixture["metadata"]["expected_all_100u_net"] == "4.47024185"
    assert fixture["metadata"]["expected_tiered_100u_50u_net"] == "9.086859325"


def test_replay_is_stable_across_one_hundred_independent_loops():
    fixture = _load_fixture()
    expected = json.dumps(_replay_once(fixture), sort_keys=True, separators=(",", ":"))

    for _ in range(100):
        actual = json.dumps(_replay_once(fixture), sort_keys=True, separators=(",", ":"))
        assert actual == expected
