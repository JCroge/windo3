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
from utils.tactical_v2.controller import TacticalV2Controller  # noqa: E402
from utils.tactical_v2.episodes import EpisodeRegistry  # noqa: E402
from utils.tactical_v2.governor import TacticalGovernor  # noqa: E402
from utils.tactical_v2.models import TacticalCandidate, TacticalIntent  # noqa: E402
from utils.tactical_v2.store import TacticalStore  # noqa: E402


SYNTHETIC_BOUNDARY_REASON = "synthetic_admission_window_opportunity_boundary"
STABILITY_FIELDS = (
    "accepted_identities",
    "episode_ids",
    "row_reasons",
)
CONTROLLER_STABILITY_FIELDS = (
    "controller_replay_intent_ids",
    "controller_replay_receipts",
    "controller_replay_results",
    "controller_replay_event_seq_contiguous",
    "controller_replay_integrity_failure",
)
PARITY_PROJECTION_FIELDS = (
    "raw_candidates",
    "accepted",
    "accepted_by_symbol",
    "reasons",
    "accepted_reasons",
    "rejected",
    "unknown",
    "accepted_identities",
    "episode_ids",
    "entry_decision_checks",
)
REPLAY_EVIDENCE_PROJECTION_FIELDS = PARITY_PROJECTION_FIELDS + (
    "row_reasons",
    "source_shadow_ids",
    "source_evidence_payload_hashes",
)
EXPECTED_SCHEMA_VERSION = 1
EXPECTED_TOPIC = "tactical_candidate.v2"
EXPECTED_WINDOW_START = 1786183980
EXPECTED_WINDOW_END = 1786443180
EXPECTED_CANDIDATE_COUNT = 22
PINNED_FIXTURE_SHA256 = (
    "65dd6e2f3cd21dd1aaa9d163126c818f0a0db8f92997d80f24e548f44e72fa5f"
)
PINNED_PARITY_SHA256 = (
    "d3cd2fe742b5bae5dafa1e018d007bb431d4f2314bcb13c31508e3697c0d02f5"
)
PINNED_REPLAY_EVIDENCE_SHA256 = (
    "897580be19ba533e1a6b0bf4877d85ccbecf572143f23f35d1e83a8150499cd3"
)
PINNED_STABILITY_SHA256 = (
    "73175bcdd2435db7c7be81f242be5264390acc318655c8c7cd758dd293ca2ab0"
)
FIXTURE_ROOT_FIELDS = frozenset({
    "schema_version",
    "source_evidence",
    "initial_episode_state",
    "candidates",
})
SOURCE_EVIDENCE_FIELDS = frozenset({
    "description",
    "topic",
    "window_start_epoch",
    "window_end_epoch",
    "raw_candidate_count",
})
CANDIDATE_ROW_FIELDS = frozenset({
    "msg_id",
    "source_evidence_payload_hash",
    "journal_timestamp",
    "candidate",
})
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
INITIAL_EPISODE_FIELDS = frozenset({
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
})
INITIAL_EPISODE_ID = (
    "53642e33465ffe749cbb7da042f486c2bd4d68a350dcef68a42f8cad6bbd11dd"
)
INITIAL_EPISODE_BAR_TS = 1786072500000.0
INITIAL_EPISODE_STRUCTURE_TOKEN = "break_up:a09b50a62afef967206a"
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
EXPECTED_SOURCE_SHADOW_IDS = (
    "d1e7880d",
    "e75637c6",
    "15cf7200",
    "fc579a9b",
    "f978fd43",
    "d8dc7fca",
    "2a3aae8c",
    "f8a09aaa",
    "dfb3ab09",
    "bf64ccac",
    "80a92cd8",
    "1054371e",
    "a86ae45a",
    "3bce3dd2",
    "b1863929",
    "62bb7dda",
    "ad899485",
    "ae3aa4b1",
    "d8e48042",
    "ba2a1cd4",
    "72524a13",
    "90de5091",
)
EXPECTED_SOURCE_EVIDENCE_HASHES = (
    "e0f2281a7717",
    "f1beec6efcf1",
    "3a0de78789cf",
    "d9db09d56c4c",
    "4b0add3b6efe",
    "8d1502040f00",
    "5f7ca11d4485",
    "ecb06d6b7ae0",
    "1c2983da430e",
    "857ad0512e8c",
    "415998e81eff",
    "92a136cb30e2",
    "c4712ea77187",
    "08b7b05c6048",
    "e498c34ff9a9",
    "698b723b9e8e",
    "56c3158c0050",
    "bb1356709a71",
    "a4db65634048",
    "9fa2556efac9",
    "80534592ec6d",
    "054c4cf96d78",
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


def _projection_fingerprint(source: Any, fields: Sequence[str]) -> str:
    projection = {
        field: source[field] if isinstance(source, Mapping) else getattr(source, field)
        for field in fields
    }
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AdmissionReplayReport:
    raw_candidates: int
    accepted: int
    accepted_metric: str
    accepted_by_symbol: dict[str, int]
    reasons: dict[str, int]
    accepted_reasons: dict[str, int]
    rejected: int
    unknown: int
    stable_iterations: int
    stability_compared_fields: tuple[str, ...]
    stability_fingerprint: str
    fixture_fingerprint: str
    pinned_fixture_fingerprint_match: bool
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
    controller_replay_intents: int
    controller_replay_intent_ids: tuple[str, ...]
    controller_replay_receipts: int
    controller_replay_results: dict[str, int]
    controller_replay_event_seq_contiguous: bool
    controller_replay_integrity_failure: Optional[dict[str, Any]]
    controller_replay_lifecycle_evidence: str
    controller_five_intent_parity_proven: bool
    controller_stable_iterations: int
    controller_stability_compared_fields: tuple[str, ...]
    controller_stability_fingerprint: str

    @property
    def parity_expected_values_passed(self) -> bool:
        try:
            accepted_identity_pairs = tuple(
                (row["candidate_id"], row["source_shadow_id"])
                for row in self.accepted_identities
            )
            identity_episode_pairs = tuple(
                (row["candidate_id"], row["episode_id"])
                for row in self.accepted_identities
            )
            identity_episode_ids = tuple(
                row["episode_id"] for row in self.accepted_identities
            )
            check_episode_pairs = tuple(
                (check["candidate_id"], check["episode_id"])
                for check in self.entry_decision_checks
            )
            checks_are_admitted = len(self.entry_decision_checks) == 5 and all(
                isinstance(check, Mapping)
                and check.get("label") == "entry-decision check"
                and check.get("quote_evidence") == "synthetic"
                and check.get("synthetic_quote_role") == "reducer_boundary_only"
                and check.get("observed_at") == check.get("evaluated_at")
                and check.get("action") == "immediate"
                and check.get("reason") == "within_entry_drift"
                and check.get("ttl_fresh") is True
                and check.get("governor_allowed") is True
                and check.get("governor_reason") == "admitted"
                for check in self.entry_decision_checks
            )
            return bool(
                self.raw_candidates == EXPECTED_CANDIDATE_COUNT
                and self.accepted == 5
                and len(self.accepted_identities) == self.accepted
                and self.accepted_by_symbol == {"BICO-USDT": 3, "PUMP-USDT": 2}
                and self.reasons == {"duplicate_episode": 17}
                and self.accepted_reasons == {
                    "eligible": 3,
                    "new_confirmed_structure": 2,
                }
                and self.rejected == 0
                and self.unknown == 0
                and accepted_identity_pairs == EXPECTED_ACCEPTED
                and tuple(self.episode_ids) == EXPECTED_EPISODE_IDS
                and identity_episode_ids == tuple(self.episode_ids)
                and identity_episode_pairs == check_episode_pairs
                and checks_are_admitted
                and _projection_fingerprint(self, PARITY_PROJECTION_FIELDS)
                == PINNED_PARITY_SHA256
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    @property
    def replay_integrity_passed(self) -> bool:
        try:
            return bool(
                self.fixture_fingerprint == PINNED_FIXTURE_SHA256
                and self.pinned_fixture_fingerprint_match is True
                and self.raw_candidates == EXPECTED_CANDIDATE_COUNT
                and self.unknown == 0
                and len(self.source_shadow_ids) == EXPECTED_CANDIDATE_COUNT
                and len(self.source_evidence_payload_hashes)
                == EXPECTED_CANDIDATE_COUNT
                and len(self.row_reasons) == EXPECTED_CANDIDATE_COUNT
                and len(self.accepted_identities) == 5
                and len(self.episode_ids) == 5
                and tuple(self.source_shadow_ids) == EXPECTED_SOURCE_SHADOW_IDS
                and tuple(self.source_evidence_payload_hashes)
                == EXPECTED_SOURCE_EVIDENCE_HASHES
                and self.stability_compared_fields == STABILITY_FIELDS
                and self.stability_fingerprint == PINNED_STABILITY_SHA256
                and _projection_fingerprint(self, STABILITY_FIELDS)
                == self.stability_fingerprint
                and _projection_fingerprint(self, REPLAY_EVIDENCE_PROJECTION_FIELDS)
                == PINNED_REPLAY_EVIDENCE_SHA256
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    @property
    def stability_requirement_passed(self) -> bool:
        try:
            return bool(
                self.stable_iterations == 100
                and self.stability_compared_fields == STABILITY_FIELDS
                and self.stability_fingerprint == PINNED_STABILITY_SHA256
                and _projection_fingerprint(self, STABILITY_FIELDS)
                == self.stability_fingerprint
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    @property
    def admission_replay_passed(self) -> bool:
        return bool(
            self.parity_expected_values_passed
            and self.replay_integrity_passed
            and self.stability_requirement_passed
            and self.historical_receipt_context == "predates_durable_receipts"
            and self.historical_receipt_evidence == "unknown"
            and self.historical_receipt_unknown
            == self.raw_candidates
            == EXPECTED_CANDIDATE_COUNT
            and self.synthetic_boundary_reason == SYNTHETIC_BOUNDARY_REASON
            and self.synthetic_boundary_role == "admission_normalization_only"
            and self.synthetic_boundary_market_settlement is False
            and self.exchange_fill is False
            and self.historical_executable_quote_available is False
            and self.protection_evidence_proven is False
            and self.protection_check_status == "not_run_no_fill"
            and self.protection_live_rollout_gate_passed is False
            and self.live_rollout_ready is False
            and self.controller_replay_expected_values_passed
            and self.controller_replay_stability_requirement_passed
        )

    @property
    def controller_replay_expected_values_passed(self) -> bool:
        return bool(
            self.accepted_metric == "normalized_admission_opportunities"
            and self.controller_replay_intents == 2
            and len(self.controller_replay_intent_ids) == 2
            and self.controller_replay_receipts == EXPECTED_CANDIDATE_COUNT
            and self.controller_replay_results
            == {
                "accepted": 2,
                "duplicate_episode": 17,
                "same_symbol_exposure": 3,
            }
            and self.controller_replay_event_seq_contiguous is True
            and self.controller_replay_integrity_failure is None
            and self.controller_replay_lifecycle_evidence == "absent_from_fixture"
            and self.controller_five_intent_parity_proven is False
        )

    @property
    def controller_replay_stability_requirement_passed(self) -> bool:
        return bool(
            self.controller_replay_expected_values_passed
            and self.controller_stable_iterations == 100
            and self.controller_stability_compared_fields
            == CONTROLLER_STABILITY_FIELDS
            and _projection_fingerprint(
                self,
                CONTROLLER_STABILITY_FIELDS,
            )
            == self.controller_stability_fingerprint
            and self.controller_replay_event_seq_contiguous is True
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["parity_expected_values_passed"] = self.parity_expected_values_passed
        payload["replay_integrity_passed"] = self.replay_integrity_passed
        payload["stability_requirement_passed"] = self.stability_requirement_passed
        payload["admission_replay_passed"] = self.admission_replay_passed
        payload["controller_replay_expected_values_passed"] = (
            self.controller_replay_expected_values_passed
        )
        payload["controller_replay_stability_requirement_passed"] = (
            self.controller_replay_stability_requirement_passed
        )
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


def _candidate_rows(
    fixture: Mapping[str, Any],
    window_start: float,
    window_end: float,
) -> tuple[_CandidateRow, ...]:
    raw_rows = fixture.get("candidates")
    if not isinstance(raw_rows, list):
        raise ValueError("fixture candidates must be a list")
    if len(raw_rows) != EXPECTED_CANDIDATE_COUNT:
        raise ValueError("source evidence candidate count mismatch")

    rows = []
    seen_msg_ids = set()
    seen_payload_hashes = set()
    previous_journal_timestamp = None
    for raw_row in raw_rows:
        if not isinstance(raw_row, Mapping):
            raise ValueError("candidate evidence row must be an object")
        if frozenset(raw_row) != CANDIDATE_ROW_FIELDS:
            raise ValueError(
                "candidate evidence row fields do not match the exact replay schema"
            )
        raw_candidate = raw_row.get("candidate")
        if not isinstance(raw_candidate, Mapping):
            raise ValueError("candidate payload must be an object")
        candidate_data = dict(raw_candidate)
        if frozenset(candidate_data) != CANDIDATE_ALLOWED_FIELDS:
            raise ValueError("candidate fields do not match the exact replay schema")
        if candidate_data.get("namespace") != "live":
            raise ValueError("candidate namespace must be live")
        created_at = _required_finite(
            candidate_data.get("created_at"), "candidate created_at"
        )
        if not window_start <= created_at <= window_end:
            raise ValueError(
                "candidate created_at must be inside the source evidence window"
            )
        candidate = TacticalCandidate.from_raw(candidate_data)
        if candidate.created_at != created_at:
            raise ValueError("candidate created_at is not canonical")
        msg_id = _required_text(raw_row.get("msg_id"), "msg_id")
        if msg_id in seen_msg_ids:
            raise ValueError("duplicate msg_id in source evidence")
        seen_msg_ids.add(msg_id)
        payload_hash = _required_text(
            raw_row.get("source_evidence_payload_hash"),
            "source_evidence_payload_hash",
        )
        if len(payload_hash) != 12 or any(
            character not in "0123456789abcdef" for character in payload_hash
        ):
            raise ValueError(
                "source evidence payload hash must be 12 lowercase hex characters"
            )
        if payload_hash in seen_payload_hashes:
            raise ValueError("duplicate source evidence payload hash")
        seen_payload_hashes.add(payload_hash)
        journal_timestamp = _required_finite(
            raw_row.get("journal_timestamp"), "journal_timestamp"
        )
        if created_at > journal_timestamp:
            raise ValueError("journal_timestamp must not precede created_at")
        if not window_start <= journal_timestamp <= window_end:
            raise ValueError(
                "journal_timestamp must be inside the source evidence window"
            )
        if (
            previous_journal_timestamp is not None
            and journal_timestamp <= previous_journal_timestamp
        ):
            raise ValueError("journal timestamps must be strictly increasing")
        previous_journal_timestamp = journal_timestamp
        rows.append(
            _CandidateRow(
                msg_id=msg_id,
                source_evidence_payload_hash=payload_hash,
                journal_timestamp=journal_timestamp,
                raw=candidate_data,
                candidate=candidate,
                opportunity=_opportunity_for(candidate, candidate_data),
            )
        )
    # Unique msg_id is the explicit final tie-breaker for created/journal ties.
    ordered_rows = tuple(
        sorted(
            rows,
            key=lambda row: (
                row.candidate.created_at,
                row.journal_timestamp,
                row.msg_id,
            ),
        )
    )
    previous_replay_journal_timestamp = None
    for row in ordered_rows:
        if (
            previous_replay_journal_timestamp is not None
            and row.journal_timestamp <= previous_replay_journal_timestamp
        ):
            raise ValueError(
                "replay-order journal timestamps must be strictly increasing"
            )
        previous_replay_journal_timestamp = row.journal_timestamp
    return ordered_rows


def _episode_id_for(namespace: str, symbol: str, side: str, epoch_seq: int) -> str:
    encoded = json.dumps(
        {
            "namespace": namespace,
            "symbol": symbol,
            "side": side,
            "epoch_seq": epoch_seq,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_initial_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("initial_episode_state must be an object")
    state = dict(raw)
    if frozenset(state) != INITIAL_EPISODE_FIELDS:
        raise ValueError("initial episode state fields do not match the replay schema")
    if (
        state["namespace"] != "live"
        or state["symbol"] != "PUMP-USDT"
        or state["side"] != "long"
        or type(state["epoch_seq"]) is not int
        or state["epoch_seq"] != 14
        or state["attempted"] is not True
        or state["terminal"] is not True
        or state["terminal_reason"] != "loss_streak_pause"
        or state["current_bias"] != "bullish"
        or state["neutral_seen"] is not False
        or state["last_block"] is not False
        or state["reset_pending"] is not None
    ):
        raise ValueError("initial episode state semantics are not pinned")
    last_closed_bar_ts = _required_finite(
        state["last_closed_bar_ts"], "initial episode last_closed_bar_ts"
    )
    max_observed_closed_bar_ts = _required_finite(
        state["max_observed_closed_bar_ts"],
        "initial episode max_observed_closed_bar_ts",
    )
    if (
        last_closed_bar_ts != INITIAL_EPISODE_BAR_TS
        or max_observed_closed_bar_ts != INITIAL_EPISODE_BAR_TS
        or max_observed_closed_bar_ts < last_closed_bar_ts
    ):
        raise ValueError("initial episode bar state is not coherent")
    structure_token = _required_text(
        state["last_structure_token"], "initial episode last_structure_token"
    )
    if structure_token != INITIAL_EPISODE_STRUCTURE_TOKEN:
        raise ValueError("initial episode structure token is not pinned")
    derived_episode_id = _episode_id_for(
        state["namespace"], state["symbol"], state["side"], state["epoch_seq"]
    )
    if (
        state["episode_id"] != derived_episode_id
        or derived_episode_id != INITIAL_EPISODE_ID
    ):
        raise ValueError("initial episode id is not pinned to the registry identity")
    return state


def _validate_fixture(
    fixture: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[_CandidateRow, ...]]:
    if frozenset(fixture) != FIXTURE_ROOT_FIELDS:
        raise ValueError("fixture root fields do not match the exact replay schema")
    if (
        type(fixture["schema_version"]) is not int
        or fixture["schema_version"] != EXPECTED_SCHEMA_VERSION
    ):
        raise ValueError("schema_version must be 1")
    evidence = fixture["source_evidence"]
    if not isinstance(evidence, Mapping):
        raise ValueError("source_evidence must be an object")
    if frozenset(evidence) != SOURCE_EVIDENCE_FIELDS:
        raise ValueError("source_evidence fields do not match the exact replay schema")
    _required_text(evidence["description"], "source evidence description")
    if evidence["topic"] != EXPECTED_TOPIC:
        raise ValueError("source evidence topic must be tactical_candidate.v2")
    if (
        type(evidence["window_start_epoch"]) is not int
        or type(evidence["window_end_epoch"]) is not int
        or evidence["window_start_epoch"] != EXPECTED_WINDOW_START
        or evidence["window_end_epoch"] != EXPECTED_WINDOW_END
    ):
        raise ValueError("source evidence window does not match the pinned replay window")
    if (
        type(evidence["raw_candidate_count"]) is not int
        or evidence["raw_candidate_count"] != EXPECTED_CANDIDATE_COUNT
    ):
        raise ValueError(
            "source evidence candidate count does not match the pinned window"
        )
    rows = _candidate_rows(
        fixture,
        float(evidence["window_start_epoch"]),
        float(evidence["window_end_epoch"]),
    )
    if len(rows) != evidence["raw_candidate_count"]:
        raise ValueError("source evidence candidate count mismatch")
    initial_state = _validate_initial_state(fixture["initial_episode_state"])
    return initial_state, rows


def _fixture_fingerprint(fixture: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        fixture,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


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


class _ReplayExecutor:
    def __init__(self):
        self.positions = {}


def _controller_replay_once(
    initial_state: Mapping[str, Any],
    rows: Sequence[_CandidateRow],
    temp_root: Path,
) -> dict[str, Any]:
    paths = SimpleNamespace(
        namespace="live",
        tactical_v2_events=str(temp_root / "controller-events.jsonl"),
        tactical_v2_state=str(temp_root / "controller-state.json"),
        tactical_v2_status=str(temp_root / "controller-status.json"),
    )
    store = TacticalStore(paths)
    _seed_initial_episode(store, initial_state)
    controller = TacticalV2Controller(
        executor=_ReplayExecutor(),
        config={"tactical_v2_mode": "shadow"},
        paths=paths,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        publish=None,
        now_fn=lambda: rows[-1].journal_timestamp if rows else 0.0,
    )

    results = [
        controller.handle_candidate_sync(
            row.raw,
            now=row.journal_timestamp,
            message_id=row.msg_id,
        )
        for row in rows
    ]
    events = store.read_events()
    intent_ids = tuple(
        str(event["data"]["intent_id"])
        for event in events
        if event["event_type"] == "intent_created"
    )
    reasons = Counter(result.reason for result in results)
    seqs = [event["seq"] for event in events]
    return {
        "controller_replay_intents": len(intent_ids),
        "controller_replay_intent_ids": intent_ids,
        "controller_replay_receipts": sum(
            event["event_type"] == "candidate_handled" for event in events
        ),
        "controller_replay_results": dict(sorted(reasons.items())),
        "controller_replay_event_seq_contiguous": seqs
        == list(range(1, len(seqs) + 1)),
        "controller_replay_integrity_failure": store.rebuild()["integrity_failure"],
    }


def _replay_once(
    initial_state: Mapping[str, Any],
    rows: Sequence[_CandidateRow],
    temp_root: Path,
) -> dict[str, Any]:
    paths = SimpleNamespace(
        tactical_v2_events=str(temp_root / "events.jsonl"),
        tactical_v2_state=str(temp_root / "state.json"),
    )
    store = TacticalStore(paths)
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


def _controller_stability_serialization(run: Mapping[str, Any]) -> str:
    projection = {field: run[field] for field in CONTROLLER_STABILITY_FIELDS}
    return json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
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
    initial_state, rows = _validate_fixture(fixture)
    fixture_fingerprint = _fixture_fingerprint(fixture)
    pinned_fixture_fingerprint_match = fixture_fingerprint == PINNED_FIXTURE_SHA256

    parent = None if scratch_root is None else str(Path(scratch_root))
    first_run = None
    stable_serialization = None
    first_controller_run = None
    controller_stable_serialization = None
    for _ in range(iterations):
        with TemporaryDirectory(prefix="tactical-v2-admission-", dir=parent) as temp_dir:
            iteration_root = Path(temp_dir)
            run = _replay_once(initial_state, rows, iteration_root / "normalized")
            controller_run = _controller_replay_once(
                initial_state,
                rows,
                iteration_root / "controller",
            )
        serialized = _stability_serialization(run)
        controller_serialized = _controller_stability_serialization(controller_run)
        if stable_serialization is None:
            first_run = run
            stable_serialization = serialized
        elif serialized != stable_serialization:
            raise RuntimeError("admission replay is not stable across independent runs")
        if controller_stable_serialization is None:
            first_controller_run = controller_run
            controller_stable_serialization = controller_serialized
        elif controller_serialized != controller_stable_serialization:
            raise RuntimeError(
                "controller replay is not stable across independent runs"
            )

    if (
        first_run is None
        or stable_serialization is None
        or first_controller_run is None
        or controller_stable_serialization is None
    ):
        raise RuntimeError("admission replay produced no run")
    controller_run = first_controller_run
    return AdmissionReplayReport(
        raw_candidates=first_run["raw_candidates"],
        accepted=first_run["accepted"],
        accepted_metric="normalized_admission_opportunities",
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
        fixture_fingerprint=fixture_fingerprint,
        pinned_fixture_fingerprint_match=pinned_fixture_fingerprint_match,
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
        controller_replay_intents=controller_run["controller_replay_intents"],
        controller_replay_intent_ids=tuple(controller_run["controller_replay_intent_ids"]),
        controller_replay_receipts=controller_run["controller_replay_receipts"],
        controller_replay_results=controller_run["controller_replay_results"],
        controller_replay_event_seq_contiguous=(
            controller_run["controller_replay_event_seq_contiguous"]
        ),
        controller_replay_integrity_failure=(
            controller_run["controller_replay_integrity_failure"]
        ),
        controller_replay_lifecycle_evidence="absent_from_fixture",
        controller_five_intent_parity_proven=False,
        controller_stable_iterations=iterations,
        controller_stability_compared_fields=CONTROLLER_STABILITY_FIELDS,
        controller_stability_fingerprint=hashlib.sha256(
            controller_stable_serialization.encode("ascii")
        ).hexdigest(),
        live_rollout_ready=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args(argv)
    report = replay_fixture(args.fixture, iterations=args.iterations)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.admission_replay_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
