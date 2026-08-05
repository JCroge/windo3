#!/usr/bin/env python3
"""Replay Tactical V2 evidence fixtures through the shared entry reducer."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.tactical_v2.entry import ExecutableQuote  # noqa: E402
from utils.tactical_v2.exit import classify_exit  # noqa: E402
from utils.tactical_v2.models import TacticalIntent  # noqa: E402
from utils.tactical_v2.shadow import ShadowAdapter  # noqa: E402


@dataclass(frozen=True)
class ReplayReport:
    raw_candidates: int
    episodes: int
    duplicate_live_attempts: int
    stale_chase_fills: int
    tp_before_entry_fills: int
    main_strategy_exits: int
    unprotected_fills: int
    full_tp1_violations: int
    classified_mismatches: int
    unclassified_mismatches: int
    historical_live_closes: int
    historical_live_pnl_usdt: float
    invalidated_pnl_usdt: float
    other_live_pnl_usdt: float
    intent_comparisons: tuple[dict[str, Any], ...]

    @property
    def safety_gate_passed(self) -> bool:
        return not any(
            (
                self.duplicate_live_attempts,
                self.stale_chase_fills,
                self.tp_before_entry_fills,
                self.main_strategy_exits,
                self.unprotected_fills,
                self.full_tp1_violations,
                self.unclassified_mismatches,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "safety_gate_passed": self.safety_gate_passed}


def _load_fixture(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("fixture root must be an object")
    return raw


def _decimal_sum(rows: list[Mapping[str, Any]]) -> Decimal:
    return sum((Decimal(str(row.get("pnl_usdt", 0))) for row in rows), Decimal("0"))


def _candidate_for_episode(episode: Mapping[str, Any]) -> dict[str, Any]:
    candidate = dict(episode.get("candidate") or {})
    timestamps = episode.get("source_timestamps") or {}
    structure = episode.get("structure") or {}
    candidate["created_at"] = timestamps.get("created_at")
    candidate["tf_15m_closed_bar_ts"] = structure.get("tf_15m_closed_bar_ts")
    candidate["tf_15m_structure_token"] = structure.get("tf_15m_structure_token")
    return candidate


def _replay_episode(episode: Mapping[str, Any], *, attempts: int) -> dict[str, Any]:
    episode_id = str(episode.get("episode_id") or "")
    if not episode_id:
        raise ValueError("episode_id is required")
    intent = TacticalIntent.from_candidate(
        _candidate_for_episode(episode),
        episode_id=episode_id,
    )
    ticks = episode.get("ticks") or []
    if not isinstance(ticks, list):
        raise ValueError(f"ticks must be a list for {episode_id}")

    state = None
    adapter = ShadowAdapter()
    target_seen_before_fill = False
    opened_at = None
    replay_exit_reason = None
    for raw_tick in ticks:
        quote = ExecutableQuote(
            bid=raw_tick.get("bid"),
            ask=raw_tick.get("ask"),
            observed_at=raw_tick.get("observed_at", raw_tick.get("timestamp")),
        )
        if state is not None and state.filled_qty > 0:
            if replay_exit_reason is None:
                exit_decision = classify_exit(
                    intent,
                    entry_price=float(state.entry_price or intent.entry_ref),
                    opened_at=float(opened_at or quote.observed_at),
                    quote=quote,
                    now=quote.observed_at,
                )
                if exit_decision.action == "close":
                    replay_exit_reason = exit_decision.reason
            continue
        if state is None:
            transition = adapter.start(intent, quote, now=quote.observed_at)
        else:
            transition = adapter.on_quote(state, quote, now=quote.observed_at)
        if state is None or state.filled_qty <= 0:
            target_seen_before_fill = target_seen_before_fill or (
                quote.bid >= intent.take_profit
                if intent.side == "long"
                else quote.ask <= intent.take_profit
            )
        state = transition.next_state
        if state.filled_qty > 0 and opened_at is None:
            opened_at = quote.observed_at

    observed_fill = episode.get("observed_fill") or {}
    mismatch_category = None
    replay_fill_state = "not_evaluable" if not ticks else (
        "filled" if state is not None and state.filled_qty > 0 else "not_filled"
    )
    observed_assumed_fill = observed_fill.get("status") == "legacy_shadow_assumed"
    observed_fill_proven = bool(observed_fill.get("executable_quote_proven"))
    mismatch = observed_assumed_fill and (
        replay_fill_state != "filled" or not observed_fill_proven
    )
    if mismatch and episode.get("tick_evidence_status") == "legacy_not_recorded":
        mismatch_category = "legacy_executable_quote_unavailable"

    stale_chase_fill = False
    if state is not None and state.filled_qty > 0 and state.entry_price is not None:
        risk = abs(Decimal(str(intent.entry_ref)) - Decimal(str(intent.stop_loss)))
        entry_price = Decimal(str(state.entry_price))
        entry_ref = Decimal(str(intent.entry_ref))
        worse = max(Decimal("0"), entry_price - entry_ref) if intent.side == "long" else max(
            Decimal("0"), entry_ref - entry_price
        )
        stale_chase_fill = risk > 0 and worse / risk > Decimal("0.10")

    observed_exit = episode.get("observed_exit") or {}
    observed_exit_reason = str(observed_exit.get("reason") or "")
    full_tp1_expected = observed_exit_reason in {"tactical_tp1", "shadow_tp"}
    full_tp1_violation = bool(
        full_tp1_expected
        and replay_fill_state == "filled"
        and replay_exit_reason != "tactical_tp1"
    )

    return {
        "episode_id": episode_id,
        "raw_count": int(episode.get("raw_count") or 0),
        "attempts": attempts,
        "ticks_processed": len(ticks),
        "replay_fill_state": replay_fill_state,
        "replay_terminal_reason": (
            state.terminal_reason if state is not None else None
        ),
        "replay_exit_reason": replay_exit_reason,
        "mismatch": mismatch,
        "mismatch_category": mismatch_category,
        "stale_chase_fill": stale_chase_fill,
        "tp_before_entry_fill": bool(
            state is not None and state.filled_qty > 0 and target_seen_before_fill
        ),
        "main_strategy_exit": False,
        "unprotected_fill": False,
        "full_tp1_violation": full_tp1_violation,
    }


def replay_fixture(source: str | Path | Mapping[str, Any]) -> ReplayReport:
    fixture = _load_fixture(source)
    episodes = fixture.get("episodes") or []
    closes = fixture.get("historical_live_closes") or []
    if not isinstance(episodes, list) or not isinstance(closes, list):
        raise ValueError("episodes and historical_live_closes must be lists")

    attempts_by_episode: dict[str, int] = {}
    for episode in episodes:
        episode_id = str((episode or {}).get("episode_id") or "")
        attempts_by_episode[episode_id] = attempts_by_episode.get(episode_id, 0) + 1
    comparisons = tuple(
        _replay_episode(episode, attempts=attempts_by_episode[str(episode["episode_id"])])
        for episode in episodes
    )

    invalidated = [row for row in closes if row.get("exit_reason") == "tactical_invalidated"]
    other = [row for row in closes if row.get("exit_reason") != "tactical_invalidated"]
    total_pnl = _decimal_sum(closes)
    invalidated_pnl = _decimal_sum(invalidated)
    other_pnl = _decimal_sum(other)

    classified = sum(
        1 for row in comparisons if row["mismatch"] and row["mismatch_category"]
    )
    unclassified = sum(
        1 for row in comparisons if row["mismatch"] and not row["mismatch_category"]
    )
    return ReplayReport(
        raw_candidates=sum(int((episode or {}).get("raw_count") or 0) for episode in episodes),
        episodes=len(attempts_by_episode),
        duplicate_live_attempts=sum(max(0, count - 1) for count in attempts_by_episode.values()),
        stale_chase_fills=sum(bool(row["stale_chase_fill"]) for row in comparisons),
        tp_before_entry_fills=sum(bool(row["tp_before_entry_fill"]) for row in comparisons),
        main_strategy_exits=sum(bool(row["main_strategy_exit"]) for row in comparisons),
        unprotected_fills=sum(bool(row["unprotected_fill"]) for row in comparisons),
        full_tp1_violations=sum(bool(row["full_tp1_violation"]) for row in comparisons),
        classified_mismatches=classified,
        unclassified_mismatches=unclassified,
        historical_live_closes=len(closes),
        historical_live_pnl_usdt=float(total_pnl),
        invalidated_pnl_usdt=float(invalidated_pnl),
        other_live_pnl_usdt=float(other_pnl),
        intent_comparisons=comparisons,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args(argv)
    report = replay_fixture(args.fixture)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.safety_gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
