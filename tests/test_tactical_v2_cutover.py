import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from utils.shadow_tactical_live import SidecarPaths, SidecarStateStore


def _complete_report(**overrides):
    from utils.tactical_v2.cutover import build_drain_report

    values = {
        "namespace": "live",
        "sidecar_bot_owner_id": "stlive",
        "admission_state": {
            "admission_enabled": False,
            "admission_disabled_at": 900.0,
        },
        "pending_entries": [],
        "owners": [],
        "local_positions": [],
        "exchange_positions": [],
        "protection_orders": [],
        "ownership_proof": {
            "ownership": True,
            "orders": True,
            "positions": True,
            "protection": True,
        },
        "exchange_state": "flat",
        "pending_pnl": [],
        "final_pnl": [],
        "documented_exceptions": [],
        "generated_at": 1000.0,
    }
    values.update(overrides)
    return build_drain_report(**values)


def test_unknown_exchange_or_open_owner_keeps_drain_unresolved(tmp_path):
    from utils.tactical_v2.cutover import validate_live_cutover, write_drain_report

    report = _complete_report(
        exchange_state="unknown",
        owners=[{"shadow_id": "s1", "status": "open"}],
    )
    path = tmp_path / "retirement.json"
    write_drain_report(report, path)

    decision = validate_live_cutover(
        path,
        namespace="live",
        sidecar_bot_owner_id="stlive",
    )

    assert report["complete"] is False
    assert report["unresolved"]["open_owners"] == 1
    assert decision.allowed is False
    assert decision.reason == "sidecar_drain_unresolved"


def test_complete_drain_archives_and_allows_matching_live_cutover(tmp_path):
    from utils.tactical_v2.cutover import archive_drain_report, validate_live_cutover

    path = tmp_path / "retirement.json"
    proof = archive_drain_report(_complete_report(), path, archived_at=1001.0)
    decision = validate_live_cutover(
        path,
        namespace="live",
        sidecar_bot_owner_id="stlive",
    )

    assert proof["retired"] is True
    assert proof["archived_at"] == 1001.0
    assert decision.allowed is True
    assert decision.reason == "sidecar_retirement_verified"


def test_cutover_rejects_tampered_hash_namespace_and_owner(tmp_path):
    from utils.tactical_v2.cutover import archive_drain_report, validate_live_cutover

    path = tmp_path / "retirement.json"
    archive_drain_report(_complete_report(), path, archived_at=1001.0)
    stored = json.loads(path.read_text())
    stored["exchange_state"] = "present"
    path.write_text(json.dumps(stored))
    assert validate_live_cutover(
        path, namespace="live", sidecar_bot_owner_id="stlive"
    ).reason == "sidecar_retirement_hash_mismatch"

    archive_drain_report(_complete_report(), path, archived_at=1001.0)
    assert validate_live_cutover(
        path, namespace="testnet", sidecar_bot_owner_id="stlive"
    ).reason == "sidecar_retirement_namespace_mismatch"
    assert validate_live_cutover(
        path, namespace="live", sidecar_bot_owner_id="other"
    ).reason == "sidecar_retirement_owner_mismatch"


def test_documented_pending_pnl_exception_is_visible_and_can_complete():
    report = _complete_report(
        pending_pnl=[{"resolution_id": "pnl-1", "status": "pending"}],
        documented_exceptions=[{
            "type": "pending_pnl",
            "object_id": "pnl-1",
            "accepted": True,
            "reason": "exchange history unavailable after manual bill reconciliation",
        }],
    )

    assert report["complete"] is True
    assert report["unresolved"]["pending_pnl"] == 0
    assert report["documented_exceptions"][0]["object_id"] == "pnl-1"


def _controller_paths(tmp_path):
    return SimpleNamespace(
        namespace="live",
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
        tactical_v2_status=str(tmp_path / "status.json"),
        sidecar_retirement=str(tmp_path / "retirement.json"),
    )


def _controller(tmp_path, monkeypatch):
    from utils.tactical_v2.controller import TacticalV2Controller

    monkeypatch.setenv("SIDECAR_BOT_INSTANCE_ID", "stlive")
    executor = SimpleNamespace(positions={})
    return TacticalV2Controller(
        executor=executor,
        config={"tactical_v2_mode": "live"},
        paths=_controller_paths(tmp_path),
        logger=logging.getLogger("test_tactical_v2_cutover"),
        publish=None,
        now_fn=lambda: 1000.0,
    )


def test_live_request_without_retirement_proof_stays_shadow_only(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)

    assert controller.requested_mode == "live"
    assert controller.mode == "shadow"
    assert controller.cutover_decision.allowed is False
    assert controller.cutover_decision.reason == "sidecar_retirement_missing"
    status = json.loads(Path(_controller_paths(tmp_path).tactical_v2_status).read_text())
    assert status["requested_mode"] == "live"
    assert status["mode"] == "shadow"
    assert status["cutover"] == {
        "allowed": False,
        "reason": "sidecar_retirement_missing",
    }


def test_matching_retirement_proof_enables_live_mode(tmp_path, monkeypatch):
    from utils.tactical_v2.cutover import archive_drain_report

    archive_drain_report(
        _complete_report(),
        _controller_paths(tmp_path).sidecar_retirement,
        archived_at=1001.0,
    )

    controller = _controller(tmp_path, monkeypatch)

    assert controller.mode == "live"
    assert controller.cutover_decision.allowed is True


def test_legacy_sidecar_owner_file_is_never_adopted_as_v2_state(tmp_path, monkeypatch):
    owner_path = tmp_path / "owners.json"
    owner_path.write_text(json.dumps({
        "owners": {
            "legacy-1": {
                "shadow_id": "legacy-1",
                "symbol": "WLD-USDT-SWAP",
                "side": "long",
                "status": "open",
            }
        }
    }))
    monkeypatch.setenv("SHADOW_TACTICAL_OWNER_REGISTRY", str(owner_path))

    controller = _controller(tmp_path, monkeypatch)

    assert controller.snapshot(now=1000.0)["intents"] == []
    assert controller.snapshot(now=1000.0)["active_slots"] == 0
    assert controller.mode == "shadow"


def _sidecar_paths(tmp_path):
    return SidecarPaths(
        events=str(tmp_path / "events.jsonl"),
        state=str(tmp_path / "sidecar_state.json"),
        audit=str(tmp_path / "audit.jsonl"),
        owners=str(tmp_path / "owners.json"),
        positions=str(tmp_path / "positions.json"),
        risk_state=str(tmp_path / "risk.json"),
        halt_state=str(tmp_path / "halt.json"),
        live_order_events=str(tmp_path / "orders.jsonl"),
        live_position_lifecycle=str(tmp_path / "lifecycle.json"),
    )


def _drain_executor(*, exchange_positions=None, algos=None, pending_pnl=None):
    executor = SimpleNamespace(
        exchange_id="okx",
        positions={},
        logger=logging.getLogger("test_cutover_drain"),
        ledger=SimpleNamespace(
            find_pending_external_closes=lambda: list(pending_pnl or [])
        ),
    )
    executor._fetch_positions_with_retry = lambda: list(exchange_positions or [])
    executor._normalize_okx_position = lambda row: row
    executor._list_pending_algos = lambda symbol: list(algos or [])
    return executor


def test_drain_collection_fails_closed_when_exchange_truth_is_unknown(tmp_path):
    from scripts.shadow_tactical_live_sidecar import collect_sidecar_drain_report

    paths = _sidecar_paths(tmp_path)
    SidecarStateStore(paths.state).disable_admission(source="cutover", now=900.0)
    executor = _drain_executor()
    executor._fetch_positions_with_retry = MagicMock(side_effect=RuntimeError("offline"))

    report = collect_sidecar_drain_report(
        paths,
        executor,
        namespace="live",
        sidecar_bot_owner_id="stlive",
        generated_at=1000.0,
    )

    assert report["exchange_state"] == "unknown"
    assert report["complete"] is False
    assert report["unresolved"]["exchange_state"] == 1


def test_drain_collection_completes_only_after_flat_proven_state(tmp_path):
    from scripts.shadow_tactical_live_sidecar import collect_sidecar_drain_report

    paths = _sidecar_paths(tmp_path)
    SidecarStateStore(paths.state).disable_admission(source="cutover", now=900.0)
    Path(paths.owners).write_text(json.dumps({
        "owners": {
            "s1": {
                "shadow_id": "s1",
                "symbol": "WLD-USDT-SWAP",
                "side": "long",
                "status": "closed",
                "close_pnl_status": "final",
            }
        }
    }))
    executor = _drain_executor()

    report = collect_sidecar_drain_report(
        paths,
        executor,
        namespace="live",
        sidecar_bot_owner_id="stlive",
        generated_at=1000.0,
    )

    assert report["complete"] is True
    assert report["ownership_proof"] == {
        "ownership": True,
        "orders": True,
        "positions": True,
        "protection": True,
    }
    assert report["final_pnl"][0]["shadow_id"] == "s1"


class RollbackExecutor:
    def __init__(self):
        self.positions = {}
        self.submissions = []
        self.cancel_calls = []
        self.cancel_protection_calls = []
        self.query_result = None
        self.exchange_position = {"side": "long", "available_contracts": 500.0}

    @staticmethod
    def _normalize_symbol(symbol):
        return symbol if symbol.endswith("-SWAP") else f"{symbol}-SWAP"

    @staticmethod
    def make_tactical_clord_id(intent_id, purpose):
        return f"TV2{purpose[:2]}{intent_id[:20]}"

    def submit_tactical_entry(self, intent, *, order_type):
        self.submissions.append(intent.intent_id)
        return {
            "order_id": "entry-1",
            "requested_qty": 500.0,
            "remaining_qty": 500.0,
            "entry_client_id": self.make_tactical_clord_id(intent.intent_id, "entry"),
        }

    def query_tactical_entry(self, intent):
        return self.query_result

    def cancel_tactical_entry(self, intent):
        self.cancel_calls.append(intent.intent_id)
        return {"proven": True, "reason": "cancel_confirmed", "filled_qty": 0.0}

    def verify_tactical_protection(self, intent, *, filled_qty):
        return {
            "complete": True,
            "reason": "complete",
            "representation": "combined_oco",
            "protected_qty": filled_qty,
            "tp_algo_ids": ["tp-1"],
            "sl_algo_ids": ["sl-1"],
        }

    def cancel_tactical_protection(self, intent):
        self.cancel_protection_calls.append(intent.intent_id)
        return {"cancelled_algo_ids": ["tp-1", "sl-1"]}

    def _fetch_okx_position_state(self, symbol, raise_on_error=True):
        return self.exchange_position

    def _save_positions(self):
        return None


def _rollback_paths(tmp_path):
    return SimpleNamespace(
        namespace="testnet",
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
        tactical_v2_status=str(tmp_path / "status.json"),
        sidecar_retirement=str(tmp_path / "retirement.json"),
    )


def _candidate():
    return {
        "candidate_id": "candidate-rollback",
        "namespace": "testnet",
        "symbol": "WLD-USDT",
        "side": "long",
        "entry_ref": 1.0,
        "stop_loss": 0.95,
        "take_profit": 1.08,
        "leverage": 5,
        "source_shadow_id": "shadow-rollback",
        "tactical_source": "main_quality_failed",
        "created_at": 1000.0,
        "tf_15m_available": True,
        "tf_15m_bias": "bullish",
        "tf_15m_closed_bar_ts": 900.0,
        "tf_15m_structure_token": "break_up:wld",
        "tf_15m_block_long": False,
    }


def _rollback_controller(tmp_path, executor, mode):
    from utils.tactical_v2.controller import TacticalV2Controller

    return TacticalV2Controller(
        executor=executor,
        config={"tactical_v2_mode": mode},
        paths=_rollback_paths(tmp_path),
        logger=logging.getLogger("test_tactical_v2_rollback"),
        publish=None,
        now_fn=lambda: 1000.0,
    )


@pytest.mark.asyncio
async def test_rollback_cancels_proven_pending_v2_entry_without_retry(tmp_path):
    live_executor = RollbackExecutor()
    live = _rollback_controller(tmp_path, live_executor, "live")
    await live.handle_candidate(_candidate(), now=1000.0)
    await live.handle_quote(
        "WLD-USDT", {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0}, now=1000.0
    )

    stopped_executor = RollbackExecutor()
    stopped = _rollback_controller(tmp_path, stopped_executor, "off")
    await stopped.recover(now=1001.0)

    assert stopped_executor.submissions == []
    assert len(stopped_executor.cancel_calls) == 1
    intent = stopped.snapshot(now=1001.0)["intents"][0]
    assert intent["state"] == "entry_terminal"
    assert intent["lane"] == "live"


@pytest.mark.asyncio
async def test_rollback_keeps_protected_v2_management_until_flat(tmp_path):
    live_executor = RollbackExecutor()
    live = _rollback_controller(tmp_path, live_executor, "live")
    accepted = await live.handle_candidate(_candidate(), now=1000.0)
    await live.handle_quote(
        "WLD-USDT", {"bid": 0.999, "ask": 1.001, "timestamp": 1000.0}, now=1000.0
    )
    live_executor.query_result = {
        "order_id": "entry-1",
        "status": "closed",
        "filled_qty": 500.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    }
    await live.tick(now=1001.0)

    stopped_executor = RollbackExecutor()
    stopped = _rollback_controller(tmp_path, stopped_executor, "off")
    await stopped.recover(now=1002.0)
    assert stopped.snapshot(now=1002.0)["intents"][0]["state"] == "protected"
    assert stopped_executor.positions["WLD-USDT-SWAP"]["intent_id"] == accepted.intent_id
    assert stopped.blocks_main_symbol("WLD-USDT") is True

    stopped_executor.exchange_position = None
    await stopped.tick(now=1003.0)

    assert stopped.snapshot(now=1003.0)["intents"][0]["state"] == (
        "exchange_closed_pending_pnl"
    )
    assert stopped_executor.submissions == []


@pytest.mark.asyncio
async def test_live_cutover_never_converts_durable_shadow_intent_to_live(tmp_path):
    shadow_executor = RollbackExecutor()
    shadow = _rollback_controller(tmp_path, shadow_executor, "shadow")
    accepted = await shadow.handle_candidate(_candidate(), now=1000.0)

    live_executor = RollbackExecutor()
    live = _rollback_controller(tmp_path, live_executor, "live")
    await live.handle_quote(
        "WLD-USDT", {"bid": 0.999, "ask": 1.001, "timestamp": 1001.0}, now=1001.0
    )

    intent = next(
        row
        for row in live.snapshot(now=1001.0)["intents"]
        if row["intent_id"] == accepted.intent_id
    )
    assert intent["lane"] == "shadow"
    assert live_executor.submissions == []
