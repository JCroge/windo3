import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest


CRASH_POINTS = (
    "before_entry_io",
    "after_entry_accept",
    "after_partial_fill",
    "before_cancel_remainder",
    "after_cancel",
    "before_protection_verify",
    "after_exchange_tp",
    "before_local_close_persist",
    "after_local_close",
    "before_pending_pnl",
    "after_final_pnl",
)


class SimulatedProcessCrash(BaseException):
    pass


@dataclass
class ExchangeTruth:
    now: float = 1000.0
    entry: dict | None = None
    position_qty: float = 0.0
    tp_active: bool = False
    sl_active: bool = False
    entry_submissions: int = 0
    reduce_only_closes: int = 0
    safe_close_attempts: int = 0
    crash_point: str | None = None
    crashed: bool = False
    close_order_id: str | None = None

    def inject(self, point: str) -> None:
        if self.crash_point == point and not self.crashed:
            self.crashed = True
            raise SimulatedProcessCrash(point)


class CrashExecutor:
    def __init__(self, truth: ExchangeTruth):
        self.truth = truth
        self.positions = {}

    @staticmethod
    def _normalize_symbol(symbol):
        return symbol if symbol.endswith("-SWAP") else f"{symbol}-SWAP"

    @staticmethod
    def make_tactical_clord_id(intent_id, purpose):
        return f"TV2{purpose[:2]}{intent_id[:20]}"

    def submit_tactical_entry(self, intent, *, order_type):
        self.truth.inject("before_entry_io")
        if self.truth.entry is None:
            self.truth.entry_submissions += 1
            self.truth.entry = {
                "order_id": "entry-1",
                "client_order_id": self.make_tactical_clord_id(intent.intent_id, "entry"),
                "status": "open",
                "filled_qty": 0.0,
                "remaining_qty": 10.0,
                "average_price": None,
            }
        self.truth.inject("after_entry_accept")
        return {
            **self.truth.entry,
            "requested_qty": 10.0,
            "entry_client_id": self.make_tactical_clord_id(intent.intent_id, "entry"),
            "tp_client_id": self.make_tactical_clord_id(intent.intent_id, "tp"),
            "sl_client_id": self.make_tactical_clord_id(intent.intent_id, "sl"),
        }

    def query_tactical_entry(self, intent):
        return None if self.truth.entry is None else dict(self.truth.entry)

    def cancel_tactical_entry(self, intent):
        self.truth.inject("before_cancel_remainder")
        if self.truth.entry is None:
            return {"proven": False, "reason": "entry_not_found"}
        self.truth.entry["status"] = "canceled"
        self.truth.entry["remaining_qty"] = 0.0
        self.truth.inject("after_cancel")
        return {
            "proven": True,
            "reason": "cancel_confirmed",
            "order_id": self.truth.entry["order_id"],
            "filled_qty": self.truth.entry["filled_qty"],
            "average_price": self.truth.entry["average_price"],
        }

    def verify_tactical_protection(self, intent, *, filled_qty):
        self.truth.inject("before_protection_verify")
        self.truth.tp_active = True
        self.truth.sl_active = True
        return {
            "complete": True,
            "reason": "complete",
            "representation": "separate",
            "protected_qty": filled_qty,
            "tp_algo_ids": ["tp-algo"],
            "sl_algo_ids": ["sl-algo"],
        }

    def cancel_tactical_protection(self, intent):
        cancelled = []
        if self.truth.tp_active:
            cancelled.append("tp-algo")
        if self.truth.sl_active:
            cancelled.append("sl-algo")
        self.truth.tp_active = False
        self.truth.sl_active = False
        return {"cancelled_algo_ids": cancelled, "preserved_algo_ids": []}

    def _fetch_okx_position_state(self, symbol, raise_on_error=True):
        if self.truth.position_qty <= 0:
            return None
        return {
            "symbol": symbol,
            "side": "long",
            "available_contracts": self.truth.position_qty,
        }

    def close_tactical_position(
        self,
        intent,
        *,
        filled_qty,
        ownership_proof,
        reason,
    ):
        if reason == "risk_forced:protection_integrity":
            self.truth.safe_close_attempts += 1
        if self.truth.close_order_id is None:
            self.truth.reduce_only_closes += 1
            self.truth.close_order_id = "close-1"
            self.truth.position_qty = 0.0
            self.truth.tp_active = False
            self.truth.sl_active = False
        self.truth.inject("after_local_close")
        return {
            "status": "submitted",
            "order_id": self.truth.close_order_id,
            "client_order_id": self.make_tactical_clord_id(intent.intent_id, "close"),
            "closed_qty": filled_qty,
            "reason": reason,
        }

    def _save_positions(self):
        return None


def _paths(tmp_path):
    return SimpleNamespace(
        namespace="testnet",
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
        tactical_v2_status=str(tmp_path / "status.json"),
    )


def _candidate(candidate_id="candidate-1", created_at=1000.0):
    return {
        "candidate_id": candidate_id,
        "namespace": "testnet",
        "symbol": "WLD-USDT",
        "side": "long",
        "entry_ref": 1.0,
        "stop_loss": 0.95,
        "take_profit": 1.08,
        "leverage": 5,
        "source_shadow_id": f"shadow-{candidate_id}",
        "tactical_source": "main_quality_failed",
        "created_at": created_at,
        "tf_15m_available": True,
        "tf_15m_bias": "bullish",
        "tf_15m_closed_bar_ts": 900.0,
        "tf_15m_structure_token": "break_up:wld",
        "tf_15m_block_long": False,
    }


def _controller(tmp_path, truth):
    from utils.tactical_v2.controller import TacticalV2Controller

    executor = CrashExecutor(truth)
    controller = TacticalV2Controller(
        executor=executor,
        config={"tactical_v2_mode": "live"},
        paths=_paths(tmp_path),
        logger=logging.getLogger("test_tactical_v2_crash_recovery"),
        publish=None,
        now_fn=lambda: truth.now,
    )
    return controller, executor


async def _admit_and_submit(controller, truth):
    accepted = await controller.handle_candidate(_candidate(), now=truth.now)
    assert accepted.accepted is True
    await controller.handle_quote(
        "WLD-USDT",
        {"bid": 0.999, "ask": 1.001, "timestamp": truth.now},
        now=truth.now,
    )
    return accepted


def _set_partial_fill(truth):
    truth.entry.update({
        "status": "partially_filled",
        "filled_qty": 4.0,
        "remaining_qty": 6.0,
        "average_price": 1.001,
    })
    truth.position_qty = 4.0


def _set_full_fill(truth):
    truth.entry.update({
        "status": "filled",
        "filled_qty": 10.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    })
    truth.position_qty = 10.0


def _final_payload(intent_id, episode_id, plan_hash, resolved_at):
    return {
        "resolution_id": "resolution-1",
        "position_id": f"tv2:{intent_id}",
        "entry_request_id": CrashExecutor.make_tactical_clord_id(intent_id, "entry"),
        "strategy_owner": "tactical_v2",
        "intent_id": intent_id,
        "episode_id": episode_id,
        "plan_hash": plan_hash,
        "pnl_status": "final",
        "realized_pnl_net_usdt": -1.25,
        "timestamp": resolved_at,
        "close_cause": "exchange_tp_or_sl",
        "tp_algo_ids": ["tp-algo"],
        "sl_algo_ids": ["sl-algo"],
    }


async def _crash_once(point, tmp_path):
    truth = ExchangeTruth(crash_point=point)
    controller, _ = _controller(tmp_path, truth)

    if point in {"before_entry_io", "after_entry_accept"}:
        with pytest.raises(SimulatedProcessCrash, match=point):
            await _admit_and_submit(controller, truth)
    else:
        accepted = await _admit_and_submit(controller, truth)
        if point in {
            "after_partial_fill",
            "before_cancel_remainder",
            "after_cancel",
            "before_protection_verify",
        }:
            _set_partial_fill(truth)
            if point == "after_partial_fill":
                async def crash_after_observation(*args, **kwargs):
                    truth.inject(point)

                controller._settle_live_fill = crash_after_observation
            with pytest.raises(SimulatedProcessCrash, match=point):
                await controller.tick(now=truth.now + 1)
        else:
            _set_full_fill(truth)
            await controller.tick(now=truth.now + 1)
            assert controller.snapshot(now=truth.now + 1)["intents"][0]["state"] == "protected"
            truth.now += 2
            if point == "after_exchange_tp":
                truth.position_qty = 0.0
                truth.tp_active = False
                truth.sl_active = False
                with pytest.raises(SimulatedProcessCrash, match=point):
                    truth.inject(point)
            elif point == "before_local_close_persist":
                truth.position_qty = 0.0
                truth.tp_active = False
                truth.sl_active = False

                def crash_before_persist(*args, **kwargs):
                    truth.inject(point)

                controller._mark_exchange_closed = crash_before_persist
                with pytest.raises(SimulatedProcessCrash, match=point):
                    await controller.tick(now=truth.now)
            elif point == "after_local_close":
                truth.now += 90 * 60
                with pytest.raises(SimulatedProcessCrash, match=point):
                    await controller.tick(now=truth.now)
            elif point == "before_pending_pnl":
                truth.position_qty = 0.0
                truth.tp_active = False
                truth.sl_active = False
                await controller.tick(now=truth.now)
                with pytest.raises(SimulatedProcessCrash, match=point):
                    truth.inject(point)
            elif point == "after_final_pnl":
                truth.position_qty = 0.0
                truth.tp_active = False
                truth.sl_active = False
                await controller.tick(now=truth.now)
                record = controller._intents[accepted.intent_id]
                payload = _final_payload(
                    accepted.intent_id,
                    accepted.episode_id,
                    record["intent"].plan_hash,
                    truth.now,
                )
                persist = controller._persist_record_state

                def crash_after_governor(record, state, evaluated_at, **fields):
                    if state == "closed_final":
                        truth.inject(point)
                    return persist(record, state, evaluated_at, **fields)

                controller._persist_record_state = crash_after_governor
                with pytest.raises(SimulatedProcessCrash, match=point):
                    await controller.handle_pnl_resolution(payload)

    assert truth.crashed is True
    truth.now += 1
    restarted, _ = _controller(tmp_path, truth)
    await restarted.recover(now=truth.now)

    if point == "after_final_pnl":
        intent_id, record = next(iter(restarted._intents.items()))
        await restarted.handle_pnl_resolution(
            _final_payload(
                intent_id,
                record["intent"].episode_id,
                record["intent"].plan_hash,
                truth.now,
            )
        )
    return restarted, truth


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_point", CRASH_POINTS)
async def test_every_external_boundary_recovers_without_duplicate_risk(tmp_path, crash_point):
    controller, truth = await _crash_once(crash_point, tmp_path)
    snapshot = controller.snapshot(now=truth.now)
    state = snapshot["intents"][0]["state"]

    assert truth.entry_submissions <= 1
    assert truth.reduce_only_closes <= 1

    repeated = await controller.handle_candidate(
        _candidate(candidate_id="candidate-repeated", created_at=truth.now),
        now=truth.now,
    )
    assert repeated.accepted is False
    assert repeated.reason == "duplicate_episode"

    pending_entry = bool(
        truth.entry
        and truth.entry.get("remaining_qty", 0) > 0
        and truth.entry.get("status") not in {"canceled", "filled"}
    )
    if pending_entry or truth.position_qty > 0 or state == "integrity_required":
        assert snapshot["active_slots"] == 1

    if truth.position_qty > 0:
        protected = state == "protected" and truth.tp_active and truth.sl_active
        failed_closed = (
            snapshot["integrity_halt"]
            and truth.safe_close_attempts >= 1
        )
        assert protected or failed_closed

    expected_state = {
        "before_entry_io": "reconciling_entry",
        "after_entry_accept": "pending_entry",
        "after_partial_fill": "protected",
        "before_cancel_remainder": "protected",
        "after_cancel": "protected",
        "before_protection_verify": "protected",
        "after_exchange_tp": "exchange_closed_pending_pnl",
        "before_local_close_persist": "exchange_closed_pending_pnl",
        "after_local_close": "exchange_closed_pending_pnl",
        "before_pending_pnl": "exchange_closed_pending_pnl",
        "after_final_pnl": "closed_final",
    }[crash_point]
    assert state == expected_state


@pytest.mark.asyncio
async def test_final_pnl_crash_keeps_one_governor_resolution(tmp_path):
    controller, _ = await _crash_once("after_final_pnl", tmp_path)
    event_types = [
        json.loads(line)["event_type"]
        for line in Path(tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert event_types.count("governor_final_applied") == 1
    assert controller.governor.final_episode_count == 1
    assert controller.governor.rolling_pnl == -1.25
