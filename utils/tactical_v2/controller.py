"""Serialized Tactical V2 admission and shadow lifecycle controller."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Dict, Mapping, Optional

from utils.symbol import to_internal

from .entry import (
    EntryState,
    ExecutableQuote,
    classify_entry,
    pending_entry,
    reduce_quote,
)
from .exit import classify_exit, max_hold_due
from .cutover import CutoverDecision, validate_live_cutover
from .episodes import EpisodeRegistry
from .exchange import LiveExchangeAdapter, ProtectionProof
from .governor import TacticalGovernor, is_complete_integrity_proof
from .models import TACTICAL_V2_ENTRY_TTL_SECONDS, TacticalCandidate, TacticalIntent
from .shadow import ShadowAdapter
from .status import STATUS_REFRESH_SECONDS, build_status_snapshot, write_status
from .store import TacticalStore


ENTRY_VISIBILITY_GRACE_SECONDS = 15.0
ENTRY_HALT_RECHECK_SECONDS = 30.0

_SLOT_STATES = frozenset({
    "ready_for_quote",
    "submitting_entry",
    "pending_entry",
    "reconciling_entry",
    "canceling_entry",
    "partial_fill",
    "filled_unverified",
    "protected",
    "closing",
    "integrity_required",
})

_ENTRY_RECONCILE_STATES = frozenset({
    "submitting_entry",
    "pending_entry",
    "reconciling_entry",
    "canceling_entry",
    "partial_fill",
    "filled_unverified",
})

_ENTRY_RECHECK_INTEGRITY_REASONS = frozenset({
    "entry_reconciliation_unknown",
    "entry_cancel_unproven",
    "entry_fill_flat_awaiting_final_pnl",
    "entry_recovery_position_mismatch",
    "tactical_protection_incomplete",
})

_ENTRY_RECOVERY_HALT_REASONS = frozenset({
    "entry_reconciliation_unknown",
    "entry_cancel_unproven",
    "tactical_protection_incomplete",
})

_CANDIDATE_RECEIPT_FIELDS = frozenset({
    "candidate_id",
    "source_shadow_id",
    "message_id",
    "symbol",
    "side",
    "accepted",
    "reason",
    "episode_id",
    "intent_id",
    "evaluated_at",
    "replayed",
    "payload_hash",
})

_PRE_ASSIGNMENT_RECEIPT_REASONS = frozenset({
    "invalid_candidate",
    "namespace_mismatch",
    "candidate_from_future",
    "candidate_expired",
    "admission_disabled",
})

_EPISODE_RECEIPT_REASONS = frozenset({
    "duplicate_episode",
    "opposing_block",
    "capacity_skipped",
    "integrity_halt",
    "loss_streak_pause",
    "rolling_loss_pause",
    "same_symbol_exposure",
    "account_reject",
})


@dataclass(frozen=True)
class CandidateHandlingResult:
    accepted: bool
    reason: str
    intent_id: Optional[str] = None
    episode_id: Optional[str] = None


class TacticalV2Controller:
    """Own one persistent Tactical V2 state machine inside MultiExecutor."""

    def __init__(
        self,
        *,
        executor: Any,
        config: Optional[Mapping[str, Any]],
        paths: Any,
        logger: Any,
        publish: Optional[Callable[..., Any]],
        now_fn: Callable[[], float] = time.time,
    ):
        self.executor = executor
        self.config = dict(config or {})
        self.paths = paths
        self.logger = logger
        self.publish = publish
        self.now_fn = now_fn
        self.namespace = str(paths.namespace).strip().lower()
        self.requested_mode = str(
            self.config.get("tactical_v2_mode", "off")
        ).strip().lower()
        self.cutover_decision = CutoverDecision(True, "cutover_not_required")
        self.mode = self.requested_mode
        if self.requested_mode == "live" and self.namespace == "live":
            self.cutover_decision = validate_live_cutover(
                paths,
                namespace=self.namespace,
                sidecar_bot_owner_id=(
                    os.getenv("SIDECAR_BOT_INSTANCE_ID") or "stlive"
                ).strip(),
            )
            if not self.cutover_decision.allowed:
                self.mode = "shadow"
        self.store = TacticalStore(paths)
        self.episodes = EpisodeRegistry(self.store, namespace=self.namespace)
        self.governor = TacticalGovernor(store=self.store, now_fn=now_fn)
        self.shadow = ShadowAdapter()
        self.live = LiveExchangeAdapter(
            executor=executor,
            store=self.store,
            governor=self.governor,
        )
        self._lock = asyncio.Lock()
        self._intents: Dict[str, Dict[str, Any]] = {}
        self._candidate_receipts = []
        self._candidate_receipts_by_message_id: Dict[str, Dict[str, Any]] = {}
        self._candidate_receipts_by_payload_hash: Dict[str, Dict[str, Any]] = {}
        self._conflicting_candidate_receipt_message_ids = set()
        self._conflicting_candidate_receipt_payload_hashes = set()
        self._quarantined_candidate_receipt_candidates: Dict[str, set] = {}
        self._candidate_receipt_integrity_halt: Optional[Dict[str, Any]] = None
        self._handled_message_identity_conflicts = set()
        self._receipt_intent_ids = set()
        self._candidate_handling_gaps: Dict[str, Dict[str, Any]] = {}
        self._unknown_replays: Dict[str, CandidateHandlingResult] = {}
        self._episode_outcomes: Dict[str, int] = {}
        self._parity_mismatches = 0
        self._last_status_write_at = 0.0
        self._entry_io_inflight = set()
        self._entry_pnl_recovery_queued = set()
        self._restore()
        self._refresh_status(force=True)

    async def handle_candidate(
        self,
        raw: Any,
        *,
        now: Optional[float] = None,
        message_id: Optional[str] = None,
        replayed: bool = False,
    ) -> CandidateHandlingResult:
        evaluated_at = float(self.now_fn()) if now is None else float(now)
        receipt_context = self._candidate_receipt_context(raw, message_id=message_id)
        handled = self._handled_candidate_result(
            receipt_context,
            evaluated_at=evaluated_at,
        )
        if handled is not None:
            return handled
        gap = self._candidate_handling_gap(receipt_context)
        if gap is not None:
            if (
                receipt_context.get("message_id")
                and gap.get("payload_hash") != receipt_context.get("payload_hash")
            ):
                incident_recorded = self._record_message_identity_conflict(
                    message_id=receipt_context["message_id"],
                    stored_payload_hash=gap.get("payload_hash"),
                    incoming_payload_hash=receipt_context.get("payload_hash"),
                )
                if incident_recorded:
                    self._refresh_status(force=True, now=evaluated_at)
                return CandidateHandlingResult(False, "message_identity_conflict")
            return self._remember_unknown_replay(
                receipt_context,
                evaluated_at=evaluated_at,
            )
        if self._unreceipted_intent_for_candidate(receipt_context) is not None:
            return self._remember_unknown_replay(
                receipt_context,
                evaluated_at=evaluated_at,
            )
        if replayed:
            return self._remember_unknown_replay(
                receipt_context,
                evaluated_at=evaluated_at,
            )

        def finish(
            result: CandidateHandlingResult,
            candidate: Optional[TacticalCandidate] = None,
        ) -> CandidateHandlingResult:
            return self._persist_candidate_handled(
                receipt_context,
                candidate=candidate,
                result=result,
                evaluated_at=evaluated_at,
                replayed=replayed,
            )

        if not isinstance(raw, Mapping):
            return finish(CandidateHandlingResult(False, "invalid_candidate"))
        raw_namespace = self._safe_text(raw.get("namespace")).lower()
        if raw_namespace != self.namespace:
            return finish(CandidateHandlingResult(False, "namespace_mismatch"))
        try:
            candidate = TacticalCandidate.from_raw(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            self._log_warning("invalid Tactical V2 candidate: %s", exc)
            return finish(CandidateHandlingResult(False, "invalid_candidate"))
        if evaluated_at < candidate.created_at:
            return finish(
                CandidateHandlingResult(False, "candidate_from_future"),
                candidate,
            )
        if evaluated_at - candidate.created_at > TACTICAL_V2_ENTRY_TTL_SECONDS:
            return finish(CandidateHandlingResult(False, "candidate_expired"), candidate)
        if self.mode == "off":
            return finish(CandidateHandlingResult(False, "admission_disabled"), candidate)

        structure = self._structure_from(raw)
        assignment = self.episodes.assign(candidate, structure)
        if not assignment.eligible:
            return finish(
                CandidateHandlingResult(
                    False,
                    assignment.reason,
                    episode_id=assignment.episode_id,
                ),
                candidate,
            )

        same_symbol = self._symbol_occupied(candidate.symbol)
        admission = self.governor.can_open(
            now=evaluated_at,
            active_count=self._active_slot_count(),
            pending_count=0,
            same_symbol_state=same_symbol,
            integrity_state=self._has_live_integrity_required(),
        )
        lane = self.mode
        if not admission.allowed:
            reason = (
                "capacity_skipped"
                if admission.reason == "capacity_full"
                else admission.reason
            )
            self._consume_episode(
                assignment.episode_id,
                reason,
                candidate_handling_gap={
                    "candidate_id": candidate.candidate_id,
                    "message_id": receipt_context["message_id"],
                    "payload_hash": receipt_context["payload_hash"],
                },
            )
            return finish(
                CandidateHandlingResult(
                    False,
                    reason,
                    episode_id=assignment.episode_id,
                ),
                candidate,
            )

        intent = TacticalIntent.from_candidate(candidate, assignment.episode_id)
        shadow_rejection = (
            self._shadow_admission_reason(candidate.symbol)
            if self.mode == "live"
            else None
        )
        record = self._register_intent(
            intent,
            lane=lane,
            replayed=replayed,
            evaluated_at=evaluated_at,
        )
        if shadow_rejection:
            self._persist_shadow_projection(
                record,
                "entry_terminal",
                evaluated_at,
                terminal_reason=shadow_rejection,
            )
        return finish(
            CandidateHandlingResult(
                True,
                "accepted",
                intent_id=intent.intent_id,
                episode_id=intent.episode_id,
            ),
            candidate,
        )

    def _persist_candidate_handled(
        self,
        context: Mapping[str, Any],
        *,
        candidate: Optional[TacticalCandidate],
        result: CandidateHandlingResult,
        evaluated_at: float,
        replayed: bool,
    ) -> CandidateHandlingResult:
        receipt = {
            "candidate_id": (
                candidate.candidate_id if candidate is not None else context["candidate_id"]
            ),
            "source_shadow_id": (
                candidate.source_shadow_id
                if candidate is not None
                else context["source_shadow_id"]
            ),
            "message_id": context["message_id"],
            "symbol": candidate.symbol if candidate is not None else context["symbol"],
            "side": candidate.side if candidate is not None else context["side"],
            "accepted": bool(result.accepted),
            "reason": str(result.reason),
            "episode_id": result.episode_id,
            "intent_id": result.intent_id,
            "evaluated_at": float(evaluated_at),
            "replayed": bool(replayed),
            "payload_hash": context["payload_hash"],
        }
        try:
            event = self.store.append(
                "candidate_handled",
                receipt,
                emitted_at=evaluated_at,
            )
        except Exception:
            self._remember_candidate_handling_gap(
                context,
                episode_id=result.episode_id,
            )
            self._refresh_status(force=True, now=evaluated_at)
            raise
        self._remember_candidate_receipt(
            receipt,
            incident_id=self._optional_text(event.get("event_id")),
        )
        self._refresh_status(force=True, now=evaluated_at)
        return result

    def _handled_candidate_result(
        self,
        context: Mapping[str, Any],
        *,
        evaluated_at: float,
    ) -> Optional[CandidateHandlingResult]:
        message_id = context.get("message_id")
        if message_id:
            receipt = self._candidate_receipts_by_message_id.get(str(message_id))
            if (
                receipt is not None
                and receipt.get("payload_hash") != context.get("payload_hash")
            ):
                incident_recorded = self._record_message_identity_conflict(
                    message_id=str(message_id),
                    stored_payload_hash=receipt.get("payload_hash"),
                    incoming_payload_hash=context.get("payload_hash"),
                )
                if incident_recorded:
                    self._refresh_status(force=True, now=evaluated_at)
                return CandidateHandlingResult(False, "message_identity_conflict")
        else:
            payload_hash = str(context.get("payload_hash") or "")
            receipt = (
                None
                if payload_hash in self._conflicting_candidate_receipt_payload_hashes
                else self._candidate_receipts_by_payload_hash.get(payload_hash)
            )
        if receipt is None:
            return None
        return CandidateHandlingResult(
            bool(receipt.get("accepted")),
            str(receipt.get("reason") or "unknown_handling_evidence"),
            intent_id=self._optional_text(receipt.get("intent_id")),
            episode_id=self._optional_text(receipt.get("episode_id")),
        )

    def _remember_unknown_replay(
        self,
        context: Mapping[str, Any],
        *,
        evaluated_at: float,
    ) -> CandidateHandlingResult:
        identity = self._candidate_handling_identity(context)
        previous = self._unknown_replays.get(identity)
        if previous is not None:
            self._refresh_status(force=True, now=evaluated_at)
            return previous

        gap = self._candidate_handling_gap(context)
        matched_intent = self._unreceipted_intent_for_candidate(context)
        if gap is not None:
            result = CandidateHandlingResult(
                False,
                "unknown_handling_evidence",
                intent_id=(
                    matched_intent.intent_id if matched_intent is not None else None
                ),
                episode_id=(
                    self._optional_text(gap.get("episode_id"))
                    or (
                        matched_intent.episode_id
                        if matched_intent is not None
                        else None
                    )
                ),
            )
        else:
            result = CandidateHandlingResult(
                False,
                "unknown_handling_evidence",
                intent_id=(
                    matched_intent.intent_id if matched_intent is not None else None
                ),
                episode_id=(
                    matched_intent.episode_id if matched_intent is not None else None
                ),
            )
        self._unknown_replays[identity] = result
        self._refresh_status(force=True, now=evaluated_at)
        return result

    def _candidate_handling_gap(
        self,
        context: Mapping[str, Any],
    ) -> Optional[Dict[str, Any]]:
        return self._candidate_handling_gaps.get(
            self._candidate_handling_identity(context)
        )

    def _remember_candidate_handling_gap(
        self,
        raw: Mapping[str, Any],
        *,
        episode_id: Optional[str],
    ) -> None:
        candidate_id = raw.get("candidate_id")
        message_id = raw.get("message_id")
        payload_hash = raw.get("payload_hash")
        if not isinstance(candidate_id, str):
            return
        if message_id is not None and (
            not isinstance(message_id, str) or not message_id
        ):
            return
        if (
            not isinstance(payload_hash, str)
            or len(payload_hash) != 64
            or any(char not in "0123456789abcdef" for char in payload_hash)
        ):
            return
        gap = {
            "candidate_id": candidate_id,
            "message_id": message_id,
            "payload_hash": payload_hash,
            "episode_id": self._optional_text(episode_id),
        }
        self._candidate_handling_gaps[
            self._candidate_handling_identity(gap)
        ] = gap

    def _unreceipted_intent_for_candidate(
        self,
        context: Mapping[str, Any],
    ) -> Optional[TacticalIntent]:
        candidate_id = str(context.get("candidate_id") or "")
        if not candidate_id:
            return None
        for record in self._intents.values():
            intent = record.get("intent")
            if (
                isinstance(intent, TacticalIntent)
                and intent.candidate_id == candidate_id
                and intent.intent_id not in self._receipt_intent_ids
            ):
                return intent
        return None

    def _remember_candidate_receipt(
        self,
        raw: Mapping[str, Any],
        *,
        halted_at: Optional[float] = None,
        incident_id: Optional[str] = None,
    ) -> None:
        receipt = dict(raw)
        validation_error = self._candidate_receipt_validation_error(receipt)
        if validation_error is not None:
            self._quarantine_candidate_receipt(receipt)
            self._activate_candidate_receipt_integrity_halt(
                "candidate_receipt_invalid",
                evidence={
                    "validation_error": validation_error,
                    "message_id": self._optional_text(receipt.get("message_id")),
                    "payload_hash": self._optional_text(receipt.get("payload_hash")),
                },
                halted_at=halted_at,
                incident_id=incident_id,
            )
            return
        message_id = self._optional_text(receipt.get("message_id"))
        payload_hash = self._optional_text(receipt.get("payload_hash"))
        if message_id in self._conflicting_candidate_receipt_message_ids:
            self._quarantine_candidate_receipt(receipt)
            self._activate_candidate_receipt_integrity_halt(
                "candidate_receipt_message_conflict",
                evidence={
                    "message_id": message_id,
                    "conflicting_payload_hash": payload_hash,
                    "known_conflict": True,
                },
                halted_at=halted_at,
                incident_id=incident_id,
            )
            return
        if (
            not message_id
            and payload_hash in self._conflicting_candidate_receipt_payload_hashes
        ):
            self._quarantine_candidate_receipt(receipt)
            self._activate_candidate_receipt_integrity_halt(
                "candidate_receipt_payload_conflict",
                evidence={
                    "payload_hash": payload_hash,
                    "known_conflict": True,
                },
                halted_at=halted_at,
                incident_id=incident_id,
            )
            return
        existing = (
            self._candidate_receipts_by_message_id.get(message_id)
            if message_id
            else None
        )
        if existing == receipt:
            return
        if existing is not None and existing != receipt:
            self._conflicting_candidate_receipt_message_ids.add(message_id)
            self._quarantine_candidate_receipt(existing)
            self._quarantine_candidate_receipt(receipt)
            self._candidate_receipts = [
                item
                for item in self._candidate_receipts
                if self._optional_text(item.get("message_id")) != message_id
            ]
            self._rebuild_candidate_receipt_indexes()
            self._activate_candidate_receipt_integrity_halt(
                "candidate_receipt_message_conflict",
                evidence={
                    "message_id": message_id,
                    "stored_payload_hash": existing["payload_hash"],
                    "conflicting_payload_hash": receipt["payload_hash"],
                },
                halted_at=halted_at,
                incident_id=incident_id,
            )
            return

        existing_fallback = None
        if payload_hash:
            existing_fallback = next(
                (
                    item
                    for item in self._candidate_receipts
                    if self._optional_text(item.get("message_id")) is None
                    and self._optional_text(item.get("payload_hash")) == payload_hash
                ),
                None,
            )
        if existing_fallback is not None and message_id:
            if not self._candidate_receipt_decisions_match(
                existing_fallback,
                receipt,
            ):
                self._conflicting_candidate_receipt_payload_hashes.add(payload_hash)
                self._quarantine_candidate_receipt(existing_fallback)
                self._candidate_receipts = [
                    item
                    for item in self._candidate_receipts
                    if not (
                        self._optional_text(item.get("message_id")) is None
                        and self._optional_text(item.get("payload_hash"))
                        == payload_hash
                    )
                ]
                self._rebuild_candidate_receipt_indexes()
                self._activate_candidate_receipt_integrity_halt(
                    "candidate_receipt_payload_conflict",
                    evidence={
                        "payload_hash": payload_hash,
                        "message_id": message_id,
                        "message_reason": receipt["reason"],
                        "fallback_reason": existing_fallback["reason"],
                    },
                    halted_at=halted_at,
                    incident_id=incident_id,
                )
            existing_fallback = None
        if not message_id and payload_hash:
            conflicting_message = next(
                (
                    item
                    for item in self._candidate_receipts
                    if self._optional_text(item.get("message_id")) is not None
                    and self._optional_text(item.get("payload_hash")) == payload_hash
                    and not self._candidate_receipt_decisions_match(item, receipt)
                ),
                None,
            )
            if conflicting_message is not None:
                conflicting_message_id = self._optional_text(
                    conflicting_message.get("message_id")
                )
                self._conflicting_candidate_receipt_payload_hashes.add(payload_hash)
                self._quarantine_candidate_receipt(receipt)
                self._activate_candidate_receipt_integrity_halt(
                    "candidate_receipt_payload_conflict",
                    evidence={
                        "payload_hash": payload_hash,
                        "message_id": conflicting_message_id,
                        "message_reason": conflicting_message["reason"],
                        "fallback_reason": receipt["reason"],
                    },
                    halted_at=halted_at,
                    incident_id=incident_id,
                )
                return
        if existing_fallback == receipt:
            return
        if existing_fallback is not None and existing_fallback != receipt:
            self._conflicting_candidate_receipt_payload_hashes.add(payload_hash)
            self._quarantine_candidate_receipt(existing_fallback)
            self._quarantine_candidate_receipt(receipt)
            self._candidate_receipts = [
                item
                for item in self._candidate_receipts
                if not (
                    self._optional_text(item.get("message_id")) is None
                    and self._optional_text(item.get("payload_hash")) == payload_hash
                )
            ]
            self._rebuild_candidate_receipt_indexes()
            self._activate_candidate_receipt_integrity_halt(
                "candidate_receipt_payload_conflict",
                evidence={
                    "payload_hash": payload_hash,
                    "stored_reason": existing_fallback["reason"],
                    "conflicting_reason": receipt["reason"],
                },
                halted_at=halted_at,
                incident_id=incident_id,
            )
            return

        self._candidate_receipts.append(receipt)
        intent_id = self._optional_text(receipt.get("intent_id"))
        if message_id:
            self._candidate_receipts_by_message_id.setdefault(message_id, receipt)
        if payload_hash:
            self._candidate_receipts_by_payload_hash.setdefault(payload_hash, receipt)
        if intent_id:
            self._receipt_intent_ids.add(intent_id)
        self._unknown_replays.pop(
            self._candidate_handling_identity(receipt),
            None,
        )
        self._candidate_handling_gaps.pop(
            self._candidate_handling_identity(receipt),
            None,
        )

    @staticmethod
    def _candidate_receipt_decisions_match(
        first: Mapping[str, Any],
        second: Mapping[str, Any],
    ) -> bool:
        delivery_fields = {"message_id", "evaluated_at", "replayed"}
        return all(
            first.get(field) == second.get(field)
            for field in _CANDIDATE_RECEIPT_FIELDS - delivery_fields
        )

    def _quarantine_candidate_receipt(self, receipt: Mapping[str, Any]) -> None:
        identity = self._candidate_handling_identity(receipt)
        candidate_id = self._optional_text(receipt.get("candidate_id"))
        candidates = self._quarantined_candidate_receipt_candidates.setdefault(
            identity,
            set(),
        )
        if candidate_id:
            candidates.add(candidate_id)

    def _activate_candidate_receipt_integrity_halt(
        self,
        reason: str,
        *,
        evidence: Mapping[str, Any],
        halted_at: Optional[float],
        incident_id: Optional[str],
    ) -> None:
        if self._candidate_receipt_integrity_halt is None:
            effective_halted_at = (
                float(self.now_fn()) if halted_at is None else float(halted_at)
            )
            self._candidate_receipt_integrity_halt = {
                "reason": str(reason),
                "evidence": dict(evidence),
                "halted_at": effective_halted_at,
                "incident_id": (
                    incident_id
                    or self._candidate_receipt_incident_fingerprint(
                        reason,
                        evidence,
                        effective_halted_at,
                    )
                ),
            }

    @staticmethod
    def _candidate_receipt_incident_fingerprint(
        reason: str,
        evidence: Mapping[str, Any],
        halted_at: float,
    ) -> str:
        encoded = json.dumps(
            {
                "reason": str(reason),
                "evidence": dict(evidence),
                "halted_at": float(halted_at),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def acknowledge_candidate_receipt_integrity(
        self,
        reconciliation_id: str,
        proof: Mapping[str, Any],
    ) -> bool:
        if (
            not isinstance(reconciliation_id, str)
            or not reconciliation_id
            or reconciliation_id != reconciliation_id.strip()
            or not is_complete_integrity_proof(proof)
        ):
            return False
        halt = self._candidate_receipt_integrity_halt
        if halt is None:
            return True
        acknowledged_at = float(self.now_fn())
        event = self.store.append(
            "candidate_receipt_integrity_acknowledged",
            {
                "reconciliation_id": reconciliation_id,
                "incident_id": halt["incident_id"],
                "proof": dict(proof),
                "acknowledged_at": acknowledged_at,
            },
            emitted_at=acknowledged_at,
        )
        self._apply_candidate_receipt_integrity_acknowledgement(
            event.get("data") or {}
        )
        self._refresh_status(force=True, now=acknowledged_at)
        return self._candidate_receipt_integrity_halt is None

    def _apply_candidate_receipt_integrity_acknowledgement(
        self,
        data: Mapping[str, Any],
    ) -> None:
        reconciliation_id = data.get("reconciliation_id")
        proof = data.get("proof")
        incident_id = data.get("incident_id")
        acknowledged_at = data.get("acknowledged_at")
        if (
            not isinstance(reconciliation_id, str)
            or not reconciliation_id
            or reconciliation_id != reconciliation_id.strip()
            or not isinstance(incident_id, str)
            or not incident_id
            or isinstance(acknowledged_at, bool)
            or not isinstance(acknowledged_at, (int, float))
            or not math.isfinite(float(acknowledged_at))
            or not is_complete_integrity_proof(proof)
        ):
            return
        halt = self._candidate_receipt_integrity_halt
        if halt is not None and halt.get("incident_id") == incident_id:
            self._candidate_receipt_integrity_halt = None

    def _record_message_identity_conflict(
        self,
        *,
        message_id: Any,
        stored_payload_hash: Any,
        incoming_payload_hash: Any,
    ) -> bool:
        evidence = {
            "message_id": str(message_id),
            "stored_payload_hash": stored_payload_hash,
            "incoming_payload_hash": incoming_payload_hash,
        }
        identity = self._message_identity_conflict_identity(evidence)
        if identity in self._handled_message_identity_conflicts:
            return False
        activated = self.governor.activate_integrity_halt_if_clear(
            "message_identity_conflict",
            evidence=evidence,
        )
        if activated:
            self._handled_message_identity_conflicts.add(identity)
        return activated

    @staticmethod
    def _message_identity_conflict_identity(
        evidence: Mapping[str, Any],
    ) -> tuple[str, str, str]:
        return (
            str(evidence.get("message_id") or ""),
            str(evidence.get("stored_payload_hash") or ""),
            str(evidence.get("incoming_payload_hash") or ""),
        )

    def _rebuild_candidate_receipt_indexes(self) -> None:
        self._candidate_receipts_by_message_id = {}
        self._candidate_receipts_by_payload_hash = {}
        self._receipt_intent_ids = set()
        for receipt in self._candidate_receipts:
            message_id = self._optional_text(receipt.get("message_id"))
            payload_hash = self._optional_text(receipt.get("payload_hash"))
            intent_id = self._optional_text(receipt.get("intent_id"))
            if message_id:
                self._candidate_receipts_by_message_id.setdefault(message_id, receipt)
            if payload_hash:
                self._candidate_receipts_by_payload_hash.setdefault(payload_hash, receipt)
            if intent_id:
                self._receipt_intent_ids.add(intent_id)

    def _candidate_receipt_validation_error(
        self,
        receipt: Mapping[str, Any],
    ) -> Optional[str]:
        if set(receipt) != _CANDIDATE_RECEIPT_FIELDS:
            return "schema_fields"
        for field in ("candidate_id", "source_shadow_id", "symbol", "side", "reason"):
            if not isinstance(receipt.get(field), str):
                return f"{field}_type"
        episode_id = receipt.get("episode_id")
        if receipt["reason"] in _EPISODE_RECEIPT_REASONS and (
            episode_id is None
            or (isinstance(episode_id, str) and not episode_id.strip())
        ):
            return (
                "duplicate_episode_episode_id"
                if receipt["reason"] == "duplicate_episode"
                else "episode_reason_episode_id"
            )
        for field in ("message_id", "episode_id", "intent_id"):
            value = receipt.get(field)
            if value is not None and (
                not isinstance(value, str) or not value or value != value.strip()
            ):
                return f"{field}_type"
        if type(receipt.get("accepted")) is not bool:
            return "accepted_type"
        if type(receipt.get("replayed")) is not bool:
            return "replayed_type"
        evaluated_at = receipt.get("evaluated_at")
        if (
            isinstance(evaluated_at, bool)
            or not isinstance(evaluated_at, (int, float))
            or not math.isfinite(float(evaluated_at))
        ):
            return "evaluated_at_type"
        payload_hash = receipt.get("payload_hash")
        if (
            not isinstance(payload_hash, str)
            or len(payload_hash) != 64
            or any(char not in "0123456789abcdef" for char in payload_hash)
        ):
            return "payload_hash_type"

        accepted = receipt["accepted"]
        intent_id = receipt.get("intent_id")
        if not accepted:
            if intent_id is not None:
                return "rejected_intent_id"
            reason = receipt["reason"]
            if reason in _PRE_ASSIGNMENT_RECEIPT_REASONS:
                return "pre_assignment_episode_id" if episode_id is not None else None
            if reason in _EPISODE_RECEIPT_REASONS:
                return (
                    None
                    if self.episodes.matches_episode(
                        episode_id,
                        receipt["symbol"],
                        receipt["side"],
                    )
                    else "rejected_episode_mismatch"
                )
            return "rejected_reason"
        if receipt["reason"] != "accepted":
            return "accepted_reason"
        if intent_id is None or episode_id is None:
            return "accepted_identity"

        record = self._intents.get(intent_id)
        intent = record.get("intent") if isinstance(record, Mapping) else None
        if not isinstance(intent, TacticalIntent):
            return "accepted_intent_missing"
        expected = {
            "candidate_id": intent.candidate_id,
            "source_shadow_id": intent.source_shadow_id,
            "symbol": intent.symbol,
            "side": intent.side,
            "episode_id": intent.episode_id,
            "intent_id": intent.intent_id,
        }
        if any(receipt.get(field) != value for field, value in expected.items()):
            return "accepted_intent_mismatch"
        if not self.episodes.matches_episode(
            episode_id,
            receipt["symbol"],
            receipt["side"],
        ):
            return "accepted_episode_mismatch"
        return None

    @staticmethod
    def _candidate_handling_identity(context: Mapping[str, Any]) -> str:
        message_id = str(context.get("message_id") or "")
        if message_id:
            return f"message:{message_id}"
        return f"payload:{str(context.get('payload_hash') or '')}"

    @classmethod
    def _candidate_receipt_context(
        cls,
        raw: Any,
        *,
        message_id: Optional[str],
    ) -> Dict[str, Any]:
        fields = raw if isinstance(raw, Mapping) else {}
        symbol = cls._receipt_text(fields.get("symbol"))
        try:
            symbol = to_internal(symbol) if symbol else ""
        except (AttributeError, TypeError, ValueError):
            symbol = ""
        encoded = json.dumps(
            cls._json_safe(raw),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        return {
            "candidate_id": cls._receipt_text(fields.get("candidate_id")),
            "source_shadow_id": cls._receipt_text(fields.get("source_shadow_id")),
            "message_id": cls._receipt_text(message_id) or None,
            "symbol": symbol,
            "side": cls._receipt_text(fields.get("side")).lower(),
            "payload_hash": hashlib.sha256(encoded).hexdigest(),
        }

    @classmethod
    def _json_safe(cls, value: Any, _active_container_ids: Optional[set] = None) -> Any:
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else {"non_finite_float": str(value)}
        if isinstance(value, bytes):
            return {"bytes_hex": value.hex()}
        if isinstance(value, (Mapping, list, tuple, set, frozenset)):
            active = set() if _active_container_ids is None else _active_container_ids
            container_id = id(value)
            if container_id in active:
                return {"recursive_reference": True}
            active.add(container_id)
            try:
                if isinstance(value, Mapping):
                    return {
                        cls._safe_text(key): cls._json_safe(item, active)
                        for key, item in value.items()
                    }
                if isinstance(value, (list, tuple)):
                    return [cls._json_safe(item, active) for item in value]
                items = [cls._json_safe(item, active) for item in value]
                return sorted(
                    items,
                    key=lambda item: json.dumps(
                        item,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                )
            finally:
                active.remove(container_id)
        return {
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": cls._safe_text(value),
        }

    @staticmethod
    def _safe_text(value: Any) -> str:
        if value is None:
            return ""
        try:
            return str(value).strip()
        except Exception:
            return ""

    @staticmethod
    def _receipt_text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    @classmethod
    def _optional_text(cls, value: Any) -> Optional[str]:
        normalized = cls._safe_text(value)
        return normalized or None

    def _register_intent(
        self,
        intent: TacticalIntent,
        *,
        lane: str,
        replayed: bool,
        evaluated_at: float,
        state: str = "ready_for_quote",
        terminal_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = {
            "intent": intent,
            "entry_state": None,
            "state": state,
            "lane": lane,
            "replayed": bool(replayed),
            "updated_at": evaluated_at,
            "shadow_entry_state": None,
            "shadow_state": "ready_for_quote" if lane == "live" else None,
            "shadow_updated_at": evaluated_at if lane == "live" else None,
            "shadow_filled": False,
            "parity_category": None,
        }
        if terminal_reason:
            record["terminal_reason"] = terminal_reason
        self._intents[intent.intent_id] = record
        self.store.append(
            "intent_created",
            {
                "intent_id": intent.intent_id,
                "episode_id": intent.episode_id,
                "intent": asdict(intent),
                "state": state,
                "lane": lane,
                "shadow_state": "ready_for_quote" if lane == "live" else None,
                "terminal_reason": terminal_reason,
                "replayed": bool(replayed),
                "updated_at": evaluated_at,
            },
            emitted_at=evaluated_at,
        )
        return record

    async def handle_quote(
        self,
        symbol: str,
        raw_quote: Mapping[str, Any],
        *,
        now: Optional[float] = None,
    ) -> None:
        if self.mode not in {"shadow", "live"}:
            return
        evaluated_at = float(self.now_fn()) if now is None else float(now)
        try:
            quote = ExecutableQuote(
                bid=raw_quote.get("bid"),
                ask=raw_quote.get("ask"),
                observed_at=raw_quote.get(
                    "timestamp", raw_quote.get("observed_at", evaluated_at)
                ),
            )
        except (TypeError, ValueError):
            return

        normalized = to_internal(symbol)
        async with self._lock:
            live_ready_ids = [
                intent_id
                for intent_id, record in self._intents.items()
                if self.mode == "live"
                and record.get("lane") == "live"
                and record["intent"].symbol == normalized
                and record["state"] == "ready_for_quote"
            ]
            cancel_requests = []
            if self.mode == "live":
                for intent_id, record in self._intents.items():
                    state = record.get("entry_state")
                    if (
                        record.get("lane") != "live"
                        or record["intent"].symbol != normalized
                        or record["state"] != "pending_entry"
                        or not isinstance(state, EntryState)
                    ):
                        continue
                    transition = reduce_quote(state, quote, now=evaluated_at)
                    if transition.command == "cancel_entry":
                        cancel_requests.append((
                            intent_id,
                            transition.terminal_reason or transition.reason or "entry_invalidated",
                        ))
        for intent_id in live_ready_ids:
            await self._start_live_entry(intent_id, quote, evaluated_at)
        for intent_id, reason in cancel_requests:
            await self._cancel_live_entry(
                intent_id,
                reason=reason,
                evaluated_at=evaluated_at,
            )

        async with self._lock:
            for intent_id, record in list(self._intents.items()):
                if record["intent"].symbol != normalized:
                    continue
                self._advance_shadow_lane(intent_id, quote, evaluated_at)

    async def handle_structure(
        self,
        symbol: str,
        tech: Mapping[str, Any],
        *,
        now: Optional[float] = None,
    ) -> None:
        evaluated_at = float(self.now_fn()) if now is None else float(now)
        structure = self._structure_from(tech)
        for side in ("long", "short"):
            self.episodes.observe(symbol, side, structure)
        if self.mode not in {"shadow", "live"}:
            return
        normalized = to_internal(symbol)
        invalid_ids = []
        async with self._lock:
            for intent_id, record in self._intents.items():
                intent = record["intent"]
                block_key = "tf_15m_block_long" if intent.side == "long" else "tf_15m_block_short"
                if intent.symbol != normalized or not structure.get(block_key):
                    continue
                self._invalidate_shadow_entry(
                    record,
                    reason="structure_invalidated",
                    evaluated_at=evaluated_at,
                )
                if record.get("lane") != "live":
                    continue
                if record["state"] == "ready_for_quote":
                    self._set_terminal_record(
                        record,
                        "structure_invalidated",
                        evaluated_at,
                    )
                    self._terminalize(
                        intent,
                        "structure_invalidated",
                        evaluated_at,
                    )
                elif record["state"] in _ENTRY_RECONCILE_STATES:
                    invalid_ids.append(intent_id)
        for intent_id in invalid_ids:
            await self._cancel_live_entry(
                intent_id,
                reason="structure_invalidated",
                evaluated_at=evaluated_at,
            )

    async def handle_pnl_resolution(self, payload: Mapping[str, Any]) -> None:
        received_at = float(self.now_fn())
        attribution = payload.get("attribution")
        owner = str(
            payload.get("strategy_owner")
            or (attribution.get("strategy_owner") if isinstance(attribution, Mapping) else "")
            or ""
        )
        intent_id = str(payload.get("intent_id") or "")
        if owner and owner != "tactical_v2":
            return
        if owner != "tactical_v2" and intent_id not in self._intents:
            return
        normalized = self._normalize_pnl_resolution(payload)
        recovery_record = self._intents.get(intent_id)
        recovery_final = self._is_entry_flat_recovery_record(
            intent_id,
            recovery_record,
        )
        if recovery_final and not self._matches_entry_flat_recovery_final(
            recovery_record,
            normalized,
        ):
            self._refresh_status(force=True, now=received_at)
            return
        result = self.governor.apply_final(normalized)
        if not result.accepted:
            if result.reason != "duplicate_resolution":
                self._refresh_status(force=True)
                return
            durable = self.governor.resolution_by_id(
                str(normalized.get("resolution_id") or "")
            )
            if durable is None:
                self._refresh_status(force=True)
                return
            normalized = durable
            intent_id = str(normalized.get("intent_id") or intent_id)
        async with self._lock:
            record = self._intents.get(intent_id)
            if record is not None and record.get("state") in {
                "protected",
                "closing",
                "integrity_required",
                "exchange_closed_pending_pnl",
                "closed_final",
            }:
                if record.get("state") in {
                    "protected",
                    "closing",
                    "integrity_required",
                }:
                    self._consume_episode(
                        record["intent"].episode_id,
                        str(normalized.get("close_reason") or "pnl_resolved"),
                    )
                if (
                    record.get("state") == "closed_final"
                    and record.get("resolution_id") == normalized.get("resolution_id")
                ):
                    self._refresh_status(force=False, now=received_at)
                    return
                self._persist_record_state(
                    record,
                    "closed_final",
                    received_at,
                    resolution_id=normalized.get("resolution_id"),
                    resolved_at=normalized.get("resolved_at"),
                    position_id=normalized.get("position_id"),
                    entry_request_id=normalized.get("entry_request_id"),
                    final_pnl_usdt=normalized.get("pnl_usdt"),
                    close_reason=normalized.get("close_reason"),
                    strategy_owner="tactical_v2",
                    plan_hash=normalized.get("plan_hash"),
                    tp_algo_ids=normalized.get("tp_algo_ids"),
                    sl_algo_ids=normalized.get("sl_algo_ids"),
                )
                if recovery_final:
                    self._clear_entry_flat_recovery_halt(
                        intent_id,
                        normalized,
                    )
        self._refresh_status(force=True, now=received_at)

    def should_replay_durable_pnl_final(self, payload: Mapping[str, Any]) -> bool:
        """Limit crash replay to intents that are durably waiting for final PnL."""
        attribution = payload.get("attribution")
        owner = str(
            payload.get("strategy_owner")
            or (
                attribution.get("strategy_owner")
                if isinstance(attribution, Mapping)
                else ""
            )
            or ""
        )
        intent_id = str(
            payload.get("intent_id")
            or (
                attribution.get("intent_id")
                if isinstance(attribution, Mapping)
                else ""
            )
            or ""
        )
        if owner != "tactical_v2" or not intent_id:
            return False
        record = self._intents.get(intent_id)
        if record is None:
            return False
        if record.get("state") == "exchange_closed_pending_pnl":
            return True
        if (
            record.get("state") == "closed_final"
            and payload.get("pnl_delivery_required") is True
            and record.get("resolution_id") == payload.get("resolution_id")
        ):
            return True
        if (
            record.get("state") == "integrity_required"
            and record.get("pnl_recovery_queued") is True
        ):
            return True
        return (
            record.get("state") == "integrity_required"
            and record.get("integrity_reason")
            == "entry_fill_flat_awaiting_final_pnl"
        )

    async def handle_pnl_mismatch(self, payload: Mapping[str, Any]) -> None:
        attribution = payload.get("attribution")
        owner = str(
            payload.get("strategy_owner")
            or (attribution.get("strategy_owner") if isinstance(attribution, Mapping) else "")
            or ""
        )
        intent_id = str(payload.get("intent_id") or "")
        if owner and owner != "tactical_v2":
            return
        if owner != "tactical_v2" and intent_id not in self._intents:
            return
        self.governor.activate_integrity_halt(
            "pnl_mismatch",
            evidence={"resolution_id": payload.get("resolution_id")},
        )
        self._refresh_status(force=True)

    async def tick(self, *, now: Optional[float] = None) -> None:
        evaluated_at = float(self.now_fn()) if now is None else float(now)
        async with self._lock:
            records = {
                intent_id: (record["state"], record.get("lane"))
                for intent_id, record in self._intents.items()
            }
        for intent_id, (state, lane) in records.items():
            record = self._intents.get(intent_id)
            if record is None:
                continue
            intent = record["intent"]
            if lane == "live":
                if self.mode != "live":
                    await self._rollback_live_intent(intent_id, state, evaluated_at)
                    continue
                if state in _ENTRY_RECONCILE_STATES:
                    if evaluated_at >= intent.expires_at and state not in {
                        "filled_unverified",
                        "partial_fill",
                    }:
                        await self._cancel_live_entry(
                            intent_id,
                            reason="expired",
                            evaluated_at=evaluated_at,
                        )
                    else:
                        await self._reconcile_live_entry(intent_id, evaluated_at)
                elif self._entry_halt_due(intent_id, state, evaluated_at):
                    await self._recheck_entry_integrity_halt(
                        intent_id,
                        evaluated_at,
                    )
                elif state == "protected":
                    await self._reconcile_protected(intent_id, evaluated_at)
                elif state == "closing":
                    await self._reconcile_closing(intent_id, evaluated_at)
                async with self._lock:
                    self._expire_shadow_projection(intent_id, evaluated_at)
                continue
            if lane != "shadow":
                continue
            if record["state"] not in {"ready_for_quote", "pending_entry"}:
                continue
            if evaluated_at < intent.expires_at:
                continue
            state = record.get("entry_state")
            if isinstance(state, EntryState):
                terminal = replace(
                    state,
                    status="entry_terminal",
                    remaining_qty=0.0,
                    terminal_reason="expired",
                    slot_held=False,
                )
                self._apply_transition(intent_id, terminal, evaluated_at)
            else:
                record["state"] = "entry_terminal"
                record["updated_at"] = evaluated_at
                self._terminalize(intent, "expired", evaluated_at)
        self._refresh_status(now=evaluated_at)

    async def recover(self, *, now: Optional[float] = None) -> None:
        """Reconcile every durable live command state without blind retries."""
        evaluated_at = float(self.now_fn()) if now is None else float(now)
        self._requeue_durable_pnl_recoveries()
        self._recover_durable_entry_flat_final_halt()
        async with self._lock:
            protected_ids = [
                intent_id
                for intent_id, record in self._intents.items()
                if record.get("lane") == "live" and record["state"] == "protected"
            ]
        for intent_id in protected_ids:
            await self._recover_protected(intent_id, evaluated_at)
        await self.tick(now=evaluated_at)

    def _requeue_durable_pnl_recoveries(self) -> None:
        for record in self._intents.values():
            if record.get("lane") != "live" or not record.get(
                "pnl_recovery_queued"
            ):
                continue
            snapshot = record.get("pnl_recovery_snapshot")
            intent = record.get("intent")
            if isinstance(snapshot, Mapping) and isinstance(intent, TacticalIntent):
                self._emit_pnl_recovery_snapshot(intent, snapshot)

    async def _rollback_live_intent(
        self,
        intent_id: str,
        state: str,
        evaluated_at: float,
    ) -> None:
        if state == "ready_for_quote":
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is None or record.get("lane") != "live":
                    return
                intent = record["intent"]
                reason = "rollback_admission_disabled"
                self._set_terminal_record(record, reason, evaluated_at)
                self._terminalize(intent, reason, evaluated_at)
            return
        if state in {"pending_entry", "canceling_entry"}:
            async with self._lock:
                record = self._intents.get(intent_id)
                proven = record is not None and self._entry_owner_proven(record)
                if record is not None and not proven:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="rollback_entry_owner_unproven",
                    )
                    self.governor.activate_integrity_halt(
                        "rollback_entry_owner_unproven",
                        evidence={"intent_id": intent_id},
                    )
            if proven:
                await self._cancel_live_entry(
                    intent_id,
                    reason="rollback_admission_disabled",
                    evaluated_at=evaluated_at,
                )
            return
        if state in _ENTRY_RECONCILE_STATES:
            await self._reconcile_live_entry(intent_id, evaluated_at)
            async with self._lock:
                record = self._intents.get(intent_id)
                pending = record is not None and record["state"] == "pending_entry"
            if pending:
                await self._rollback_live_intent(
                    intent_id,
                    "pending_entry",
                    evaluated_at,
                )
            return
        if state == "protected":
            await self._reconcile_protected(intent_id, evaluated_at)
        elif state == "closing":
            await self._reconcile_closing(intent_id, evaluated_at)

    def _entry_owner_proven(self, record: Mapping[str, Any]) -> bool:
        if record.get("lane") != "live":
            return False
        intent = record.get("intent")
        if not isinstance(intent, TacticalIntent):
            return False
        expected = self.executor.make_tactical_clord_id(intent.intent_id, "entry")
        return str(record.get("entry_client_id") or "") == str(expected)

    async def close_for_safety(self, symbol: str, *, source: str) -> Optional[dict]:
        """Allow only system safety to close a proven V2 position."""
        normalized = to_internal(symbol)
        async with self._lock:
            intent_id = next(
                (
                    key
                    for key, record in self._intents.items()
                    if record["intent"].symbol == normalized
                    and record.get("lane") == "live"
                    and record["state"] in {"protected", "closing"}
                ),
                None,
            )
        if intent_id is None:
            return None
        reason_source = str(source or "unknown").strip() or "unknown"
        reason = (
            reason_source
            if reason_source.startswith("risk_forced:")
            else f"risk_forced:{reason_source}"
        )
        return await self._close_intent(intent_id, reason=reason, evaluated_at=float(self.now_fn()))

    def blocks_main_symbol(self, symbol: str) -> bool:
        normalized = to_internal(symbol)
        return any(
            record.get("lane") == "live"
            and record["intent"].symbol == normalized
            and record["state"] in _SLOT_STATES
            for record in self._intents.values()
        )

    def snapshot(self, *, now: Optional[float] = None) -> dict:
        evaluated_at = float(self.now_fn()) if now is None else float(now)
        integrity_halt = self._effective_integrity_halt()
        intents = []
        for record in self._intents.values():
            intent = record["intent"]
            shadow_state = (
                record.get("shadow_state")
                if record.get("lane") == "live"
                else record.get("state")
            )
            intents.append({
                "intent_id": intent.intent_id,
                "episode_id": intent.episode_id,
                "symbol": intent.symbol,
                "side": intent.side,
                "state": record["state"],
                "lane": record.get("lane", "unknown"),
                "updated_at": record["updated_at"],
                "close_reason": record.get("close_reason"),
                "terminal_reason": record.get("terminal_reason"),
                "shadow_filled": bool(record.get("shadow_filled")),
                "shadow_state": shadow_state,
                "shadow_terminal_reason": (
                    record.get("shadow_terminal_reason")
                    if record.get("lane") == "live"
                    else record.get("terminal_reason")
                ),
                "shadow_close_reason": record.get("shadow_close_reason"),
                "parity_category": record.get("parity_category"),
                "handling_evidence": (
                    "handled"
                    if intent.intent_id in self._receipt_intent_ids
                    else "unknown_handling_evidence"
                ),
            })
        intents.sort(key=lambda row: (row["updated_at"], row["intent_id"]))
        parity = self._parity_summary()
        candidate_handling = self._candidate_handling_summary()
        return {
            "mode": self.mode,
            "namespace": self.namespace,
            "as_of": evaluated_at,
            "active_slots": self._active_slot_count(),
            "intents": intents,
            "candidate_handling": candidate_handling,
            "episode_outcomes": dict(self._episode_outcomes),
            "rolling_pnl_usdt": self.governor.rolling_pnl,
            "loss_streak": self.governor.loss_streak,
            "pause_until": self.governor.pause_until,
            "integrity_halt": integrity_halt,
            "parity": parity,
        }

    def operational_status(self, *, now: Optional[float] = None) -> dict:
        evaluated_at = float(self.now_fn()) if now is None else float(now)
        snapshot = self.snapshot(now=evaluated_at)
        status = build_status_snapshot(
            mode=self.mode,
            requested_mode=self.requested_mode,
            cutover_allowed=self.cutover_decision.allowed,
            cutover_reason=self.cutover_decision.reason,
            namespace=self.namespace,
            intents=snapshot["intents"],
            rolling_pnl=self.governor.rolling_pnl,
            loss_streak=self.governor.loss_streak,
            pause_until=self.governor.pause_until,
            integrity_halt=snapshot["integrity_halt"],
            episode_outcomes=self._episode_outcomes,
            updated_at=evaluated_at,
            margin_usdt=float(self.config.get("tactical_v2_margin_usdt", 100.0)),
            max_concurrent=int(self.config.get("tactical_v2_max_concurrent", 3)),
            rolling_loss_limit_usdt=float(
                self.config.get("tactical_v2_rolling_loss_limit_usdt", -15.0)
            ),
            loss_streak_limit=int(self.config.get("tactical_v2_loss_streak_count", 3)),
            parity_mismatches=snapshot["parity"]["mismatch_count"],
            parity_summary=snapshot["parity"],
        )
        status["candidate_handling"] = dict(snapshot["candidate_handling"])
        return status

    def _candidate_handling_summary(self) -> Dict[str, int]:
        missing_intents = {
            record["intent"].intent_id: record["intent"].candidate_id
            for record in self._intents.values()
            if record["intent"].intent_id not in self._receipt_intent_ids
        }
        missing_candidate_ids = set(missing_intents.values())
        unknown_identities = set(self._candidate_handling_gaps)
        unknown_identities.update(
            identity
            for identity, result in self._unknown_replays.items()
            if result.intent_id not in missing_intents
        )
        unknown_identities.update(
            identity
            for identity, candidate_ids in (
                self._quarantined_candidate_receipt_candidates.items()
            )
            if not candidate_ids.intersection(missing_candidate_ids)
        )
        return {
            "receipt_count": len(self._candidate_receipts),
            "unknown_handling_evidence": (
                len(missing_intents) + len(unknown_identities)
            ),
        }

    def _advance_shadow_lane(
        self,
        intent_id: str,
        quote: ExecutableQuote,
        evaluated_at: float,
    ) -> None:
        record = self._intents.get(intent_id)
        if record is None:
            return
        projection = record.get("lane") == "live"
        if not projection and record.get("lane") != "shadow":
            return
        intent = record["intent"]
        state_key = "shadow_state" if projection else "state"
        entry_key = "shadow_entry_state" if projection else "entry_state"
        state = record.get(state_key)
        entry_state = record.get(entry_key)

        if state == "ready_for_quote":
            transition = self.shadow.start(intent, quote, now=evaluated_at)
            self._apply_shadow_entry_transition(
                record,
                transition.next_state,
                evaluated_at,
                projection=projection,
            )
            state = transition.next_state.status
            entry_state = transition.next_state
        elif state in _ENTRY_RECONCILE_STATES and isinstance(entry_state, EntryState):
            transition = self.shadow.on_quote(entry_state, quote, now=evaluated_at)
            self._apply_shadow_entry_transition(
                record,
                transition.next_state,
                evaluated_at,
                projection=projection,
            )
            state = transition.next_state.status
            entry_state = transition.next_state

        if state == "filled_unverified" and isinstance(entry_state, EntryState):
            entry_price = float(entry_state.entry_price or intent.entry_ref)
            fields = {
                "filled_qty": float(entry_state.filled_qty),
                "remaining_qty": 0.0,
                "entry_price": entry_price,
                "opened_at": evaluated_at,
                "simulated_protection": True,
            }
            if projection:
                record["shadow_filled"] = True
                self._persist_shadow_projection(
                    record,
                    "protected",
                    evaluated_at,
                    **fields,
                )
            else:
                record["shadow_filled"] = True
                self._persist_record_state(
                    record,
                    "protected",
                    evaluated_at,
                    shadow_filled=True,
                    **fields,
                )
            return

        if state != "protected":
            return
        entry_price = float(
            record.get("shadow_entry_price" if projection else "entry_price")
            or intent.entry_ref
        )
        opened_at = float(
            record.get("shadow_opened_at" if projection else "opened_at")
            or evaluated_at
        )
        decision = classify_exit(
            intent,
            entry_price=entry_price,
            opened_at=opened_at,
            quote=quote,
            now=evaluated_at,
        )
        if decision.action != "close":
            return
        fields = {
            "close_reason": decision.reason,
            "exit_price": decision.executable_price,
            "pnl_pct": decision.pnl_pct,
            "close_fraction": decision.close_fraction,
        }
        if projection:
            self._persist_shadow_projection(
                record,
                "closed_final",
                evaluated_at,
                **fields,
            )
        else:
            self._consume_episode(intent.episode_id, decision.reason)
            self._persist_record_state(
                record,
                "closed_final",
                evaluated_at,
                shadow_filled=True,
                **fields,
            )

    def _expire_shadow_projection(self, intent_id: str, evaluated_at: float) -> None:
        record = self._intents.get(intent_id)
        if record is None or record.get("lane") != "live":
            return
        intent = record["intent"]
        if (
            record.get("shadow_state") not in {"ready_for_quote", "pending_entry"}
            or evaluated_at < intent.expires_at
        ):
            return
        state = record.get("shadow_entry_state")
        if isinstance(state, EntryState):
            terminal = replace(
                state,
                status="entry_terminal",
                remaining_qty=0.0,
                terminal_reason="expired",
                slot_held=False,
            )
            self._apply_shadow_entry_transition(
                record,
                terminal,
                evaluated_at,
                projection=True,
            )
            return
        self._persist_shadow_projection(
            record,
            "entry_terminal",
            evaluated_at,
            terminal_reason="expired",
        )

    def _invalidate_shadow_entry(
        self,
        record: Dict[str, Any],
        *,
        reason: str,
        evaluated_at: float,
    ) -> None:
        projection = record.get("lane") == "live"
        state_key = "shadow_state" if projection else "state"
        entry_key = "shadow_entry_state" if projection else "entry_state"
        state = record.get(state_key)
        entry_state = record.get(entry_key)
        if state == "ready_for_quote":
            if projection:
                self._persist_shadow_projection(
                    record,
                    "entry_terminal",
                    evaluated_at,
                    terminal_reason=reason,
                )
            elif record.get("lane") == "shadow":
                self._set_terminal_record(record, reason, evaluated_at)
                self._terminalize(record["intent"], reason, evaluated_at)
            return
        if state != "pending_entry" or not isinstance(entry_state, EntryState):
            return
        transition = self.shadow.cancel_pending(entry_state, reason)
        self._apply_shadow_entry_transition(
            record,
            transition.next_state,
            evaluated_at,
            projection=projection,
        )

    def _apply_shadow_entry_transition(
        self,
        record: Dict[str, Any],
        state: EntryState,
        evaluated_at: float,
        *,
        projection: bool,
    ) -> None:
        if not projection:
            self._apply_transition(state.intent.intent_id, state, evaluated_at)
            return
        record["shadow_entry_state"] = state
        fields = {
            "requested_qty": state.requested_qty,
            "filled_qty": state.filled_qty,
            "remaining_qty": state.remaining_qty,
            "entry_price": state.entry_price,
            "terminal_reason": state.terminal_reason,
        }
        self._persist_shadow_projection(
            record,
            state.status,
            evaluated_at,
            **fields,
        )

    def _persist_shadow_projection(
        self,
        record: Dict[str, Any],
        state: str,
        evaluated_at: float,
        **fields: Any,
    ) -> None:
        record["shadow_state"] = state
        record["shadow_updated_at"] = evaluated_at
        for key, value in fields.items():
            if value is not None:
                record[f"shadow_{key}"] = value
        if float(fields.get("filled_qty") or 0) > 0 or state in {
            "protected",
            "closing",
            "closed_final",
        }:
            record["shadow_filled"] = True
        data = {
            "intent_id": record["intent"].intent_id,
            "episode_id": record["intent"].episode_id,
            "state": state,
            "lane": "shadow",
            "projection": True,
            "updated_at": evaluated_at,
        }
        data.update(fields)
        self.store.append("intent_transition", data, emitted_at=evaluated_at)
        self._update_parity(record, evaluated_at)
        self._refresh_status(force=True, now=evaluated_at)

    def _update_parity(self, record: Dict[str, Any], evaluated_at: float) -> None:
        if record.get("lane") != "live":
            return
        category = self._parity_category(record)
        if record.get("parity_category") == category:
            return
        record["parity_category"] = category
        self.store.append(
            "parity_compared",
            {
                "intent_id": record["intent"].intent_id,
                "episode_id": record["intent"].episode_id,
                "live_state": record.get("state"),
                "shadow_state": record.get("shadow_state"),
                "category": category,
                "matched": category is None,
                "updated_at": evaluated_at,
            },
            emitted_at=evaluated_at,
        )
        self._parity_mismatches = self._parity_summary()["mismatch_count"]

    @classmethod
    def _parity_category(cls, record: Mapping[str, Any]) -> Optional[str]:
        live_state = str(record.get("state") or "unknown")
        shadow_state = str(record.get("shadow_state") or "unknown")
        live_phase = cls._parity_phase(live_state)
        shadow_phase = cls._parity_phase(shadow_state)
        if live_phase == shadow_phase:
            if live_state == "partial_fill" and shadow_state != "partial_fill":
                return "partial_fill"
            if live_phase == "nonfilled":
                live_reason = str(record.get("terminal_reason") or "")
                shadow_reason = str(record.get("shadow_terminal_reason") or "")
                return None if live_reason == shadow_reason else "exchange_fill"
            return None
        if live_phase == "integrity":
            reason = str(record.get("integrity_reason") or "").lower()
            return "protection_failure" if "protect" in reason else "shared_system_risk"
        if live_phase == "nonfilled":
            reason = str(record.get("terminal_reason") or "").lower()
            if "capacity" in reason or "account" in reason:
                return "account_capacity"
            if "same_symbol" in reason or "position_exists" in reason:
                return "same_symbol_account_exposure"
            if "reject" in reason:
                return "order_rejection"
            return "exchange_fill"
        if shadow_phase == "unavailable":
            return "process_availability"
        if shadow_phase == "pending" and live_phase in {"open", "closed"}:
            return "stale_or_missing_tick"
        return "exchange_fill"

    @staticmethod
    def _parity_phase(state: str) -> str:
        if state in {"ready_for_quote", "submitting_entry", "pending_entry", "reconciling_entry", "canceling_entry"}:
            return "pending"
        if state in {"partial_fill", "filled_unverified", "protected"}:
            return "open"
        if state == "closing":
            return "closing"
        if state in {"exchange_closed_pending_pnl", "closed_final"}:
            return "closed"
        if state == "entry_terminal":
            return "nonfilled"
        if state == "integrity_required":
            return "integrity"
        return "unavailable"

    def _parity_summary(self) -> dict:
        categories: Dict[str, int] = {}
        compared = 0
        shadow_filled = 0
        shadow_nonfilled = 0
        for record in self._intents.values():
            if record.get("lane") == "live":
                compared += 1
                category = record.get("parity_category")
                shadow_state = record.get("shadow_state")
                filled = bool(record.get("shadow_filled"))
            elif record.get("lane") == "shadow":
                category = None
                shadow_state = record.get("state")
                filled = bool(record.get("shadow_filled")) or float(
                    record.get("filled_qty") or 0
                ) > 0
            else:
                continue
            if category:
                categories[str(category)] = categories.get(str(category), 0) + 1
            if filled:
                shadow_filled += 1
            elif shadow_state == "entry_terminal":
                shadow_nonfilled += 1
        return {
            "compared_intents": compared,
            "mismatch_count": sum(categories.values()),
            "categories": dict(sorted(categories.items())),
            "shadow_filled": shadow_filled,
            "shadow_nonfilled": shadow_nonfilled,
        }

    async def _start_live_entry(
        self,
        intent_id: str,
        quote: ExecutableQuote,
        evaluated_at: float,
    ) -> None:
        claimed = False
        try:
            async with self._lock:
                record = self._intents.get(intent_id)
                if (
                    record is None
                    or record.get("lane") != "live"
                    or record["state"] != "ready_for_quote"
                ):
                    return
                intent = record["intent"]
                decision = classify_entry(intent, quote, now=evaluated_at)
                if decision.action == "terminal":
                    self._set_terminal_record(record, decision.reason, evaluated_at)
                    self._terminalize(intent, decision.reason, evaluated_at)
                    return
                if decision.action not in {"immediate", "pending_limit"}:
                    return
                if not self._try_claim_entry_io(intent_id):
                    return
                claimed = True
                order_type = "market" if decision.action == "immediate" else "limit"
                entry_client_id = self.executor.make_tactical_clord_id(
                    intent.intent_id,
                    "entry",
                )
                entry_visibility_deadline = (
                    evaluated_at + ENTRY_VISIBILITY_GRACE_SECONDS
                )
                self._persist_record_state(
                    record,
                    "submitting_entry",
                    evaluated_at,
                    order_type=order_type,
                    entry_client_id=entry_client_id,
                    executable_price=decision.executable_price,
                    worse_r=decision.worse_r,
                    entry_visibility_deadline=entry_visibility_deadline,
                )
        except BaseException:
            if claimed:
                self._entry_io_inflight.discard(intent_id)
            raise

        try:
            try:
                submitted = await self.live.submit_entry(intent, order_type=order_type)
            except Exception as exc:
                async with self._lock:
                    current = self._intents.get(intent_id)
                    if current is not None and current["state"] == "submitting_entry":
                        self._persist_record_state(
                            current,
                            "reconciling_entry",
                            evaluated_at,
                            reconciliation_from="submitting_entry",
                            entry_query_state="submit_error",
                            entry_submit_error=str(exc),
                        )
            else:
                async with self._lock:
                    current = self._intents.get(intent_id)
                    if current is None or current["state"] != "submitting_entry":
                        return
                    requested_qty = float(submitted.get("requested_qty") or 0)
                    if not math.isfinite(requested_qty) or requested_qty <= 0:
                        self._persist_record_state(
                            current,
                            "integrity_required",
                            evaluated_at,
                            integrity_reason="invalid_submitted_quantity",
                        )
                        self.governor.activate_integrity_halt(
                            "invalid_submitted_quantity",
                            evidence={"intent_id": intent_id},
                        )
                        return
                    current["entry_state"] = pending_entry(
                        intent,
                        lane="live",
                        requested_qty=requested_qty,
                    )
                    self._persist_record_state(
                        current,
                        "pending_entry",
                        evaluated_at,
                        requested_qty=requested_qty,
                        filled_qty=float(submitted.get("filled_qty") or 0),
                        remaining_qty=float(
                            submitted.get("remaining_qty", requested_qty)
                        ),
                        order_id=submitted.get("order_id"),
                        order_type=order_type,
                        entry_client_id=submitted.get("entry_client_id"),
                        tp_client_id=submitted.get("tp_client_id"),
                        sl_client_id=submitted.get("sl_client_id"),
                    )

            async with self._lock:
                current = self._intents.get(intent_id)
                deferred_reason = str(
                    (current or {}).get("deferred_cancel_reason") or ""
                )
                ready_to_cancel = bool(
                    current is not None and current.get("state") == "pending_entry"
                )
            if deferred_reason and ready_to_cancel:
                await self._cancel_live_entry_owned(
                    intent_id,
                    reason=deferred_reason,
                    evaluated_at=evaluated_at,
                )
        finally:
            self._entry_io_inflight.discard(intent_id)

    async def _reconcile_live_entry(self, intent_id: str, evaluated_at: float) -> None:
        claimed = False
        try:
            async with self._lock:
                record = self._intents.get(intent_id)
                if (
                    record is None
                    or record.get("lane") != "live"
                    or record["state"] not in _ENTRY_RECONCILE_STATES
                    or not self._try_claim_entry_io(intent_id)
                ):
                    return
                claimed = True
                prior_state = record["state"]
                intent = record["intent"]
                self._persist_record_state(
                    record,
                    "reconciling_entry",
                    evaluated_at,
                    reconciliation_from=prior_state,
                )
        except BaseException:
            if claimed:
                self._entry_io_inflight.discard(intent_id)
            raise

        try:
            await self._reconcile_live_entry_owned(
                intent_id,
                intent,
                prior_state=prior_state,
                evaluated_at=evaluated_at,
            )
        finally:
            self._entry_io_inflight.discard(intent_id)

    async def _reconcile_live_entry_owned(
        self,
        intent_id: str,
        intent: TacticalIntent,
        *,
        prior_state: str,
        evaluated_at: float,
    ) -> None:
        query = await self.live.query_entry(intent)
        query_state = str(query.get("query_state") or "query_error")
        observation = query.get("observation")
        if query_state != "found" or not isinstance(observation, Mapping):
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is None:
                    return
                deadline = self._entry_visibility_deadline(record, evaluated_at)
                query_errors = list(query.get("errors") or ())
                if evaluated_at < deadline:
                    self._persist_record_state(
                        record,
                        "reconciling_entry",
                        evaluated_at,
                        entry_visibility_deadline=deadline,
                        entry_query_state=query_state,
                        entry_query_errors=query_errors,
                        reconciliation_from=prior_state,
                    )
                    return
                next_recheck = evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                self._persist_record_state(
                    record,
                    "integrity_required",
                    evaluated_at,
                    integrity_reason="entry_reconciliation_unknown",
                    entry_visibility_deadline=deadline,
                    entry_query_state=query_state,
                    entry_query_errors=query_errors,
                    next_entry_recheck_at=next_recheck,
                )
                self.governor.activate_integrity_halt_if_clear(
                    "entry_reconciliation_unknown",
                    evidence={
                        "intent_id": intent_id,
                        "prior_state": prior_state,
                        "entry_visibility_deadline": deadline,
                        "entry_query_state": query_state,
                    },
                )
            return

        filled_qty = float(observation.get("filled_qty") or 0)
        remaining_qty = float(observation.get("remaining_qty") or 0)
        if filled_qty > 0:
            await self._settle_live_fill(
                intent_id,
                observation,
                evaluated_at=evaluated_at,
            )
            return

        terminal_statuses = {"canceled", "cancelled", "closed", "filled", "rejected", "expired"}
        observed_status = str(observation.get("status") or "").lower()
        if remaining_qty <= 0 and observed_status in terminal_statuses:
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is None:
                    return
                if observed_status == "rejected":
                    reason = "entry_rejected"
                elif observed_status == "expired":
                    reason = "expired"
                else:
                    reason = str(
                        record.get("deferred_cancel_reason")
                        or record.get("cancel_reason")
                        or "entry_unfilled_terminal"
                    )
                self._set_terminal_record(record, reason, evaluated_at)
                self._terminalize(intent, reason, evaluated_at)
            return

        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None or record.get("lane") != "live":
                return
            deferred_reason = str(record.get("deferred_cancel_reason") or "")
        if deferred_reason:
            await self._cancel_live_entry_owned(
                intent_id,
                reason=deferred_reason,
                evaluated_at=evaluated_at,
            )
            return

        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None or record.get("lane") != "live":
                return
            requested_qty = float(record.get("requested_qty") or remaining_qty)
            record["entry_state"] = pending_entry(
                intent,
                lane="live",
                requested_qty=requested_qty,
            )
            self._persist_record_state(
                record,
                "pending_entry",
                evaluated_at,
                order_id=observation.get("order_id"),
                requested_qty=requested_qty,
                filled_qty=0.0,
                remaining_qty=remaining_qty,
                entry_price=observation.get("average_price"),
            )

    def _entry_visibility_deadline(
        self,
        record: Mapping[str, Any],
        evaluated_at: float,
    ) -> float:
        raw = record.get("entry_visibility_deadline")
        try:
            deadline = float(raw)
        except (TypeError, ValueError):
            deadline = float(record.get("updated_at") or evaluated_at) + (
                ENTRY_VISIBILITY_GRACE_SECONDS
            )
        if not math.isfinite(deadline):
            return evaluated_at
        return deadline

    def _entry_halt_due(
        self,
        intent_id: str,
        state: str,
        evaluated_at: float,
    ) -> bool:
        if state != "integrity_required":
            return False
        record = self._intents.get(intent_id) or {}
        if (
            record.get("integrity_reason") not in _ENTRY_RECHECK_INTEGRITY_REASONS
            and not self._is_legacy_protection_failure_record(
                intent_id,
                record,
            )
        ):
            return False
        try:
            due = float(record.get("next_entry_recheck_at") or 0)
        except (TypeError, ValueError):
            due = 0.0
        return evaluated_at >= due

    def _is_protection_failure_record(
        self,
        intent_id: str,
        record: Mapping[str, Any],
    ) -> bool:
        if (
            record.get("state") != "integrity_required"
            or record.get("lane") != "live"
        ):
            return False
        if record.get("integrity_reason") == "tactical_protection_incomplete":
            return True
        halt = self.governor.integrity_halt or {}
        evidence = halt.get("evidence") or {}
        return (
            halt.get("reason") == "tactical_protection_incomplete"
            and evidence.get("intent_id") == intent_id
            and float(record.get("filled_qty") or 0) > 0
        )

    def _is_legacy_protection_failure_record(
        self,
        intent_id: str,
        record: Mapping[str, Any],
    ) -> bool:
        return (
            self._is_protection_failure_record(intent_id, record)
            and record.get("integrity_reason")
            != "tactical_protection_incomplete"
        )

    async def _recheck_entry_integrity_halt(
        self,
        intent_id: str,
        evaluated_at: float,
    ) -> None:
        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None or not self._entry_halt_due(
                intent_id,
                str(record.get("state") or ""),
                evaluated_at,
            ) or not self._try_claim_entry_io(intent_id):
                return
            intent = record["intent"]
            self.governor.activate_integrity_halt_if_clear(
                "entry_reconciliation_unknown",
                evidence={
                    "intent_id": intent_id,
                    "recovered_integrity_reason": record.get("integrity_reason"),
                },
            )

        try:
            await self._recheck_entry_integrity_halt_owned(
                intent_id,
                intent,
                evaluated_at,
            )
        finally:
            self._entry_io_inflight.discard(intent_id)

    async def _recheck_entry_integrity_halt_owned(
        self,
        intent_id: str,
        intent: TacticalIntent,
        evaluated_at: float,
    ) -> None:
        record = self._intents.get(intent_id)
        if (
            record is not None
            and self._is_protection_failure_record(intent_id, record)
        ):
            await self._recover_protection_failure_halt(
                intent_id,
                intent,
                evaluated_at,
            )
            return
        query = await self.live.query_entry(intent)
        query_state = str(query.get("query_state") or "query_error")
        observation = query.get("observation")
        if query_state != "found" or not isinstance(observation, Mapping):
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="entry_reconciliation_unknown",
                        entry_query_state=query_state,
                        entry_query_errors=list(query.get("errors") or ()),
                        next_entry_recheck_at=(
                            evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                        ),
                    )
            return

        filled_qty = float(observation.get("filled_qty") or 0)
        remaining_qty = float(observation.get("remaining_qty") or 0)
        if filled_qty <= 0:
            await self._recover_exact_unfilled_entry_halt(
                intent_id,
                intent,
                observation,
                evaluated_at,
            )
            return

        await self._recover_exact_filled_entry_halt(
            intent_id,
            intent,
            observation,
            evaluated_at,
        )

    async def _recover_exact_filled_entry_halt(
        self,
        intent_id: str,
        intent: TacticalIntent,
        observation: Mapping[str, Any],
        evaluated_at: float,
    ) -> None:
        filled_qty = float(observation.get("filled_qty") or 0)
        remaining_qty = float(observation.get("remaining_qty") or 0)
        try:
            exchange_position = await self.live.query_position(intent)
        except Exception as exc:
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="entry_reconciliation_unknown",
                        entry_query_state="position_query_error",
                        entry_query_errors=[{
                            "source": "query_position",
                            "error": str(exc),
                        }],
                        next_entry_recheck_at=(
                            evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                        ),
                    )
            return
        if exchange_position is None:
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="entry_fill_flat_awaiting_final_pnl",
                        entry_query_state="found_filled_flat",
                        order_id=observation.get("order_id"),
                        filled_qty=filled_qty,
                        remaining_qty=remaining_qty,
                        next_entry_recheck_at=(
                            evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                        ),
                    )
                    self._queue_entry_flat_pnl_recovery(
                        record,
                        observation,
                        evaluated_at,
                    )
            return
        try:
            available = float(exchange_position.get("available_contracts") or 0)
        except (TypeError, ValueError):
            available = 0.0
        if (
            exchange_position.get("side") != intent.side
            or not math.isfinite(available)
            or not math.isclose(
                available,
                filled_qty,
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
        ):
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="entry_recovery_position_mismatch",
                        entry_query_state="found_position_mismatch",
                        next_entry_recheck_at=(
                            evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                        ),
                    )
            return

        await self._settle_live_fill(
            intent_id,
            observation,
            evaluated_at=evaluated_at,
        )
        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None or record.get("state") != "protected":
                return
            current_halt = self.governor.integrity_halt or {}
            current_evidence = current_halt.get("evidence") or {}
            if (
                current_halt.get("reason") not in _ENTRY_RECOVERY_HALT_REASONS
                or current_evidence.get("intent_id") != intent_id
            ):
                return
            proof = {
                "ownership": True,
                "orders": True,
                "positions": True,
                "protection": True,
                "intent_id": intent_id,
                "entry_order_id": observation.get("order_id"),
                "entry_client_id": observation.get("client_order_id"),
                "filled_qty": filled_qty,
                "exchange_position_qty": available,
                "tp_algo_ids": list(record.get("tp_algo_ids") or ()),
                "sl_algo_ids": list(record.get("sl_algo_ids") or ()),
            }
            self.governor.clear_integrity_halt(
                f"entry-auto-reconcile:{intent_id}:{int(evaluated_at)}",
                proof,
            )
            self._refresh_status(force=True, now=evaluated_at)

    async def _recover_protection_failure_halt(
        self,
        intent_id: str,
        intent: TacticalIntent,
        evaluated_at: float,
    ) -> None:
        """Converge a fail-closed protection failure after the safe close settles."""
        try:
            exchange_position = await self.live.query_position(intent)
        except Exception as exc:
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="tactical_protection_incomplete",
                        entry_query_state="protection_position_query_error",
                        entry_query_errors=[{
                            "source": "query_position",
                            "error": str(exc),
                        }],
                        next_entry_recheck_at=(
                            evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                        ),
                    )
            return

        if exchange_position is not None:
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="tactical_protection_incomplete",
                        entry_query_state="protection_safe_close_pending",
                        next_entry_recheck_at=(
                            evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                        ),
                    )
            return

        try:
            cleanup = await self.live.cancel_protection(intent)
        except Exception as exc:
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="tactical_protection_incomplete",
                        entry_query_state="protection_cleanup_error",
                        entry_query_errors=[{
                            "source": "cancel_protection",
                            "error": str(exc),
                        }],
                        next_entry_recheck_at=(
                            evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                        ),
                    )
            return

        async with self._lock:
            record = self._intents.get(intent_id)
            if (
                record is None
                or record.get("state") != "integrity_required"
                or not self._is_protection_failure_record(
                    intent_id,
                    record,
                )
            ):
                return
            self._queue_protection_flat_pnl_recovery(
                record,
                evaluated_at,
            )
            self._consume_episode(
                intent.episode_id,
                "risk_forced:protection_integrity",
            )
            self._persist_record_state(
                record,
                "exchange_closed_pending_pnl",
                evaluated_at,
                close_reason="risk_forced:protection_integrity",
                close_order_id=record.get("safe_close_order_id"),
                close_client_id=record.get("safe_close_client_id"),
                protection_cleanup_proven=True,
                recovery_kind="protection_failure",
                entry_query_state="protection_safe_close_confirmed_flat",
                next_entry_recheck_at=None,
            )
            self._refresh_status(force=True, now=evaluated_at)

    async def _recover_exact_unfilled_entry_halt(
        self,
        intent_id: str,
        intent: TacticalIntent,
        observation: Mapping[str, Any],
        evaluated_at: float,
    ) -> None:
        remaining_qty = float(observation.get("remaining_qty") or 0)
        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None:
                return
            persisted_cancel_reason = str(
                record.get("deferred_cancel_reason")
                or record.get("cancel_reason")
                or ""
            )
        cancel_reason = persisted_cancel_reason or (
            "expired" if evaluated_at >= intent.expires_at else ""
        )

        if remaining_qty > 0 and cancel_reason:
            cancelled = await self.live.cancel_entry(intent)
            if not cancelled.get("proven"):
                async with self._lock:
                    record = self._intents.get(intent_id)
                    if record is not None:
                        self._persist_record_state(
                            record,
                            "integrity_required",
                            evaluated_at,
                            integrity_reason="entry_reconciliation_unknown",
                            entry_query_state="cancel_unproven",
                            entry_cancel_reason=cancelled.get("reason"),
                            next_entry_recheck_at=(
                                evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                            ),
                        )
                return
            cancel_filled_qty = float(cancelled.get("filled_qty") or 0)
            if cancel_filled_qty > 0:
                fill_observation = {
                    **dict(observation),
                    "order_id": (
                        cancelled.get("order_id")
                        or observation.get("order_id")
                    ),
                    "filled_qty": cancel_filled_qty,
                    "remaining_qty": 0.0,
                    "average_price": (
                        cancelled.get("average_price")
                        or observation.get("average_price")
                    ),
                }
                await self._recover_exact_filled_entry_halt(
                    intent_id,
                    intent,
                    fill_observation,
                    evaluated_at,
                )
                return
            remaining_qty = 0.0

        try:
            exchange_position = await self.live.query_position(intent)
        except Exception as exc:
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="entry_reconciliation_unknown",
                        entry_query_state="position_query_error",
                        entry_query_errors=[{
                            "source": "query_position",
                            "error": str(exc),
                        }],
                        next_entry_recheck_at=(
                            evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                        ),
                    )
            return
        if exchange_position is not None:
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="entry_recovery_position_mismatch",
                        entry_query_state="found_unfilled_position_present",
                        next_entry_recheck_at=(
                            evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                        ),
                    )
            return

        terminal_reason = cancel_reason if remaining_qty <= 0 else ""
        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None:
                return
            if remaining_qty <= 0:
                terminal_reason = terminal_reason or "entry_unfilled_terminal"
                self._set_terminal_record(record, terminal_reason, evaluated_at)
                self._terminalize(intent, terminal_reason, evaluated_at)
            else:
                requested_qty = float(record.get("requested_qty") or remaining_qty)
                record["entry_state"] = pending_entry(
                    intent,
                    lane="live",
                    requested_qty=requested_qty,
                )
                self._persist_record_state(
                    record,
                    "pending_entry",
                    evaluated_at,
                    order_id=observation.get("order_id"),
                    requested_qty=requested_qty,
                    filled_qty=0.0,
                    remaining_qty=remaining_qty,
                    entry_price=observation.get("average_price"),
                    entry_query_state="found_unfilled_exact",
                )
            self._clear_exact_unfilled_entry_halt(
                intent_id,
                observation,
                remaining_qty,
                evaluated_at,
            )
            self._refresh_status(force=True, now=evaluated_at)

    def _clear_exact_unfilled_entry_halt(
        self,
        intent_id: str,
        observation: Mapping[str, Any],
        remaining_qty: float,
        evaluated_at: float,
    ) -> bool:
        current_halt = self.governor.integrity_halt or {}
        current_evidence = current_halt.get("evidence") or {}
        if (
            current_halt.get("reason") not in _ENTRY_RECOVERY_HALT_REASONS
            or current_evidence.get("intent_id") != intent_id
        ):
            return False
        return self.governor.clear_integrity_halt(
            f"entry-unfilled-reconcile:{intent_id}:{int(evaluated_at)}",
            {
                "ownership": True,
                "orders": True,
                "positions": True,
                "protection": True,
                "intent_id": intent_id,
                "entry_order_id": observation.get("order_id"),
                "entry_client_id": observation.get("client_order_id"),
                "filled_qty": 0.0,
                "remaining_qty": remaining_qty,
                "exchange_position_qty": 0.0,
            },
        )

    async def _cancel_live_entry(
        self,
        intent_id: str,
        *,
        reason: str,
        evaluated_at: float,
    ) -> None:
        async with self._lock:
            record = self._intents.get(intent_id)
            if (
                record is None
                or record.get("lane") != "live"
                or record["state"] not in _ENTRY_RECONCILE_STATES
            ):
                return
            if not self._try_claim_entry_io(intent_id):
                deferred_reason = str(
                    record.get("deferred_cancel_reason") or reason
                )
                self._persist_record_state(
                    record,
                    record["state"],
                    evaluated_at,
                    deferred_cancel_reason=deferred_reason,
                )
                return
        try:
            await self._cancel_live_entry_owned(
                intent_id,
                reason=reason,
                evaluated_at=evaluated_at,
            )
        finally:
            self._entry_io_inflight.discard(intent_id)

    async def _cancel_live_entry_owned(
        self,
        intent_id: str,
        *,
        reason: str,
        evaluated_at: float,
    ) -> None:
        async with self._lock:
            record = self._intents.get(intent_id)
            if (
                record is None
                or record.get("lane") != "live"
                or record["state"] not in _ENTRY_RECONCILE_STATES
            ):
                return
            intent = record["intent"]
            self._persist_record_state(
                record,
                "canceling_entry",
                evaluated_at,
                cancel_reason=reason,
            )
        cancelled = await self.live.cancel_entry(intent)
        if not cancelled.get("proven"):
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is None:
                    return
                self._persist_record_state(
                    record,
                    "integrity_required",
                    evaluated_at,
                    integrity_reason="entry_cancel_unproven",
                    next_entry_recheck_at=(
                        evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                    ),
                )
                self.governor.activate_integrity_halt_if_clear(
                    "entry_cancel_unproven",
                    evidence={"intent_id": intent_id, "reason": cancelled.get("reason")},
                )
            return
        filled_qty = float(cancelled.get("filled_qty") or 0)
        if filled_qty > 0:
            await self._settle_live_fill(
                intent_id,
                {
                    "filled_qty": filled_qty,
                    "remaining_qty": 0.0,
                    "average_price": cancelled.get("average_price"),
                    "order_id": cancelled.get("order_id"),
                },
                evaluated_at=evaluated_at,
            )
            return
        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None or record.get("lane") != "live":
                return
            self._set_terminal_record(record, reason, evaluated_at)
            self._terminalize(intent, reason, evaluated_at)

    def _try_claim_entry_io(self, intent_id: str) -> bool:
        if intent_id in self._entry_io_inflight:
            return False
        self._entry_io_inflight.add(intent_id)
        return True

    async def _settle_live_fill(
        self,
        intent_id: str,
        observation: Mapping[str, Any],
        *,
        evaluated_at: float,
    ) -> None:
        filled_qty = float(observation.get("filled_qty") or 0)
        remaining_qty = float(observation.get("remaining_qty") or 0)
        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None or record.get("lane") != "live":
                return
            intent = record["intent"]
            self._persist_record_state(
                record,
                "filled_unverified",
                evaluated_at,
                filled_qty=filled_qty,
                remaining_qty=remaining_qty,
                entry_price=observation.get("average_price"),
                order_id=observation.get("order_id"),
            )

        proof = await self.live.settle_fill(
            intent,
            filled_qty=filled_qty,
            remaining_qty=remaining_qty,
        )
        if not proof.complete:
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="tactical_protection_incomplete",
                        protection_failure_reason=proof.reason,
                        safe_close_order_id=proof.safe_close_order_id,
                        safe_close_client_id=proof.safe_close_client_id,
                        protection_cleanup_errors=list(proof.cleanup_errors),
                        next_entry_recheck_at=(
                            evaluated_at + ENTRY_HALT_RECHECK_SECONDS
                        ),
                    )
            return

        protected_qty = float(proof.protected_qty)
        entry_price = observation.get("average_price")
        if entry_price in (None, ""):
            entry_price = intent.entry_ref
        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None:
                return
            self._persist_record_state(
                record,
                "protected",
                evaluated_at,
                filled_qty=protected_qty,
                remaining_qty=0.0,
                entry_price=float(entry_price),
                opened_at=evaluated_at,
                protection_representation=proof.representation,
                tp_algo_ids=list(proof.tp_algo_ids),
                sl_algo_ids=list(proof.sl_algo_ids),
            )
            self._persist_common_position(record, proof)

    async def _reconcile_protected(self, intent_id: str, evaluated_at: float) -> None:
        async with self._lock:
            record = self._intents.get(intent_id)
            if (
                record is None
                or record.get("lane") != "live"
                or record["state"] != "protected"
            ):
                return
            intent = record["intent"]
            opened_at = float(record.get("opened_at") or record["updated_at"])
        exchange_position = await self.live.query_position(intent)
        if exchange_position is None:
            await self.live.cancel_protection(intent)
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None and record["state"] == "protected":
                    self._mark_exchange_closed(record, "exchange_tp_or_sl", evaluated_at)
            return
        if not max_hold_due(intent, opened_at=opened_at, now=evaluated_at):
            return
        await self._close_intent(
            intent_id,
            reason="tactical_max_hold",
            evaluated_at=evaluated_at,
        )

    async def _recover_protected(self, intent_id: str, evaluated_at: float) -> None:
        async with self._lock:
            record = self._intents.get(intent_id)
            if (
                record is None
                or record.get("lane") != "live"
                or record["state"] != "protected"
            ):
                return
            intent = record["intent"]
            filled_qty = float(record.get("filled_qty") or 0)
            opened_at = float(record.get("opened_at") or record["updated_at"])
        exchange_position = await self.live.query_position(intent)
        if exchange_position is None:
            await self.live.cancel_protection(intent)
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None and record["state"] == "protected":
                    self._mark_exchange_closed(
                        record,
                        "exchange_tp_or_sl",
                        evaluated_at,
                    )
            return
        available = float(exchange_position.get("available_contracts") or 0)
        if (
            exchange_position.get("side") != intent.side
            or not math.isfinite(available)
            or not math.isclose(available, filled_qty, rel_tol=1e-9, abs_tol=1e-9)
        ):
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._persist_record_state(
                        record,
                        "integrity_required",
                        evaluated_at,
                        integrity_reason="protected_position_mismatch",
                    )
                    self.governor.activate_integrity_halt(
                        "protected_position_mismatch",
                        evidence={
                            "intent_id": intent_id,
                            "expected_qty": filled_qty,
                            "exchange_qty": available,
                            "exchange_side": exchange_position.get("side"),
                        },
                    )
            return
        proof = await self.live.settle_fill(
            intent,
            filled_qty=filled_qty,
            remaining_qty=0.0,
        )
        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None:
                return
            if not proof.complete:
                self._persist_record_state(
                    record,
                    "integrity_required",
                    evaluated_at,
                    integrity_reason=proof.reason,
                )
                return
            record["opened_at"] = opened_at
            self._persist_common_position(record, proof)

    async def _close_intent(
        self,
        intent_id: str,
        *,
        reason: str,
        evaluated_at: float,
    ) -> Optional[dict]:
        async with self._lock:
            record = self._intents.get(intent_id)
            if (
                record is None
                or record.get("lane") != "live"
                or record["state"] not in {"protected", "closing"}
            ):
                return None
            intent = record["intent"]
            filled_qty = float(record.get("filled_qty") or 0)
            if record["state"] == "closing" and record.get("close_order_id"):
                return {
                    "status": record.get("close_status", "submitted"),
                    "order_id": record.get("close_order_id"),
                    "client_order_id": record.get("close_client_id"),
                    "reason": record.get("close_reason", reason),
                }
            self._persist_record_state(
                record,
                "closing",
                evaluated_at,
                close_reason=reason,
                close_client_id=self.executor.make_tactical_clord_id(intent.intent_id, "close"),
            )

        exchange_position = await self.live.query_position(intent)
        if exchange_position is None:
            await self.live.cancel_protection(intent)
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._mark_exchange_closed(record, reason, evaluated_at)
            return {"status": "already_flat", "closed_qty": 0.0, "reason": reason}

        result = await self.live.close_position(
            intent,
            filled_qty=filled_qty,
            ownership_proof=self.executor.make_tactical_clord_id(intent.intent_id, "entry"),
            reason=reason,
        )
        async with self._lock:
            record = self._intents.get(intent_id)
            if record is None:
                return result
            if result.get("status") == "already_flat":
                self._mark_exchange_closed(record, reason, evaluated_at)
            else:
                self._persist_record_state(
                    record,
                    "closing",
                    evaluated_at,
                    close_reason=reason,
                    close_order_id=result.get("order_id"),
                    close_client_id=result.get("client_order_id"),
                    close_status=result.get("status", "submitted"),
                )
        return result

    async def _reconcile_closing(self, intent_id: str, evaluated_at: float) -> None:
        async with self._lock:
            record = self._intents.get(intent_id)
            if (
                record is None
                or record.get("lane") != "live"
                or record["state"] != "closing"
            ):
                return
            intent = record["intent"]
            close_order_id = record.get("close_order_id")
            reason = str(record.get("close_reason") or "tactical_close")
        exchange_position = await self.live.query_position(intent)
        if exchange_position is None:
            await self.live.cancel_protection(intent)
            async with self._lock:
                record = self._intents.get(intent_id)
                if record is not None:
                    self._mark_exchange_closed(record, reason, evaluated_at)
            return
        if close_order_id:
            return
        await self._close_intent(intent_id, reason=reason, evaluated_at=evaluated_at)

    def _persist_common_position(
        self,
        record: Dict[str, Any],
        proof: ProtectionProof,
    ) -> None:
        intent = record["intent"]
        symbol = self.executor._normalize_symbol(intent.symbol)
        existing = (getattr(self.executor, "positions", {}) or {}).get(symbol)
        if existing and existing.get("intent_id") != intent.intent_id:
            self.governor.activate_integrity_halt(
                "position_owner_collision",
                evidence={"intent_id": intent.intent_id, "symbol": symbol},
            )
            raise RuntimeError("Tactical V2 position collided with existing owner")
        position = {
            "symbol": symbol,
            "side": intent.side,
            "entry_price": float(record.get("entry_price") or intent.entry_ref),
            "amount": float(record.get("filled_qty") or proof.protected_qty),
            "amount_usdt": float(intent.margin_usdt),
            "leverage": int(intent.leverage),
            "stop_loss": float(intent.stop_loss),
            "original_sl": float(intent.stop_loss),
            "take_profit": float(intent.take_profit),
            "take_profit_levels": [float(intent.take_profit)],
            "tp_filled": 0,
            "highest_price": float(record.get("entry_price") or intent.entry_ref),
            "lowest_price": float(record.get("entry_price") or intent.entry_ref),
            "open_time": float(record.get("opened_at") or record["updated_at"]),
            "opened_at": float(record.get("opened_at") or record["updated_at"]),
            "track": intent.track,
            "exit_profile": intent.exit_profile,
            "strategy_owner": intent.strategy_owner,
            "intent_id": intent.intent_id,
            "episode_id": intent.episode_id,
            "plan_hash": intent.plan_hash,
            "position_id": f"tv2:{intent.intent_id}",
            "source_shadow_id": intent.source_shadow_id,
            "tactical_source": intent.tactical_source,
            "tactical_cost_gate": intent.tactical_cost_gate,
            "entry_client_id": self.executor.make_tactical_clord_id(intent.intent_id, "entry"),
            "entry_request_id": self.executor.make_tactical_clord_id(intent.intent_id, "entry"),
            "request_id": self.executor.make_tactical_clord_id(intent.intent_id, "entry"),
            "tp_client_id": self.executor.make_tactical_clord_id(intent.intent_id, "tp"),
            "sl_client_id": self.executor.make_tactical_clord_id(intent.intent_id, "sl"),
            "tp_algo_ids": list(proof.tp_algo_ids),
            "sl_algo_ids": list(proof.sl_algo_ids),
            "tp_algo_id": proof.tp_algo_ids[0] if proof.tp_algo_ids else "",
            "sl_algo_id": proof.sl_algo_ids[0] if proof.sl_algo_ids else "",
            "protection_state": "protected",
            "sl_sync_state": "active",
            "attribution": {
                "strategy_owner": intent.strategy_owner,
                "intent_id": intent.intent_id,
                "episode_id": intent.episode_id,
                "plan_hash": intent.plan_hash,
                "track": intent.track,
                "exit_profile": intent.exit_profile,
                "tactical_source": intent.tactical_source,
            },
        }
        self.executor.positions[symbol] = position
        save = getattr(self.executor, "_save_positions", None)
        if callable(save):
            save()

    def _mark_exchange_closed(
        self,
        record: Dict[str, Any],
        reason: str,
        evaluated_at: float,
    ) -> None:
        intent = record["intent"]
        symbol = self.executor._normalize_symbol(intent.symbol)
        position = (getattr(self.executor, "positions", {}) or {}).get(symbol)
        self._consume_episode(intent.episode_id, reason)
        if position and position.get("intent_id") == intent.intent_id:
            removed = dict(position)
            removed.update({
                "symbol": symbol,
                "strategy_owner": "tactical_v2",
                "tactical_close_reason": reason,
                "close_reason": reason,
            })
            for key in ("close_order_id", "close_client_id"):
                value = record.get(key)
                if value:
                    removed[key] = value
            if not hasattr(self.executor, "_removed_positions_data"):
                self.executor._removed_positions_data = []
            if not hasattr(self.executor, "_last_removed_symbols"):
                self.executor._last_removed_symbols = []
            self.executor._removed_positions_data.append(removed)
            self.executor._last_removed_symbols.append(symbol)
        self._persist_record_state(
            record,
            "exchange_closed_pending_pnl",
            evaluated_at,
            close_reason=reason,
        )
        if position and position.get("intent_id") == intent.intent_id:
            self.executor.positions.pop(symbol, None)
            save = getattr(self.executor, "_save_positions", None)
            if callable(save):
                save()
        self._refresh_status(force=True, now=evaluated_at)

    def _queue_entry_flat_pnl_recovery(
        self,
        record: Dict[str, Any],
        observation: Mapping[str, Any],
        evaluated_at: float,
    ) -> None:
        self._queue_flat_pnl_recovery(
            record,
            evaluated_at,
            entry_price=(
                observation.get("average_price")
                or record.get("entry_price")
                or record["intent"].entry_ref
            ),
            filled_qty=observation.get("filled_qty"),
            close_reason="entry_recovery_exchange_closed",
        )

    def _queue_protection_flat_pnl_recovery(
        self,
        record: Dict[str, Any],
        evaluated_at: float,
    ) -> None:
        self._queue_flat_pnl_recovery(
            record,
            evaluated_at,
            entry_price=(
                record.get("entry_price")
                or record["intent"].entry_ref
            ),
            filled_qty=record.get("filled_qty"),
            close_reason="risk_forced:protection_integrity",
            close_order_id=record.get("safe_close_order_id"),
            close_client_id=record.get("safe_close_client_id"),
        )

    def _queue_flat_pnl_recovery(
        self,
        record: Dict[str, Any],
        evaluated_at: float,
        *,
        entry_price: Any,
        filled_qty: Any,
        close_reason: str,
        close_order_id: Optional[str] = None,
        close_client_id: Optional[str] = None,
    ) -> None:
        intent = record["intent"]
        if record.get("pnl_recovery_queued"):
            snapshot = record.get("pnl_recovery_snapshot")
            if isinstance(snapshot, Mapping):
                self._emit_pnl_recovery_snapshot(intent, snapshot)
            return
        symbol = self.executor._normalize_symbol(intent.symbol)
        entry_request_id = self.executor.make_tactical_clord_id(
            intent.intent_id,
            "entry",
        )
        close_client_id = (
            close_client_id
            or record.get("close_client_id")
            or self.executor.make_tactical_clord_id(intent.intent_id, "close")
        )
        recovery = {
            "symbol": symbol,
            "side": intent.side,
            "entry_price": float(entry_price or intent.entry_ref),
            "amount": float(filled_qty or 0),
            "amount_usdt": float(intent.margin_usdt),
            "leverage": int(intent.leverage),
            "opened_at": float(
                record.get("submitted_at")
                or record.get("created_at")
                or intent.created_at
            ),
            "closed_at": float(evaluated_at),
            "strategy_owner": "tactical_v2",
            "intent_id": intent.intent_id,
            "episode_id": intent.episode_id,
            "plan_hash": intent.plan_hash,
            "position_id": f"tv2:{intent.intent_id}",
            "entry_client_id": entry_request_id,
            "entry_request_id": entry_request_id,
            "request_id": entry_request_id,
            "tp_client_id": self.executor.make_tactical_clord_id(
                intent.intent_id,
                "tp",
            ),
            "sl_client_id": self.executor.make_tactical_clord_id(
                intent.intent_id,
                "sl",
            ),
            "close_client_id": close_client_id,
            "tp_algo_clord_id": self.executor.make_tactical_clord_id(
                intent.intent_id,
                "tp",
            ),
            "sl_algo_clord_id": self.executor.make_tactical_clord_id(
                intent.intent_id,
                "sl",
            ),
            "close_order_id": close_order_id or record.get("close_order_id", ""),
            "tactical_close_reason": close_reason,
            "close_reason": close_reason,
            "attribution": {
                "strategy_owner": "tactical_v2",
                "intent_id": intent.intent_id,
                "episode_id": intent.episode_id,
                "plan_hash": intent.plan_hash,
                "track": intent.track,
                "exit_profile": intent.exit_profile,
                "tactical_source": intent.tactical_source,
            },
        }
        self._persist_record_state(
            record,
            record.get("state", "integrity_required"),
            evaluated_at,
            pnl_recovery_queued=True,
            pnl_recovery_snapshot=recovery,
        )
        self._emit_pnl_recovery_snapshot(intent, recovery)

    def _emit_pnl_recovery_snapshot(
        self,
        intent: TacticalIntent,
        recovery: Mapping[str, Any],
    ) -> None:
        if intent.intent_id in self._entry_pnl_recovery_queued:
            return
        if not hasattr(self.executor, "_removed_positions_data"):
            self.executor._removed_positions_data = []
        if not hasattr(self.executor, "_last_removed_symbols"):
            self.executor._last_removed_symbols = []
        self.executor._removed_positions_data.append(dict(recovery))
        self.executor._last_removed_symbols.append(str(recovery["symbol"]))
        self._entry_pnl_recovery_queued.add(intent.intent_id)

    def _is_entry_flat_recovery_record(
        self,
        intent_id: str,
        record: Optional[Mapping[str, Any]],
    ) -> bool:
        intent = record.get("intent") if record is not None else None
        return (
            record is not None
            and (
                (
                    record.get("state") in {
                        "integrity_required",
                        "closed_final",
                    }
                    and record.get("integrity_reason")
                    == "entry_fill_flat_awaiting_final_pnl"
                )
                or (
                    record.get("recovery_kind") == "protection_failure"
                    and record.get("state") in {
                        "exchange_closed_pending_pnl",
                        "closed_final",
                    }
                )
            )
            and isinstance(intent, TacticalIntent)
            and intent.intent_id == intent_id
        )

    def _matches_entry_flat_recovery_final(
        self,
        record: Optional[Mapping[str, Any]],
        normalized: Mapping[str, Any],
    ) -> bool:
        if record is None:
            return False
        intent = record.get("intent")
        if not isinstance(intent, TacticalIntent):
            return False
        entry_request_id = self.executor.make_tactical_clord_id(
            intent.intent_id,
            "entry",
        )
        proof = normalized.get("tactical_v2_proof")
        if not isinstance(proof, Mapping) or proof.get("complete") is not True:
            return False
        try:
            entry_qty = float(proof.get("entry_qty"))
            close_qty = float(proof.get("close_qty"))
            entry_fee = float(proof.get("entry_fee_usdt"))
        except (TypeError, ValueError):
            return False
        return (
            normalized.get("strategy_owner") == "tactical_v2"
            and normalized.get("intent_id") == intent.intent_id
            and normalized.get("episode_id") == intent.episode_id
            and normalized.get("plan_hash") == intent.plan_hash
            and normalized.get("position_id") == f"tv2:{intent.intent_id}"
            and normalized.get("entry_request_id") == entry_request_id
            and proof.get("entry_request_id") == entry_request_id
            and bool(proof.get("entry_order_ids"))
            and bool(proof.get("close_order_ids"))
            and math.isfinite(entry_qty)
            and math.isfinite(close_qty)
            and math.isfinite(entry_fee)
            and entry_qty > 0
            and math.isclose(entry_qty, close_qty, rel_tol=1e-9, abs_tol=1e-9)
        )

    def _clear_entry_flat_recovery_halt(
        self,
        intent_id: str,
        normalized: Mapping[str, Any],
    ) -> bool:
        current_halt = self.governor.integrity_halt or {}
        current_evidence = current_halt.get("evidence") or {}
        if (
            current_halt.get("reason") not in _ENTRY_RECOVERY_HALT_REASONS
            or current_evidence.get("intent_id") != intent_id
        ):
            return False
        proof = dict(normalized.get("tactical_v2_proof") or {})
        return self.governor.clear_integrity_halt(
            f"entry-final-pnl:{intent_id}:{normalized.get('resolution_id')}",
            {
                "ownership": True,
                "orders": True,
                "positions": True,
                "protection": (
                    current_halt.get("reason")
                    != "tactical_protection_incomplete"
                    or normalized.get("tactical_v2_proof", {}).get("complete")
                    is True
                ),
                "intent_id": intent_id,
                "position_id": normalized.get("position_id"),
                "entry_request_id": normalized.get("entry_request_id"),
                "entry_order_ids": list(proof.get("entry_order_ids") or ()),
                "close_order_ids": list(proof.get("close_order_ids") or ()),
                "final_pnl_usdt": normalized.get("pnl_usdt"),
            },
        )

    def _recover_durable_entry_flat_final_halt(self) -> bool:
        halt = self.governor.integrity_halt or {}
        evidence = halt.get("evidence") or {}
        if halt.get("reason") not in _ENTRY_RECOVERY_HALT_REASONS:
            return False
        intent_id = str(evidence.get("intent_id") or "")
        record = self._intents.get(intent_id)
        if (
            record is None
            or record.get("state") != "closed_final"
            or not self._is_entry_flat_recovery_record(intent_id, record)
        ):
            return False
        resolution_id = str(record.get("resolution_id") or "")
        normalized = self.governor.resolution_by_id(resolution_id)
        if normalized is None or not self._matches_entry_flat_recovery_final(
            record,
            normalized,
        ):
            return False
        return self._clear_entry_flat_recovery_halt(intent_id, normalized)

    def _persist_record_state(
        self,
        record: Dict[str, Any],
        state: str,
        evaluated_at: float,
        **fields: Any,
    ) -> None:
        record["state"] = state
        record["updated_at"] = evaluated_at
        for key, value in fields.items():
            if value is not None:
                record[key] = value
        data = {
            "intent_id": record["intent"].intent_id,
            "episode_id": record["intent"].episode_id,
            "state": state,
            "lane": record.get("lane", "unknown"),
            "updated_at": evaluated_at,
        }
        data.update(fields)
        self.store.append("intent_transition", data, emitted_at=evaluated_at)
        if record.get("lane") == "live":
            self._update_parity(record, evaluated_at)
        self._refresh_status(force=True, now=evaluated_at)

    @staticmethod
    def _set_terminal_record(
        record: Dict[str, Any],
        reason: str,
        evaluated_at: float,
    ) -> None:
        record["state"] = "entry_terminal"
        record["terminal_reason"] = reason
        record["updated_at"] = evaluated_at

    def _apply_transition(
        self,
        intent_id: str,
        state: EntryState,
        evaluated_at: float,
    ) -> None:
        record = self._intents[intent_id]
        record["entry_state"] = state
        record["state"] = state.status
        record["updated_at"] = evaluated_at
        if state.terminal_reason is not None:
            record["terminal_reason"] = state.terminal_reason
        self.store.append(
            "intent_transition",
            {
                "intent_id": intent_id,
                "episode_id": state.intent.episode_id,
                "state": state.status,
                "lane": state.lane,
                "requested_qty": state.requested_qty,
                "filled_qty": state.filled_qty,
                "remaining_qty": state.remaining_qty,
                "entry_price": state.entry_price,
                "terminal_reason": state.terminal_reason,
                "updated_at": evaluated_at,
            },
            emitted_at=evaluated_at,
        )
        if state.status == "entry_terminal":
            self._terminalize(
                state.intent,
                state.terminal_reason or "entry_terminal",
                evaluated_at,
                persist=False,
            )
        elif state.status == "integrity_required":
            self.governor.activate_integrity_halt(
                "entry_state_integrity",
                evidence={"intent_id": intent_id},
            )
        self._refresh_status(force=True, now=evaluated_at)

    def _terminalize(
        self,
        intent: TacticalIntent,
        reason: str,
        evaluated_at: float,
        *,
        persist: bool = True,
    ) -> None:
        self._consume_episode(intent.episode_id, reason)
        record = self._intents.get(intent.intent_id)
        if persist:
            self.store.append(
                "intent_transition",
                {
                    "intent_id": intent.intent_id,
                    "episode_id": intent.episode_id,
                    "state": "entry_terminal",
                    "lane": record.get("lane", "unknown") if record else "unknown",
                    "terminal_reason": reason,
                    "updated_at": evaluated_at,
                },
                emitted_at=evaluated_at,
            )
        if record is not None and record.get("lane") == "live":
            self._update_parity(record, evaluated_at)
        self._refresh_status(force=True, now=evaluated_at)

    def _active_slot_count(self) -> int:
        lane = self.mode if self.mode in {"live", "shadow"} else "live"
        return sum(
            1
            for record in self._intents.values()
            if record.get("lane") == lane and record["state"] in _SLOT_STATES
        )

    def _has_live_integrity_required(self) -> bool:
        return self._candidate_receipt_integrity_halt is not None or (
            self.mode == "live"
            and any(
                record.get("lane") == "live"
                and record.get("state") == "integrity_required"
                for record in self._intents.values()
            )
        )

    def _effective_integrity_halt(self) -> Optional[Dict[str, Any]]:
        governor_halt = self.governor.integrity_halt
        if governor_halt is not None:
            return governor_halt
        if self._candidate_receipt_integrity_halt is not None:
            return dict(self._candidate_receipt_integrity_halt)
        durable = [
            record
            for record in self._intents.values()
            if record.get("lane") == "live"
            and record.get("state") == "integrity_required"
        ]
        if not durable:
            return None
        durable.sort(
            key=lambda record: (
                float(record.get("updated_at") or 0),
                record["intent"].intent_id,
            )
        )
        reasons = sorted({
            str(record.get("integrity_reason") or "durable_integrity_required")
            for record in durable
        })
        return {
            "reason": (
                reasons[0] if len(reasons) == 1 else "durable_integrity_required"
            ),
            "evidence": {
                "intent_ids": [record["intent"].intent_id for record in durable],
                "reasons": reasons,
                "source": "durable_intent_state",
            },
            "halted_at": float(durable[0].get("updated_at") or 0),
        }

    def _shadow_admission_reason(self, symbol: str) -> Optional[str]:
        normalized = to_internal(symbol)
        occupied = 0
        for record in self._intents.values():
            if record.get("lane") == "live":
                state = record.get("shadow_state")
            elif record.get("lane") == "shadow":
                state = record.get("state")
            else:
                continue
            if state not in _SLOT_STATES:
                continue
            occupied += 1
            if record["intent"].symbol == normalized:
                return "same_symbol_exposure"
        if occupied >= 3:
            return "capacity_skipped"
        return None

    def _symbol_occupied(self, symbol: str) -> bool:
        normalized = to_internal(symbol)
        if any(
            record.get("lane") == self.mode
            and record["intent"].symbol == normalized
            and record["state"] in _SLOT_STATES
            for record in self._intents.values()
        ):
            return True
        for key, position in (getattr(self.executor, "positions", {}) or {}).items():
            position_symbol = position.get("symbol", key) if isinstance(position, Mapping) else key
            if to_internal(position_symbol) == normalized:
                return True
        load_owners = getattr(self.executor, "_load_sidecar_owner_registry", None)
        if callable(load_owners):
            try:
                owners = load_owners()
                if owners is not None and any(
                    owners.matches_position(normalized, side)
                    for side in ("long", "short")
                ):
                    return True
            except Exception as exc:
                self._log_warning(
                    "sidecar owner lookup failed for %s: %s", normalized, exc
                )
        return False

    @staticmethod
    def _structure_from(raw: Mapping[str, Any]) -> dict:
        timing = raw.get("entry_timing") if isinstance(raw, Mapping) else None
        source = timing if isinstance(timing, Mapping) else raw
        return {
            "tf_15m_available": bool(source.get("tf_15m_available", False)),
            "tf_15m_bias": source.get("tf_15m_bias", "unavailable"),
            "tf_15m_closed_bar_ts": source.get("tf_15m_closed_bar_ts"),
            "tf_15m_structure_token": source.get("tf_15m_structure_token"),
            "tf_15m_block_long": bool(source.get("tf_15m_block_long", False)),
            "tf_15m_block_short": bool(source.get("tf_15m_block_short", False)),
        }

    def _count_outcome(self, reason: str) -> None:
        self._episode_outcomes[reason] = self._episode_outcomes.get(reason, 0) + 1

    def _consume_episode(
        self,
        episode_id: str,
        reason: str,
        *,
        candidate_handling_gap: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        if self.episodes.terminal_reason(episode_id) is not None:
            return False
        terminal_evidence = (
            {"candidate_handling_gap": dict(candidate_handling_gap)}
            if candidate_handling_gap is not None
            else None
        )
        self.episodes.mark_terminal(
            episode_id,
            reason,
            evidence=terminal_evidence,
        )
        if candidate_handling_gap is not None:
            self._remember_candidate_handling_gap(
                candidate_handling_gap,
                episode_id=episode_id,
            )
        self._count_outcome(reason)
        return True

    @staticmethod
    def _normalize_pnl_resolution(payload: Mapping[str, Any]) -> dict:
        normalized = dict(payload)
        attribution = payload.get("attribution")
        if isinstance(attribution, Mapping):
            for key in (
                "strategy_owner",
                "intent_id",
                "episode_id",
                "plan_hash",
                "tactical_source",
            ):
                normalized.setdefault(key, attribution.get(key))
        normalized["pnl_usdt"] = payload.get(
            "pnl_usdt", payload.get("realized_pnl_net_usdt", payload.get("pnl"))
        )
        normalized["resolved_at"] = payload.get(
            "resolved_at", payload.get("timestamp", time.time())
        )
        normalized["status"] = payload.get(
            "pnl_status", payload.get("status", "final")
        )
        normalized["estimated"] = bool(
            payload.get("estimated") or payload.get("is_estimated")
        )
        normalized["mismatch"] = bool(
            payload.get("mismatch")
            or str(payload.get("pnl_status") or "").lower() == "mismatch"
        )
        normalized["close_reason"] = (
            payload.get("close_reason")
            or payload.get("final_close_cause")
            or payload.get("close_cause")
            or "pnl_resolved"
        )
        return normalized

    def _refresh_status(
        self,
        *,
        force: bool = False,
        now: Optional[float] = None,
    ) -> None:
        evaluated_at = float(self.now_fn()) if now is None else float(now)
        if (
            not force
            and evaluated_at - self._last_status_write_at < STATUS_REFRESH_SECONDS
        ):
            return
        try:
            # Recompute rolling-window eviction before projecting the read model.
            self.governor.can_open(
                now=evaluated_at,
                active_count=self._active_slot_count(),
            )
            write_status(self.paths, self.operational_status(now=evaluated_at))
            self._last_status_write_at = evaluated_at
        except Exception as exc:
            self._log_warning("Tactical V2 status refresh failed: %s", exc)

    def _restore(self) -> None:
        terminal_by_episode: Dict[str, str] = {}
        for event in self.store.read_events():
            data = event.get("data") or {}
            event_type = event.get("event_type")
            if event_type == "governor_integrity_halted" and (
                data.get("reason") == "message_identity_conflict"
            ):
                evidence = data.get("evidence") or {}
                if isinstance(evidence, Mapping):
                    self._handled_message_identity_conflicts.add(
                        self._message_identity_conflict_identity(evidence)
                    )
            if event_type == "candidate_handled":
                self._remember_candidate_receipt(
                    data,
                    halted_at=event.get("emitted_at"),
                    incident_id=self._optional_text(event.get("event_id")),
                )
                continue
            if event_type == "candidate_receipt_integrity_acknowledged":
                self._apply_candidate_receipt_integrity_acknowledgement(data)
                continue
            if event_type == "episode_terminal":
                registry_state = data.get("registry_state") or {}
                episode_id = str(data.get("episode_id") or "")
                reason = str(registry_state.get("terminal_reason") or "")
                if episode_id and reason:
                    terminal_by_episode[episode_id] = reason
                    evidence = data.get("evidence") or {}
                    gap = (
                        evidence.get("candidate_handling_gap")
                        if isinstance(evidence, Mapping)
                        else None
                    )
                    if isinstance(gap, Mapping):
                        self._remember_candidate_handling_gap(
                            gap,
                            episode_id=episode_id,
                        )
                continue
            if event_type == "intent_created" and isinstance(data.get("intent"), dict):
                try:
                    intent = TacticalIntent(**data["intent"])
                except (TypeError, ValueError):
                    self.governor.activate_integrity_halt(
                        "intent_restore_failed",
                        evidence={"intent_id": data.get("intent_id")},
                    )
                    continue
                lane = self._validated_lane(data.get("lane"))
                self._intents[intent.intent_id] = {
                    "intent": intent,
                    "entry_state": None,
                    "state": data.get("state", "ready_for_quote"),
                    "lane": lane,
                    "replayed": bool(data.get("replayed", False)),
                    "updated_at": float(data.get("updated_at", intent.created_at)),
                    "shadow_entry_state": None,
                    "shadow_state": (
                        data.get("shadow_state", "ready_for_quote")
                        if lane == "live"
                        else None
                    ),
                    "shadow_updated_at": float(data.get("updated_at", intent.created_at)),
                    "shadow_filled": False,
                    "parity_category": None,
                }
            elif event_type == "intent_transition":
                record = self._intents.get(data.get("intent_id"))
                if record is not None:
                    transition_lane = self._validated_lane(data.get("lane"))
                    projection = (
                        record.get("lane") == "live" and transition_lane == "shadow"
                    )
                    updated_at = float(
                        data.get("updated_at", event.get("emitted_at", 0))
                    )
                    ignored = {
                        "intent_id",
                        "episode_id",
                        "state",
                        "lane",
                        "projection",
                        "updated_at",
                    }
                    if projection:
                        record["shadow_state"] = data.get(
                            "state", record.get("shadow_state")
                        )
                        record["shadow_updated_at"] = updated_at
                        for key, value in data.items():
                            if key not in ignored:
                                record[f"shadow_{key}"] = value
                        if float(data.get("filled_qty") or 0) > 0 or record.get(
                            "shadow_state"
                        ) in {"protected", "closing", "closed_final"}:
                            record["shadow_filled"] = True
                        self._restore_entry_state(record, lane="shadow", projection=True)
                    else:
                        record["state"] = data.get("state", record["state"])
                        record["updated_at"] = updated_at
                        for key, value in data.items():
                            if key not in ignored:
                                record[key] = value
                        self._restore_entry_state(
                            record,
                            lane=record.get("lane", "unknown"),
                            projection=False,
                        )
            elif event_type == "parity_compared":
                record = self._intents.get(data.get("intent_id"))
                if record is not None:
                    record["parity_category"] = data.get("category")
        self._episode_outcomes = {}
        for reason in terminal_by_episode.values():
            self._count_outcome(reason)
        self._parity_mismatches = self._parity_summary()["mismatch_count"]

    def _restore_entry_state(
        self,
        record: Dict[str, Any],
        *,
        lane: str,
        projection: bool,
    ) -> None:
        prefix = "shadow_" if projection else ""
        state = record.get(f"{prefix}state")
        requested_qty = record.get(f"{prefix}requested_qty")
        if state not in _ENTRY_RECONCILE_STATES or requested_qty is None:
            return
        try:
            if lane not in {"live", "shadow"}:
                raise ValueError("durable intent lane is unknown")
            requested = float(requested_qty)
            filled = float(record.get(f"{prefix}filled_qty") or 0)
            remaining = float(
                record.get(f"{prefix}remaining_qty", max(0.0, requested - filled))
            )
            record[f"{prefix}entry_state"] = EntryState(
                intent=record["intent"],
                lane=lane,
                status=state,
                requested_qty=requested,
                filled_qty=filled,
                remaining_qty=remaining,
                entry_price=record.get(f"{prefix}entry_price"),
                cancel_reason=record.get(f"{prefix}cancel_reason"),
                terminal_reason=record.get(f"{prefix}terminal_reason"),
                slot_held=True,
            )
        except (TypeError, ValueError):
            self.governor.activate_integrity_halt(
                "entry_state_restore_failed",
                evidence={
                    "intent_id": record["intent"].intent_id,
                    "lane": lane,
                },
            )

    @staticmethod
    def _validated_lane(value: Any) -> str:
        lane = str(value or "").strip().lower()
        return lane if lane in {"live", "shadow"} else "unknown"

    def _log_warning(self, message: str, *args: Any) -> None:
        warning = getattr(self.logger, "warning", None)
        if warning:
            warning(message, *args)
