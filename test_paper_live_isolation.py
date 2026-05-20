"""Paper 与 Live 隔离验证"""

import json
import os
import sys
import tempfile
import time
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.live_ledger import LiveLedger


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_exchange():
    exchange = MagicMock()
    exchange.fetch_order.return_value = {
        'id': 'order_1',
        'average': 100.0,
        'filled': 1.0,
        'fee': {'cost': 0.05, 'currency': 'USDT'},
        'status': 'closed',
    }
    exchange.fetch_orders.return_value = []
    return exchange


class TestPaperLiveIsolation:

    def test_paper_events_excluded_from_daily_pnl(self, tmp_dir, mock_exchange):
        """Paper 交易不计入 daily_realized_pnl"""
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=os.path.join(tmp_dir, 'events.jsonl'),
            lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
        )

        # 实盘交易：亏损 -1.0
        mock_exchange.fetch_order.return_value = {
            'id': 'live_1', 'average': 99.0, 'filled': 1.0,
            'fee': {'cost': 0.05, 'currency': 'USDT'}, 'status': 'closed',
        }
        ledger.record_open(
            order_id='live_open', symbol='BTC-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=100.0
        )
        mock_exchange.fetch_order.return_value = {
            'id': 'live_2', 'average': 98.0, 'filled': 1.0,
            'fee': {'cost': 0.05, 'currency': 'USDT'}, 'status': 'closed',
        }
        ledger.record_close(
            order_id='live_close', symbol='BTC-USDT-SWAP', side='long',
            entry_price=99.0, amount_usdt=10.0, leverage=3,
            estimated_price=98.0, close_type='close'
        )

        # Paper 交易：盈利 +5.0（不应计入）
        mock_exchange.fetch_order.return_value = {
            'id': 'paper_1', 'average': 100.0, 'filled': 1.0,
            'fee': {'cost': 0.0, 'currency': 'USDT'}, 'status': 'closed',
        }
        # 直接写入 paper 事件
        paper_event = {
            "event_id": "paper_test",
            "ts": time.time(),
            "position_id": "paper-pos-1",
            "symbol": "ETH-USDT-SWAP",
            "event_type": "close",
            "side": "long",
            "order_id": "paper_close",
            "fill_price": 110.0,
            "fee": 0.0,
            "amount_usdt": 10.0,
            "leverage": 3,
            "realized_pnl": 5.0,
            "source": "paper",
            "paper": True,
        }
        ledger._write_event(paper_event)

        # daily_realized_pnl 应只包含实盘
        daily = ledger.daily_realized_pnl()
        assert daily < 0, f"Daily PnL should be negative (live loss only), got {daily}"
        # 不应包含 paper 的 +5.0
        assert abs(daily - (-5.0)) > 3.0, "Paper PnL leaked into daily calculation"

    def test_paper_events_have_paper_flag(self, tmp_dir, mock_exchange):
        """验证 paper 事件在 JSONL 中有 paper 标记"""
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=os.path.join(tmp_dir, 'events.jsonl'),
            lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
        )

        # 写入一个 paper 事件
        paper_event = {
            "event_id": "paper_1",
            "ts": time.time(),
            "symbol": "BTC-USDT-SWAP",
            "event_type": "open",
            "paper": True,
            "realized_pnl": 0.0,
        }
        ledger._write_event(paper_event)

        events = ledger._read_events()
        assert len(events) == 1
        assert events[0].get("paper") is True

    def test_live_events_no_paper_flag(self, tmp_dir, mock_exchange):
        """实盘事件没有 paper 标记"""
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=os.path.join(tmp_dir, 'events.jsonl'),
            lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
        )

        event = ledger.record_open(
            order_id='live_1', symbol='BTC-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=100.0
        )
        assert event.get("paper") is None or event.get("paper") is False
