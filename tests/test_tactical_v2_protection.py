import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _intent(**overrides):
    from utils.tactical_v2.models import TacticalIntent

    raw = {
        "candidate_id": "cand-protection-1",
        "namespace": "testnet",
        "symbol": "WLD-USDT",
        "side": "long",
        "entry_ref": 1.0,
        "stop_loss": 0.95,
        "take_profit": 1.08,
        "leverage": 5,
        "source_shadow_id": "shadow-1",
        "tactical_source": "main_quality_failed",
        "created_at": 1000.0,
    }
    raw.update(overrides)
    return TacticalIntent.from_candidate(raw, episode_id="episode-protection")


def _executor(monkeypatch):
    from executor import ContractExecutor

    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = ContractExecutor.__new__(ContractExecutor)
    executor.logger = logging.getLogger("test_tactical_v2_protection")
    executor.exchange_id = "okx"
    executor.testnet = True
    executor._okx_pos_mode = "net_mode"
    executor.positions = {}
    executor.exchange = MagicMock()
    executor.exchange.amount_to_precision.side_effect = lambda symbol, qty: str(qty)
    executor.exchange.create_order.return_value = {"id": "safe-close-1"}
    return executor


def _separate_rows(executor, intent, qty=4.0):
    return [
        {
            "algoId": "tp-algo",
            "algoClOrdId": executor.make_tactical_clord_id(intent.intent_id, "tp"),
            "ordType": "conditional",
            "tp_trigger": "1.08",
            "sl_trigger": "",
            "quantity": str(qty),
        },
        {
            "algoId": "sl-algo",
            "algoClOrdId": executor.make_tactical_clord_id(intent.intent_id, "sl"),
            "ordType": "conditional",
            "tp_trigger": "",
            "sl_trigger": "0.95",
            "quantity": str(qty),
        },
    ]


def test_separate_tp_and_sl_require_exact_owner_price_and_quantity(monkeypatch):
    executor = _executor(monkeypatch)
    intent = _intent()
    executor._list_pending_algos = MagicMock(return_value=_separate_rows(executor, intent))

    proof = executor.verify_tactical_protection(intent, filled_qty=4.0)

    assert proof["complete"] is True
    assert proof["representation"] == "separate"
    assert proof["protected_qty"] == 4.0
    assert proof["tp_algo_ids"] == ["tp-algo"]
    assert proof["sl_algo_ids"] == ["sl-algo"]


def test_combined_oco_is_equivalent_when_both_legs_and_quantity_are_proven(monkeypatch):
    executor = _executor(monkeypatch)
    intent = _intent()
    executor._list_pending_algos = MagicMock(return_value=[{
        "algoId": "oco-algo",
        "algoClOrdId": executor.make_tactical_clord_id(intent.intent_id, "tp"),
        "ordType": "oco",
        "tp_trigger": "1.08",
        "sl_trigger": "0.95",
        "quantity": "4",
    }])

    proof = executor.verify_tactical_protection(intent, filled_qty=4.0)

    assert proof["complete"] is True
    assert proof["representation"] == "combined_oco"
    assert proof["tp_algo_ids"] == ["oco-algo"]
    assert proof["sl_algo_ids"] == ["oco-algo"]


@pytest.mark.parametrize("mutation", ["missing_tp", "wrong_qty", "wrong_price", "foreign"])
def test_incomplete_or_ambiguous_protection_fails_proof(monkeypatch, mutation):
    executor = _executor(monkeypatch)
    intent = _intent()
    rows = _separate_rows(executor, intent)
    if mutation == "missing_tp":
        rows = rows[1:]
    elif mutation == "wrong_qty":
        rows[0]["quantity"] = "5"
    elif mutation == "wrong_price":
        rows[1]["sl_trigger"] = "0.94"
    elif mutation == "foreign":
        rows[0]["algoClOrdId"] = "manual-tp"
    executor._list_pending_algos = MagicMock(return_value=rows)

    proof = executor.verify_tactical_protection(intent, filled_qty=4.0)

    assert proof["complete"] is False
    assert proof["reason"] in {
        "missing_tp", "quantity_mismatch", "price_mismatch", "ownership_mismatch"
    }


@pytest.mark.asyncio
async def test_partial_fill_cancels_remainder_and_protects_only_filled_quantity(monkeypatch):
    from utils.tactical_v2.exchange import LiveExchangeAdapter

    executor = _executor(monkeypatch)
    intent = _intent()
    executor.cancel_tactical_entry = MagicMock(return_value={"proven": True})
    executor.verify_tactical_protection = MagicMock(return_value={
        "complete": True,
        "reason": "complete",
        "representation": "separate",
        "protected_qty": 2.0,
        "tp_algo_ids": ["tp"],
        "sl_algo_ids": ["sl"],
    })
    adapter = LiveExchangeAdapter(executor=executor)

    proof = await adapter.settle_fill(intent, filled_qty=2.0, remaining_qty=3.0)

    executor.cancel_tactical_entry.assert_called_once_with(intent)
    executor.verify_tactical_protection.assert_called_once_with(intent, filled_qty=2.0)
    assert proof.complete is True
    assert proof.protected_qty == 2.0


@pytest.mark.asyncio
async def test_cancel_fill_race_protects_final_cumulative_fill(monkeypatch):
    from utils.tactical_v2.exchange import LiveExchangeAdapter

    executor = _executor(monkeypatch)
    intent = _intent()
    executor.cancel_tactical_entry = MagicMock(return_value={
        "proven": True,
        "reason": "cancel_confirmed",
        "filled_qty": 5.0,
    })
    executor.verify_tactical_protection = MagicMock(return_value={
        "complete": True,
        "reason": "complete",
        "representation": "separate",
        "protected_qty": 5.0,
        "tp_algo_ids": ["tp"],
        "sl_algo_ids": ["sl"],
    })
    adapter = LiveExchangeAdapter(executor=executor)

    proof = await adapter.settle_fill(intent, filled_qty=2.0, remaining_qty=3.0)

    executor.verify_tactical_protection.assert_called_once_with(intent, filled_qty=5.0)
    assert proof.protected_qty == 5.0


@pytest.mark.asyncio
async def test_failed_protection_halts_persists_and_closes_only_proven_exposure(monkeypatch):
    from utils.tactical_v2.exchange import LiveExchangeAdapter

    executor = _executor(monkeypatch)
    intent = _intent()
    executor.verify_tactical_protection = MagicMock(return_value={
        "complete": False,
        "reason": "missing_tp",
        "representation": "incomplete",
        "protected_qty": 0.0,
        "tp_algo_ids": [],
        "sl_algo_ids": ["owned-sl"],
    })
    executor.cancel_tactical_protection = MagicMock(return_value={
        "cancelled_algo_ids": ["owned-sl"],
        "preserved_algo_ids": ["manual-order"],
    })
    executor.close_tactical_position = MagicMock(return_value={"order_id": "safe-close"})
    store = SimpleNamespace(append=MagicMock())
    governor = SimpleNamespace(activate_integrity_halt=MagicMock())
    adapter = LiveExchangeAdapter(executor=executor, store=store, governor=governor)

    proof = await adapter.settle_fill(intent, filled_qty=4.0, remaining_qty=0.0)

    assert proof.complete is False
    store.append.assert_called_once()
    governor.activate_integrity_halt.assert_called_once()
    executor.cancel_tactical_protection.assert_called_once_with(intent)
    executor.close_tactical_position.assert_called_once_with(
        intent, filled_qty=4.0,
        ownership_proof=executor.make_tactical_clord_id(intent.intent_id, "entry"),
        reason="risk_forced:protection_integrity",
    )


@pytest.mark.asyncio
async def test_safe_close_is_still_attempted_when_owned_protection_cancel_fails(monkeypatch):
    from utils.tactical_v2.exchange import LiveExchangeAdapter

    executor = _executor(monkeypatch)
    intent = _intent()
    executor.verify_tactical_protection = MagicMock(return_value={
        "complete": False,
        "reason": "missing_tp",
        "representation": "incomplete",
        "protected_qty": 0.0,
        "tp_algo_ids": [],
        "sl_algo_ids": ["owned-sl"],
    })
    executor.cancel_tactical_protection = MagicMock(
        side_effect=RuntimeError("cancel api unavailable")
    )
    executor.close_tactical_position = MagicMock(return_value={"order_id": "safe-close"})
    store = SimpleNamespace(append=MagicMock())
    governor = SimpleNamespace(activate_integrity_halt=MagicMock())
    adapter = LiveExchangeAdapter(executor=executor, store=store, governor=governor)

    proof = await adapter.settle_fill(intent, filled_qty=4.0, remaining_qty=0.0)

    assert proof.complete is False
    governor.activate_integrity_halt.assert_called_once()
    executor.close_tactical_position.assert_called_once()


def test_cancel_protection_preserves_foreign_and_manual_orders(monkeypatch):
    executor = _executor(monkeypatch)
    intent = _intent()
    rows = _separate_rows(executor, intent)
    rows.append({
        "algoId": "manual",
        "algoClOrdId": "manual-order",
        "ordType": "conditional",
        "tp_trigger": "1.20",
        "sl_trigger": "",
        "quantity": "4",
    })
    executor._list_pending_algos = MagicMock(return_value=rows)
    executor._cancel_algo_by_id = MagicMock(return_value=True)

    result = executor.cancel_tactical_protection(intent)

    assert result["cancelled_algo_ids"] == ["tp-algo", "sl-algo"]
    assert result["preserved_algo_ids"] == ["manual"]
    assert executor._cancel_algo_by_id.call_count == 2
