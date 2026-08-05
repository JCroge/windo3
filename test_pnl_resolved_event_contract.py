"""F4-002 pnl_resolved/pnl_mismatch 总线事件契约测试矩阵。"""

import asyncio

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
            "tactical_v2_proof": {
                "complete": True,
                "entry_order_ids": ["entry-1"],
                "close_order_ids": ["ord-1"],
                "entry_qty": 1.0,
                "close_qty": 1.0,
                "entry_fee_usdt": -0.1,
            },
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
        assert s["tactical_v2_proof"] == {
            "complete": True,
            "entry_order_ids": ["entry-1"],
            "close_order_ids": ["ord-1"],
            "entry_qty": 1.0,
            "close_qty": 1.0,
            "entry_fee_usdt": -0.1,
        }


class TestResolveExternalCloseAsyncPublish:
    @pytest.mark.asyncio
    async def test_publishes_final_cause_and_resolution_id(self):
        """_resolve_external_close_async 发布 pnl_resolved 时必须含
        final_close_cause / close_evidence / resolution_id。"""
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock, AsyncMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()

        # _pnl_resolver is the actual attribute name
        resolver_mock = MagicMock()
        resolver_mock.resolve_external_close.return_value = {
            "pnl_status": "final",
            "symbol": "BTC-USDT",
            "side": "long",
            "position_id": "pos-1",
            "entry_request_id": "req-1",
            "realized_pnl_net_usdt": -9.5,
            "close_cause": "exchange_sl",
            "final_close_cause": "exchange_sl",
            "is_strategy_stop": True,
            "close_evidence": {"match_rule": "sl_algo_id_exact", "confidence": 1.0},
            "order_ids": ["ord-1"],
            "close_match_key": "K-1",
            "warnings": [],
            "match_confidence": 1.0,
            "estimated_pnl": -10.0,
            "exchange_pnl_usdt": -9.5,
            "fills_pnl_usdt": -10.0,
            "sl_algo_id": "algo-1",
            "sl_algo_clord_id": "casllivebot42",
            "tp_algo_id": "",
            "tp_algo_clord_id": "",
            "entry_attribution": {},
            "pos_side": "long",
            "opened_at": 0,
            "closed_at": 0,
            "gross_close_pnl_usdt": -10,
            "fee_usdt": -0.5,
            "funding_usdt": 0,
            "bill_ids": [],
            "pnl_source": "okx_fills",
        }
        ex._pnl_resolver = resolver_mock

        # ledger is accessed via self.executor.ledger
        ledger_mock = MagicMock()
        ledger_mock.apply_pnl_resolution.return_value = {
            "event_id": "CORR-9", "supersedes_event_id": "PEND-9"
        }
        executor_mock = MagicMock()
        executor_mock.ledger = ledger_mock
        ex.executor = executor_mock

        snapshot = {"symbol": "BTC-USDT", "side": "long", "request_id": "req-1"}
        await ex._resolve_external_close_async(snapshot, {"closed_at": 1000}, "req-1")

        topics = [t for t, _ in published]
        assert "pnl_resolved" in topics
        payload = next(p for t, p in published if t == "pnl_resolved")
        assert payload["final_close_cause"] == "exchange_sl"
        assert payload["close_evidence"]["match_rule"] == "sl_algo_id_exact"
        assert payload["resolution_id"] == "corr:CORR-9"

    @pytest.mark.asyncio
    async def test_skips_publish_when_no_correction_and_pending(self):
        """correction=None 且 status=pending 时跳过发布并打 warning。"""
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()

        resolver_mock = MagicMock()
        resolver_mock.resolve_external_close.return_value = {
            "pnl_status": "pending",
            "symbol": "BTC-USDT",
            "side": "long",
            "position_id": "pos-1",
            "entry_request_id": "req-1",
            "realized_pnl_net_usdt": None,
            "close_cause": "external_unknown",
            "final_close_cause": "external_unknown",
            "is_strategy_stop": False,
            "close_evidence": {},
            "order_ids": [],
            "warnings": ["pending"],
            "match_confidence": 0,
            "estimated_pnl": -5.0,
            "pnl_source": "",
            "pos_side": "long",
            "opened_at": 0, "closed_at": 0,
            "gross_close_pnl_usdt": 0, "fee_usdt": 0, "funding_usdt": 0,
            "bill_ids": [],
            "exchange_pnl_usdt": None, "fills_pnl_usdt": None,
            "sl_algo_id": "", "sl_algo_clord_id": "",
            "tp_algo_id": "", "tp_algo_clord_id": "",
            "entry_attribution": {},
        }
        ex._pnl_resolver = resolver_mock

        # No ledger → correction stays None
        executor_mock = MagicMock()
        executor_mock.ledger = None
        ex.executor = executor_mock

        snapshot = {"symbol": "BTC-USDT", "side": "long", "request_id": "req-1"}
        await ex._resolve_external_close_async(snapshot, {"closed_at": 1000}, "req-1")

        topics = [t for t, _ in published]
        assert "pnl_resolved" not in topics
        assert "pnl_mismatch" not in topics
        ex.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_skips_when_ledger_returns_none_correction_for_pending(self):
        """ledger 存在但 apply_pnl_resolution 返回 None (pending 路径) 时跳过发布。"""
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        # 复用与 test_skips_publish_when_no_correction_and_pending 相同的 resolver
        # 但这次 ledger 存在,只是不写 correction(典型 pending/pending_fx 路径)
        ex._pnl_resolver = MagicMock()
        ex._pnl_resolver.resolve_external_close.return_value = {
            "pnl_status": "pending",
            "symbol": "BTC-USDT", "side": "long",
            "position_id": "pos-1", "entry_request_id": "req-1",
            "realized_pnl_net_usdt": None,
            "close_cause": "external_unknown",
            "final_close_cause": "external_unknown",
            "is_strategy_stop": False,
            "close_evidence": {},
            "order_ids": [], "warnings": ["pending"],
            "match_confidence": 0,
            "estimated_pnl": -5.0, "pnl_source": "",
            "pos_side": "long", "opened_at": 0, "closed_at": 0,
            "gross_close_pnl_usdt": 0, "fee_usdt": 0, "funding_usdt": 0,
            "bill_ids": [],
            "exchange_pnl_usdt": None, "fills_pnl_usdt": None,
            "sl_algo_id": "", "sl_algo_clord_id": "",
            "tp_algo_id": "", "tp_algo_clord_id": "",
            "entry_attribution": {},
        }
        ex.executor = MagicMock()
        ex.executor.ledger = MagicMock()
        ex.executor.ledger.apply_pnl_resolution.return_value = None  # ← 关键
        ex.executor.ledger.update_pending_resolution_attempt.return_value = None

        snapshot = {"side": "long", "request_id": "req-1"}
        await ex._resolve_external_close_async(snapshot, {"closed_at": 1000}, "req-1")

        topics = [t for t, _ in published]
        assert "pnl_resolved" not in topics
        assert "pnl_mismatch" not in topics
        ex.logger.warning.assert_called()


class TestRunReconciliationPublish:
    @pytest.mark.asyncio
    async def test_pending_summary_is_not_routed_as_pnl_mismatch(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import AsyncMock, MagicMock

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = AsyncMock()
        ex.executor = MagicMock()
        ex._route_pnl_event = AsyncMock()
        ex._reconciler = MagicMock()
        ex._reconciler.auto_resolve_pending.return_value = [{
            "symbol": "ADA-USDT",
            "position_id": "tv2:intent-1",
            "entry_request_id": "entry-1",
            "pnl_status": "pending",
            "entry_attribution": {
                "strategy_owner": "tactical_v2",
                "intent_id": "intent-1",
            },
        }]
        ex._reconciler.run_and_report.return_value = None

        await ex._run_reconciliation()

        ex._route_pnl_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_reconciliation_publishes_final_cause(self):
        """_run_reconciliation 收到 summary 后发布 pnl_resolved 必须透传字段。"""
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock, AsyncMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()

        # mock reconciler 返回一条 final summary
        rec = MagicMock()
        rec.auto_resolve_pending.return_value = [{
            "symbol": "BTC-USDT",
            "position_id": "pos-1",
            "entry_request_id": "req-1",
            "pnl_status": "final",
            "pnl_source": "okx_fills",
            "realized_pnl_net_usdt": -9.5,
            "estimated_pnl": -10,
            "exchange_pnl_usdt": -9.5,
            "fills_pnl_usdt": -10,
            "gross_close_pnl_usdt": -10,
            "fee_usdt": -0.5,
            "funding_usdt": 0,
            "order_ids": ["ord-1"],
            "bill_ids": ["bill-1"],
            "match_confidence": 1.0,
            "warnings": [],
            "sl_algo_id": "algo-1",
            "sl_algo_clord_id": "casllivebot42",
            "tp_algo_id": "",
            "tp_algo_clord_id": "",
            "entry_attribution": {},
            "supersedes_event_id": "PEND-9",
            "correction_event_id": "CORR-9",
            "pending_event_id": "PEND-9",
            # F4-002 新字段（来自 Task 5）
            "close_cause": "exchange_sl",
            "final_close_cause": "exchange_sl",
            "is_strategy_stop": True,
            "close_evidence": {"match_rule": "sl_algo_id_exact", "confidence": 1.0},
            "tactical_v2_proof": {
                "complete": True,
                "entry_order_ids": ["entry-1"],
                "close_order_ids": ["ord-1"],
                "entry_qty": 1.0,
                "close_qty": 1.0,
                "entry_fee_usdt": -0.1,
            },
            "resolution_id": "corr:CORR-9",
        }]
        rec.run_and_report = MagicMock(return_value=None)
        ex._reconciler = rec

        await ex._run_reconciliation()

        topics = [t for t, _ in published]
        assert "pnl_resolved" in topics
        payload = next(p for t, p in published if t == "pnl_resolved")
        assert payload["final_close_cause"] == "exchange_sl"
        assert payload["is_strategy_stop"] is True
        assert payload["close_evidence"]["match_rule"] == "sl_algo_id_exact"
        assert payload["resolution_id"] == "corr:CORR-9"
        assert payload["tactical_v2_proof"]["complete"] is True


class TestDurableTacticalFinalReplay:
    @pytest.mark.asyncio
    async def test_restart_replays_ledger_final_missing_from_governor(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import AsyncMock, MagicMock

        correction = {
            "event_id": "CORR-ADA-1",
            "ts": 1046.0,
            "event_type": "external_close_correction",
            "pnl_status": "final",
            "symbol": "ADA-USDT-SWAP",
            "side": "long",
            "position_id": "tv2:intent-1",
            "entry_request_id": "entry-1",
            "realized_pnl_net_usdt": 7.5,
            "gross_close_pnl_usdt": 8.0,
            "fee": -0.5,
            "funding_usdt": 0.0,
            "pnl_source": "okx_fills_history+okx_bills",
            "order_ids": ["close-1"],
            "bill_ids": ["bill-1"],
            "entry_attribution": {
                "strategy_owner": "tactical_v2",
                "intent_id": "intent-1",
                "episode_id": "episode-1",
                "plan_hash": "plan-1",
            },
            "tactical_v2_proof": {
                "complete": True,
                "entry_request_id": "entry-1",
                "entry_order_ids": ["entry-order-1"],
                "close_order_ids": ["close-1"],
                "entry_qty": 26.1,
                "close_qty": 26.1,
                "entry_fee_usdt": -0.25,
            },
            "close_cause": "exchange_tp",
            "final_close_cause": "exchange_tp",
            "close_evidence": {"match_rule": "anonymous_isolated_close"},
            "pnl_delivery_required": True,
        }
        controller = MagicMock()
        controller.should_replay_durable_pnl_final.return_value = True
        controller.governor.resolution_by_id.return_value = None
        controller.handle_pnl_resolution = AsyncMock()
        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = AsyncMock()
        ex.executor = MagicMock()
        ex.executor.ledger.find_unpublished_final_pnl_corrections.return_value = [
            correction
        ]
        ex._tactical_v2_controller = controller

        await ex._replay_unconsumed_tactical_v2_finals()

        payload = controller.handle_pnl_resolution.await_args.args[0]
        assert payload["resolution_id"] == "corr:CORR-ADA-1"
        assert payload["tactical_v2_proof"]["complete"] is True
        assert payload["strategy_owner"] == "tactical_v2"
        ex.publish.assert_awaited_once_with(
            "pnl_resolved", payload, symbol="ADA-USDT-SWAP"
        )

    @pytest.mark.asyncio
    async def test_restart_republishes_unacked_final_already_in_governor(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import AsyncMock, MagicMock

        correction = {
            "event_id": "CORR-ADA-1",
            "event_type": "external_close_correction",
            "pnl_status": "final",
            "entry_attribution": {"strategy_owner": "tactical_v2"},
            "pnl_delivery_required": True,
        }
        controller = MagicMock()
        controller.should_replay_durable_pnl_final.return_value = True
        controller.governor.resolution_by_id.return_value = {
            "resolution_id": "corr:CORR-ADA-1"
        }
        controller.handle_pnl_resolution = AsyncMock()
        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = AsyncMock()
        ex.executor = MagicMock()
        ex.executor.ledger.find_unpublished_final_pnl_corrections.return_value = [
            correction
        ]
        ex._tactical_v2_controller = controller

        await ex._replay_unconsumed_tactical_v2_finals()

        controller.handle_pnl_resolution.assert_awaited_once()
        ex.publish.assert_awaited_once()
        ex.executor.ledger.mark_pnl_correction_published.assert_called_once_with(
            "CORR-ADA-1",
            "corr:CORR-ADA-1",
        )

    @pytest.mark.asyncio
    async def test_restart_replays_legacy_final_when_intent_still_requires_recovery(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import AsyncMock, MagicMock

        correction = {
            "event_id": "CORR-LEGACY-RECOVERY",
            "event_type": "external_close_correction",
            "pnl_status": "final",
            "symbol": "ADA-USDT-SWAP",
            "position_id": "tv2:intent-legacy",
            "entry_request_id": "entry-legacy",
            "realized_pnl_net_usdt": -1.0,
            "entry_attribution": {
                "strategy_owner": "tactical_v2",
                "intent_id": "intent-legacy",
            },
        }
        controller = MagicMock()
        controller.should_replay_durable_pnl_final.return_value = True
        controller.governor.resolution_by_id.return_value = {
            "resolution_id": "corr:CORR-LEGACY-RECOVERY"
        }
        controller.handle_pnl_resolution = AsyncMock()
        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = AsyncMock()
        ex.executor = MagicMock()
        ex.executor.ledger.find_unpublished_final_pnl_corrections.return_value = [
            correction
        ]
        ex._tactical_v2_controller = controller

        await ex._replay_unconsumed_tactical_v2_finals()

        controller.handle_pnl_resolution.assert_awaited_once()
        ex.publish.assert_awaited_once()
        ex.executor.ledger.mark_pnl_correction_published.assert_called_once_with(
            "CORR-LEGACY-RECOVERY",
            "corr:CORR-LEGACY-RECOVERY",
        )

    @pytest.mark.asyncio
    async def test_restart_acks_legacy_applied_final_without_republishing(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import AsyncMock, MagicMock

        correction = {
            "event_id": "CORR-LEGACY-CLOSED",
            "event_type": "external_close_correction",
            "pnl_status": "final",
            "entry_attribution": {"strategy_owner": "tactical_v2"},
        }
        controller = MagicMock()
        controller.should_replay_durable_pnl_final.return_value = False
        controller.governor.resolution_by_id.return_value = {
            "resolution_id": "corr:CORR-LEGACY-CLOSED"
        }
        controller.handle_pnl_resolution = AsyncMock()
        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = AsyncMock()
        ex.executor = MagicMock()
        ex.executor.ledger.find_unpublished_final_pnl_corrections.return_value = [
            correction
        ]
        ex._tactical_v2_controller = controller

        await ex._replay_unconsumed_tactical_v2_finals()

        controller.handle_pnl_resolution.assert_not_awaited()
        ex.publish.assert_not_awaited()
        ex.executor.ledger.mark_pnl_correction_published.assert_called_once_with(
            "CORR-LEGACY-CLOSED",
            "corr:CORR-LEGACY-CLOSED",
        )

    @pytest.mark.asyncio
    async def test_restart_skips_historical_final_without_pending_intent(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import AsyncMock, MagicMock

        correction = {
            "event_id": "CORR-OLD-1",
            "event_type": "external_close_correction",
            "pnl_status": "final",
            "entry_attribution": {
                "strategy_owner": "tactical_v2",
                "intent_id": "old-intent",
            },
        }
        controller = MagicMock()
        controller.should_replay_durable_pnl_final.return_value = False
        controller.governor.resolution_by_id.return_value = None
        controller.handle_pnl_resolution = AsyncMock()
        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = AsyncMock()
        ex.executor = MagicMock()
        ex.executor.ledger.find_unpublished_final_pnl_corrections.return_value = [
            correction
        ]
        ex._tactical_v2_controller = controller

        await ex._replay_unconsumed_tactical_v2_finals()

        controller.governor.resolution_by_id.assert_called_once_with(
            "corr:CORR-OLD-1"
        )
        controller.handle_pnl_resolution.assert_not_awaited()
        ex.publish.assert_not_awaited()
        ex.executor.ledger.mark_pnl_correction_published.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_failure_keeps_final_unacked_for_restart_retry(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import AsyncMock, MagicMock

        correction = {
            "event_id": "CORR-RETRY-1",
            "event_type": "external_close_correction",
            "pnl_status": "final",
            "symbol": "ADA-USDT-SWAP",
            "position_id": "tv2:intent-1",
            "entry_request_id": "entry-1",
            "realized_pnl_net_usdt": 1.0,
            "entry_attribution": {
                "strategy_owner": "tactical_v2",
                "intent_id": "intent-1",
            },
            "pnl_delivery_required": True,
        }
        controller = MagicMock()
        controller.should_replay_durable_pnl_final.return_value = True
        controller.governor.resolution_by_id.return_value = {
            "resolution_id": "corr:CORR-RETRY-1"
        }
        controller.handle_pnl_resolution = AsyncMock()
        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = AsyncMock(side_effect=RuntimeError("publish failed"))
        ex.executor = MagicMock()
        ex.executor.ledger.find_unpublished_final_pnl_corrections.return_value = [
            correction
        ]
        ex._tactical_v2_controller = controller

        await ex._replay_unconsumed_tactical_v2_finals()
        ex.executor.ledger.mark_pnl_correction_published.assert_not_called()
        ex.logger.warning.assert_called()

        ex.publish = AsyncMock()
        await ex._replay_unconsumed_tactical_v2_finals()

        ex.publish.assert_awaited_once()
        ex.executor.ledger.mark_pnl_correction_published.assert_called_once_with(
            "CORR-RETRY-1",
            "corr:CORR-RETRY-1",
        )


class TestTacticalStartupRecoveryOrder:
    @pytest.mark.asyncio
    async def test_replays_durable_finals_before_exchange_recovery(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import AsyncMock, MagicMock

        order = []
        ex = MultiExecutor.__new__(MultiExecutor)
        ex._replay_unconsumed_tactical_v2_finals = AsyncMock(
            side_effect=lambda: order.append("replay")
        )
        ex._tactical_v2_controller = MagicMock()
        ex._tactical_v2_controller.recover = AsyncMock(
            side_effect=lambda: order.append("recover")
        )

        await ex._recover_tactical_v2_startup()

        assert order == ["replay", "recover"]


class TestTacticalPnlRouteConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_routes_publish_and_ack_correction_once(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import AsyncMock, MagicMock

        published_corrections = set()
        ledger = MagicMock()
        ledger.is_pnl_correction_published.side_effect = (
            lambda correction_id: correction_id in published_corrections
        )

        def mark_published(correction_id, resolution_id):
            published_corrections.add(correction_id)
            return {
                "correction_event_id": correction_id,
                "resolution_id": resolution_id,
            }

        ledger.mark_pnl_correction_published.side_effect = mark_published
        controller = MagicMock()
        controller.handle_pnl_resolution = AsyncMock()
        ex = MultiExecutor.__new__(MultiExecutor)
        ex.executor = MagicMock()
        ex.executor.ledger = ledger
        ex._tactical_v2_controller = controller
        ex.publish = AsyncMock()
        payload = {
            "correction_event_id": "CORR-CONCURRENT-1",
            "resolution_id": "corr:CORR-CONCURRENT-1",
            "pnl_status": "final",
            "realized_pnl_net_usdt": 1.25,
        }

        results = await asyncio.gather(
            ex._route_pnl_event(
                "pnl_resolved",
                dict(payload),
                symbol="ADA-USDT-SWAP",
            ),
            ex._route_pnl_event(
                "pnl_resolved",
                dict(payload),
                symbol="ADA-USDT-SWAP",
            ),
        )

        assert results.count(True) == 1
        assert results.count(False) == 1
        controller.handle_pnl_resolution.assert_awaited_once()
        ex.publish.assert_awaited_once()
        ledger.mark_pnl_correction_published.assert_called_once_with(
            "CORR-CONCURRENT-1",
            "corr:CORR-CONCURRENT-1",
        )



class TestSubscriberDeduplication:
    """F4-002: Judge/Reviewer 按 resolution_id 优先去重 pnl_resolved。"""

    def _make_judge(self):
        from agents.trading.judge import MultiJudge
        from unittest.mock import MagicMock
        j = MultiJudge.__new__(MultiJudge)
        j._symbol_state = {}
        j.logger = MagicMock()
        j._processed_resolution_ids = set()
        j._processed_resolution_max = 1024
        j._archetype_cooldown = MagicMock()
        j._record_sl_hit = MagicMock()
        j._probe_short_active = None
        j._probe_short_sl_count = 0
        j._probe_short_cooldown_until = 0
        j._probe_short_cooldown_hours = 24
        return j

    @pytest.mark.asyncio
    async def test_judge_dedup_by_resolution_id_only(self):
        """resolution_id 存在、correction_event_id 为空时 Judge 仍能去重(验证优先级)。"""
        j = self._make_judge()
        msg = {
            "type": "pnl_resolved",
            "symbol": "BTC-USDT",
            "timestamp": 1000.0,
            "payload": {
                "symbol": "BTC-USDT",
                "pnl_status": "final",
                "is_strategy_stop": True,
                "close_cause": "exchange_sl",
                "final_close_cause": "exchange_sl",
                "resolution_id": "key:K-7",
                "correction_event_id": "",
                "supersedes_event_id": "",
                "realized_pnl_net_usdt": -9.5,
                "attribution": {},
                "direction": "long",
                "position_id": "pos-7",
            },
        }
        await j.on_message(msg)
        await j.on_message(msg)
        # Under old code correction_event_id="" -> no key -> no dedup -> called twice.
        # Under fix resolution_id="key:K-7" is used -> dedup -> called once.
        assert j._record_sl_hit.call_count == 1

    @pytest.mark.asyncio
    async def test_judge_skips_duplicate_resolution_id(self):
        """Judge 收到同一 resolution_id 第二次时不重复 record SL hit。"""
        j = self._make_judge()
        msg = {
            "type": "pnl_resolved",
            "symbol": "BTC-USDT",
            "timestamp": 1000.0,
            "payload": {
                "symbol": "BTC-USDT",
                "pnl_status": "final",
                "is_strategy_stop": True,
                "close_cause": "exchange_sl",
                "final_close_cause": "exchange_sl",
                "resolution_id": "corr:E-1",
                "correction_event_id": "E-1",
                "realized_pnl_net_usdt": -9.5,
                "attribution": {},
                "direction": "long",
                "position_id": "pos-1",
            },
        }
        await j.on_message(msg)
        await j.on_message(msg)
        assert j._record_sl_hit.call_count == 1

    @pytest.mark.asyncio
    async def test_judge_falls_back_to_correction_event_id_when_no_resolution_id(self):
        """payload 缺 resolution_id 时按 correction_event_id 去重(fail-safe)。"""
        j = self._make_judge()
        msg = {
            "type": "pnl_resolved",
            "symbol": "BTC-USDT",
            "timestamp": 1000.0,
            "payload": {
                "symbol": "BTC-USDT",
                "pnl_status": "final",
                "is_strategy_stop": True,
                "close_cause": "exchange_sl",
                "correction_event_id": "E-LEGACY",
                "realized_pnl_net_usdt": -9.5,
                "attribution": {},
                "direction": "long",
                "position_id": "pos-2",
            },
        }
        await j.on_message(msg)
        await j.on_message(msg)
        assert j._record_sl_hit.call_count == 1

    @pytest.mark.asyncio
    async def test_reviewer_dedup_by_resolution_id_only(self):
        """resolution_id 存在、correction_event_id 为空時 Reviewer 仍能去重(验证优先级)。"""
        from agents.trading.reviewer import ReviewerAgent
        from unittest.mock import MagicMock
        r = ReviewerAgent.__new__(ReviewerAgent)
        r.logger = MagicMock()
        r._processed_resolution_ids = set()
        r._processed_resolution_max = 1024
        r.trade_history = []
        r._save_trade_history = MagicMock()
        r._hard_stop_triggered_date = ''
        r.daily_pnl_hard_stop = -50.0
        r.consecutive_loss_limit = 3

        msg = {
            "type": "pnl_resolved",
            "symbol": "BTC-USDT",
            "timestamp": 1000.0,
            "payload": {
                "symbol": "BTC-USDT",
                "pnl_status": "final",
                "resolution_id": "key:K-7",
                "correction_event_id": "",
                "supersedes_event_id": "",
                "realized_pnl_net_usdt": -9.5,
                "position_id": "pos-7",
                "attribution": {},
            },
        }
        await r._apply_pnl_resolution(msg)
        await r._apply_pnl_resolution(msg)
        # Under old code empty correction_event_id -> no key -> no dedup -> two appends.
        # Under fix resolution_id="key:K-7" is used -> dedup -> one append.
        assert len(r.trade_history) == 1

    @pytest.mark.asyncio
    async def test_reviewer_skips_duplicate_resolution_id(self):
        """Reviewer 收到同一 resolution_id 第二次时不重复 append trade_history。"""
        from agents.trading.reviewer import ReviewerAgent
        from unittest.mock import MagicMock
        r = ReviewerAgent.__new__(ReviewerAgent)
        r.logger = MagicMock()
        r._processed_resolution_ids = set()
        r._processed_resolution_max = 1024
        r.trade_history = []
        r._save_trade_history = MagicMock()
        r._hard_stop_triggered_date = ''
        r.daily_pnl_hard_stop = -50.0
        r.consecutive_loss_limit = 3

        msg = {
            "type": "pnl_resolved",
            "symbol": "BTC-USDT",
            "timestamp": 1000.0,
            "payload": {
                "symbol": "BTC-USDT",
                "pnl_status": "final",
                "resolution_id": "corr:E-1",
                "correction_event_id": "E-1",
                "realized_pnl_net_usdt": -9.5,
                "position_id": "pos-1",
                "attribution": {},
            },
        }
        await r._apply_pnl_resolution(msg)
        await r._apply_pnl_resolution(msg)
        assert len(r.trade_history) == 1

    @pytest.mark.asyncio
    async def test_reviewer_falls_back_to_correction_event_id_when_no_resolution_id(self):
        """payload 缺 resolution_id 时 Reviewer 按 correction_event_id 去重(fail-safe)。"""
        from agents.trading.reviewer import ReviewerAgent
        from unittest.mock import MagicMock
        r = ReviewerAgent.__new__(ReviewerAgent)
        r.logger = MagicMock()
        r._processed_resolution_ids = set()
        r._processed_resolution_max = 1024
        r.trade_history = []
        r._save_trade_history = MagicMock()
        r._hard_stop_triggered_date = ''
        r.daily_pnl_hard_stop = -50.0
        r.consecutive_loss_limit = 3

        msg = {
            "type": "pnl_resolved",
            "symbol": "BTC-USDT",
            "timestamp": 1000.0,
            "payload": {
                "symbol": "BTC-USDT",
                "pnl_status": "final",
                "correction_event_id": "E-LEGACY",
                "realized_pnl_net_usdt": -9.5,
                "position_id": "pos-3",
                "attribution": {},
            },
        }
        await r._apply_pnl_resolution(msg)
        await r._apply_pnl_resolution(msg)
        assert len(r.trade_history) == 1
