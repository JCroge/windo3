from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


SIDECAR_POLICY_VERSION = "shadow-sidecar-v1"
SIDECAR_POLICY_MAX_AGE_SECONDS = 5.0
SIDECAR_POLICY_FUTURE_TOLERANCE_SECONDS = 1.0
SIDECAR_MAX_ACTIVE_POSITIONS = 3

_EVIDENCE_FIELDS = (
    "tactical_track_gate",
    "tactical_trend_exhaustion_warning",
    "tactical_weak_volume_oi",
    "tactical_weak_provenance",
)


@dataclass(frozen=True)
class SidecarPolicyDecision:
    eligible: bool
    risk_tier: str
    rejection_reason: str


@dataclass(frozen=True)
class SidecarPolicyVerification:
    valid: bool
    admissible: bool
    eligible: bool
    policy_version: str | None
    risk_tier: str
    rejection_reason: str
    age_seconds: float | None
    policy_evidence: Mapping[str, object] | None
    frozen_eligible: bool | None
    frozen_risk_tier: str | None
    frozen_rejection_reason: str | None

    def audit_payload(self, shadow_id: str | None = None) -> dict:
        payload = {
            "reason": self.rejection_reason,
            "sidecar_live_eligible": self.frozen_eligible,
            "sidecar_policy_version": self.policy_version,
            "sidecar_risk_tier": self.frozen_risk_tier,
            "sidecar_policy_age_seconds": self.age_seconds,
            "sidecar_policy_evidence": dict(self.policy_evidence or {}),
        }
        if shadow_id is not None:
            payload["shadow_id"] = shadow_id
        return payload


def canonical_policy_evidence(plan: dict) -> dict | None:
    if not isinstance(plan, dict):
        return None

    gate = plan.get("tactical_track_gate")
    if type(gate) is not str or gate not in {"pass", "fail"}:
        return None

    for field in _EVIDENCE_FIELDS[1:]:
        if type(plan.get(field)) is not bool:
            return None

    return {field: plan[field] for field in _EVIDENCE_FIELDS}


def classify_sidecar_policy(evidence_or_plan: dict) -> SidecarPolicyDecision:
    evidence = canonical_policy_evidence(evidence_or_plan)
    if evidence is None:
        return SidecarPolicyDecision(False, "none", "malformed_policy_evidence")

    if evidence["tactical_track_gate"] != "pass":
        return SidecarPolicyDecision(
            False,
            "none",
            "tactical_track_gate_failed",
        )
    if evidence["tactical_trend_exhaustion_warning"]:
        return SidecarPolicyDecision(
            False,
            "none",
            "trend_exhaustion_warning",
        )
    if (
        evidence["tactical_weak_volume_oi"]
        or evidence["tactical_weak_provenance"]
    ):
        return SidecarPolicyDecision(True, "reduced", "")
    return SidecarPolicyDecision(True, "full", "")


def stamp_sidecar_policy(plan: dict, *, decided_at: float) -> dict:
    stamped = dict(plan)
    evidence = canonical_policy_evidence(plan)
    decision = classify_sidecar_policy(plan)
    stamped.update(
        {
            "sidecar_live_eligible": decision.eligible,
            "sidecar_policy_version": SIDECAR_POLICY_VERSION,
            "sidecar_risk_tier": decision.risk_tier,
            "sidecar_rejection_reason": decision.rejection_reason,
            "sidecar_decided_at": decided_at,
            "sidecar_policy_evidence": (
                dict(evidence) if evidence is not None else None
            ),
        }
    )
    return stamped


def verify_sidecar_policy(record: dict, *, now: float) -> SidecarPolicyVerification:
    if not isinstance(record, dict):
        record = {}

    version = record.get("sidecar_policy_version")
    frozen_eligible = _strict_optional_bool(record.get("sidecar_live_eligible"))
    frozen_risk_tier = _strict_optional_str(record.get("sidecar_risk_tier"))
    frozen_reason = _strict_optional_str(record.get("sidecar_rejection_reason"))
    evidence = canonical_policy_evidence(record)

    if version is None:
        return _verification_failure(
            record,
            "sidecar_policy_version_missing",
            policy_version=None,
            evidence=evidence,
        )
    if version != SIDECAR_POLICY_VERSION:
        return _verification_failure(
            record,
            "sidecar_policy_version_unsupported",
            policy_version=version if type(version) is str else None,
            evidence=evidence,
        )
    if evidence is None:
        return _verification_failure(
            record,
            "sidecar_policy_evidence_invalid",
            policy_version=version,
        )

    nested_evidence = record.get("sidecar_policy_evidence")
    if type(nested_evidence) is not dict or nested_evidence != evidence:
        return _verification_failure(
            record,
            "sidecar_policy_evidence_mismatch",
            policy_version=version,
            evidence=evidence,
        )

    decision = classify_sidecar_policy(evidence)
    if (
        frozen_eligible is None
        or frozen_risk_tier is None
        or frozen_reason is None
        or frozen_eligible is not decision.eligible
        or frozen_risk_tier != decision.risk_tier
        or frozen_reason != decision.rejection_reason
    ):
        return _verification_failure(
            record,
            "sidecar_policy_outcome_mismatch",
            policy_version=version,
            evidence=evidence,
            decision=decision,
        )

    decided_at = record.get("sidecar_decided_at")
    if not _is_finite_number(decided_at) or not _is_finite_number(now):
        return _verification_failure(
            record,
            "sidecar_policy_timestamp_invalid",
            policy_version=version,
            evidence=evidence,
            decision=decision,
        )

    age_seconds = float(now) - float(decided_at)
    if age_seconds < -SIDECAR_POLICY_FUTURE_TOLERANCE_SECONDS:
        return _verification_failure(
            record,
            "sidecar_policy_future",
            policy_version=version,
            evidence=evidence,
            decision=decision,
            age_seconds=age_seconds,
        )
    if age_seconds > SIDECAR_POLICY_MAX_AGE_SECONDS:
        return _verification_failure(
            record,
            "sidecar_policy_stale",
            policy_version=version,
            evidence=evidence,
            decision=decision,
            age_seconds=age_seconds,
        )

    return SidecarPolicyVerification(
        valid=True,
        admissible=decision.eligible,
        eligible=decision.eligible,
        policy_version=version,
        risk_tier=decision.risk_tier,
        rejection_reason=decision.rejection_reason,
        age_seconds=age_seconds,
        policy_evidence=_freeze_evidence(evidence),
        frozen_eligible=frozen_eligible,
        frozen_risk_tier=frozen_risk_tier,
        frozen_rejection_reason=frozen_reason,
    )


def _verification_failure(
    record: dict,
    reason: str,
    *,
    policy_version: str | None,
    evidence: dict | None = None,
    decision: SidecarPolicyDecision | None = None,
    age_seconds: float | None = None,
) -> SidecarPolicyVerification:
    frozen_eligible = _strict_optional_bool(record.get("sidecar_live_eligible"))
    frozen_risk_tier = _strict_optional_str(record.get("sidecar_risk_tier"))
    frozen_reason = _strict_optional_str(record.get("sidecar_rejection_reason"))
    return SidecarPolicyVerification(
        valid=False,
        admissible=False,
        eligible=decision.eligible if decision is not None else False,
        policy_version=policy_version,
        risk_tier=decision.risk_tier if decision is not None else "none",
        rejection_reason=reason,
        age_seconds=age_seconds,
        policy_evidence=_freeze_evidence(evidence),
        frozen_eligible=frozen_eligible,
        frozen_risk_tier=frozen_risk_tier,
        frozen_rejection_reason=frozen_reason,
    )


def _freeze_evidence(evidence: dict | None) -> Mapping[str, object] | None:
    if evidence is None:
        return None
    return MappingProxyType(dict(evidence))


def _strict_optional_bool(value: object) -> bool | None:
    return value if type(value) is bool else None


def _strict_optional_str(value: object) -> str | None:
    return value if type(value) is str else None


def _is_finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value))


__all__ = [
    "SIDECAR_MAX_ACTIVE_POSITIONS",
    "SIDECAR_POLICY_FUTURE_TOLERANCE_SECONDS",
    "SIDECAR_POLICY_MAX_AGE_SECONDS",
    "SIDECAR_POLICY_VERSION",
    "SidecarPolicyDecision",
    "SidecarPolicyVerification",
    "canonical_policy_evidence",
    "classify_sidecar_policy",
    "stamp_sidecar_policy",
    "verify_sidecar_policy",
]
