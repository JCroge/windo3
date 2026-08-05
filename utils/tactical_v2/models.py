"""Immutable Tactical V2 domain schemas and deterministic identities."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from utils.symbol import to_internal


SCHEMA_VERSION = 2
TACTICAL_V2_MARGIN_USDT = 100.0
TACTICAL_V2_MAX_LEVERAGE = 5
TACTICAL_V2_ENTRY_TTL_SECONDS = 900
TACTICAL_V2_MAX_HOLD_SECONDS = 90 * 60
_VALID_SIDES = frozenset({"long", "short"})


def _finite_float(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    if positive and parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _canonical_decimal(value: Any, name: str) -> str:
    parsed = _finite_float(value, name)
    try:
        decimal = Decimal(str(parsed))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if decimal == 0:
        return "0"
    return format(decimal.normalize(), "f")


def _stable_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = str(raw.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _first_take_profit(value: Any) -> float:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if not value:
            raise ValueError("take_profit is required")
        value = value[0]
    return _finite_float(value, "take_profit", positive=True)


def _validate_plan_side(side: str, entry: float, stop: float, take_profit: float) -> None:
    if side == "long":
        if stop >= entry:
            raise ValueError("stop_loss must be below long entry_ref")
        if take_profit <= entry:
            raise ValueError("take_profit must be above long entry_ref")
        return
    if stop <= entry:
        raise ValueError("stop_loss must be above short entry_ref")
    if take_profit >= entry:
        raise ValueError("take_profit must be below short entry_ref")


@dataclass(frozen=True)
class TacticalCandidate:
    candidate_id: str
    namespace: str
    symbol: str
    side: str
    entry_ref: float
    stop_loss: float
    take_profit: float
    leverage: int
    source_shadow_id: str
    tactical_source: str
    created_at: float
    tactical_rr: Optional[float] = None
    tactical_ev: Optional[float] = None
    tactical_cost_gate: Optional[str] = None
    tf_15m_closed_bar_ts: Optional[float] = None
    tf_15m_structure_token: Optional[str] = None
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "TacticalCandidate":
        side = str(raw.get("side") or "").strip().lower()
        if side not in _VALID_SIDES:
            raise ValueError(f"side must be one of {sorted(_VALID_SIDES)}")

        symbol = to_internal(_required_text(raw, "symbol"))
        entry = _finite_float(raw.get("entry_ref"), "entry_ref", positive=True)
        stop = _finite_float(raw.get("stop_loss"), "stop_loss", positive=True)
        take_profit = _first_take_profit(raw.get("take_profit"))
        _validate_plan_side(side, entry, stop, take_profit)

        leverage_raw = raw.get("leverage")
        if isinstance(leverage_raw, bool):
            raise ValueError("leverage must be an integer")
        try:
            leverage = int(leverage_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("leverage must be an integer") from exc
        if leverage != leverage_raw or not 1 <= leverage <= TACTICAL_V2_MAX_LEVERAGE:
            raise ValueError(
                f"leverage must be an integer between 1 and {TACTICAL_V2_MAX_LEVERAGE}"
            )

        created_at = _finite_float(raw.get("created_at"), "created_at")
        namespace = str(raw.get("namespace") or "live").strip().lower()
        if not namespace:
            raise ValueError("namespace is required")

        def optional_finite(key: str) -> Optional[float]:
            value = raw.get(key)
            return None if value is None else _finite_float(value, key)

        return cls(
            candidate_id=_required_text(raw, "candidate_id"),
            namespace=namespace,
            symbol=symbol,
            side=side,
            entry_ref=entry,
            stop_loss=stop,
            take_profit=take_profit,
            leverage=leverage,
            source_shadow_id=_required_text(raw, "source_shadow_id"),
            tactical_source=_required_text(raw, "tactical_source"),
            created_at=created_at,
            tactical_rr=optional_finite("tactical_rr"),
            tactical_ev=optional_finite("tactical_ev"),
            tactical_cost_gate=(
                None
                if raw.get("tactical_cost_gate") is None
                else str(raw.get("tactical_cost_gate"))
            ),
            tf_15m_closed_bar_ts=optional_finite("tf_15m_closed_bar_ts"),
            tf_15m_structure_token=(
                None
                if raw.get("tf_15m_structure_token") is None
                else str(raw.get("tf_15m_structure_token"))
            ),
        )


@dataclass(frozen=True)
class TacticalIntent:
    intent_id: str
    candidate_id: str
    episode_id: str
    plan_hash: str
    namespace: str
    symbol: str
    side: str
    entry_ref: float
    stop_loss: float
    take_profit: float
    leverage: int
    margin_usdt: float
    source_shadow_id: str
    tactical_source: str
    created_at: float
    expires_at: float
    max_hold_seconds: int
    tactical_rr: Optional[float] = None
    tactical_ev: Optional[float] = None
    tactical_cost_gate: Optional[str] = None
    tf_15m_closed_bar_ts: Optional[float] = None
    tf_15m_structure_token: Optional[str] = None
    schema_version: int = SCHEMA_VERSION
    track: str = "tactical"
    exit_profile: str = "tactical_v2"
    strategy_owner: str = "tactical_v2"

    @classmethod
    def from_candidate(
        cls,
        raw: Mapping[str, Any] | TacticalCandidate,
        episode_id: str,
    ) -> "TacticalIntent":
        candidate = raw if isinstance(raw, TacticalCandidate) else TacticalCandidate.from_raw(raw)
        episode = str(episode_id or "").strip()
        if not episode:
            raise ValueError("episode_id is required")

        plan_hash = _stable_digest(
            {
                "entry_ref": _canonical_decimal(candidate.entry_ref, "entry_ref"),
                "leverage": str(candidate.leverage),
                "side": candidate.side,
                "source_shadow_id": candidate.source_shadow_id,
                "stop_loss": _canonical_decimal(candidate.stop_loss, "stop_loss"),
                "symbol": candidate.symbol,
                "tactical_source": candidate.tactical_source,
                "take_profit": _canonical_decimal(candidate.take_profit, "take_profit"),
            }
        )
        intent_id = _stable_digest(
            {
                "candidate_id": candidate.candidate_id,
                "episode_id": episode,
                "plan_hash": plan_hash,
            }
        )

        return cls(
            intent_id=intent_id,
            candidate_id=candidate.candidate_id,
            episode_id=episode,
            plan_hash=plan_hash,
            namespace=candidate.namespace,
            symbol=candidate.symbol,
            side=candidate.side,
            entry_ref=candidate.entry_ref,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            leverage=candidate.leverage,
            margin_usdt=TACTICAL_V2_MARGIN_USDT,
            source_shadow_id=candidate.source_shadow_id,
            tactical_source=candidate.tactical_source,
            created_at=candidate.created_at,
            expires_at=candidate.created_at + TACTICAL_V2_ENTRY_TTL_SECONDS,
            max_hold_seconds=TACTICAL_V2_MAX_HOLD_SECONDS,
            tactical_rr=candidate.tactical_rr,
            tactical_ev=candidate.tactical_ev,
            tactical_cost_gate=candidate.tactical_cost_gate,
            tf_15m_closed_bar_ts=candidate.tf_15m_closed_bar_ts,
            tf_15m_structure_token=candidate.tf_15m_structure_token,
        )


@dataclass(frozen=True)
class TacticalEvent:
    seq: int
    event_id: str
    event_type: str
    emitted_at: float
    data: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.seq < 1:
            raise ValueError("seq must be positive")
        _finite_float(self.emitted_at, "emitted_at")
        if not self.event_id:
            raise ValueError("event_id is required")
        if not self.event_type:
            raise ValueError("event_type is required")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True)
class LaneState:
    lane: str
    intent_id: str
    state: str
    updated_at: float
    filled_qty: float = 0.0
    position_id: Optional[str] = None
    terminal_reason: Optional[str] = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class ProtectionIdentity:
    entry_client_id: str
    tp_client_id: str
    sl_client_id: str
    protected_qty: float = 0.0
    exchange_algo_ids: Tuple[str, ...] = ()
    state: str = "unverified"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class FinalResolution:
    resolution_id: str
    position_id: str
    intent_id: str
    episode_id: str
    pnl_usdt: float
    resolved_at: float
    close_reason: str
    entry_request_id: Optional[str] = None
    status: str = "final"
    estimated: bool = False
    mismatch: bool = False
    lane: str = "live"
    schema_version: int = SCHEMA_VERSION
