#!/usr/bin/env python3
"""Replay a Tactical V2 candidate window through shared admission reducers."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.tactical_v2.entry import ExecutableQuote, classify_entry  # noqa: E402
from utils.tactical_v2.episodes import EpisodeRegistry  # noqa: E402
from utils.tactical_v2.governor import TacticalGovernor  # noqa: E402
from utils.tactical_v2.models import TacticalCandidate, TacticalIntent  # noqa: E402
from utils.tactical_v2.store import TacticalStore  # noqa: E402


SYNTHETIC_BOUNDARY_REASON = "synthetic_admission_window_opportunity_boundary"
STABILITY_FIELDS = ("accepted_identities", "episode_ids", "row_reasons")
CANDIDATE_ALLOWED_FIELDS = frozenset({
    "candidate_id",
    "created_at",
    "entry_ref",
    "leverage",
    "namespace",
    "side",
    "source_shadow_id",
    "stop_loss",
    "symbol",
    "tactical_cost_gate",
    "tactical_ev",
    "tactical_rr",
    "tactical_source",
    "take_profit",
    "tf_15m_available",
    "tf_15m_bias",
    "tf_15m_block_long",
    "tf_15m_block_short",
    "tf_15m_closed_bar_ts",
    "tf_15m_structure_token",
})
EXPECTED_ACCEPTED = (
    ("92ae52b2a067b12a6c00f1ef80cbfa0b", "d1e7880d"),
    ("5e19050a9f8272e6e25b928137f2ac4a", "f978fd43"),
    ("677197cc02691c503966cd09830d6164", "3bce3dd2"),
    ("7ef72c05d3297688f04a00879ed2bbd5", "d8e48042"),
    ("438d069dfe63a83cced089717ad5c7fa", "72524a13"),
)
EXPECTED_EPISODE_IDS = (
    "b321a646e2a0b5f0b65e2478a4cd65bdd9af3c4652f32daa9c118ce885b439c5",
    "73576673e6db1172b618f2b387eaf7793f15baa8be05156ddc89bd5e6b0236bf",
    "4778bb24537d7ef25d348393ceceaeb18d6b6d1c50bbf792dda1732e9c3195a2",
    "96ee7827c312372cf15c241bf3f990c0fc14fe434138f98614a2df389da8b382",
    "69cd302eac72ba654afccd84797b20ab27722fabb343e979b1f43af6460c848d",
)


class OpportunityEvidenceError(ValueError):
    """Raised when a structural opportunity cannot be identified safely."""


@dataclass(frozen=True)
class _CandidateRow:
    msg_id: str
    source_evidence_payload_hash: str
    journal_timestamp: float
    raw: dict[str, Any]
    candidate: TacticalCandidate
    opportunity: tuple[Any, ...]


@dataclass(frozen=True)
class AdmissionReplayReport:
    raw_candidates: int
    accepted: int
    accepted_by_symbol: dict[str, int]
    reasons: dict[str, int]
    accepted_reasons: dict[str, int]
    rejected: int
    unknown: int
    stable_iterations: int
    stability_compared_fields: tuple[str, ...]
    stability_fingerprint: str
    accepted_identities: tuple[dict[str, Any], ...]
    episode_ids: tuple[str, ...]
    row_reasons: tuple[dict[str, Any], ...]
    entry_decision_checks: tuple[dict[str, Any], ...]
    source_shadow_ids: tuple[str, ...]
    source_evidence_payload_hashes: tuple[str, ...]
    historical_receipt_context: str
    historical_receipt_evidence: str
    historical_receipt_unknown: int
    synthetic_boundary_reason: str
    synthetic_boundary_role: str
    synthetic_boundary_market_settlement: bool
    exchange_fill: bool
    historical_executable_quote_available: bool
    protection_evidence_proven: bool
    protection_check_status: str
    protection_live_rollout_gate_passed: bool
    live_rollout_ready: bool
    parity_expected_values_passed: bool
    replay_integrity_passed: bool
    stability_requirement_passed: bool
    admission_replay_passed: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entry-decision check"] = payload["entry_decision_checks"]
        return json.loads(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


def _required_text(value: Any, name: str) -> str:
    parsed = str(value or "").strip()
    if not parsed:
        raise ValueError(f"{name} is required")
    return parsed


def _required_finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _opportunity_for(
    candidate: TacticalCandidate,
    structure: Mapping[str, Any],
) -> tuple[Any, ...]:
    if structure.get("tf_15m_available") is not True:
        raise OpportunityEvidenceError("15m structure must be available")
    for field in ("tf_15m_block_long", "tf_15m_block_short"):
        if not isinstance(structure.get(field), bool):
            raise OpportunityEvidenceError(f"{field} must be boolean")

    bias = str(structure.get("tf_15m_bias") or "").strip().lower()
    lane = (candidate.symbol, candidate.side)
    if bias == "neutral":
        try:
            closed_bar = _required_finite(
                structure.get("tf_15m_closed_bar_ts"),
                "tf_15m_closed_bar_ts",
            )
        except ValueError as exc:
            raise OpportunityEvidenceError(str(exc)) from exc
        return (*lane, "neutral_bar", closed_bar)

    aligned_bias = "bullish" if candidate.side == "long" else "bearish"
    if bias != aligned_bias:
        raise OpportunityEvidenceError("15m bias is neither neutral nor side-aligned")
    token = str(structure.get("tf_15m_structure_token") or "").strip()
    if not token:
        raise OpportunityEvidenceError(
            "tf_15m_structure_token is required for aligned structure"
        )
    return (*lane, "aligned_token", token)


def normalized_structural_opportunity(raw: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the normalized symbol/side opportunity identity, or fail closed."""
    candidate = TacticalCandidate.from_raw(raw)
    return _opportunity_for(candidate, raw)


def _load_fixture(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        fixture = dict(source)
    else:
        fixture = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(fixture, dict):
        raise ValueError("fixture root must be an object")
    return fixture


def _candidate_rows(fixture: Mapping[str, Any]) -> tuple[_CandidateRow, ...]:
    raw_rows = fixture.get("candidates")
    if not isinstance(raw_rows, list):
        raise ValueError("fixture candidates must be a list")

    rows = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, Mapping):
            raise ValueError("candidate evidence row must be an object")
        raw_candidate = raw_row.get("candidate")
        if not isinstance(raw_candidate, Mapping):
            raise ValueError("candidate payload must be an object")
        candidate_data = dict(raw_candidate)
        if frozenset(candidate_data) != CANDIDATE_ALLOWED_FIELDS:
            raise ValueError("candidate fields do not match the exact replay schema")
        candidate = TacticalCandidate.from_raw(candidate_data)
        payload_hash = _required_text(
            raw_row.get("source_evidence_payload_hash"),
            "source_evidence_payload_hash",
        )
        if len(payload_hash) != 12 or any(
            character not in "0123456789abcdef" for character in payload_hash
        ):
            raise ValueError("source evidence payload hash must be 12 lowercase hex characters")
        rows.append(
            _CandidateRow(
                msg_id=_required_text(raw_row.get("msg_id"), "msg_id"),
                source_evidence_payload_hash=payload_hash,
                journal_timestamp=_required_finite(
                    raw_row.get("journal_timestamp"), "journal_timestamp"
                ),
                raw=candidate_data,
                candidate=candidate,
                opportunity=_opportunity_for(candidate, candidate_data),
            )
        )
    return tuple(sorted(rows, key=lambda row: row.candidate.created_at))


def _validate_initial_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("initial_episode_state must be an object")
    state = dict(raw)
    required = {
        "namespace",
        "symbol",
        "side",
        "epoch_seq",
        "episode_id",
        "attempted",
        "terminal",
        "terminal_reason",
        "current_bias",
        "neutral_seen",
        "last_block",
        "reset_pending",
        "last_closed_bar_ts",
        "max_observed_closed_bar_ts",
        "last_structure_token",
    }
    if set(state) != required:
        raise ValueError("initial episode state fields do not match the replay schema")
    if state["terminal"] is not True or state["terminal_reason"] != "loss_streak_pause":
        raise ValueError("initial episode must carry its recorded terminal state")
    return state


def _seed_initial_episode(store: TacticalStore, state: Mapping[str, Any]) -> None:
    key = f"{state['symbol']}|{state['side']}"
    episode_id = str(state["episode_id"])
    assigned_state = {**state, "terminal": False, "terminal_reason": None}
    baseline = float(state["last_closed_bar_ts"]) / 1000.0
    store.append(
        "episode_assigned",
        {
            "registry_key": key,
            "registry_state": assigned_state,
            "episode_id": episode_id,
            "evidence": {"reason": "source_window_initial_state"},
        },
        emitted_at=baseline,
        event_id="seed-pump-epoch-14-assigned",
    )
    store.append(
        "episode_terminal",
        {
            "registry_key": key,
            "registry_state": dict(state),
            "episode_id": episode_id,
            "evidence": {"reason": state["terminal_reason"]},
        },
        emitted_at=baseline + 1.0,
        event_id="seed-pump-epoch-14-terminal",
    )


def _entry_decision_check(
    candidate: TacticalCandidate,
    episode_id: str,
    governor: TacticalGovernor,
    evaluated_at: float,
) -> dict[str, Any]:
    intent = TacticalIntent.from_candidate(candidate, episode_id=episode_id)
    quote = ExecutableQuote(
        bid=candidate.entry_ref,
        ask=candidate.entry_ref,
        observed_at=evaluated_at,
    )
    entry_decision = classify_entry(intent, quote, now=evaluated_at)
    governor_decision = governor.can_open(
        now=evaluated_at,
        active_count=0,
        pending_count=0,
        same_symbol_state=False,
        account_gate=True,
        integrity_state={"halted": False},
    )
    return {
        "label": "entry-decision check",
        "candidate_id": candidate.candidate_id,
        "episode_id": episode_id,
        "quote_evidence": "synthetic",
        "synthetic_quote_role": "reducer_boundary_only",
        "bid": quote.bid,
        "ask": quote.ask,
        "observed_at": quote.observed_at,
        "evaluated_at": evaluated_at,
        "action": entry_decision.action,
        "reason": entry_decision.reason,
        "ttl_fresh": evaluated_at < intent.expires_at,
        "governor_allowed": governor_decision.allowed,
        "governor_reason": governor_decision.reason,
    }


def _replay_once(
    fixture: Mapping[str, Any],
    rows: Sequence[_CandidateRow],
    temp_root: Path,
) -> dict[str, Any]:
    paths = SimpleNamespace(
        tactical_v2_events=str(temp_root / "events.jsonl"),
        tactical_v2_state=str(temp_root / "state.json"),
    )
    store = TacticalStore(paths)
    initial_state = _validate_initial_state(fixture.get("initial_episode_state"))
    _seed_initial_episode(store, initial_state)
    registry = EpisodeRegistry(store, namespace="live")
    first_created_at = rows[0].candidate.created_at if rows else 0.0
    governor = TacticalGovernor(store=store, now_fn=lambda: first_created_at)

    current_opportunity: dict[tuple[str, str], tuple[Any, ...]] = {}
    accepted_episode: dict[tuple[str, str], str] = {}
    accepted_identities = []
    row_reasons = []
    entry_checks = []
    rejection_reasons: Counter[str] = Counter()
    accepted_reasons: Counter[str] = Counter()
    accepted_by_symbol: Counter[str] = Counter()
    rejected = 0
    unknown = 0

    for row in rows:
        candidate = row.candidate
        lane = (candidate.symbol, candidate.side)
        previous_opportunity = current_opportunity.get(lane)
        if previous_opportunity is not None and previous_opportunity != row.opportunity:
            previous_episode = accepted_episode.get(lane)
            if previous_episode is not None:
                registry.mark_terminal(
                    previous_episode,
                    SYNTHETIC_BOUNDARY_REASON,
                    evidence={
                        "reason": SYNTHETIC_BOUNDARY_REASON,
                        "normalization": "admission_window_boundary",
                    },
                )
        current_opportunity[lane] = row.opportunity

        assignment = registry.assign(candidate, row.raw)
        row_reason = {
            "candidate_id": candidate.candidate_id,
            "source_shadow_id": candidate.source_shadow_id,
            "msg_id": row.msg_id,
            "opportunity": list(row.opportunity),
            "episode_id": assignment.episode_id,
            "eligible": assignment.eligible,
            "reason": assignment.reason,
        }
        row_reasons.append(row_reason)

        if assignment.eligible:
            check = _entry_decision_check(
                candidate,
                assignment.episode_id,
                governor,
                row.journal_timestamp,
            )
            entry_checks.append(check)
            admitted = bool(
                check["action"] == "immediate"
                and check["reason"] == "within_entry_drift"
                and check["ttl_fresh"]
                and check["governor_allowed"]
                and check["governor_reason"] == "admitted"
            )
            if not admitted:
                rejected += 1
                continue
            identity = {
                "candidate_id": candidate.candidate_id,
                "source_shadow_id": candidate.source_shadow_id,
                "msg_id": row.msg_id,
                "source_evidence_payload_hash": row.source_evidence_payload_hash,
                "opportunity": list(row.opportunity),
                "episode_id": assignment.episode_id,
                "epoch_seq": assignment.epoch_seq,
            }
            accepted_identities.append(identity)
            accepted_episode[lane] = assignment.episode_id
            accepted_reasons[assignment.reason] += 1
            accepted_by_symbol[candidate.symbol] += 1
        elif assignment.reason == "duplicate_episode":
            rejection_reasons[assignment.reason] += 1
        elif assignment.reason in {"opposing_block"}:
            rejected += 1
        else:
            unknown += 1

    return {
        "raw_candidates": len(rows),
        "accepted": len(accepted_identities),
        "accepted_by_symbol": dict(sorted(accepted_by_symbol.items())),
        "reasons": dict(sorted(rejection_reasons.items())),
        "accepted_reasons": dict(sorted(accepted_reasons.items())),
        "rejected": rejected,
        "unknown": unknown,
        "accepted_identities": accepted_identities,
        "episode_ids": [row["episode_id"] for row in accepted_identities],
        "row_reasons": row_reasons,
        "entry_decision_checks": entry_checks,
        "source_shadow_ids": [row.candidate.source_shadow_id for row in rows],
        "source_evidence_payload_hashes": [
            row.source_evidence_payload_hash for row in rows
        ],
    }


def _stability_serialization(run: Mapping[str, Any]) -> str:
    projection = {field: run[field] for field in STABILITY_FIELDS}
    return json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _matches_expected_values(run: Mapping[str, Any]) -> bool:
    accepted = tuple(
        (row["candidate_id"], row["source_shadow_id"])
        for row in run["accepted_identities"]
    )
    checks = run["entry_decision_checks"]
    return bool(
        run["raw_candidates"] == 22
        and run["accepted"] == 5
        and run["accepted_by_symbol"] == {"BICO-USDT": 3, "PUMP-USDT": 2}
        and run["reasons"] == {"duplicate_episode": 17}
        and run["rejected"] == 0
        and run["unknown"] == 0
        and accepted == EXPECTED_ACCEPTED
        and tuple(run["episode_ids"]) == EXPECTED_EPISODE_IDS
        and len(checks) == 5
        and all(
            check["action"] == "immediate"
            and check["reason"] == "within_entry_drift"
            and check["ttl_fresh"] is True
            and check["governor_allowed"] is True
            and check["governor_reason"] == "admitted"
            for check in checks
        )
    )


def replay_fixture(
    source: str | Path | Mapping[str, Any],
    *,
    iterations: int = 100,
    scratch_root: str | Path | None = None,
) -> AdmissionReplayReport:
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise ValueError("iterations must be a positive integer")
    fixture = _load_fixture(source)
    rows = _candidate_rows(fixture)
    evidence = fixture.get("source_evidence")
    if not isinstance(evidence, Mapping) or evidence.get("raw_candidate_count") != len(rows):
        raise ValueError("source evidence candidate count mismatch")

    parent = None if scratch_root is None else str(Path(scratch_root))
    first_run = None
    stable_serialization = None
    for _ in range(iterations):
        with TemporaryDirectory(prefix="tactical-v2-admission-", dir=parent) as temp_dir:
            run = _replay_once(fixture, rows, Path(temp_dir))
        serialized = _stability_serialization(run)
        if stable_serialization is None:
            first_run = run
            stable_serialization = serialized
        elif serialized != stable_serialization:
            raise RuntimeError("admission replay is not stable across independent runs")

    if first_run is None or stable_serialization is None:
        raise RuntimeError("admission replay produced no run")
    parity_passed = _matches_expected_values(first_run)
    replay_integrity_passed = bool(
        len(first_run["row_reasons"]) == len(rows)
        and len(first_run["source_shadow_ids"]) == len(rows)
        and len(first_run["source_evidence_payload_hashes"]) == len(rows)
        and first_run["unknown"] == 0
    )
    stability_requirement_passed = iterations == 100
    evidence_limitations_accurate = bool(
        first_run["raw_candidates"] == 22
        and len(rows) == 22
    )
    admission_replay_passed = bool(
        parity_passed
        and replay_integrity_passed
        and stability_requirement_passed
        and evidence_limitations_accurate
    )
    return AdmissionReplayReport(
        raw_candidates=first_run["raw_candidates"],
        accepted=first_run["accepted"],
        accepted_by_symbol=first_run["accepted_by_symbol"],
        reasons=first_run["reasons"],
        accepted_reasons=first_run["accepted_reasons"],
        rejected=first_run["rejected"],
        unknown=first_run["unknown"],
        stable_iterations=iterations,
        stability_compared_fields=STABILITY_FIELDS,
        stability_fingerprint=hashlib.sha256(
            stable_serialization.encode("ascii")
        ).hexdigest(),
        accepted_identities=tuple(first_run["accepted_identities"]),
        episode_ids=tuple(first_run["episode_ids"]),
        row_reasons=tuple(first_run["row_reasons"]),
        entry_decision_checks=tuple(first_run["entry_decision_checks"]),
        source_shadow_ids=tuple(first_run["source_shadow_ids"]),
        source_evidence_payload_hashes=tuple(
            first_run["source_evidence_payload_hashes"]
        ),
        historical_receipt_context="predates_durable_receipts",
        historical_receipt_evidence="unknown",
        historical_receipt_unknown=len(rows),
        synthetic_boundary_reason=SYNTHETIC_BOUNDARY_REASON,
        synthetic_boundary_role="admission_normalization_only",
        synthetic_boundary_market_settlement=False,
        exchange_fill=False,
        historical_executable_quote_available=False,
        protection_evidence_proven=False,
        protection_check_status="not_run_no_fill",
        protection_live_rollout_gate_passed=False,
        live_rollout_ready=False,
        parity_expected_values_passed=parity_passed,
        replay_integrity_passed=replay_integrity_passed,
        stability_requirement_passed=stability_requirement_passed,
        admission_replay_passed=admission_replay_passed,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args(argv)
    report = replay_fixture(args.fixture, iterations=args.iterations)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    limitations_are_fail_closed = bool(
        report.exchange_fill is False
        and report.historical_executable_quote_available is False
        and report.protection_evidence_proven is False
        and report.protection_check_status == "not_run_no_fill"
        and report.protection_live_rollout_gate_passed is False
        and report.live_rollout_ready is False
    )
    return 0 if report.admission_replay_passed and limitations_are_fail_closed else 1


if __name__ == "__main__":
    raise SystemExit(main())
