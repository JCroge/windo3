import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _intent(**overrides):
    from utils.tactical_v2.models import TacticalIntent

    raw = {
        "candidate_id": "cand-live-1",
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
    return TacticalIntent.from_candidate(raw, episode_id="episode-1")


def _executor():
    from executor import ContractExecutor

    executor = ContractExecutor.__new__(ContractExecutor)
    executor.logger = logging.getLogger("test_tactical_v2_exchange")
    executor.exchange_id = "okx"
    executor.testnet = True
    executor._okx_pos_mode = "net_mode"
    executor.positions = {}
    executor.exchange = MagicMock()
    executor.exchange.fetch_ticker.return_value = {"last": 1.0, "ask": 1.001, "bid": 0.999}
    executor.exchange.market.return_value = {
        "contractSize": 1,
        "limits": {"amount": {"min": 0.001}},
    }
    executor.exchange.amount_to_precision.side_effect = lambda symbol, qty: str(round(qty, 6))
    executor.exchange.price_to_precision.side_effect = lambda symbol, price: str(price)
    executor.exchange.create_order.return_value = {"id": "entry-order-1", "status": "open"}
    executor.exchange.set_leverage.return_value = None
    executor.balance_adapter = SimpleNamespace(get_free=lambda: 1000.0)
    executor.caps = None
    return executor


def test_tactical_ids_are_stable_distinct_and_owner_tagged(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()

    entry = executor.make_tactical_clord_id("intent-abc", "entry")

    assert entry == executor.make_tactical_clord_id("intent-abc", "entry")
    assert executor._is_owner_clord_id(entry)
    assert len(entry) <= 32
    assert entry.isalnum()
    assert executor.make_tactical_clord_id("intent-abc", "tp") != entry
    assert executor.make_tactical_clord_id("intent-abc", "sl") != entry


def test_market_entry_uses_fixed_margin_and_attaches_full_tp_and_sl(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    intent = _intent()

    submitted = executor.submit_tactical_entry(intent, order_type="market")

    assert submitted["margin_usdt"] == 100.0
    assert submitted["requested_qty"] == pytest.approx(500 / 1.001)
    call = executor.exchange.create_order.call_args.kwargs
    assert call["type"] == "market"
    assert call["price"] is None
    assert call["params"]["clOrdId"] == executor.make_tactical_clord_id(
        intent.intent_id, "entry"
    )
    attached = call["params"]["attachAlgoOrds"]
    assert {row["attachAlgoClOrdId"] for row in attached} == {
        executor.make_tactical_clord_id(intent.intent_id, "tp"),
        executor.make_tactical_clord_id(intent.intent_id, "sl"),
    }
    assert any(row.get("tpTriggerPx") == "1.08" for row in attached)
    assert any(row.get("slTriggerPx") == "0.95" for row in attached)


def test_limit_entry_uses_frozen_entry_without_main_drift_or_size_cap(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    executor.risk_manager = MagicMock()
    executor.risk_manager.max_trade_amount = 30.0

    submitted = executor.submit_tactical_entry(_intent(), order_type="limit")

    call = executor.exchange.create_order.call_args.kwargs
    assert call["type"] == "limit"
    assert call["price"] == 1.0
    assert submitted["margin_usdt"] == 100.0
    assert executor.risk_manager.max_trade_amount == 30.0


def test_submit_response_loss_recovers_by_deterministic_client_id(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    intent = _intent()
    entry_id = executor.make_tactical_clord_id(intent.intent_id, "entry")
    executor.exchange.create_order.side_effect = RuntimeError("response lost")
    executor.exchange.fetch_open_orders.return_value = [{
        "id": "accepted-order",
        "clientOrderId": entry_id,
        "status": "open",
        "filled": 0,
        "remaining": 499.5005,
        "average": None,
    }]
    executor.exchange.fetch_orders.return_value = []

    submitted = executor.submit_tactical_entry(intent, order_type="market")

    assert submitted["order_id"] == "accepted-order"
    assert submitted["entry_client_id"] == entry_id
    assert submitted["recovered_after_submit_error"] is True


def test_query_entry_uses_exact_okx_client_id_when_ccxt_history_omits_fill(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    intent = _intent()
    entry_id = executor.make_tactical_clord_id(intent.intent_id, "entry")
    executor.exchange.fetch_open_orders.return_value = []
    executor.exchange.fetch_orders.return_value = []
    executor.exchange.market.side_effect = KeyError("CCXT requires a unified symbol")
    executor.exchange.private_get_trade_order.return_value = {
        "code": "0",
        "data": [{
            "ordId": "filled-order",
            "clOrdId": entry_id,
            "instId": "WLD-USDT-SWAP",
            "state": "filled",
            "sz": "500",
            "accFillSz": "500",
            "avgPx": "1.001",
        }],
    }

    observed = executor.query_tactical_entry(intent)

    assert observed["query_state"] == "found"
    assert observed["observation"] == {
        "order_id": "filled-order",
        "client_order_id": entry_id,
        "status": "filled",
        "filled_qty": 500.0,
        "remaining_qty": 0.0,
        "average_price": 1.001,
    }
    executor.exchange.private_get_trade_order.assert_called_once_with({
        "instId": "WLD-USDT-SWAP",
        "clOrdId": entry_id,
    })


def test_canceled_exact_entry_has_zero_remainder_and_is_not_canceled_again(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    intent = _intent(
        symbol="PUMP-USDT",
        entry_ref=0.002496,
        stop_loss=0.00242,
        take_profit=0.002572,
    )
    entry_id = executor.make_tactical_clord_id(intent.intent_id, "entry")
    executor.exchange.private_get_trade_order.return_value = {
        "code": "0",
        "data": [{
            "ordId": "3805724946214244352",
            "clOrdId": entry_id,
            "instId": "PUMP-USDT-SWAP",
            "state": "canceled",
            "sz": "200",
            "accFillSz": "0",
            "avgPx": "",
        }],
    }

    observed = executor.query_tactical_entry(intent)
    canceled = executor.cancel_tactical_entry(intent)

    assert observed["observation"]["status"] == "canceled"
    assert observed["observation"]["remaining_qty"] == 0.0
    assert canceled["proven"] is True
    assert canceled["reason"] == "no_remainder"
    executor.exchange.cancel_order.assert_not_called()


def test_cancel_entry_rechecks_exact_terminal_state_after_cancel_error(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    intent = _intent(
        symbol="PUMP-USDT",
        entry_ref=0.002496,
        stop_loss=0.00242,
        take_profit=0.002572,
    )
    entry_id = executor.make_tactical_clord_id(intent.intent_id, "entry")
    open_order = {
        "ordId": "3805724946214244352",
        "clOrdId": entry_id,
        "instId": "PUMP-USDT-SWAP",
        "state": "live",
        "sz": "200",
        "accFillSz": "0",
        "avgPx": "",
    }
    canceled_order = {
        **open_order,
        "state": "canceled",
    }
    executor.exchange.private_get_trade_order.side_effect = [
        {"code": "0", "data": [open_order]},
        {"code": "0", "data": [canceled_order]},
    ]
    executor.exchange.cancel_order.side_effect = RuntimeError(
        "51400 Order cancellation failed as the order has been filled, "
        "canceled or does not exist"
    )

    canceled = executor.cancel_tactical_entry(intent)

    assert canceled["proven"] is True
    assert canceled["reason"] == "cancel_confirmed"
    executor.exchange.cancel_order.assert_called_once_with(
        "3805724946214244352", "PUMP-USDT-SWAP"
    )


def test_canceled_partial_fill_preserves_fill_for_position_recovery(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    intent = _intent()
    entry_id = executor.make_tactical_clord_id(intent.intent_id, "entry")
    executor.exchange.private_get_trade_order.return_value = {
        "code": "0",
        "data": [{
            "ordId": "partially-filled-entry",
            "clOrdId": entry_id,
            "instId": "WLD-USDT-SWAP",
            "state": "canceled",
            "sz": "500",
            "accFillSz": "125",
            "avgPx": "1.001",
        }],
    }

    canceled = executor.cancel_tactical_entry(intent)

    assert canceled["proven"] is True
    assert canceled["reason"] == "no_remainder"
    assert canceled["filled_qty"] == 125.0
    assert canceled["average_price"] == 1.001
    executor.exchange.cancel_order.assert_not_called()


def test_query_and_cancel_entry_use_deterministic_identity_only(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    intent = _intent()
    entry_id = executor.make_tactical_clord_id(intent.intent_id, "entry")
    open_rows = [
        {"id": "foreign", "clientOrderId": "manual-order", "status": "open"},
        {
            "id": "owned",
            "clientOrderId": entry_id,
            "status": "open",
            "filled": 2,
            "remaining": 3,
            "average": 1.0,
        },
    ]
    canceled_row = {
        "id": "owned",
        "clientOrderId": entry_id,
        "status": "canceled",
        "filled": 2,
        "remaining": 0,
        "average": 1.0,
    }
    executor.exchange.fetch_open_orders.side_effect = [open_rows, open_rows[1:], []]
    executor.exchange.fetch_orders.side_effect = [[], [], [canceled_row]]

    observed = executor.query_tactical_entry(intent)
    canceled = executor.cancel_tactical_entry(intent)

    assert observed["query_state"] == "found"
    assert observed["observation"] == {
        "order_id": "owned",
        "client_order_id": entry_id,
        "status": "open",
        "filled_qty": 2.0,
        "remaining_qty": 3.0,
        "average_price": 1.0,
    }
    assert canceled["proven"] is True
    assert canceled["reason"] == "cancel_confirmed"
    executor.exchange.cancel_order.assert_called_once_with("owned", "WLD-USDT-SWAP")


def test_cancel_entry_is_unproven_while_exchange_still_reports_remainder(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    intent = _intent()
    entry_id = executor.make_tactical_clord_id(intent.intent_id, "entry")
    still_open = {
        "id": "owned",
        "clientOrderId": entry_id,
        "status": "open",
        "filled": 2,
        "remaining": 3,
        "average": 1.0,
    }
    executor.exchange.fetch_open_orders.side_effect = [[still_open], [still_open]]
    executor.exchange.fetch_orders.return_value = []

    canceled = executor.cancel_tactical_entry(intent)

    assert canceled["proven"] is False
    assert canceled["reason"] == "cancel_unconfirmed"


def test_query_entry_reports_confirmed_not_found_when_any_source_succeeds(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    intent = _intent()
    executor.exchange.private_get_trade_order.side_effect = RuntimeError("not visible")
    executor.exchange.fetch_open_orders.return_value = []
    executor.exchange.fetch_orders.return_value = []

    result = executor.query_tactical_entry(intent)

    assert result["query_state"] == "not_found"
    assert result["observation"] is None
    assert result["successful_sources"] == ["fetch_open_orders", "fetch_orders"]
    assert result["errors"][0]["source"] == "private_get_trade_order"


def test_query_entry_reports_query_error_when_every_source_fails(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    executor = _executor()
    intent = _intent()
    executor.exchange.private_get_trade_order.side_effect = RuntimeError("private down")
    executor.exchange.fetch_open_orders.side_effect = RuntimeError("open down")
    executor.exchange.fetch_orders.side_effect = RuntimeError("history down")

    result = executor.query_tactical_entry(intent)

    assert result["query_state"] == "query_error"
    assert result["observation"] is None
    assert result["successful_sources"] == []
    assert {row["source"] for row in result["errors"]} == {
        "private_get_trade_order",
        "fetch_open_orders",
        "fetch_orders",
    }
