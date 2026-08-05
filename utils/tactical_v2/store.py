"""Durable append-only event store for Tactical V2."""

from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .models import SCHEMA_VERSION


class TacticalStoreIntegrityError(RuntimeError):
    """Raised when new events cannot safely follow corrupted committed history."""


class TacticalStore:
    """One fsynced Tactical event writer with replayable atomic snapshots."""

    def __init__(self, paths: Any):
        self.event_path = Path(paths.tactical_v2_events)
        self.snapshot_path = Path(paths.tactical_v2_state)
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        events, warnings, integrity = self._read_ledger()
        self._integrity_failure = integrity
        self._next_seq = events[-1]["seq"] + 1 if events else 1
        self._partial_tail = any(
            warning.get("reason") == "partial_tail_ignored" for warning in warnings
        )
        self._unterminated_valid_row = (
            self.event_path.exists()
            and self.event_path.stat().st_size > 0
            and not self.event_path.read_bytes().endswith(b"\n")
            and not self._partial_tail
        )

    def append(
        self,
        event_type: str,
        data: Mapping[str, Any],
        *,
        emitted_at: Optional[float] = None,
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not event_type:
            raise ValueError("event_type is required")
        if not isinstance(data, Mapping):
            raise ValueError("data must be a mapping")

        timestamp = time.time() if emitted_at is None else float(emitted_at)
        if not math.isfinite(timestamp):
            raise ValueError("emitted_at must be finite")

        with self._lock:
            if self._integrity_failure is not None:
                raise TacticalStoreIntegrityError(
                    "committed Tactical V2 history is corrupt; refusing to append"
                )
            self._repair_tail_before_append()
            event = {
                "schema_version": SCHEMA_VERSION,
                "seq": self._next_seq,
                "event_id": event_id or uuid.uuid4().hex,
                "event_type": str(event_type),
                "emitted_at": timestamp,
                "data": dict(data),
            }
            encoded = json.dumps(
                event,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            with self.event_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._next_seq += 1
            return event

    def read_events(self) -> List[Dict[str, Any]]:
        """Return committed valid events in sequence order."""
        with self._lock:
            events, _, _ = self._read_ledger()
            return copy.deepcopy(events)

    def write_snapshot(self, state: Mapping[str, Any]) -> None:
        snapshot = copy.deepcopy(dict(state))
        snapshot.setdefault("schema_version", SCHEMA_VERSION)
        snapshot.setdefault("last_seq", self._next_seq - 1)
        encoded = json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        temp_path = self.snapshot_path.with_name(
            f".{self.snapshot_path.name}.{os.getpid()}.tmp"
        )

        with self._lock:
            with temp_path.open("w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.snapshot_path)
            self._fsync_directory(self.snapshot_path.parent)

    def rebuild(self) -> Dict[str, Any]:
        with self._lock:
            events, warnings, integrity = self._read_ledger()
            state, snapshot_warning = self._load_snapshot(
                last_ledger_seq=events[-1]["seq"] if events else 0
            )
            if snapshot_warning:
                warnings.append(snapshot_warning)
            if state is None:
                state = self._empty_state()

            snapshot_seq = state.get("last_seq", 0)
            for event in events:
                if event["seq"] <= snapshot_seq:
                    continue
                self._apply_event(state, event)
            state["last_seq"] = events[-1]["seq"] if events else snapshot_seq
            state["schema_version"] = SCHEMA_VERSION
            state["recovery_warnings"] = warnings
            state["integrity_failure"] = integrity
            self._integrity_failure = integrity
            self._next_seq = state["last_seq"] + 1
            self._partial_tail = any(
                warning.get("reason") == "partial_tail_ignored" for warning in warnings
            )
            return state

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "last_seq": 0,
            "intents": {},
            "episodes": {},
            "governor": {},
            "recovery_warnings": [],
            "integrity_failure": None,
        }

    def _load_snapshot(
        self,
        *,
        last_ledger_seq: int,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not self.snapshot_path.exists():
            return None, None
        try:
            raw = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("snapshot root must be an object")
            last_seq = raw.get("last_seq")
            if isinstance(last_seq, bool) or not isinstance(last_seq, int) or last_seq < 0:
                raise ValueError("snapshot last_seq must be a non-negative integer")
            if last_seq > last_ledger_seq:
                raise ValueError("snapshot is ahead of the authoritative ledger")
            raw.setdefault("intents", {})
            raw.setdefault("episodes", {})
            raw.setdefault("governor", {})
            return raw, None
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return None, {
                "reason": "invalid_snapshot_ignored",
                "detail": str(exc),
            }

    def _read_ledger(
        self,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not self.event_path.exists():
            return [], [], None
        raw = self.event_path.read_bytes()
        lines = raw.splitlines(keepends=True)
        events: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []
        expected_seq = 1

        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line.decode("utf-8"))
                self._validate_event(event, expected_seq)
            except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                is_partial_tail = index == len(lines) - 1 and not line.endswith(b"\n")
                if is_partial_tail:
                    warnings.append(
                        {
                            "reason": "partial_tail_ignored",
                            "line": index + 1,
                            "detail": str(exc),
                        }
                    )
                    break
                return events, warnings, {
                    "reason": "malformed_committed_event",
                    "line": index + 1,
                    "detail": str(exc),
                }
            events.append(event)
            expected_seq += 1
        return events, warnings, None

    @staticmethod
    def _validate_event(event: Any, expected_seq: int) -> None:
        if not isinstance(event, dict):
            raise ValueError("event root must be an object")
        required = {
            "schema_version",
            "seq",
            "event_id",
            "event_type",
            "emitted_at",
            "data",
        }
        if set(event) != required:
            raise ValueError("event envelope fields do not match schema")
        if event["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported event schema_version")
        if event["seq"] != expected_seq:
            raise ValueError(
                f"event sequence {event['seq']} does not match expected {expected_seq}"
            )
        if not event["event_id"] or not event["event_type"]:
            raise ValueError("event identity fields are required")
        if not isinstance(event["data"], dict):
            raise ValueError("event data must be an object")
        emitted_at = event["emitted_at"]
        if isinstance(emitted_at, bool) or not isinstance(emitted_at, (int, float)):
            raise ValueError("event emitted_at must be numeric")
        if not math.isfinite(float(emitted_at)):
            raise ValueError("event emitted_at must be finite")

    @staticmethod
    def _apply_event(state: Dict[str, Any], event: Mapping[str, Any]) -> None:
        event_type = event["event_type"]
        data = copy.deepcopy(event["data"])
        intent_id = data.get("intent_id")
        if event_type == "intent_created" and intent_id:
            state["intents"][intent_id] = data
        elif intent_id:
            previous = state["intents"].get(intent_id, {})
            if not isinstance(previous, dict):
                previous = {"state": previous}
            previous.update(data)
            if event_type == "episode_terminal":
                previous["state"] = "terminal"
            else:
                previous.setdefault("state", event_type)
            state["intents"][intent_id] = previous

        registry_key = data.get("registry_key")
        registry_state = data.get("registry_state")
        if registry_key and isinstance(registry_state, dict):
            current = state["episodes"].get(registry_key)
            if (
                not isinstance(current, dict)
                or TacticalStore._episode_epoch(registry_state)
                >= TacticalStore._episode_epoch(current)
            ):
                state["episodes"][registry_key] = registry_state
        state["last_seq"] = event["seq"]

    @staticmethod
    def _episode_epoch(state: Mapping[str, Any]) -> int:
        try:
            return int(state.get("epoch_seq", -1))
        except (TypeError, ValueError):
            return -1

    def _repair_tail_before_append(self) -> None:
        if self._partial_tail:
            raw = self.event_path.read_bytes()
            committed_end = raw.rfind(b"\n") + 1
            with self.event_path.open("r+b") as handle:
                handle.truncate(committed_end)
                handle.flush()
                os.fsync(handle.fileno())
            self._partial_tail = False
            self._unterminated_valid_row = False
            return
        if self._unterminated_valid_row:
            with self.event_path.open("ab") as handle:
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._unterminated_valid_row = False

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            descriptor = os.open(str(directory), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)
