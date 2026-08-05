"""Persistent structural episode registry for one-attempt Tactical admission."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from utils.symbol import to_internal

from .store import TacticalStore


@dataclass(frozen=True)
class EpisodeAssignment:
    episode_id: str
    epoch_seq: int
    eligible: bool
    reason: str
    terminal_reason: Optional[str] = None


class EpisodeRegistry:
    """Assign stable symbol/side epochs and persist reset evidence first."""

    def __init__(self, store: TacticalStore, *, namespace: str):
        self.store = store
        self.namespace = str(namespace).strip().lower()
        if not self.namespace:
            raise ValueError("namespace is required")
        self._lock = threading.RLock()
        self._states: Dict[str, Dict[str, Any]] = {}
        self._episode_states: Dict[str, Dict[str, Any]] = {}
        self._episode_keys: Dict[str, str] = {}
        self._restore()

    def assign(
        self,
        candidate: Mapping[str, Any] | Any,
        structure: Mapping[str, Any],
    ) -> EpisodeAssignment:
        symbol = to_internal(self._value(candidate, "symbol"))
        side = str(self._value(candidate, "side")).strip().lower()
        self._validate_side(side)
        key = self._key(symbol, side)

        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = self._new_state(
                    symbol=symbol,
                    side=side,
                    epoch_seq=1,
                    structure=structure,
                )
                self._persist("episode_assigned", key, state)
                return self._assignment(state, eligible=True, reason="eligible")

            self._observe_locked(key, state, structure)
            reset_reason = self._reset_reason(state, side, structure)
            if reset_reason is not None:
                evidence_state = copy.deepcopy(state)
                self._persist(
                    "episode_reset_evidence",
                    key,
                    evidence_state,
                    evidence={
                        "reason": reset_reason,
                        "closed_bar_ts": structure.get("tf_15m_closed_bar_ts"),
                        "structure_token": structure.get("tf_15m_structure_token"),
                    },
                )
                state = self._new_state(
                    symbol=symbol,
                    side=side,
                    epoch_seq=state["epoch_seq"] + 1,
                    structure=structure,
                )
                self._states[key] = state
                self._persist("episode_assigned", key, state)
                return self._assignment(state, eligible=True, reason="eligible")

            reason = "opposing_block" if self._is_blocked(side, structure) else "duplicate_episode"
            return self._assignment(state, eligible=False, reason=reason)

    def observe(
        self,
        symbol: str,
        side: str,
        structure: Mapping[str, Any],
    ) -> None:
        normalized_symbol = to_internal(symbol)
        normalized_side = str(side).strip().lower()
        self._validate_side(normalized_side)
        key = self._key(normalized_symbol, normalized_side)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return
            self._observe_locked(key, state, structure)

    def mark_terminal(self, episode_id: str, reason: str) -> None:
        if not reason:
            raise ValueError("terminal reason is required")
        with self._lock:
            state = self._episode_states.get(episode_id)
            key = self._episode_keys.get(episode_id)
            if state is None or key is None:
                raise KeyError(f"unknown episode_id: {episode_id}")
            if state.get("terminal"):
                if state.get("terminal_reason") != reason:
                    raise ValueError("episode already has a different terminal reason")
                return
            terminal_state = copy.deepcopy(state)
            terminal_state["terminal"] = True
            terminal_state["terminal_reason"] = str(reason)
            current = self._states.get(key)
            self._persist(
                "episode_terminal",
                key,
                terminal_state,
                evidence={"reason": reason},
                make_current=(
                    current is not None
                    and current.get("episode_id") == episode_id
                ),
            )

    def terminal_reason(self, episode_id: str) -> Optional[str]:
        with self._lock:
            state = self._episode_states.get(episode_id)
            if state is not None:
                reason = state.get("terminal_reason")
                return str(reason) if reason else None
        return None

    def _observe_locked(
        self,
        key: str,
        state: Dict[str, Any],
        structure: Mapping[str, Any],
    ) -> None:
        if not structure.get("tf_15m_available", False):
            return

        side = state["side"]
        bias = str(structure.get("tf_15m_bias") or "unavailable").lower()
        blocked = self._is_blocked(side, structure)
        changed = False
        evidence_reason: Optional[str] = None

        if blocked and not state.get("last_block", False):
            state["reset_pending"] = "opposing_block"
            evidence_reason = "opposing_block"
            changed = True
        if bias == "neutral" and not state.get("neutral_seen", False):
            state["neutral_seen"] = True
            evidence_reason = evidence_reason or "neutral_seen"
            changed = True
        if state.get("last_block") != blocked:
            state["last_block"] = blocked
            changed = True
        if state.get("current_bias") != bias:
            state["current_bias"] = bias
            changed = True

        if changed:
            event_type = "episode_reset_evidence" if evidence_reason else "episode_observed"
            self._persist(
                event_type,
                key,
                state,
                evidence={
                    "reason": evidence_reason or "observation",
                    "closed_bar_ts": structure.get("tf_15m_closed_bar_ts"),
                    "structure_token": structure.get("tf_15m_structure_token"),
                },
            )

    def _reset_reason(
        self,
        state: Mapping[str, Any],
        side: str,
        structure: Mapping[str, Any],
    ) -> Optional[str]:
        if not structure.get("tf_15m_available", False):
            return None
        if self._is_blocked(side, structure):
            return None

        bias = str(structure.get("tf_15m_bias") or "unavailable").lower()
        desired_bias = "bullish" if side == "long" else "bearish"
        if bias != desired_bias:
            return None
        if state.get("reset_pending") == "opposing_block":
            return "opposing_block_then_renewed"
        if state.get("neutral_seen"):
            return "neutral_then_renewed"

        token = structure.get("tf_15m_structure_token")
        if (
            state.get("terminal")
            and token
            and state.get("last_structure_token")
            and token != state.get("last_structure_token")
        ):
            return "new_confirmed_structure"
        return None

    def _new_state(
        self,
        *,
        symbol: str,
        side: str,
        epoch_seq: int,
        structure: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return {
            "namespace": self.namespace,
            "symbol": symbol,
            "side": side,
            "epoch_seq": epoch_seq,
            "episode_id": self._episode_id(symbol, side, epoch_seq),
            "attempted": True,
            "terminal": False,
            "terminal_reason": None,
            "current_bias": str(
                structure.get("tf_15m_bias") or "unavailable"
            ).lower(),
            "neutral_seen": False,
            "last_block": self._is_blocked(side, structure),
            "reset_pending": None,
            "last_structure_token": structure.get("tf_15m_structure_token"),
            "last_closed_bar_ts": structure.get("tf_15m_closed_bar_ts"),
        }

    def _persist(
        self,
        event_type: str,
        key: str,
        state: Mapping[str, Any],
        *,
        evidence: Optional[Mapping[str, Any]] = None,
        make_current: bool = True,
    ) -> None:
        stored_state = copy.deepcopy(dict(state))
        episode_id = str(stored_state["episode_id"])
        self._episode_states[episode_id] = stored_state
        self._episode_keys[episode_id] = key
        if make_current:
            self._states[key] = copy.deepcopy(stored_state)
        data: Dict[str, Any] = {
            "registry_key": key,
            "registry_state": copy.deepcopy(stored_state),
            "episode_id": episode_id,
        }
        if evidence is not None:
            data["evidence"] = dict(evidence)
        self.store.append(event_type, data)

    def _restore(self) -> None:
        for event in self.store.read_events():
            data = event.get("data", {})
            key = data.get("registry_key")
            state = data.get("registry_state")
            if key and isinstance(state, dict):
                restored = copy.deepcopy(state)
                episode_id = str(restored.get("episode_id") or "")
                if not episode_id:
                    continue
                self._episode_states[episode_id] = restored
                self._episode_keys[episode_id] = str(key)
                current = self._states.get(str(key))
                if (
                    current is None
                    or self._epoch_seq(restored) >= self._epoch_seq(current)
                ):
                    self._states[str(key)] = copy.deepcopy(restored)

    @staticmethod
    def _epoch_seq(state: Mapping[str, Any]) -> int:
        try:
            return int(state.get("epoch_seq", -1))
        except (TypeError, ValueError):
            return -1

    def _episode_id(self, symbol: str, side: str, epoch_seq: int) -> str:
        encoded = json.dumps(
            {
                "namespace": self.namespace,
                "symbol": symbol,
                "side": side,
                "epoch_seq": epoch_seq,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _value(candidate: Mapping[str, Any] | Any, key: str) -> Any:
        if isinstance(candidate, Mapping):
            value = candidate.get(key)
        else:
            value = getattr(candidate, key, None)
        if value is None or value == "":
            raise ValueError(f"candidate {key} is required")
        return value

    @staticmethod
    def _validate_side(side: str) -> None:
        if side not in {"long", "short"}:
            raise ValueError("side must be long or short")

    @staticmethod
    def _is_blocked(side: str, structure: Mapping[str, Any]) -> bool:
        return bool(structure.get(f"tf_15m_block_{side}", False))

    @staticmethod
    def _key(symbol: str, side: str) -> str:
        return f"{symbol}|{side}"

    @staticmethod
    def _assignment(
        state: Mapping[str, Any],
        *,
        eligible: bool,
        reason: str,
    ) -> EpisodeAssignment:
        return EpisodeAssignment(
            episode_id=state["episode_id"],
            epoch_seq=state["epoch_seq"],
            eligible=eligible,
            reason=reason,
            terminal_reason=state.get("terminal_reason"),
        )
