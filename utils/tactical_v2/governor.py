"""Persistent rolling-PnL and admission governor for Tactical V2."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from .store import TacticalStore


ROLLING_WINDOW_SECONDS = 24 * 60 * 60
ROLLING_LOSS_LIMIT_USDT = -15.0
LOSS_STREAK_COUNT = 3
LOSS_STREAK_PAUSE_SECONDS = 60 * 60
MAX_CONCURRENT = 3
_INTEGRITY_PROOF_KEYS = frozenset({"ownership", "orders", "positions", "protection"})


@dataclass(frozen=True)
class FinalApplyResult:
    accepted: bool
    reason: str
    delta_pnl: float = 0.0
    close_identity: Optional[str] = None


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason: str


class TacticalGovernor:
    """Reconstruct final truth and centralize Tactical V2 admission gates."""

    def __init__(
        self,
        *,
        store: Optional[TacticalStore] = None,
        now_fn: Callable[[], float] = time.time,
        rolling_window_seconds: int = ROLLING_WINDOW_SECONDS,
        rolling_loss_limit_usdt: float = ROLLING_LOSS_LIMIT_USDT,
        loss_streak_count: int = LOSS_STREAK_COUNT,
        loss_streak_pause_seconds: int = LOSS_STREAK_PAUSE_SECONDS,
        max_concurrent: int = MAX_CONCURRENT,
    ):
        self.store = store
        self.now_fn = now_fn
        self.rolling_window_seconds = int(rolling_window_seconds)
        self.rolling_loss_limit_usdt = float(rolling_loss_limit_usdt)
        self.loss_streak_count = int(loss_streak_count)
        self.loss_streak_pause_seconds = int(loss_streak_pause_seconds)
        self.max_concurrent = int(max_concurrent)
        if self.rolling_window_seconds <= 0:
            raise ValueError("rolling_window_seconds must be positive")
        if not math.isfinite(self.rolling_loss_limit_usdt):
            raise ValueError("rolling_loss_limit_usdt must be finite")
        if self.loss_streak_count <= 0 or self.loss_streak_pause_seconds <= 0:
            raise ValueError("loss streak settings must be positive")
        if self.max_concurrent != MAX_CONCURRENT:
            raise ValueError("Tactical V2 max_concurrent must remain fixed at 3")

        self._lock = threading.RLock()
        self._seen_resolution_ids = set()
        self._resolutions_by_id: Dict[str, Dict[str, Any]] = {}
        self._finals: Dict[str, Dict[str, Any]] = {}
        self._memory_events = []
        self._memory_seq = 0
        self._last_cooldown_trigger_seq = 0
        self._pause_until = 0.0
        self._integrity_halt: Optional[Dict[str, Any]] = None
        self._rolling_pnl = 0.0
        self._final_episode_count = 0
        self._loss_streak = 0
        self._evaluation_now = float(self.now_fn())
        self._restore()
        self._recompute(self._evaluation_now)

    @property
    def rolling_pnl(self) -> float:
        return self._rolling_pnl

    @property
    def final_episode_count(self) -> int:
        return self._final_episode_count

    @property
    def loss_streak(self) -> int:
        return self._loss_streak

    @property
    def pause_until(self) -> float:
        return self._pause_until

    @property
    def integrity_halt(self) -> Optional[Dict[str, Any]]:
        return None if self._integrity_halt is None else dict(self._integrity_halt)

    def resolution_by_id(self, resolution_id: str) -> Optional[Dict[str, Any]]:
        """Return canonical durable final truth for idempotent downstream recovery."""
        with self._lock:
            resolution = self._resolutions_by_id.get(str(resolution_id or ""))
            return None if resolution is None else dict(resolution)

    def apply_final(self, resolution: Any) -> FinalApplyResult:
        normalized, rejection = self._normalize_final(resolution)
        if rejection is not None:
            return FinalApplyResult(False, rejection)
        assert normalized is not None

        with self._lock:
            resolution_id = normalized["resolution_id"]
            if resolution_id in self._seen_resolution_ids:
                return FinalApplyResult(False, "duplicate_resolution")

            close_identity = normalized["close_identity"]
            previous = self._finals.get(close_identity)
            previous_pnl = float(previous["pnl_usdt"]) if previous else 0.0
            event = self._append_event(
                "governor_final_applied",
                {
                    "resolution": normalized,
                    "close_identity": close_identity,
                },
            )
            self._apply_event(event)

            effective_now = max(
                self._evaluation_now,
                float(self.now_fn()),
                normalized["resolved_at"],
            )
            self._recompute(effective_now)
            if self._loss_streak >= self.loss_streak_count:
                pause_until = max(
                    self._pause_until,
                    effective_now + self.loss_streak_pause_seconds,
                )
                cooldown = self._append_event(
                    "governor_cooldown_started",
                    {
                        "trigger_seq": event["seq"],
                        "pause_until": pause_until,
                        "reason": "loss_streak",
                    },
                )
                self._apply_event(cooldown)
                self._recompute(effective_now)

            return FinalApplyResult(
                True,
                "final_applied",
                delta_pnl=normalized["pnl_usdt"] - previous_pnl,
                close_identity=close_identity,
            )

    def can_open(
        self,
        *,
        now: Optional[float] = None,
        active_count: int = 0,
        pending_count: int = 0,
        same_symbol_state: Any = False,
        account_gate: Any = True,
        integrity_state: Any = None,
    ) -> AdmissionDecision:
        evaluated_at = float(self.now_fn()) if now is None else float(now)
        if not math.isfinite(evaluated_at):
            return AdmissionDecision(False, "integrity_halt")
        with self._lock:
            self._recompute(evaluated_at)
            external_integrity = (
                bool(integrity_state.get("halted"))
                if isinstance(integrity_state, Mapping)
                else bool(integrity_state)
            )
            if self._integrity_halt is not None or external_integrity:
                return AdmissionDecision(False, "integrity_halt")
            if evaluated_at < self._pause_until:
                return AdmissionDecision(False, "loss_streak_pause")
            if self._rolling_pnl <= self.rolling_loss_limit_usdt:
                return AdmissionDecision(False, "rolling_loss_pause")
            try:
                occupied = int(active_count) + int(pending_count)
            except (TypeError, ValueError):
                return AdmissionDecision(False, "integrity_halt")
            if occupied >= self.max_concurrent:
                return AdmissionDecision(False, "capacity_full")
            if self._same_symbol_occupied(same_symbol_state):
                return AdmissionDecision(False, "same_symbol_exposure")
            if not self._account_gate_allowed(account_gate):
                return AdmissionDecision(False, "account_reject")
            return AdmissionDecision(True, "admitted")

    def activate_integrity_halt(
        self,
        reason: str,
        *,
        evidence: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if not reason:
            raise ValueError("integrity halt reason is required")
        with self._lock:
            event = self._append_event(
                "governor_integrity_halted",
                {
                    "reason": str(reason),
                    "evidence": dict(evidence or {}),
                    "halted_at": float(self.now_fn()),
                },
            )
            self._apply_event(event)

    def activate_integrity_halt_if_clear(
        self,
        reason: str,
        *,
        evidence: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        """Atomically preserve any halt raised while recovery I/O was in flight."""
        if not reason:
            raise ValueError("integrity halt reason is required")
        with self._lock:
            if self._integrity_halt is not None:
                return False
            event = self._append_event(
                "governor_integrity_halted",
                {
                    "reason": str(reason),
                    "evidence": dict(evidence or {}),
                    "halted_at": float(self.now_fn()),
                },
            )
            self._apply_event(event)
            return True

    def clear_integrity_halt(
        self,
        reconciliation_id: str,
        proof: Mapping[str, Any],
    ) -> bool:
        if self._integrity_halt is None:
            return True
        if not reconciliation_id or not isinstance(proof, Mapping):
            return False
        if not all(proof.get(key) is True for key in _INTEGRITY_PROOF_KEYS):
            return False
        with self._lock:
            event = self._append_event(
                "governor_integrity_cleared",
                {
                    "reconciliation_id": str(reconciliation_id),
                    "proof": dict(proof),
                    "cleared_at": float(self.now_fn()),
                },
            )
            self._apply_event(event)
            return True

    def _restore(self) -> None:
        if self.store is None:
            return
        for event in self.store.read_events():
            self._apply_event(event)

    def _append_event(self, event_type: str, data: Mapping[str, Any]) -> Dict[str, Any]:
        if self.store is not None:
            return self.store.append(event_type, data)
        self._memory_seq += 1
        event = {
            "schema_version": 2,
            "seq": self._memory_seq,
            "event_id": f"memory-{self._memory_seq}",
            "event_type": event_type,
            "emitted_at": float(self.now_fn()),
            "data": dict(data),
        }
        self._memory_events.append(event)
        return event

    def _apply_event(self, event: Mapping[str, Any]) -> None:
        event_type = event.get("event_type")
        data = event.get("data") or {}
        if event_type == "governor_final_applied":
            resolution = dict(data.get("resolution") or {})
            resolution_id = resolution.get("resolution_id")
            close_identity = data.get("close_identity") or resolution.get("close_identity")
            if not resolution_id or not close_identity:
                return
            self._seen_resolution_ids.add(resolution_id)
            self._resolutions_by_id[resolution_id] = dict(resolution)
            previous = self._finals.get(close_identity)
            resolution["_first_seq"] = (
                previous["_first_seq"] if previous else int(event["seq"])
            )
            resolution["_event_seq"] = int(event["seq"])
            self._finals[close_identity] = resolution
        elif event_type == "governor_cooldown_started":
            self._last_cooldown_trigger_seq = max(
                self._last_cooldown_trigger_seq,
                int(data.get("trigger_seq", 0) or 0),
            )
            self._pause_until = max(
                self._pause_until,
                float(data.get("pause_until", 0) or 0),
            )
        elif event_type == "governor_integrity_halted":
            self._integrity_halt = dict(data)
        elif event_type == "governor_integrity_cleared":
            self._integrity_halt = None

    def _recompute(self, now: float) -> None:
        self._evaluation_now = float(now)
        cutoff = self._evaluation_now - self.rolling_window_seconds
        rolling = [
            final
            for final in self._finals.values()
            if cutoff < float(final["resolved_at"]) <= self._evaluation_now
        ]
        self._rolling_pnl = sum(float(final["pnl_usdt"]) for final in rolling)
        self._final_episode_count = len(rolling)

        unconsumed = [
            final
            for final in self._finals.values()
            if int(final.get("_first_seq", 0)) > self._last_cooldown_trigger_seq
        ]
        unconsumed.sort(
            key=lambda final: (
                float(final["resolved_at"]),
                int(final.get("_first_seq", 0)),
            )
        )
        streak = 0
        for final in unconsumed:
            if float(final["pnl_usdt"]) < 0:
                streak += 1
            else:
                streak = 0
        self._loss_streak = streak

    @staticmethod
    def _normalize_final(
        resolution: Any,
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        if is_dataclass(resolution):
            raw = asdict(resolution)
        elif isinstance(resolution, Mapping):
            raw = dict(resolution)
        else:
            return None, "not_final"

        resolution_id = str(raw.get("resolution_id") or "").strip()
        if not resolution_id:
            return None, "missing_resolution_id"
        position_id = str(raw.get("position_id") or "").strip()
        entry_request_id = str(raw.get("entry_request_id") or "").strip()
        if not position_id and not entry_request_id:
            return None, "missing_close_identity"
        if str(raw.get("status", "final")).strip().lower() != "final":
            return None, "not_final"
        if raw.get("estimated") is True or raw.get("is_estimated") is True:
            return None, "estimated_final"
        if raw.get("mismatch") is True:
            return None, "mismatched_final"
        try:
            pnl = float(raw.get("pnl_usdt"))
            resolved_at = float(raw.get("resolved_at"))
        except (TypeError, ValueError):
            return None, "non_finite_final"
        if not math.isfinite(pnl) or not math.isfinite(resolved_at):
            return None, "non_finite_final"

        close_identity = (
            f"position:{position_id}"
            if position_id
            else f"entry_request:{entry_request_id}"
        )
        normalized = dict(raw)
        normalized.update(
            {
                "resolution_id": resolution_id,
                "position_id": position_id,
                "entry_request_id": entry_request_id,
                "pnl_usdt": pnl,
                "resolved_at": resolved_at,
                "status": "final",
                "close_identity": close_identity,
            }
        )
        return normalized, None

    @staticmethod
    def _same_symbol_occupied(value: Any) -> bool:
        if isinstance(value, Mapping):
            return bool(value.get("occupied") or value.get("blocked") or value.get("exposure"))
        return bool(value)

    @staticmethod
    def _account_gate_allowed(value: Any) -> bool:
        if isinstance(value, AdmissionDecision):
            return value.allowed
        if isinstance(value, Mapping):
            return bool(value.get("allowed", False))
        if isinstance(value, tuple) and value:
            return bool(value[0])
        return bool(value)
