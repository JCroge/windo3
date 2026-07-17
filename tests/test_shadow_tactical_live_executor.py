import logging
from unittest.mock import MagicMock

from executor import ContractExecutor


def _executor():
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger("test_shadow_tactical_live_executor")
    ex.exchange_id = "okx"
    ex.testnet = False
    ex.leverage = 20
    ex.positions = {}
    ex.risk_manager = MagicMock()
    ex.risk_manager.max_trade_amount = 30.0
    ex.risk_manager.check_can_trade.return_value = (True, "ok")
    ex.get_balance = MagicMock(return_value=300.0)
    ex.balance_adapter = MagicMock()
    ex.balance_adapter.get_free.return_value = 100.0
    ex.caps = MagicMock()
    ex.caps.precheck_order.return_value = (True, "ok", {})
    ex.idempotency = None
    ex.ledger = None
    ex._okx_pos_mode = "net_mode"
    ex.is_symbol_halted = MagicMock(return_value=False)
    ex._halt_symbol = MagicMock()
    ex._check_slippage = MagicMock(return_value=True)
    ex._verify_attached_sl_after_fill = MagicMock(return_value="algo-1")
    ex._make_owner_tag_clord_id = MagicMock(return_value="castliveWLD1")
    ex._save_positions = MagicMock()
    ex._build_okx_attach_algo = ContractExecutor._build_okx_attach_algo.__get__(
        ex, ContractExecutor
    )
    ex._build_tp_sl_params = ContractExecutor._build_tp_sl_params.__get__(
        ex, ContractExecutor
    )
    ex._build_attach_algo_from_tp_sl = (
        ContractExecutor._build_attach_algo_from_tp_sl.__get__(ex, ContractExecutor)
    )
    ex._build_open_order_params = ContractExecutor._build_open_order_params.__get__(
        ex, ContractExecutor
    )
    ex.exchange = MagicMock()
    ex.exchange.fetch_ticker.return_value = {"last": 1.25}
    ex.exchange.set_leverage.return_value = None
    ex.exchange.market.return_value = {
        "contractSize": 1,
        "limits": {"amount": {"min": 1e-8}},
    }
    ex.exchange.amount_to_precision.side_effect = (
        lambda symbol, amount: str(round(float(amount), 6))
    )
    ex.exchange.create_order.return_value = {"id": "ord-1"}
    return ex


def _plan(**overrides):
    plan = {
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "entry_ref": 1.25,
        "stop_loss": 1.20,
        "take_profit": [1.32, 1.38],
        "leverage": 20,
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "tactical_source": "shadow_only",
        "tactical_max_hold_minutes": 90,
        "shadow_id": "shadow-1",
        "sidecar_source": "shadow_tactical_live",
        "gate_metadata": {
            "reject_reason": "rr_below_floor",
            "tactical_track_gate": "pass",
        },
    }
    plan.update(overrides)
    return plan


def test_open_sidecar_plan_places_order_without_drift_gate():
    ex = _executor()

    pos = ex.open_sidecar_plan(_plan(), size_usdt=30.0)

    assert pos["symbol"] == "WLD-USDT-SWAP"
    assert pos["amount_usdt"] == 30.0
    assert pos["sidecar_source"] == "shadow_tactical_live"
    assert pos["shadow_id"] == "shadow-1"
    ex.exchange.create_order.assert_called_once()
    assert not hasattr(ex, "_pending_drift_alerts") or ex._pending_drift_alerts == []


def test_open_sidecar_plan_canonicalizes_internal_symbol_to_swap():
    ex = _executor()

    pos = ex.open_sidecar_plan(_plan(symbol="ONDO-USDT"), size_usdt=30.0)

    assert pos["symbol"] == "ONDO-USDT-SWAP"
    assert pos["internal_symbol"] == "ONDO-USDT"
    ex.exchange.fetch_ticker.assert_called_with("ONDO-USDT-SWAP")


def test_open_sidecar_plan_persists_tactical_exit_metadata():
    ex = _executor()

    pos = ex.open_sidecar_plan(_plan(), size_usdt=30.0)

    assert pos["track"] == "tactical"
    assert pos["exit_profile"] == "tactical_v1"
    assert pos["tactical_source"] == "shadow_only"
    assert pos["tactical_max_hold_minutes"] == 90
    assert pos["entry_ref"] == 1.25
    assert pos["gate_metadata"] == {
        "reject_reason": "rr_below_floor",
        "tactical_track_gate": "pass",
    }


def test_open_sidecar_plan_rejects_invalid_long_stop_side():
    ex = _executor()

    assert ex.open_sidecar_plan(_plan(stop_loss=1.30), size_usdt=30.0) is None

    ex.exchange.create_order.assert_not_called()


def test_open_sidecar_plan_enforces_hard_size_cap():
    ex = _executor()

    pos = ex.open_sidecar_plan(_plan(), size_usdt=99.0)

    assert pos["amount_usdt"] == 30.0


def test_open_sidecar_plan_fails_closed_when_sl_unverified():
    ex = _executor()
    ex._verify_attached_sl_after_fill.return_value = None

    assert ex.open_sidecar_plan(_plan(), size_usdt=30.0) is None

    ex._halt_symbol.assert_called_once_with(
        "WLD-USDT-SWAP",
        reason="sidecar_sl_unverified",
    )
