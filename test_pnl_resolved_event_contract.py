"""F4-002 pnl_resolved/pnl_mismatch 总线事件契约测试矩阵。"""

import pytest
from utils.realized_pnl_resolver import make_resolution_id


class TestMakeResolutionId:
    def test_correction_event_id_takes_priority(self):
        resolution = {"position_id": "p1", "order_ids": ["o1"]}
        correction = {"event_id": "E-123", "supersedes_event_id": "E-old"}
        rid = make_resolution_id(resolution, correction)
        assert rid == "corr:E-123"

    def test_supersedes_when_no_event_id(self):
        resolution = {"position_id": "p1"}
        correction = {"supersedes_event_id": "E-old"}
        rid = make_resolution_id(resolution, correction)
        assert rid == "sup:E-old"

    def test_close_match_key_when_no_correction(self):
        resolution = {"position_id": "p1", "close_match_key": "K-7"}
        rid = make_resolution_id(resolution, None)
        assert rid == "key:K-7"

    def test_pos_orders_fallback(self):
        resolution = {"position_id": "p1", "order_ids": ["o2", "o1"]}
        rid = make_resolution_id(resolution, None)
        assert rid == "pos:p1|orders:o1,o2"

    def test_empty_orders_fallback(self):
        resolution = {"position_id": "", "order_ids": []}
        rid = make_resolution_id(resolution, None)
        assert rid == "pos:|orders:"

    def test_same_resolution_same_id(self):
        resolution = {"position_id": "p1", "order_ids": ["o1"]}
        correction = {"event_id": "E-1"}
        a = make_resolution_id(resolution, correction)
        b = make_resolution_id(resolution, correction)
        assert a == b

    def test_empty_correction_dict_falls_through(self):
        """correction={} (falsy) 应当 fall through 到 close_match_key/pos 兜底。"""
        resolution = {"position_id": "p1", "close_match_key": "K-7"}
        rid = make_resolution_id(resolution, {})
        assert rid == "key:K-7"

    def test_pos_fallback_idempotent_under_order_shuffle(self):
        """order_ids 顺序变化不应改变 resolution_id (基于 sort)。"""
        a = make_resolution_id(
            {"position_id": "p1", "order_ids": ["o3", "o1", "o2"]}, None)
        b = make_resolution_id(
            {"position_id": "p1", "order_ids": ["o1", "o2", "o3"]}, None)
        assert a == b == "pos:p1|orders:o1,o2,o3"


class TestReconcilerSummaryFields:
    def test_auto_resolve_pending_summary_carries_final_cause_and_resolution_id(self):
        """auto_resolve_pending 返回的 summary 必须含 close_cause /
        final_close_cause / is_strategy_stop / close_evidence / resolution_id。"""
        from utils.reconciliation import Reconciler
        from unittest.mock import MagicMock

        # mock ledger 返回一条 pending 事件
        ledger = MagicMock()
        ledger.find_pending_external_closes.return_value = [{
            "event_id": "PEND-1",
            "symbol": "BTC-USDT",
            "side": "long",
            "position_id": "pos-1",
            "entry_request_id": "req-1",
            "opened_at": 1000.0,
            "closed_at": 2000.0,
            "estimated_pnl": -10.0,
            "entry_price": 50000,
            "amount_usdt": 100,
            "leverage": 5,
            "sl_algo_id": "algo-1",
            "sl_algo_clord_id": "casllivebot42",
            "tp_algo_id": "",
            "tp_algo_clord_id": "",
            "entry_attribution": {"archetype": "long_v1"},
            "close_match_key": "K-1",
        }]
        ledger.apply_pnl_resolution.return_value = {
            "event_id": "CORR-1",
            "supersedes_event_id": "PEND-1",
        }

        # mock resolver 返回 final 状态
        resolver = MagicMock()
        resolver.resolve_external_close.return_value = {
            "pnl_status": "final",
            "pnl_source": "okx_fills",
            "symbol": "BTC-USDT",
            "side": "long",
            "position_id": "pos-1",
            "entry_request_id": "req-1",
            "realized_pnl_net_usdt": -9.5,
            "gross_close_pnl_usdt": -10.0,
            "fee_usdt": -0.5,
            "funding_usdt": 0.0,
            "order_ids": ["ord-1"],
            "bill_ids": ["bill-1"],
            "close_match_key": "K-1",
            "close_cause": "exchange_sl",
            "final_close_cause": "exchange_sl",
            "is_strategy_stop": True,
            "close_evidence": {
                "match_rule": "sl_algo_id_exact",
                "confidence": 1.0,
                "matched_algo_id": "algo-1",
                "matched_algo_clord_id": "casllivebot42",
                "matched_order_ids": ["ord-1"],
            },
            "warnings": [],
            "match_confidence": 1.0,
            "exchange_pnl_usdt": -9.5,
            "fills_pnl_usdt": -10.0,
            "sl_algo_id": "algo-1",
            "sl_algo_clord_id": "casllivebot42",
            "tp_algo_id": "",
            "tp_algo_clord_id": "",
            "entry_attribution": {"archetype": "long_v1"},
        }

        rec = Reconciler.__new__(Reconciler)
        rec.ledger = ledger
        rec.resolver = resolver
        rec.logger = MagicMock()

        results = rec.auto_resolve_pending()
        assert len(results) == 1
        s = results[0]
        assert s["close_cause"] == "exchange_sl"
        assert s["final_close_cause"] == "exchange_sl"
        assert s["is_strategy_stop"] is True
        assert s["close_evidence"]["match_rule"] == "sl_algo_id_exact"
        assert s["resolution_id"] == "corr:CORR-1"
