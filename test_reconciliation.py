"""对账模块测试"""

import os
import sys
import time
import tempfile
import json
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.live_ledger import LiveLedger
from utils.reconciliation import Reconciler, ReconcileResult


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_exchange():
    exchange = MagicMock()
    exchange.fetch_order.return_value = {
        'id': 'order_1', 'average': 100.0, 'filled': 1.0,
        'fee': {'cost': 0.05, 'currency': 'USDT'}, 'status': 'closed',
    }
    exchange.fetch_orders.return_value = []
    return exchange


class TestReconciler:

    def test_no_mismatches_when_bills_empty(self, tmp_dir, mock_exchange):
        """无账单时返回空列表"""
        mock_exchange.private_get_account_bills.return_value = {'data': []}
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=os.path.join(tmp_dir, 'events.jsonl'),
            lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
        )
        reconciler = Reconciler(mock_exchange, ledger)
        result = reconciler.check_recent_bills()
        assert result.query_ok is True
        assert len(result.mismatches) == 0

    def test_detects_mismatch(self, tmp_dir, mock_exchange):
        """检测本地与交易所 PnL 偏差"""
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=os.path.join(tmp_dir, 'events.jsonl'),
            lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
        )

        local_event = {
            "event_id": "ev1",
            "ts": time.time(),
            "symbol": "BTC-USDT-SWAP",
            "event_type": "close",
            "order_id": "ord_123",
            "realized_pnl": -2.0,
            "source": "okx_fill",
        }
        ledger._write_event(local_event)

        mock_exchange.private_get_account_bills.return_value = {
            'data': [
                {'ordId': 'ord_123', 'pnl': '-5.0', 'instId': 'BTC-USDT-SWAP'}
            ]
        }

        reconciler = Reconciler(mock_exchange, ledger)
        result = reconciler.check_recent_bills()
        assert result.query_ok is True
        assert len(result.mismatches) == 1
        assert result.mismatches[0]['status'] == 'mismatch'
        assert result.mismatches[0]['local_pnl'] == -2.0
        assert result.mismatches[0]['exchange_pnl'] == -5.0
        assert result.mismatches[0]['delta'] == 3.0

    def test_detects_missing_local(self, tmp_dir, mock_exchange):
        """检测交易所有账单但本地无记录"""
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=os.path.join(tmp_dir, 'events.jsonl'),
            lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
        )

        mock_exchange.private_get_account_bills.return_value = {
            'data': [
                {'ordId': 'unknown_order', 'pnl': '-3.0', 'instId': 'ETH-USDT-SWAP'}
            ]
        }

        reconciler = Reconciler(mock_exchange, ledger)
        result = reconciler.check_recent_bills()
        assert result.query_ok is True
        assert len(result.mismatches) == 1
        assert result.mismatches[0]['status'] == 'missing_local'
        assert result.mismatches[0]['local_pnl'] is None

    def test_no_mismatch_within_threshold(self, tmp_dir, mock_exchange):
        """偏差在阈值内不报告"""
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=os.path.join(tmp_dir, 'events.jsonl'),
            lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
        )

        local_event = {
            "event_id": "ev1",
            "ts": time.time(),
            "symbol": "BTC-USDT-SWAP",
            "event_type": "close",
            "order_id": "ord_456",
            "realized_pnl": -2.00,
            "source": "okx_fill",
        }
        ledger._write_event(local_event)

        mock_exchange.private_get_account_bills.return_value = {
            'data': [
                {'ordId': 'ord_456', 'pnl': '-2.05', 'instId': 'BTC-USDT-SWAP'}
            ]
        }

        reconciler = Reconciler(mock_exchange, ledger)
        result = reconciler.check_recent_bills()
        assert result.query_ok is True
        assert len(result.mismatches) == 0

    def test_should_run_interval(self):
        """should_run 按间隔控制"""
        exchange = MagicMock()
        ledger = MagicMock()
        reconciler = Reconciler(exchange, ledger)
        reconciler._last_check_ts = time.time() - 700
        assert reconciler.should_run(interval_sec=600) is True
        assert reconciler.should_run(interval_sec=600) is False

    def test_run_and_report_no_issues(self, tmp_dir, mock_exchange):
        """无差异时返回 None"""
        mock_exchange.private_get_account_bills.return_value = {'data': []}
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=os.path.join(tmp_dir, 'events.jsonl'),
            lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
        )
        reconciler = Reconciler(mock_exchange, ledger)
        assert reconciler.run_and_report() is None

    def test_run_and_report_with_issues(self, tmp_dir, mock_exchange):
        """有差异时返回告警摘要"""
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=os.path.join(tmp_dir, 'events.jsonl'),
            lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
        )
        local_event = {
            "event_id": "ev1", "ts": time.time(),
            "symbol": "BTC-USDT-SWAP", "event_type": "close",
            "order_id": "ord_789", "realized_pnl": -1.0, "source": "okx_fill",
        }
        ledger._write_event(local_event)

        mock_exchange.private_get_account_bills.return_value = {
            'data': [{'ordId': 'ord_789', 'pnl': '-4.0', 'instId': 'BTC-USDT-SWAP'}]
        }

        reconciler = Reconciler(mock_exchange, ledger)
        report = reconciler.run_and_report()
        assert report is not None
        assert "对账告警" in report
        assert "ord_789" in report

    def test_api_failure_returns_error_state(self, tmp_dir, mock_exchange):
        """API 失败时返回 query_ok=False，run_and_report 返回告警"""
        mock_exchange.private_get_account_bills.side_effect = Exception("network")
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=os.path.join(tmp_dir, 'events.jsonl'),
            lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
        )
        reconciler = Reconciler(mock_exchange, ledger)
        result = reconciler.check_recent_bills()
        assert result.query_ok is False
        assert result.error == "network"
        assert len(result.mismatches) == 0

        # run_and_report should return warning, not None
        report = reconciler.run_and_report()
        assert report is not None
        assert "API查询失败" in report
