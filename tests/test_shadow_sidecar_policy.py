from dataclasses import FrozenInstanceError

import pytest

from utils.shadow_sidecar_policy import (
    SIDECAR_MAX_ACTIVE_POSITIONS,
    SIDECAR_POLICY_FUTURE_TOLERANCE_SECONDS,
    SIDECAR_POLICY_MAX_AGE_SECONDS,
    SIDECAR_POLICY_VERSION,
    canonical_policy_evidence,
    classify_sidecar_policy,
    stamp_sidecar_policy,
    verify_sidecar_policy,
)


def _plan(**overrides):
    plan = {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "tactical_track_gate": "pass",
        "tactical_trend_exhaustion_warning": False,
        "tactical_weak_volume_oi": False,
        "tactical_weak_provenance": False,
    }
    plan.update(overrides)
    return plan


def test_policy_constants_are_frozen_at_version_one_values():
    assert SIDECAR_POLICY_VERSION == "shadow-sidecar-v1"
    assert SIDECAR_POLICY_MAX_AGE_SECONDS == 5.0
    assert SIDECAR_POLICY_FUTURE_TOLERANCE_SECONDS == 1.0
    assert SIDECAR_MAX_ACTIVE_POSITIONS == 3


def test_canonical_policy_evidence_contains_only_exact_typed_raw_fields():
    evidence = canonical_policy_evidence(_plan(unrelated="ignored"))

    assert evidence == {
        "tactical_track_gate": "pass",
        "tactical_trend_exhaustion_warning": False,
        "tactical_weak_volume_oi": False,
        "tactical_weak_provenance": False,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"tactical_track_gate": None},
        {"tactical_track_gate": "PASS"},
        {"tactical_trend_exhaustion_warning": 0},
        {"tactical_trend_exhaustion_warning": "false"},
        {"tactical_weak_volume_oi": 1},
        {"tactical_weak_provenance": None},
    ],
)
def test_canonical_policy_evidence_rejects_missing_or_malformed_values(overrides):
    assert canonical_policy_evidence(_plan(**overrides)) is None


def test_canonical_policy_evidence_rejects_missing_field():
    plan = _plan()
    del plan["tactical_weak_provenance"]

    assert canonical_policy_evidence(plan) is None


@pytest.mark.parametrize(
    ("overrides", "eligible", "tier", "reason"),
    [
        ({}, True, "full", ""),
        ({"tactical_weak_volume_oi": True}, True, "reduced", ""),
        ({"tactical_weak_provenance": True}, True, "reduced", ""),
        (
            {
                "tactical_weak_volume_oi": True,
                "tactical_weak_provenance": True,
            },
            True,
            "reduced",
            "",
        ),
        (
            {"tactical_track_gate": "fail"},
            False,
            "none",
            "tactical_track_gate_failed",
        ),
        (
            {"tactical_trend_exhaustion_warning": True},
            False,
            "none",
            "trend_exhaustion_warning",
        ),
        (
            {
                "tactical_track_gate": "fail",
                "tactical_trend_exhaustion_warning": True,
            },
            False,
            "none",
            "tactical_track_gate_failed",
        ),
    ],
)
def test_policy_truth_table(overrides, eligible, tier, reason):
    decision = classify_sidecar_policy(_plan(**overrides))

    assert decision.eligible is eligible
    assert decision.risk_tier == tier
    assert decision.rejection_reason == reason


@pytest.mark.parametrize(
    "overrides",
    [
        {"tactical_trend_exhaustion_warning": 1},
        {"tactical_weak_volume_oi": "yes"},
        {"tactical_weak_provenance": 0},
    ],
)
def test_policy_malformed_booleans_fail_closed(overrides):
    decision = classify_sidecar_policy(_plan(**overrides))

    assert decision.eligible is False
    assert decision.risk_tier == "none"
    assert decision.rejection_reason == "malformed_policy_evidence"


def test_policy_decision_is_immutable():
    decision = classify_sidecar_policy(_plan())

    with pytest.raises(FrozenInstanceError):
        decision.eligible = False


def test_stamp_copies_plan_and_freezes_canonical_outcome():
    plan = _plan(tactical_weak_provenance=True)

    stamped = stamp_sidecar_policy(plan, decided_at=100.0)

    assert stamped is not plan
    assert "sidecar_policy_version" not in plan
    assert stamped["sidecar_live_eligible"] is True
    assert stamped["sidecar_policy_version"] == SIDECAR_POLICY_VERSION
    assert stamped["sidecar_risk_tier"] == "reduced"
    assert stamped["sidecar_rejection_reason"] == ""
    assert stamped["sidecar_decided_at"] == 100.0
    assert stamped["sidecar_policy_evidence"] == canonical_policy_evidence(plan)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"tactical_track_gate": "fail"}, "tactical_track_gate_failed"),
        (
            {"tactical_trend_exhaustion_warning": True},
            "trend_exhaustion_warning",
        ),
    ],
)
def test_verify_preserves_valid_but_ineligible_policy_outcomes(overrides, reason):
    stamped = stamp_sidecar_policy(_plan(**overrides), decided_at=100.0)

    verified = verify_sidecar_policy(stamped, now=101.0)

    assert verified.valid is True
    assert verified.admissible is False
    assert verified.eligible is False
    assert verified.risk_tier == "none"
    assert verified.rejection_reason == reason


def test_stamp_and_verify_exact_ttl_boundary():
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)

    verified = verify_sidecar_policy(stamped, now=105.0)

    assert verified.valid is True
    assert verified.admissible is True
    assert verified.eligible is True
    assert verified.policy_version == SIDECAR_POLICY_VERSION
    assert verified.risk_tier == "full"
    assert verified.rejection_reason == ""
    assert verified.age_seconds == 5.0


def test_verify_rejects_stale_policy_after_ttl_boundary():
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)

    verified = verify_sidecar_policy(stamped, now=105.000001)

    assert verified.valid is False
    assert verified.admissible is False
    assert verified.rejection_reason == "sidecar_policy_stale"
    assert verified.age_seconds == pytest.approx(5.000001)


def test_verify_allows_future_skew_at_exact_tolerance():
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)

    verified = verify_sidecar_policy(stamped, now=99.0)

    assert verified.valid is True
    assert verified.admissible is True
    assert verified.age_seconds == -1.0


def test_verify_rejects_future_skew_beyond_tolerance():
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)

    verified = verify_sidecar_policy(stamped, now=98.999999)

    assert verified.valid is False
    assert verified.admissible is False
    assert verified.rejection_reason == "sidecar_policy_future"
    assert verified.age_seconds == pytest.approx(-1.000001)


@pytest.mark.parametrize("version", [None, "shadow-sidecar-v2"])
def test_verify_rejects_missing_or_unsupported_version(version):
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)
    if version is None:
        del stamped["sidecar_policy_version"]
        expected_reason = "sidecar_policy_version_missing"
    else:
        stamped["sidecar_policy_version"] = version
        expected_reason = "sidecar_policy_version_unsupported"

    verified = verify_sidecar_policy(stamped, now=101.0)

    assert verified.valid is False
    assert verified.admissible is False
    assert verified.rejection_reason == expected_reason


def test_verify_rebuilds_top_level_evidence_and_rejects_nested_mismatch():
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)
    stamped["tactical_weak_volume_oi"] = True

    verified = verify_sidecar_policy(stamped, now=101.0)

    assert verified.valid is False
    assert verified.rejection_reason == "sidecar_policy_evidence_mismatch"


def test_verify_rejects_extra_nested_evidence_field():
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)
    stamped["sidecar_policy_evidence"]["unexpected"] = False

    verified = verify_sidecar_policy(stamped, now=101.0)

    assert verified.valid is False
    assert verified.rejection_reason == "sidecar_policy_evidence_mismatch"


def test_verify_rejects_malformed_top_level_evidence():
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)
    stamped["tactical_weak_volume_oi"] = 1
    stamped["sidecar_policy_evidence"]["tactical_weak_volume_oi"] = 1

    verified = verify_sidecar_policy(stamped, now=101.0)

    assert verified.valid is False
    assert verified.rejection_reason == "sidecar_policy_evidence_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sidecar_live_eligible", False),
        ("sidecar_risk_tier", "reduced"),
        ("sidecar_rejection_reason", "tampered"),
    ],
)
def test_verify_rejects_frozen_outcome_mismatch(field, value):
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)
    stamped[field] = value

    verified = verify_sidecar_policy(stamped, now=101.0)

    assert verified.valid is False
    assert verified.rejection_reason == "sidecar_policy_outcome_mismatch"


@pytest.mark.parametrize(
    "timestamp",
    [None, True, "100.0", float("nan"), float("inf"), float("-inf")],
)
def test_verify_rejects_malformed_or_non_finite_decision_timestamp(timestamp):
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)
    stamped["sidecar_decided_at"] = timestamp

    verified = verify_sidecar_policy(stamped, now=101.0)

    assert verified.valid is False
    assert verified.admissible is False
    assert verified.rejection_reason == "sidecar_policy_timestamp_invalid"
    assert verified.age_seconds is None


def test_verification_is_immutable_and_exposes_audit_payload():
    stamped = stamp_sidecar_policy(_plan(), decided_at=100.0)
    verified = verify_sidecar_policy(stamped, now=101.0)

    with pytest.raises(FrozenInstanceError):
        verified.valid = False

    assert verified.audit_payload("shadow-1") == {
        "shadow_id": "shadow-1",
        "reason": "",
        "sidecar_live_eligible": True,
        "sidecar_policy_version": SIDECAR_POLICY_VERSION,
        "sidecar_risk_tier": "full",
        "sidecar_policy_age_seconds": 1.0,
        "sidecar_policy_evidence": canonical_policy_evidence(_plan()),
    }
