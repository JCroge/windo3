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
