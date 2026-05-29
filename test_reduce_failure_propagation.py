"""F4-001 reduce 失败回参分流测试矩阵。"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def make_classification(result, requested_pct=0.5):
    from agents.trading.executor import MultiExecutor
    return MultiExecutor._classify_reduce_outcome(result, requested_pct)


class TestClassifyReduceOutcome:
    def test_result_none_returns_rejected(self):
        c = make_classification(None)
        assert c["status"] == "rejected"
        assert c["reason"] == "executor_returned_none"
        assert c["actual_reduce_pct"] == 0.0
        assert c["protection_failed"] is False
        assert c["action_override"] is None

    def test_sl_cancel_failed_returns_rejected(self):
        c = make_classification({
            "reduce_ok": False, "reason": "sl_cancel_failed",
            "protective_update_state": "cancel_failed",
            "protection_state": "unknown",
        })
        assert c["status"] == "rejected"
        assert c["reason"] == "sl_cancel_failed"
        assert c["actual_reduce_pct"] == 0.0
        assert c["protection_failed"] is False

    def test_sl_restore_failed_returns_rejected(self):
        c = make_classification({
            "reduce_ok": False, "reason": "sl_restore_failed",
            "protective_update_state": "restore_failed",
            "protection_state": "unknown",
        })
        assert c["status"] == "rejected"
        assert c["reason"] == "sl_restore_failed"

    def test_reduce_rejected_returns_reduce_failed(self):
        c = make_classification({
            "reduce_ok": False, "reason": "reduce_rejected",
            "protective_update_state": "restored_old_sl",
            "protection_state": "protected",
        })
        assert c["status"] == "reduce_failed"
        assert c["reason"] == "reduce_rejected"
        assert c["actual_reduce_pct"] == 0.0

    def test_dust_closed_returns_executed_close(self):
        c = make_classification({
            "reduce_ok": True, "ok": True,
            "protective_update_state": "dust_closed",
            "protection_state": "closed",
            "actual_reduce_amount": 100.0,
            "requested_reduce_amount": 100.0,
        })
        assert c["status"] == "executed"
        assert c["action_override"] == "close"
        assert c["protection_state"] == "closed"
        assert c["protection_failed"] is False

    def test_replace_failed_returns_risk_reduced_with_protection_failed(self):
        c = make_classification({
            "reduce_ok": True, "ok": False,
            "protective_update_state": "replace_failed",
            "protection_state": "unknown",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
        })
        assert c["status"] == "risk_reduced"
        assert c["protection_failed"] is True
        assert c["protection_state"] == "unknown"
        # actual_reduce_pct = (50/100) * requested(0.5) = 0.25
        assert c["actual_reduce_pct"] == pytest.approx(0.25)

    def test_clean_ok_returns_risk_reduced_no_protection_failed(self):
        c = make_classification({
            "reduce_ok": True, "ok": True,
            "protective_update_state": "protected",
            "protection_state": "protected",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
        })
        assert c["status"] == "risk_reduced"
        assert c["protection_failed"] is False
        assert c["protection_state"] == "protected"


def _make_executor_for_partial_close(reduce_position_return):
    """Build a minimal MultiExecutor stub that reaches the partial-close branch."""
    from agents.trading.executor import MultiExecutor

    published = []

    async def fake_publish(topic, payload, symbol=None):
        published.append((topic, payload))

    ex = MultiExecutor.__new__(MultiExecutor)
    ex.publish = fake_publish
    ex.logger = MagicMock()

    # halt guards
    ex._trading_halted = False
    halt_state = MagicMock()
    halt_state.can_open_new = True
    ex._halt_state = halt_state

    # confidence gate
    ex.min_confidence = 60

    # executor sub-object
    inner = MagicMock()
    inner._normalize_symbol = lambda s: s
    inner.get_position = MagicMock(return_value={"side": "long", "request_id": "req-entry"})
    inner.reduce_position = MagicMock(return_value=reduce_position_return)
    ex.executor = inner

    # misc attrs used by _build_execution_result / _execute_decision
    ex.config = {"max_trade_amount": 10}
    ex._open_fail_cooldown = {}

    return ex, published


class TestPositionAnalystPartialClose:
    @pytest.mark.asyncio
    async def test_replace_failed_emits_risk_reduced_with_protection_failed(self):
        ex, published = _make_executor_for_partial_close({
            "reduce_ok": True, "ok": False,
            "protective_update_state": "replace_failed",
            "protection_state": "unknown",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
            "warnings": ["residual_protection_failed"],
        })

        decision = {
            "action": "close", "size_pct": 0.5,
            "request_id": "req-2", "source": "position_analyst",
            "confidence": 70, "plan": None, "symbol": "BTC-USDT",
        }
        await ex._execute_decision(decision)

        risk_reduced = [p for t, p in published
                        if t == "execution_result" and p.get("status") == "risk_reduced"]
        assert len(risk_reduced) == 1
        rr = risk_reduced[0]
        assert rr["protection_failed"] is True
        assert rr["protection_state"] == "unknown"
        assert rr["reduce_pct"] == pytest.approx(0.25)  # (50/100)*0.5

    @pytest.mark.asyncio
    async def test_sl_cancel_failed_emits_rejected_no_risk_reduced(self):
        ex, published = _make_executor_for_partial_close({
            "reduce_ok": False, "reason": "sl_cancel_failed",
            "protective_update_state": "cancel_failed",
            "protection_state": "unknown",
        })

        decision = {
            "action": "close", "size_pct": 0.5,
            "request_id": "req-3", "source": "position_analyst",
            "confidence": 70, "plan": None, "symbol": "BTC-USDT",
        }
        await ex._execute_decision(decision)

        statuses = [p.get("status") for t, p in published if t == "execution_result"]
        assert "rejected" in statuses
        assert "risk_reduced" not in statuses
        # rejected payload 不应带 reduce_pct
        rejected = next(p for t, p in published if p.get("status") == "rejected")
        assert "reduce_pct" not in rejected or rejected.get("reduce_pct") in (None, 0)

    @pytest.mark.asyncio
    async def test_dust_closed_emits_executed_close(self):
        ex, published = _make_executor_for_partial_close({
            "reduce_ok": True, "ok": True,
            "protective_update_state": "dust_closed",
            "protection_state": "closed",
            "actual_reduce_amount": 80.0,
            "requested_reduce_amount": 100.0,
            "pnl": -3.0,
        })

        decision = {
            "action": "close", "size_pct": 0.5,
            "request_id": "req-4", "source": "position_analyst",
            "confidence": 70, "plan": None, "symbol": "BTC-USDT",
        }
        await ex._execute_decision(decision)

        statuses_actions = [(p.get("status"), p.get("action"))
                            for t, p in published if t == "execution_result"]
        # dust_closed → executed + action=close
        assert ("executed", "close") in statuses_actions
        # 不应该出现 risk_reduced
        assert not any(s == "risk_reduced" for s, _ in statuses_actions)


class TestPortfolioExposureReduce:
    @pytest.mark.asyncio
    async def test_replace_failed_emits_risk_reduced_with_protection_failed(self):
        from agents.trading.executor import MultiExecutor

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor.get_all_positions.return_value = {
            "BTC-USDT": {"amount_usdt": 100, "request_id": "r"},
        }
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": True, "ok": False,
            "protective_update_state": "replace_failed",
            "protection_state": "unknown",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
        })

        await ex._handle_risk_alert({
            "type": "portfolio_exposure", "scope": "market",
        })

        risk_reduced = [p for t, p in published if p.get("status") == "risk_reduced"]
        assert len(risk_reduced) == 1
        assert risk_reduced[0]["protection_failed"] is True
        # actual_reduce_pct = (50/100) * 0.5 = 0.25
        assert risk_reduced[0]["reduce_pct"] == pytest.approx(0.25)

    @pytest.mark.asyncio
    async def test_reduce_rejected_no_risk_reduced(self):
        from agents.trading.executor import MultiExecutor

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor.get_all_positions.return_value = {
            "BTC-USDT": {"amount_usdt": 100, "request_id": "r"},
        }
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": False, "reason": "reduce_rejected",
            "protective_update_state": "restored_old_sl",
        })

        await ex._handle_risk_alert({
            "type": "correlation_risk", "scope": "market",
        })

        statuses = [p.get("status") for t, p in published]
        assert "reduce_failed" in statuses
        assert "risk_reduced" not in statuses

    @pytest.mark.asyncio
    async def test_clean_ok_emits_risk_reduced(self):
        """干净减仓: ok=True → risk_reduced + actual reduce_pct, protection_failed 缺失。"""
        from agents.trading.executor import MultiExecutor

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor.get_all_positions.return_value = {
            "BTC-USDT": {"amount_usdt": 100, "request_id": "r"},
        }
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": True, "ok": True,
            "protective_update_state": "protected",
            "protection_state": "protected",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
        })

        await ex._handle_risk_alert({
            "type": "portfolio_exposure", "scope": "market",
        })

        risk_reduced = [p for t, p in published if p.get("status") == "risk_reduced"]
        assert len(risk_reduced) == 1
        assert risk_reduced[0].get("protection_failed") in (None, False)
        assert risk_reduced[0]["reduce_pct"] == pytest.approx(0.25)


class TestPartialTpReduce:
    def _make_ex(self):
        from agents.trading.executor import MultiExecutor
        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor.positions = {
            "BTC-USDT": {"side": "long", "request_id": "r"},
        }
        return ex

    @pytest.mark.asyncio
    async def test_partial_tp_replace_failed_protection_failed(self):
        """partial_tp_1 + replace_failed → risk_reduced, protection_failed=True, actual pct."""
        ex = self._make_ex()

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex.publish = fake_publish
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": True, "ok": False,
            "protective_update_state": "replace_failed",
            "protection_state": "unknown",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
        })

        await ex._handle_partial_tp_trigger("BTC-USDT", "partial_tp_1")

        risk_reduced = [p for t, p in published if p.get("status") == "risk_reduced"]
        assert len(risk_reduced) == 1
        assert risk_reduced[0]["protection_failed"] is True
        # actual = (50/100) * 0.5 = 0.25
        assert risk_reduced[0]["reduce_pct"] == pytest.approx(0.25)

    @pytest.mark.asyncio
    async def test_partial_tp_clean_ok_emits_risk_reduced(self):
        """partial_tp_2 + clean ok → risk_reduced, no protection_failed, actual pct."""
        ex = self._make_ex()

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex.publish = fake_publish
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": True, "ok": True,
            "protective_update_state": "protected",
            "protection_state": "protected",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
        })

        await ex._handle_partial_tp_trigger("BTC-USDT", "partial_tp_2")  # 25%

        risk_reduced = [p for t, p in published if p.get("status") == "risk_reduced"]
        assert len(risk_reduced) == 1
        # partial_tp_2 → pct=0.25; actual = (50/100)*0.25 = 0.125
        assert risk_reduced[0]["reduce_pct"] == pytest.approx(0.125)
        assert risk_reduced[0].get("protection_failed") in (None, False)

    @pytest.mark.asyncio
    async def test_partial_tp_reduce_rejected_no_risk_reduced(self):
        """partial_tp_1 + reduce_rejected → reduce_failed, no risk_reduced."""
        ex = self._make_ex()

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex.publish = fake_publish
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": False, "reason": "reduce_rejected",
            "protective_update_state": "restored_old_sl",
        })

        await ex._handle_partial_tp_trigger("BTC-USDT", "partial_tp_1")

        statuses = [p.get("status") for t, p in published]
        assert "reduce_failed" in statuses
        assert "risk_reduced" not in statuses


class TestPortfolioRiskGuardReduceHandling:
    def _make_guard(self):
        from agents.trading.portfolio_risk_guard import PortfolioRiskGuard
        g = PortfolioRiskGuard.__new__(PortfolioRiskGuard)
        g._positions = {}
        g._prices = {}
        g._price_history = {}
        g._account_balance = 1000.0
        g.logger = MagicMock()
        g._save_state = MagicMock()
        return g

    @pytest.mark.asyncio
    async def test_rejected_does_not_shrink_exposure(self):
        g = self._make_guard()
        g._positions["BTC-USDT"] = {"amount_usdt": 100.0, "side": "long"}
        g.publish = AsyncMock()
        payload = {
            "status": "rejected",
            "action": "close",
            "symbol": "BTC-USDT",
            "reason": "sl_cancel_failed",
        }
        await g._handle_execution_result(payload)
        assert g._positions["BTC-USDT"]["amount_usdt"] == 100.0
        # rejected 不应触发 risk_alert
        g.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_reduce_failed_does_not_shrink_exposure(self):
        g = self._make_guard()
        g._positions["BTC-USDT"] = {"amount_usdt": 100.0, "side": "long"}
        g.publish = AsyncMock()
        payload = {
            "status": "reduce_failed",
            "action": "reduce",
            "symbol": "BTC-USDT",
            "reason": "reduce_rejected",
        }
        await g._handle_execution_result(payload)
        assert g._positions["BTC-USDT"]["amount_usdt"] == 100.0
        g.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_protection_failed_still_shrinks_and_emits_alert(self):
        g = self._make_guard()
        g._positions["BTC-USDT"] = {"amount_usdt": 100.0, "side": "long"}
        published = []

        async def fake_publish(topic, payload):
            published.append((topic, payload))

        g.publish = fake_publish
        payload = {
            "status": "risk_reduced",
            "action": "close",
            "symbol": "BTC-USDT",
            "reduce_pct": 0.25,
            "protection_failed": True,
            "protective_update_state": "replace_failed",
            "request_id": "r-9",
        }
        await g._handle_execution_result(payload)
        assert g._positions["BTC-USDT"]["amount_usdt"] == pytest.approx(75.0)
        types = [p.get("type") for t, p in published if t == "risk_alert"]
        assert "protection_failed" in types

    @pytest.mark.asyncio
    async def test_clean_risk_reduced_shrinks_no_alert(self):
        g = self._make_guard()
        g._positions["BTC-USDT"] = {"amount_usdt": 100.0, "side": "long"}
        g.publish = AsyncMock()
        payload = {
            "status": "risk_reduced",
            "action": "close",
            "symbol": "BTC-USDT",
            "reduce_pct": 0.5,
            # protection_failed 缺失 → 不发 alert
        }
        await g._handle_execution_result(payload)
        assert g._positions["BTC-USDT"]["amount_usdt"] == pytest.approx(50.0)
        g.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_dust_closed_removes_symbol(self):
        g = self._make_guard()
        g._positions["BTC-USDT"] = {"amount_usdt": 100.0, "side": "long"}
        g.publish = AsyncMock()
        payload = {
            "status": "executed",
            "action": "close",
            "symbol": "BTC-USDT",
            "reduce_origin": True,
            "protection_state": "closed",
        }
        await g._handle_execution_result(payload)
        assert "BTC-USDT" not in g._positions

