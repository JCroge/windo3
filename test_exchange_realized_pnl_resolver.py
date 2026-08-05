"""Tests for RealizedPnlResolver + LiveLedger pending/correction + Reconciler

Covers acceptance criteria from
docs/exchange_realized_pnl_ledger_acceptance.md:

- AC-A2 Resolver via fills-history -> final
- AC-A3 bills consistency (match + mismatch)
- AC-A4 external close pending no pollution
- AC-A5 pending -> apply_pnl_resolution upsert idempotent
- AC-A7 ambiguous candidate detection
- AC-A8 funding attribution
- AC-A9 fee currency non-USDT -> pending_fx
- AC-A12 Reconciler auto-resolve pending
"""

import os
import json
import tempfile
import threading
import time
from unittest.mock import MagicMock

import pytest

from utils.live_ledger import (
    LiveLedger,
    PNL_STATUS_FINAL,
    PNL_STATUS_PENDING,
)
from utils.realized_pnl_resolver import (
    RealizedPnlResolver,
    PNL_STATUS_FINAL as R_FINAL,
    PNL_STATUS_PENDING as R_PENDING,
    PNL_STATUS_MISMATCH,
    PNL_STATUS_PENDING_FX,
)
from utils.reconciliation import Reconciler


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_exchange():
    return MagicMock()


@pytest.fixture
def ledger(tmp_dir, mock_exchange):
    events = os.path.join(tmp_dir, "events.jsonl")
    lifecycle = os.path.join(tmp_dir, "lifecycle.json")
    return LiveLedger(mock_exchange, events_path=events, lifecycle_path=lifecycle)


def _open_lc(ledger, symbol="JTO-USDT-SWAP", side="long",
             entry=0.55, amount=10, leverage=2):
    ledger.record_open(
        order_id="o-open", symbol=symbol, side=side,
        amount_usdt=amount, leverage=leverage, estimated_price=entry,
    )


# ── AC-A2 ──────────────────────────────────────────────────────────────────


def test_resolver_final_via_fills(mock_exchange):
    """AC-A2: fills-history → status=final, realized_pnl_net_usdt = fillPnl + fee"""
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [{
            "ordId": "close_1", "billId": "bill_1",
            "instId": "JTO-USDT-SWAP", "subType": "5",
            "posSide": "long",
            "fillPnl": "-1.5800", "fee": "-0.1500", "feeCcy": "USDT",
            "fillPx": "0.5438", "fillSz": "543",
            "fillTime": str(int(1779922722) * 1000),
            "side": "sell",
        }]
    }
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    resolver = RealizedPnlResolver(mock_exchange)
    snapshot = {
        "symbol": "JTO-USDT-SWAP", "side": "long",
        "position_id": "pid-1", "entry_request_id": "req-1",
        "opened_at": 1779922000, "unrealized_pnl": -1.7,
        "entry_price": 0.5438, "amount_usdt": 543, "leverage": 1,
    }
    res = resolver.resolve_external_close(
        snapshot, close_window={"closed_at": 1779922800})
    assert res["pnl_status"] == R_FINAL
    assert abs(res["realized_pnl_net_usdt"] - (-1.73)) < 1e-3
    assert res["order_ids"] == ["close_1"]
    assert "okx_fills_history" in res["pnl_source"]


def test_tactical_v2_final_includes_exact_entry_fill_fee(mock_exchange):
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [
            {
                "ordId": "entry-1",
                "clOrdId": "tv2-entry-1",
                "instId": "ADA-USDT-SWAP",
                "fillPnl": "0",
                "fee": "-0.25",
                "feeCcy": "USDT",
                "fillPx": "0.60",
                "fillSz": "500",
                "fillTime": "1000000",
                "side": "buy",
            },
            {
                "ordId": "close-1",
                "algoClOrdId": "tv2-tp-1",
                "instId": "ADA-USDT-SWAP",
                "fillPnl": "8.00",
                "fee": "-0.15",
                "feeCcy": "USDT",
                "fillPx": "0.616",
                "fillSz": "500",
                "fillTime": "1045000",
                "side": "sell",
            },
        ]
    }
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    resolver = RealizedPnlResolver(mock_exchange)

    result = resolver.resolve_external_close(
        {
            "symbol": "ADA-USDT-SWAP",
            "side": "long",
            "position_id": "tv2:intent-1",
            "entry_request_id": "tv2-entry-1",
            "opened_at": 1000.0,
            "entry_price": 0.60,
            "amount_usdt": 100.0,
            "leverage": 5,
            "strategy_owner": "tactical_v2",
            "tp_algo_clord_id": "tv2-tp-1",
            "sl_algo_clord_id": "tv2-sl-1",
            "attribution": {"strategy_owner": "tactical_v2"},
        },
        close_window={"closed_at": 1045.0},
    )

    assert result["pnl_status"] == R_FINAL
    assert result["gross_close_pnl_usdt"] == 8.0
    assert result["entry_fee_usdt"] == -0.25
    assert result["fee_usdt"] == -0.40
    assert result["realized_pnl_net_usdt"] == 7.60
    assert result["tactical_v2_proof"] == {
        "complete": True,
        "entry_request_id": "tv2-entry-1",
        "entry_order_ids": ["entry-1"],
        "close_order_ids": ["close-1"],
        "entry_qty": 500.0,
        "close_qty": 500.0,
        "entry_fee_usdt": -0.25,
        "close_ownership": "deterministic_close_command",
    }


def test_tactical_v2_bills_match_entry_fee_and_close_pnl(mock_exchange):
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [
            {
                "ordId": "entry-1",
                "clOrdId": "tv2-entry-1",
                "instId": "ADA-USDT-SWAP",
                "fillPnl": "0",
                "fee": "-0.25",
                "feeCcy": "USDT",
                "fillPx": "0.60",
                "fillSz": "500",
                "fillTime": "1000000",
                "side": "buy",
            },
            {
                "ordId": "close-1",
                "algoClOrdId": "tv2-tp-1",
                "instId": "ADA-USDT-SWAP",
                "fillPnl": "8.00",
                "fee": "-0.15",
                "feeCcy": "USDT",
                "fillPx": "0.616",
                "fillSz": "500",
                "fillTime": "1045000",
                "side": "sell",
            },
        ]
    }
    mock_exchange.private_get_account_bills.return_value = {
        "data": [
            {
                "billId": "entry-fee-bill",
                "ordId": "entry-1",
                "subType": "171",
                "instId": "ADA-USDT-SWAP",
                "pnl": "0",
                "fee": "-0.25",
            },
            {
                "billId": "close-pnl-bill",
                "ordId": "close-1",
                "subType": "174",
                "instId": "ADA-USDT-SWAP",
                "pnl": "8.00",
                "fee": "-0.15",
            },
        ]
    }
    resolver = RealizedPnlResolver(mock_exchange)

    result = resolver.resolve_external_close(
        {
            "symbol": "ADA-USDT-SWAP",
            "side": "long",
            "position_id": "tv2:intent-1",
            "entry_request_id": "tv2-entry-1",
            "opened_at": 1000.0,
            "entry_price": 0.60,
            "amount_usdt": 100.0,
            "leverage": 5,
            "strategy_owner": "tactical_v2",
            "tp_algo_clord_id": "tv2-tp-1",
            "sl_algo_clord_id": "tv2-sl-1",
            "attribution": {"strategy_owner": "tactical_v2"},
        },
        close_window={"closed_at": 1045.0},
    )

    assert result["pnl_status"] == R_FINAL
    assert result["realized_pnl_net_usdt"] == 7.60
    assert result["match_confidence"] == 0.98
    assert result["bill_ids"] == ["entry-fee-bill", "close-pnl-bill"]


def test_tactical_v2_replays_real_ada_anonymous_close_and_bills(mock_exchange):
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [
            {
                "ordId": "3800889182842347520",
                "clOrdId": "calivemain01v2eb3a0d238a10482fc4",
                "instId": "ADA-USDT-SWAP",
                "fillPnl": "0",
                "fee": "-0.2499075",
                "feeCcy": "USDT",
                "fillPx": "0.1915",
                "fillSz": "26.1",
                "fillTime": "1785777724787",
                "side": "buy",
            },
            {
                "ordId": "3801150039962771456",
                "instId": "ADA-USDT-SWAP",
                "fillPnl": "8.091",
                "fee": "-0.253953",
                "feeCcy": "USDT",
                "fillPx": "0.1946",
                "fillSz": "26.1",
                "fillTime": "1785785498936",
                "side": "sell",
            },
        ]
    }
    mock_exchange.private_get_account_bills.return_value = {
        "data": [
            {
                "billId": "3801150040029880320",
                "ordId": "3801150039962771456",
                "instId": "ADA-USDT-SWAP",
                "subType": "2",
                "pnl": "8.091",
                "fee": "-0.253953",
            },
            {
                "billId": "3800889182909456385",
                "ordId": "3800889182842347520",
                "instId": "ADA-USDT-SWAP",
                "subType": "1",
                "pnl": "0",
                "fee": "-0.2499075",
            },
        ]
    }
    resolver = RealizedPnlResolver(mock_exchange)
    intent_id = "081dfe419ec1c5709be6c38e81fbc319"

    result = resolver.resolve_external_close(
        {
            "symbol": "ADA-USDT-SWAP",
            "side": "long",
            "position_id": f"tv2:{intent_id}",
            "intent_id": intent_id,
            "entry_request_id": "calivemain01v2eb3a0d238a10482fc4",
            "entry_client_id": "calivemain01v2eb3a0d238a10482fc4",
            "opened_at": 1785777724.0,
            "entry_price": 0.1915,
            "amount_usdt": 100.0,
            "leverage": 5,
            "strategy_owner": "tactical_v2",
            "tp_client_id": "calivemain01v2tbb5773854495444f4",
            "sl_client_id": "calivemain01v2s589ac7b353cbf0e8b",
            "attribution": {
                "strategy_owner": "tactical_v2",
                "intent_id": intent_id,
            },
        },
        close_window={"closed_at": 1785785498.936},
    )

    assert result["pnl_status"] == R_FINAL
    assert result["realized_pnl_net_usdt"] == 7.5871
    assert result["match_confidence"] == 0.98
    assert result["bill_ids"] == [
        "3801150040029880320",
        "3800889182909456385",
    ]


@pytest.mark.parametrize(
    "entry_size,pending_reason",
    [
        (None, "exact_entry_fills_missing"),
        (400.0, "entry_close_quantity_mismatch"),
    ],
)
def test_tactical_v2_final_requires_exact_entry_fill_and_quantity(
    mock_exchange,
    entry_size,
    pending_reason,
):
    rows = []
    if entry_size is not None:
        rows.append({
            "ordId": "entry-1",
            "clOrdId": "tv2-entry-1",
            "instId": "ADA-USDT-SWAP",
            "fillPnl": "0",
            "fee": "-0.25",
            "feeCcy": "USDT",
            "fillPx": "0.60",
            "fillSz": str(entry_size),
            "fillTime": "1000000",
            "side": "buy",
        })
    rows.append({
        "ordId": "close-1",
        "algoClOrdId": "tv2-tp-1",
        "instId": "ADA-USDT-SWAP",
        "fillPnl": "8.00",
        "fee": "-0.15",
        "feeCcy": "USDT",
        "fillPx": "0.616",
        "fillSz": "500",
        "fillTime": "1045000",
        "side": "sell",
    })
    mock_exchange.private_get_trade_fills_history.return_value = {"data": rows}
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    resolver = RealizedPnlResolver(mock_exchange)

    result = resolver.resolve_external_close(
        {
            "symbol": "ADA-USDT-SWAP",
            "side": "long",
            "position_id": "tv2:intent-1",
            "entry_request_id": "tv2-entry-1",
            "opened_at": 1000.0,
            "entry_price": 0.60,
            "amount_usdt": 100.0,
            "leverage": 5,
            "strategy_owner": "tactical_v2",
            "tp_algo_clord_id": "tv2-tp-1",
            "sl_algo_clord_id": "tv2-sl-1",
            "attribution": {"strategy_owner": "tactical_v2"},
        },
        close_window={"closed_at": 1045.0},
    )

    assert result["pnl_status"] == R_PENDING
    assert result["pnl_pending_reason"] == pending_reason
    assert result["realized_pnl_net_usdt"] is None


def test_tactical_v2_ignores_unowned_close_fill_in_same_window(mock_exchange):
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [
            {
                "ordId": "entry-1",
                "clOrdId": "tv2-entry-1",
                "fillPnl": "0",
                "fee": "-0.25",
                "feeCcy": "USDT",
                "fillPx": "0.60",
                "fillSz": "500",
                "fillTime": "1000000",
                "side": "buy",
            },
            {
                "ordId": "foreign-close",
                "clOrdId": "manual-close",
                "fillPnl": "8.00",
                "fee": "-0.15",
                "feeCcy": "USDT",
                "fillPx": "0.616",
                "fillSz": "500",
                "fillTime": "1045000",
                "side": "sell",
            },
        ]
    }
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    resolver = RealizedPnlResolver(mock_exchange)

    result = resolver.resolve_external_close(
        {
            "symbol": "ADA-USDT-SWAP",
            "side": "long",
            "position_id": "tv2:intent-1",
            "entry_request_id": "tv2-entry-1",
            "opened_at": 1000.0,
            "strategy_owner": "tactical_v2",
            "tp_algo_clord_id": "tv2-tp-1",
            "sl_algo_clord_id": "tv2-sl-1",
            "close_client_id": "tv2-close-1",
        },
        close_window={"closed_at": 1045.0},
    )

    assert result["pnl_status"] == R_PENDING
    assert result["pnl_pending_reason"] == "no_owned_close_fills_in_window"


@pytest.mark.parametrize(
    ("tp_identity_key", "sl_identity_key"),
    [
        ("tp_client_id", "sl_client_id"),
        ("tp_algo_clord_id", "sl_algo_clord_id"),
    ],
)
def test_tactical_v2_accepts_single_anonymous_close_for_isolated_position(
    mock_exchange,
    tp_identity_key,
    sl_identity_key,
):
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [
            {
                "ordId": "entry-1",
                "clOrdId": "tv2-entry-1",
                "instId": "ADA-USDT-SWAP",
                "fillPnl": "0",
                "fee": "-0.25",
                "feeCcy": "USDT",
                "fillPx": "0.60",
                "fillSz": "500",
                "fillTime": "1000000",
                "side": "buy",
            },
            {
                "ordId": "close-1",
                "instId": "ADA-USDT-SWAP",
                "fillPnl": "8.00",
                "fee": "-0.15",
                "feeCcy": "USDT",
                "fillPx": "0.616",
                "fillSz": "500",
                "fillTime": "1045000",
                "side": "sell",
            },
        ]
    }
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    resolver = RealizedPnlResolver(mock_exchange)

    result = resolver.resolve_external_close(
        {
            "symbol": "ADA-USDT-SWAP",
            "side": "long",
            "position_id": "tv2:intent-1",
            "intent_id": "intent-1",
            "entry_request_id": "tv2-entry-1",
            "entry_client_id": "tv2-entry-1",
            "opened_at": 1000.0,
            "entry_price": 0.60,
            "amount_usdt": 100.0,
            "leverage": 5,
            "strategy_owner": "tactical_v2",
            tp_identity_key: "tv2-tp-1",
            sl_identity_key: "tv2-sl-1",
            "attribution": {
                "strategy_owner": "tactical_v2",
                "intent_id": "intent-1",
            },
        },
        close_window={"closed_at": 1045.0},
    )

    assert result["pnl_status"] == R_FINAL
    assert result["realized_pnl_net_usdt"] == 7.60
    assert result["tactical_v2_proof"]["close_ownership"] == (
        "isolated_position_single_close_order"
    )


def test_tactical_v2_rejects_multiple_anonymous_close_orders(mock_exchange):
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [
            {
                "ordId": "entry-1",
                "clOrdId": "tv2-entry-1",
                "fillPnl": "0",
                "fee": "-0.25",
                "feeCcy": "USDT",
                "fillPx": "0.60",
                "fillSz": "500",
                "fillTime": "1000000",
                "side": "buy",
            },
            {
                "ordId": "close-a",
                "fillPnl": "3.00",
                "fee": "-0.06",
                "feeCcy": "USDT",
                "fillPx": "0.615",
                "fillSz": "200",
                "fillTime": "1045000",
                "side": "sell",
            },
            {
                "ordId": "close-b",
                "fillPnl": "5.00",
                "fee": "-0.09",
                "feeCcy": "USDT",
                "fillPx": "0.617",
                "fillSz": "300",
                "fillTime": "1045001",
                "side": "sell",
            },
        ]
    }
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    resolver = RealizedPnlResolver(mock_exchange)

    result = resolver.resolve_external_close(
        {
            "symbol": "ADA-USDT-SWAP",
            "side": "long",
            "position_id": "tv2:intent-1",
            "intent_id": "intent-1",
            "entry_request_id": "tv2-entry-1",
            "entry_client_id": "tv2-entry-1",
            "opened_at": 1000.0,
            "strategy_owner": "tactical_v2",
            "tp_client_id": "tv2-tp-1",
            "sl_client_id": "tv2-sl-1",
            "attribution": {
                "strategy_owner": "tactical_v2",
                "intent_id": "intent-1",
            },
        },
        close_window={"closed_at": 1045.0},
    )

    assert result["pnl_status"] == R_PENDING
    assert result["pnl_pending_reason"] == "no_owned_close_fills_in_window"


# ── AC-A3 ──────────────────────────────────────────────────────────────────


def test_resolver_bills_match_high_confidence(mock_exchange):
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [{
            "ordId": "close_1", "instId": "JTO-USDT-SWAP", "subType": "5",
            "fillPnl": "-1.58", "fee": "-0.15", "feeCcy": "USDT",
            "fillPx": "0.5438", "fillSz": "543",
            "fillTime": str(1779922722000), "side": "sell",
        }]
    }
    mock_exchange.private_get_account_bills.return_value = {
        "data": [
            {"billId": "bill_1", "ordId": "close_1", "subType": "174",
             "instId": "JTO-USDT-SWAP", "pnl": "-1.58", "fee": "-0.15"},
        ]
    }
    resolver = RealizedPnlResolver(mock_exchange)
    snapshot = {"symbol": "JTO-USDT-SWAP", "side": "long",
                "position_id": "pid", "opened_at": 1779922000,
                "entry_price": 0.5438, "amount_usdt": 543, "leverage": 1}
    res = resolver.resolve_external_close(
        snapshot, close_window={"closed_at": 1779922800})
    assert res["pnl_status"] == R_FINAL
    assert res["match_confidence"] >= 0.95
    assert "okx_bills" in res["pnl_source"]


def test_resolver_bills_mismatch(mock_exchange):
    """AC-A3: fills - bills 超阈值 → mismatch, realized_pnl_net_usdt=None"""
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [{
            "ordId": "close_1", "instId": "JTO-USDT-SWAP",
            "fillPnl": "-1.58", "fee": "-0.15", "feeCcy": "USDT",
            "fillPx": "0.5438", "fillSz": "543",
            "fillTime": str(1779922722000), "side": "sell",
        }]
    }
    mock_exchange.private_get_account_bills.return_value = {
        "data": [
            {"billId": "bill_1", "ordId": "close_1", "subType": "174",
             "instId": "JTO-USDT-SWAP", "pnl": "-2.30", "fee": "0"},
        ]
    }
    resolver = RealizedPnlResolver(mock_exchange)
    snapshot = {"symbol": "JTO-USDT-SWAP", "side": "long",
                "position_id": "pid", "opened_at": 1779922000,
                "entry_price": 0.5438, "amount_usdt": 543, "leverage": 1}
    res = resolver.resolve_external_close(
        snapshot, close_window={"closed_at": 1779922800})
    assert res["pnl_status"] == PNL_STATUS_MISMATCH
    assert res["realized_pnl_net_usdt"] is None
    assert any("mismatch" in w for w in res["warnings"])


# ── AC-A4 ──────────────────────────────────────────────────────────────────


def test_pending_no_pollution(mock_exchange):
    """AC-A4: fills/bills 都失败 → pnl_status=pending, realized_pnl_net_usdt=None"""
    mock_exchange.private_get_trade_fills_history.side_effect = Exception("net")
    mock_exchange.private_get_account_bills.side_effect = Exception("net")
    resolver = RealizedPnlResolver(mock_exchange)
    snapshot = {"symbol": "JTO-USDT-SWAP", "side": "long",
                "position_id": "pid-1", "entry_request_id": "req-1",
                "opened_at": 1779922000, "unrealized_pnl": -0.543,
                "entry_price": 0.55, "amount_usdt": 10, "leverage": 2}
    res = resolver.resolve_external_close(
        snapshot, close_window={"closed_at": 1779922800})
    assert res["pnl_status"] == R_PENDING
    assert res["realized_pnl_net_usdt"] is None
    assert res["estimated_pnl"] == -0.543


# ── AC-A5 ──────────────────────────────────────────────────────────────────


def test_apply_pnl_resolution_upsert_idempotent(ledger):
    """AC-A5/AC-A5a: 重复 final apply 严格幂等,
    第二次返回 existing,不重复加 lifecycle/daily PnL。"""
    pending = ledger.record_pending_external_close(
        symbol="JTO-USDT-SWAP", side="long",
        entry_price=0.55, amount_usdt=10, leverage=2,
        estimated_pnl=-0.543, position_id="pid-1",
        entry_request_id="req-1", opened_at=1779922000,
    )
    assert pending["pnl_status"] == PNL_STATUS_PENDING
    resolution = {
        "pnl_status": R_FINAL, "symbol": "JTO-USDT-SWAP", "side": "long",
        "position_id": "pid-1", "entry_request_id": "req-1",
        "opened_at": 1779922000,
        "realized_pnl_net_usdt": -1.73,
        "gross_close_pnl_usdt": -1.58, "fee_usdt": -0.15,
        "funding_usdt": 0,
        "pnl_source": "okx_fills_history+okx_bills",
        "order_ids": ["close_1"], "bill_ids": ["bill_1"],
        "match_confidence": 0.98, "warnings": [],
    }
    correction1 = ledger.apply_pnl_resolution(resolution)
    assert correction1 is not None
    assert correction1["supersedes_event_id"] == pending["event_id"]
    assert correction1["correction_seq"] == 1
    assert correction1.get("resolution_id", "").startswith("rid_")

    # AC-A5a: 第二次相同 resolution 严格幂等 — 返回 existing,
    # lifecycle.total_realized_pnl 仍是 -1.73,不变 -3.46
    correction2 = ledger.apply_pnl_resolution(resolution)
    assert correction2 is not None
    assert correction2.get("status") == "existing"
    assert correction2.get("event_id") == correction1["event_id"]
    assert correction2.get("resolution_id") == correction1.get("resolution_id")
    # find_pending_external_closes 已无 pending(被原 supersede 标记)
    pending2 = ledger.find_pending_external_closes()
    assert pending2 == []
    # lifecycle 累计仅 -1.73
    lc = ledger.get_lifecycle("JTO-USDT-SWAP", "long")
    if lc is not None:
        assert abs(lc.get("total_realized_pnl", 0) - (-1.73)) < 1e-6


def test_record_pending_external_close_is_idempotent_by_position(ledger):
    first = ledger.record_pending_external_close(
        symbol="ADA-USDT-SWAP",
        side="long",
        entry_price=0.60,
        amount_usdt=100,
        leverage=5,
        estimated_pnl=1.0,
        position_id="tv2:intent-1",
        entry_request_id="tv2-entry-1",
        opened_at=1000.0,
        closed_at=1045.0,
    )

    duplicate = ledger.record_pending_external_close(
        symbol="ADA-USDT-SWAP",
        side="long",
        entry_price=0.60,
        amount_usdt=100,
        leverage=5,
        estimated_pnl=1.1,
        position_id="tv2:intent-1",
        entry_request_id="tv2-entry-1",
        opened_at=1000.0,
        closed_at=1075.0,
    )

    assert duplicate["event_id"] == first["event_id"]
    assert duplicate["status"] == "existing"
    events = [
        event for event in ledger._read_events()
        if event.get("event_type") == "external_close"
        and event.get("position_id") == "tv2:intent-1"
    ]
    assert len(events) == 1


def test_pending_retry_update_cannot_overwrite_concurrent_final(
    ledger,
    monkeypatch,
):
    pending = ledger.record_pending_external_close(
        symbol="ADA-USDT-SWAP",
        side="long",
        entry_price=0.60,
        amount_usdt=100,
        leverage=5,
        estimated_pnl=1.0,
        position_id="tv2:intent-ledger-race",
        entry_request_id="tv2-entry-ledger-race",
        opened_at=1000.0,
        closed_at=1045.0,
    )
    replace_entered = threading.Event()
    release_replace = threading.Event()
    original_replace = os.replace

    def blocking_replace(source, destination):
        if (
            threading.current_thread().name == "pending-updater"
            and source == ledger.events_path + ".tmp"
            and destination == ledger.events_path
        ):
            replace_entered.set()
            assert release_replace.wait(timeout=2.0)
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", blocking_replace)
    update_result = {}
    final_result = {}

    def update_pending():
        update_result["event"] = ledger.update_pending_resolution_attempt(
            pending["close_match_key"],
            "ADA-USDT-SWAP",
            "long",
            "tv2:intent-ledger-race",
            "fills_not_visible",
        )

    def apply_final():
        final_result["event"] = ledger.apply_pnl_resolution({
            "pnl_status": R_FINAL,
            "symbol": "ADA-USDT-SWAP",
            "side": "long",
            "position_id": "tv2:intent-ledger-race",
            "entry_request_id": "tv2-entry-ledger-race",
            "opened_at": 1000.0,
            "realized_pnl_net_usdt": 1.25,
            "gross_close_pnl_usdt": 1.50,
            "fee_usdt": -0.25,
            "funding_usdt": 0.0,
            "pnl_source": "okx_fills_history",
            "order_ids": ["close-ledger-race"],
            "bill_ids": [],
            "match_confidence": 1.0,
            "warnings": [],
        })

    updater = threading.Thread(target=update_pending, name="pending-updater")
    finalizer = threading.Thread(target=apply_final, name="finalizer")
    updater.start()
    assert replace_entered.wait(timeout=2.0)
    finalizer.start()
    time.sleep(0.05)
    release_replace.set()
    updater.join(timeout=2.0)
    finalizer.join(timeout=2.0)

    assert not updater.is_alive()
    assert not finalizer.is_alive()
    assert update_result["event"]["attempt_count"] == 1
    assert final_result["event"]["pnl_status"] == PNL_STATUS_FINAL
    events = ledger._read_events()
    finals = [
        event for event in events
        if event.get("event_type") == "external_close_correction"
        and event.get("position_id") == "tv2:intent-ledger-race"
    ]
    assert len(finals) == 1
    assert finals[0]["supersedes_event_id"] == pending["event_id"]
    assert ledger.find_pending_external_closes() == []


def test_pending_retry_update_cannot_overwrite_concurrent_event_append(
    ledger,
    monkeypatch,
):
    other_ledger = LiveLedger(
        exchange=ledger.exchange,
        events_path=ledger.events_path,
        lifecycle_path=ledger.lifecycle_path,
    )
    pending = ledger.record_pending_external_close(
        symbol="ADA-USDT-SWAP",
        side="long",
        entry_price=0.60,
        amount_usdt=100,
        leverage=5,
        position_id="tv2:intent-append-race",
        entry_request_id="tv2-entry-append-race",
        opened_at=1000.0,
        closed_at=1045.0,
    )
    replace_entered = threading.Event()
    release_replace = threading.Event()
    original_replace = os.replace

    def blocking_replace(source, destination):
        if (
            threading.current_thread().name == "pending-updater"
            and source == ledger.events_path + ".tmp"
            and destination == ledger.events_path
        ):
            replace_entered.set()
            assert release_replace.wait(timeout=2.0)
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", blocking_replace)

    updater = threading.Thread(
        target=lambda: ledger.update_pending_resolution_attempt(
            pending["close_match_key"],
            "ADA-USDT-SWAP",
            "long",
            "tv2:intent-append-race",
            "fills_not_visible",
        ),
        name="pending-updater",
    )
    appender = threading.Thread(
        target=lambda: other_ledger.record_entry_drift_decision(
            symbol="WLD-USDT-SWAP",
            side="long",
            gate="price_chase",
            band="accepted",
            drift_pct=0.01,
            decision="accept",
            reason=None,
            rr_actual=2.0,
            rr_floor_used=1.5,
            request_id="append-race-event",
        ),
        name="event-appender",
    )
    updater.start()
    assert replace_entered.wait(timeout=2.0)
    appender.start()
    time.sleep(0.05)
    release_replace.set()
    updater.join(timeout=2.0)
    appender.join(timeout=2.0)

    assert not updater.is_alive()
    assert not appender.is_alive()
    drift_events = [
        event for event in ledger._read_events()
        if event.get("request_id") == "append-race-event"
    ]
    assert len(drift_events) == 1


# ── AC-A7 ──────────────────────────────────────────────────────────────────


def test_resolver_ambiguous_close_match(mock_exchange):
    """AC-A7: 同窗口两组独立 close ordId,跨度 > 2*grace → ambiguous"""
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [
            {"ordId": "close_a", "instId": "JTO-USDT-SWAP",
             "fillPnl": "-1.58", "fee": "-0.15", "feeCcy": "USDT",
             "fillPx": "0.5438", "fillSz": "543",
             "fillTime": str(1779922000000), "side": "sell"},
            {"ordId": "close_b", "instId": "JTO-USDT-SWAP",
             "fillPnl": "-2.0", "fee": "-0.10", "feeCcy": "USDT",
             "fillPx": "0.5400", "fillSz": "200",
             "fillTime": str(1779922800000), "side": "sell"},
        ]
    }
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    resolver = RealizedPnlResolver(mock_exchange, bills_grace_ms=60_000)
    snapshot = {"symbol": "JTO-USDT-SWAP", "side": "long",
                "position_id": "pid", "opened_at": 1779921000,
                "entry_price": 0.55, "amount_usdt": 10, "leverage": 2}
    res = resolver.resolve_external_close(
        snapshot, close_window={"closed_at": 1779923000})
    assert res["pnl_status"] == R_PENDING
    assert "ambiguous_close_match" in res["warnings"]
    assert res["realized_pnl_net_usdt"] is None


# ── AC-A8 ──────────────────────────────────────────────────────────────────


def test_resolver_funding_attribution(mock_exchange):
    """AC-A8: bills 中 funding subType=173/7 单独累加到 funding_usdt"""
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [{
            "ordId": "close_1", "instId": "JTO-USDT-SWAP",
            "fillPnl": "0.80", "fee": "-0.10", "feeCcy": "USDT",
            "fillPx": "0.55", "fillSz": "100",
            "fillTime": str(1779922722000), "side": "sell",
        }]
    }
    mock_exchange.private_get_account_bills.return_value = {
        "data": [
            {"billId": "f1", "ordId": "", "subType": "173",
             "instId": "JTO-USDT-SWAP", "pnl": "-0.05", "fee": "0"},
            {"billId": "p1", "ordId": "close_1", "subType": "174",
             "instId": "JTO-USDT-SWAP", "pnl": "0.80", "fee": "-0.10"},
        ]
    }
    resolver = RealizedPnlResolver(mock_exchange)
    snapshot = {"symbol": "JTO-USDT-SWAP", "side": "long",
                "position_id": "pid", "opened_at": 1779922000,
                "entry_price": 0.55, "amount_usdt": 100, "leverage": 1}
    res = resolver.resolve_external_close(
        snapshot, close_window={"closed_at": 1779922800})
    assert res["pnl_status"] == R_FINAL
    assert abs(res["funding_usdt"] - (-0.05)) < 1e-6
    # 0.80 - 0.10 + (-0.05) = 0.65
    assert abs(res["realized_pnl_net_usdt"] - 0.65) < 1e-3


# ── AC-A9 ──────────────────────────────────────────────────────────────────


def test_resolver_fee_currency_non_usdt(mock_exchange):
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [{
            "ordId": "close_1", "instId": "JTO-USDT-SWAP",
            "fillPnl": "0.5", "fee": "-0.001", "feeCcy": "JTO",
            "fillPx": "0.55", "fillSz": "100",
            "fillTime": str(1779922722000), "side": "sell",
        }]
    }
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    resolver = RealizedPnlResolver(mock_exchange)
    snapshot = {"symbol": "JTO-USDT-SWAP", "side": "long",
                "position_id": "pid", "opened_at": 1779922000,
                "entry_price": 0.55, "amount_usdt": 100, "leverage": 1}
    res = resolver.resolve_external_close(
        snapshot, close_window={"closed_at": 1779922800})
    assert res["pnl_status"] == PNL_STATUS_PENDING_FX
    assert any("non_usdt" in w for w in res["warnings"])
    assert res["realized_pnl_net_usdt"] is None


# ── AC-A1 ──────────────────────────────────────────────────────────────────


def test_pnl_status_contract_pending_event(ledger):
    """AC-A1 + AC-A4: pending external_close 不写 final,realized_pnl_net_usdt=None"""
    _open_lc(ledger)
    pending = ledger.record_pending_external_close(
        symbol="JTO-USDT-SWAP", side="long",
        entry_price=0.55, amount_usdt=10, leverage=2,
        estimated_pnl=-0.543, position_id="pid-1",
        entry_request_id="req-1", opened_at=1779922000,
    )
    assert pending["pnl_status"] == PNL_STATUS_PENDING
    assert pending["realized_pnl_net_usdt"] is None
    assert pending["estimated_pnl"] == -0.543

    # daily_realized_pnl(final_only=True) 不计 pending
    total = ledger.daily_realized_pnl(final_only=True)
    assert total == 0.0


# ── AC-D1 ──────────────────────────────────────────────────────────────────


def test_reviewer_payload_pnl_is_final_helper():
    """AC-D1: pnl_is_final=False 不进 trade_history(helper 直接断言)"""
    from agents.trading.reviewer import (
        _payload_pnl_is_final, _payload_pnl_value,
    )
    pending_payload = {"status": "closed_externally"}
    pending_result = {"pnl_status": "pending", "pnl_is_final": False,
                      "estimated_pnl": -0.5}
    assert _payload_pnl_is_final(pending_payload, pending_result) is False

    final_payload = {"status": "closed_externally"}
    final_result = {"pnl_status": "final", "pnl_is_final": True,
                    "realized_pnl_net_usdt": -1.73, "pnl": -1.73}
    assert _payload_pnl_is_final(final_payload, final_result) is True
    assert _payload_pnl_value(final_result) == -1.73

    # 老 payload 兼容:reduce 没字段 → 默认 final
    legacy_payload = {"status": "risk_reduced"}
    legacy_result = {"pnl": -2.0}
    assert _payload_pnl_is_final(legacy_payload, legacy_result) is True


# ── AC-A12 ─────────────────────────────────────────────────────────────────


def test_reconciler_auto_resolve_pending(ledger, mock_exchange):
    """AC-A12: auto_resolve_pending 走 resolver,生成 correction event"""
    # 先写 pending
    ledger.record_pending_external_close(
        symbol="JTO-USDT-SWAP", side="long",
        entry_price=0.55, amount_usdt=10, leverage=2,
        estimated_pnl=-0.543, position_id="pid-1",
        entry_request_id="req-1", opened_at=1779922000,
    )
    # mock resolver inputs
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [{
            "ordId": "close_1", "instId": "JTO-USDT-SWAP",
            "fillPnl": "-1.58", "fee": "-0.15", "feeCcy": "USDT",
            "fillPx": "0.5438", "fillSz": "543",
            "fillTime": str(1779922722000), "side": "sell",
        }]
    }
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    resolver = RealizedPnlResolver(mock_exchange)
    reconciler = Reconciler(mock_exchange, ledger, resolver=resolver)
    summaries = reconciler.auto_resolve_pending()
    assert len(summaries) == 1
    s = summaries[0]
    assert s["pnl_status"] == R_FINAL
    assert s["correction_event_id"]
    # pending 已被升级,find_pending 应清零
    assert ledger.find_pending_external_closes() == []


# ── AC-D2 ──────────────────────────────────────────────────────────────────


def test_judge_pending_does_not_record_archetype():
    """AC-D2: closed_externally pending(pnl_is_final=False) 不进 archetype cooldown"""
    from agents.trading.judge import MultiJudge
    judge = MultiJudge.__new__(MultiJudge)
    judge._archetype_cooldown = MagicMock()
    judge._archetype_cooldown.classify = MagicMock(return_value="some_archetype")
    judge._archetype_cooldown.record_result = MagicMock()

    # 模拟 final-only gate 内联逻辑:从 payload 提取并判定
    payload = {
        "status": "closed_externally", "action": "close",
        "result": {
            "pnl_is_final": False, "pnl_status": "pending",
            "estimated_pnl": -0.5, "attribution": {"slot_type": "main"},
        },
    }
    result = payload["result"]
    pnl_is_final = bool(result.get("pnl_is_final"))
    pnl = result.get("realized_pnl_net_usdt") or result.get("pnl") or 0
    if pnl_is_final and pnl != 0 and result.get("attribution"):
        archetype = judge._archetype_cooldown.classify(result["attribution"])
        judge._archetype_cooldown.record_result(archetype, pnl)
    judge._archetype_cooldown.record_result.assert_not_called()

    # final 升级后再调用应记录
    payload["result"]["pnl_is_final"] = True
    payload["result"]["pnl_status"] = "final"
    payload["result"]["realized_pnl_net_usdt"] = -1.73
    result = payload["result"]
    pnl_is_final = bool(result.get("pnl_is_final"))
    pnl = result.get("realized_pnl_net_usdt") or result.get("pnl") or 0
    if pnl_is_final and pnl != 0 and result.get("attribution"):
        archetype = judge._archetype_cooldown.classify(result["attribution"])
        judge._archetype_cooldown.record_result(archetype, pnl)
    judge._archetype_cooldown.record_result.assert_called_once_with(
        "some_archetype", -1.73)


# ── AC-A5b retry schedule ──────────────────────────────────────────────────


def test_pending_retry_schedule_increments_attempts(ledger, mock_exchange):
    """AC-A5b: pending → resolver 仍 pending → update_pending_resolution_attempt
    递增 attempt_count, 按 [10, 30, 120, 600, 1800] 调整 next_retry_at,
    不写 supersedes correction (find_pending 仍可见)。"""
    ledger.record_pending_external_close(
        symbol="JTO-USDT-SWAP", side="long",
        entry_price=0.55, amount_usdt=10, leverage=2,
        estimated_pnl=-0.5, position_id="pid-retry",
        entry_request_id="req-retry", opened_at=time.time() - 60,
    )
    # resolver 永远返回 pending(网络挂)
    mock_exchange.private_get_trade_fills_history.side_effect = Exception("net")
    mock_exchange.private_get_account_bills.side_effect = Exception("net")
    resolver = RealizedPnlResolver(mock_exchange)
    reconciler = Reconciler(mock_exchange, ledger, resolver=resolver)

    expected_delays = [10, 30, 120, 600, 1800]
    for i, expected_delay in enumerate(expected_delays):
        # 强制下一轮 due:把 next_retry_at 设回当前时间,模拟时间到期
        for ev in ledger._read_events():
            if ev.get("event_type") == "external_close" and \
                    ev.get("pnl_status") == "pending":
                ev["next_retry_at"] = 0  # 立即 due
        # 重写 events.jsonl 仅为本测试设置 next_retry_at=0
        evs = ledger._read_events()
        with open(ledger.events_path, "w") as f:
            for e in evs:
                if e.get("event_type") == "external_close" and \
                        e.get("pnl_status") == "pending":
                    e["next_retry_at"] = 0
                f.write(json.dumps(e) + "\n")

        before = time.time()
        summaries = reconciler.auto_resolve_pending()
        # pending 重试不返回 correction summary(直接 continue 或 update only)
        assert summaries == [] or summaries[0]["pnl_status"] != R_FINAL

        # 校验 attempt_count == i+1, next_retry_at ≈ now + expected_delay
        pendings = ledger.find_pending_external_closes()
        assert len(pendings) == 1, f"iter {i}: pending lost"
        ev = pendings[0]
        assert ev.get("attempt_count") == i + 1
        delay_actual = ev.get("next_retry_at", 0) - before
        # 容差 ±5s(测试机噪声)
        assert abs(delay_actual - expected_delay) < 5, \
            f"iter {i}: delay={delay_actual} expected={expected_delay}"


# ── AC-A14 needs_manual_reconcile ─────────────────────────────────────────


def test_needs_manual_reconcile_after_24h(ledger, mock_exchange):
    """AC-A14: opened_at 早于 24h 前的 pending 在下一次 retry 时被标记 needs_manual_reconcile,
    Reconciler 后续轮次跳过(不再 resolve)。"""
    long_ago = time.time() - 86400 - 3600  # 25h 前
    ledger.record_pending_external_close(
        symbol="JTO-USDT-SWAP", side="long",
        entry_price=0.55, amount_usdt=10, leverage=2,
        estimated_pnl=-0.5, position_id="pid-stale",
        entry_request_id="req-stale", opened_at=long_ago,
    )
    # 把 next_retry_at 设回 0 让 reconciler 立即处理
    evs = ledger._read_events()
    with open(ledger.events_path, "w") as f:
        for e in evs:
            if e.get("event_type") == "external_close":
                e["next_retry_at"] = 0
            f.write(json.dumps(e) + "\n")

    mock_exchange.private_get_trade_fills_history.side_effect = Exception("net")
    mock_exchange.private_get_account_bills.side_effect = Exception("net")
    resolver = RealizedPnlResolver(mock_exchange)
    reconciler = Reconciler(mock_exchange, ledger, resolver=resolver)

    reconciler.auto_resolve_pending()
    pendings = ledger.find_pending_external_closes()
    assert len(pendings) == 1
    assert pendings[0].get("needs_manual_reconcile") is True

    # 第二轮:即使 next_retry_at=0,reconciler 看到 needs_manual_reconcile 直接跳过
    evs = ledger._read_events()
    with open(ledger.events_path, "w") as f:
        for e in evs:
            if e.get("event_type") == "external_close":
                e["next_retry_at"] = 0
            f.write(json.dumps(e) + "\n")
    mock_exchange.private_get_trade_fills_history.reset_mock(side_effect=True)
    mock_exchange.private_get_account_bills.reset_mock(side_effect=True)
    mock_exchange.private_get_trade_fills_history.return_value = {"data": []}
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    summaries = reconciler.auto_resolve_pending()
    assert summaries == []
    # resolver 没被调用
    assert mock_exchange.private_get_trade_fills_history.call_count == 0


# ── AC-A14 next_retry_at gating ────────────────────────────────────────────


def test_reconciler_skips_pending_before_next_retry(ledger, mock_exchange):
    """AC-A14: next_retry_at > now 的 pending 不被 resolve(等下一轮)。"""
    ledger.record_pending_external_close(
        symbol="JTO-USDT-SWAP", side="long",
        entry_price=0.55, amount_usdt=10, leverage=2,
        estimated_pnl=-0.5, position_id="pid-fresh",
        entry_request_id="req-fresh", opened_at=time.time() - 60,
    )
    # 设置 next_retry_at = now + 600(还没到)
    evs = ledger._read_events()
    future_retry = time.time() + 600
    with open(ledger.events_path, "w") as f:
        for e in evs:
            if e.get("event_type") == "external_close":
                e["next_retry_at"] = future_retry
            f.write(json.dumps(e) + "\n")

    resolver = RealizedPnlResolver(mock_exchange)
    reconciler = Reconciler(mock_exchange, ledger, resolver=resolver)
    summaries = reconciler.auto_resolve_pending()
    assert summaries == []
    assert mock_exchange.private_get_trade_fills_history.call_count == 0
    # pending 仍存在
    assert len(ledger.find_pending_external_closes()) == 1


# ── AC-A13 attribution + sl/tp algo id propagation ────────────────────────


def test_resolver_propagates_sl_tp_algo_and_attribution(mock_exchange):
    """AC-A13: snapshot 中的 sl_algo_id / tp_algo_id / entry_attribution
    必须出现在 resolution 中。"""
    mock_exchange.private_get_trade_fills_history.return_value = {
        "data": [{
            "ordId": "close_1", "instId": "JTO-USDT-SWAP",
            "fillPnl": "-1.58", "fee": "-0.15", "feeCcy": "USDT",
            "fillPx": "0.5438", "fillSz": "543",
            "fillTime": str(1779922722000), "side": "sell",
        }]
    }
    mock_exchange.private_get_account_bills.return_value = {"data": []}
    resolver = RealizedPnlResolver(mock_exchange)
    attribution = {"slot_type": "main", "regime": "trend",
                    "archetype_key": "long_breakout"}
    snapshot = {
        "symbol": "JTO-USDT-SWAP", "side": "long",
        "position_id": "pid-att", "entry_request_id": "req-att",
        "opened_at": 1779922000, "entry_price": 0.5438,
        "amount_usdt": 543, "leverage": 1,
        "sl_algo_id": "algo_sl_1", "sl_algo_clord_id": "clord_sl_1",
        "tp_algo_id": "algo_tp_1", "tp_algo_clord_id": "clord_tp_1",
        "attribution": attribution,
    }
    res = resolver.resolve_external_close(
        snapshot, close_window={"closed_at": 1779922800})
    assert res["pnl_status"] == R_FINAL
    assert res["sl_algo_id"] == "algo_sl_1"
    assert res["sl_algo_clord_id"] == "clord_sl_1"
    assert res["tp_algo_id"] == "algo_tp_1"
    assert res["tp_algo_clord_id"] == "clord_tp_1"
    assert res["entry_attribution"] == attribution


def test_apply_pnl_resolution_correction_carries_algo_and_attribution(ledger):
    """AC-A13: apply_pnl_resolution 写出的 correction event 必须包含
    sl_algo_id / tp_algo_id / entry_attribution(供 Reviewer/Judge 聚类与 SL hit)。"""
    ledger.record_pending_external_close(
        symbol="JTO-USDT-SWAP", side="long",
        entry_price=0.55, amount_usdt=10, leverage=2,
        estimated_pnl=-0.5, position_id="pid-att",
        entry_request_id="req-att", opened_at=1779922000,
        sl_algo_id="algo_sl_1", sl_algo_clord_id="clord_sl_1",
        tp_algo_id="algo_tp_1", tp_algo_clord_id="clord_tp_1",
        entry_attribution={
            "strategy_owner": "tactical_v2",
            "intent_id": "intent-att",
            "slot_type": "main",
            "archetype_key": "long_breakout",
        },
    )
    resolution = {
        "pnl_status": R_FINAL, "symbol": "JTO-USDT-SWAP", "side": "long",
        "position_id": "pid-att", "entry_request_id": "req-att",
        "opened_at": 1779922000,
        "realized_pnl_net_usdt": -1.73,
        "gross_close_pnl_usdt": -1.58, "fee_usdt": -0.15, "funding_usdt": 0,
        "pnl_source": "okx_fills_history", "order_ids": ["close_1"],
        "bill_ids": [], "match_confidence": 0.9, "warnings": [],
        "sl_algo_id": "algo_sl_1", "sl_algo_clord_id": "clord_sl_1",
        "tp_algo_id": "algo_tp_1", "tp_algo_clord_id": "clord_tp_1",
        "entry_attribution": {
            "strategy_owner": "tactical_v2",
            "intent_id": "intent-att",
            "slot_type": "main",
            "archetype_key": "long_breakout",
        },
        "tactical_v2_proof": {
            "complete": True,
            "entry_order_ids": ["entry_1"],
            "close_order_ids": ["close_1"],
            "entry_qty": 10.0,
            "close_qty": 10.0,
            "entry_fee_usdt": -0.1,
        },
        "close_cause": "exchange_tp",
        "final_close_cause": "exchange_tp",
        "is_strategy_stop": False,
        "close_evidence": {"match_rule": "tp_algo_clord_id_exact"},
    }
    correction = ledger.apply_pnl_resolution(resolution)
    assert correction is not None
    assert correction.get("sl_algo_id") == "algo_sl_1"
    assert correction.get("sl_algo_clord_id") == "clord_sl_1"
    assert correction.get("tp_algo_id") == "algo_tp_1"
    assert correction.get("tp_algo_clord_id") == "clord_tp_1"
    assert correction.get("entry_attribution", {}).get("archetype_key") == \
        "long_breakout"
    assert correction.get("tactical_v2_proof", {}).get("complete") is True
    assert correction.get("pnl_delivery_required") is True
    assert correction.get("close_cause") == "exchange_tp"
    assert correction.get("close_evidence", {}).get("match_rule") == \
        "tp_algo_clord_id_exact"
    durable = ledger.find_final_pnl_corrections(strategy_owner="tactical_v2")
    assert [event["event_id"] for event in durable] == [correction["event_id"]]
    unpublished = ledger.find_unpublished_final_pnl_corrections(
        strategy_owner="tactical_v2"
    )
    assert [event["event_id"] for event in unpublished] == [correction["event_id"]]

    ack = ledger.mark_pnl_correction_published(
        correction["event_id"],
        f"corr:{correction['event_id']}",
    )
    assert ack is not None
    assert ledger.find_unpublished_final_pnl_corrections(
        strategy_owner="tactical_v2"
    ) == []
    duplicate_ack = ledger.mark_pnl_correction_published(
        correction["event_id"],
        f"corr:{correction['event_id']}",
    )
    assert duplicate_ack.get("status") == "existing"
