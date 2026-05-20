"""LiveLedger 单元测试"""

import json
import os
import time
import tempfile
import pytest
from unittest.mock import MagicMock, patch


# 确保可以导入
import sys
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
        'id': 'order_123',
        'average': 100.5,
        'filled': 0.1,
        'fee': {'cost': 0.05, 'currency': 'USDT'},
        'status': 'closed',
    }
    return exchange


@pytest.fixture
def ledger(tmp_dir, mock_exchange):
    return LiveLedger(
        exchange=mock_exchange,
        events_path=os.path.join(tmp_dir, 'events.jsonl'),
        lifecycle_path=os.path.join(tmp_dir, 'lifecycle.json'),
    )


class TestRecordOpen:
    def test_basic_open(self, ledger, mock_exchange):
        event = ledger.record_open(
            order_id='order_123', symbol='BTC-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=100.0
        )
        assert event['event_type'] == 'open'
        assert event['fill_price'] == 100.5
        assert event['fee'] == 0.05
        assert event['source'] == 'okx_fill'
        assert event['realized_pnl'] == 0.0
        assert event['position_id'] is not None
        mock_exchange.fetch_order.assert_called_once_with('order_123', 'BTC-USDT-SWAP')

    def test_open_creates_lifecycle(self, ledger):
        event = ledger.record_open(
            order_id='order_123', symbol='BTC-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=100.0
        )
        lc = ledger.get_lifecycle('BTC-USDT-SWAP')
        assert lc is not None
        assert lc['status'] == 'open'
        assert lc['side'] == 'long'
        assert lc['entry_price'] == 100.5

    def test_open_fallback_on_api_failure(self, ledger, mock_exchange):
        mock_exchange.fetch_order.side_effect = Exception("network error")
        event = ledger.record_open(
            order_id='order_123', symbol='BTC-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=100.0
        )
        assert event['fill_price'] == 100.0
        assert event['source'] == 'estimated'
        assert event['fee'] == 0.0

    def test_open_writes_jsonl(self, ledger, tmp_dir):
        ledger.record_open(
            order_id='order_123', symbol='BTC-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=100.0
        )
        events_path = os.path.join(tmp_dir, 'events.jsonl')
        with open(events_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        ev = json.loads(lines[0])
        assert ev['event_type'] == 'open'
        assert ev['fill_price'] == 100.5


class TestRecordReduce:
    def test_reduce_calculates_pnl_long(self, ledger, mock_exchange):
        # 先开仓
        ledger.record_open(
            order_id='order_1', symbol='BTC-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=100.0
        )
        # 减仓：entry=100.5, exit=105.0 (mock 返回)
        mock_exchange.fetch_order.return_value = {
            'id': 'order_2', 'average': 105.0, 'filled': 0.03,
            'fee': {'cost': 0.03, 'currency': 'USDT'}, 'status': 'closed',
        }
        event = ledger.record_reduce(
            order_id='order_2', symbol='BTC-USDT-SWAP', side='long',
            entry_price=100.5, reduce_usdt=3.0, leverage=3,
            estimated_price=104.0
        )
        # PnL = (105 - 100.5) / 100.5 * 3.0 * 3 - 0.03
        expected_pnl = (105.0 - 100.5) / 100.5 * 3.0 * 3 - 0.03
        assert abs(event['realized_pnl'] - expected_pnl) < 0.01
        assert event['source'] == 'okx_fill'

    def test_reduce_calculates_pnl_short(self, ledger, mock_exchange):
        ledger.record_open(
            order_id='order_1', symbol='ETH-USDT-SWAP', side='short',
            amount_usdt=10.0, leverage=5, estimated_price=3000.0
        )
        mock_exchange.fetch_order.return_value = {
            'id': 'order_2', 'average': 2950.0, 'filled': 0.01,
            'fee': {'cost': 0.02, 'currency': 'USDT'}, 'status': 'closed',
        }
        event = ledger.record_reduce(
            order_id='order_2', symbol='ETH-USDT-SWAP', side='short',
            entry_price=3000.0, reduce_usdt=5.0, leverage=5,
            estimated_price=2960.0
        )
        # short PnL = (3000 - 2950) / 3000 * 5.0 * 5 - 0.02
        expected_pnl = (3000.0 - 2950.0) / 3000.0 * 5.0 * 5 - 0.02
        assert abs(event['realized_pnl'] - expected_pnl) < 0.01

    def test_reduce_updates_lifecycle(self, ledger, mock_exchange):
        ledger.record_open(
            order_id='order_1', symbol='BTC-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=100.0
        )
        mock_exchange.fetch_order.return_value = {
            'id': 'order_2', 'average': 105.0, 'filled': 0.03,
            'fee': {'cost': 0.03, 'currency': 'USDT'}, 'status': 'closed',
        }
        event = ledger.record_reduce(
            order_id='order_2', symbol='BTC-USDT-SWAP', side='long',
            entry_price=100.5, reduce_usdt=3.0, leverage=3,
            estimated_price=104.0
        )
        lc = ledger.get_lifecycle('BTC-USDT-SWAP')
        assert lc['status'] == 'open'
        assert 'reduce' in lc['events']
        assert lc['total_realized_pnl'] == round(event['realized_pnl'], 4)


class TestRecordClose:
    def test_close_full_lifecycle(self, ledger, mock_exchange):
        # 开仓
        ledger.record_open(
            order_id='order_1', symbol='HYPE-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=20.0
        )
        # 平仓
        mock_exchange.fetch_order.return_value = {
            'id': 'order_2', 'average': 19.5, 'filled': 1.5,
            'fee': {'cost': 0.04, 'currency': 'USDT'}, 'status': 'closed',
        }
        event = ledger.record_close(
            order_id='order_2', symbol='HYPE-USDT-SWAP', side='long',
            entry_price=20.0, amount_usdt=10.0, leverage=3,
            estimated_price=19.6, close_type='force_close'
        )
        # PnL = (19.5 - 20.0) / 20.0 * 10.0 * 3 - 0.04
        expected_pnl = (19.5 - 20.0) / 20.0 * 10.0 * 3 - 0.04
        assert abs(event['realized_pnl'] - expected_pnl) < 0.01
        assert event['event_type'] == 'force_close'

        lc = ledger.get_lifecycle('HYPE-USDT-SWAP')
        assert lc['status'] == 'closed'
        assert lc['total_realized_pnl'] == round(expected_pnl, 4)

    def test_close_with_prior_reduce(self, ledger, mock_exchange):
        # 开仓 10 USDT
        ledger.record_open(
            order_id='o1', symbol='INJ-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=5.0
        )
        # 减仓 30% (3 USDT) 盈利
        mock_exchange.fetch_order.return_value = {
            'id': 'o2', 'average': 5.5, 'filled': 0.5,
            'fee': {'cost': 0.02, 'currency': 'USDT'}, 'status': 'closed',
        }
        reduce_ev = ledger.record_reduce(
            order_id='o2', symbol='INJ-USDT-SWAP', side='long',
            entry_price=5.0, reduce_usdt=3.0, leverage=3,
            estimated_price=5.4
        )
        # 平仓剩余 7 USDT
        mock_exchange.fetch_order.return_value = {
            'id': 'o3', 'average': 5.3, 'filled': 1.0,
            'fee': {'cost': 0.03, 'currency': 'USDT'}, 'status': 'closed',
        }
        close_ev = ledger.record_close(
            order_id='o3', symbol='INJ-USDT-SWAP', side='long',
            entry_price=5.0, amount_usdt=7.0, leverage=3,
            estimated_price=5.2
        )
        lc = ledger.get_lifecycle('INJ-USDT-SWAP')
        assert lc['status'] == 'closed'
        total = round(reduce_ev['realized_pnl'] + close_ev['realized_pnl'], 4)
        assert lc['total_realized_pnl'] == total


class TestDailyPnl:
    def test_daily_pnl_sums_correctly(self, ledger, mock_exchange):
        # 开仓 + 平仓亏损
        ledger.record_open(
            order_id='o1', symbol='A-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=100.0
        )
        mock_exchange.fetch_order.return_value = {
            'id': 'o2', 'average': 99.0, 'filled': 0.3,
            'fee': {'cost': 0.05, 'currency': 'USDT'}, 'status': 'closed',
        }
        ledger.record_close(
            order_id='o2', symbol='A-USDT-SWAP', side='long',
            entry_price=100.0, amount_usdt=10.0, leverage=3,
            estimated_price=99.0
        )
        # 开仓 + 平仓盈利
        mock_exchange.fetch_order.return_value = {
            'id': 'o3', 'average': 50.0, 'filled': 0.2,
            'fee': {'cost': 0.02, 'currency': 'USDT'}, 'status': 'closed',
        }
        ledger.record_open(
            order_id='o3', symbol='B-USDT-SWAP', side='short',
            amount_usdt=5.0, leverage=2, estimated_price=50.0
        )
        mock_exchange.fetch_order.return_value = {
            'id': 'o4', 'average': 48.0, 'filled': 0.2,
            'fee': {'cost': 0.02, 'currency': 'USDT'}, 'status': 'closed',
        }
        ledger.record_close(
            order_id='o4', symbol='B-USDT-SWAP', side='short',
            entry_price=50.0, amount_usdt=5.0, leverage=2,
            estimated_price=48.0
        )
        daily = ledger.daily_realized_pnl()
        # A: (99-100)/100 * 10 * 3 - 0.05 = -0.35
        # B: (50-48)/50 * 5 * 2 - 0.02 = 0.38
        assert daily != 0.0  # sanity
        # 精确验证
        pnl_a = (99.0 - 100.0) / 100.0 * 10.0 * 3 - 0.05
        pnl_b = (50.0 - 48.0) / 50.0 * 5.0 * 2 - 0.02
        assert abs(daily - (pnl_a + pnl_b)) < 0.01


class TestExternalClose:
    def test_external_close_with_order_info(self, ledger, mock_exchange):
        ledger.record_open(
            order_id='o1', symbol='WLD-USDT-SWAP', side='short',
            amount_usdt=8.0, leverage=3, estimated_price=2.0
        )
        event = ledger.record_external_close(
            symbol='WLD-USDT-SWAP', side='short',
            entry_price=2.0, amount_usdt=8.0, leverage=3,
            order_info={'id': 'ext_1', 'average': 2.05, 'fee': {'cost': 0.03, 'currency': 'USDT'}}
        )
        assert event['source'] == 'okx_order'
        assert event['fill_price'] == 2.05
        # short: (2.0 - 2.05) / 2.0 * 8 * 3 - 0.03 = -0.63
        expected = (2.0 - 2.05) / 2.0 * 8.0 * 3 - 0.03
        assert abs(event['realized_pnl'] - expected) < 0.01

    def test_external_close_fallback_estimated(self, ledger, mock_exchange):
        ledger.record_open(
            order_id='o1', symbol='WLD-USDT-SWAP', side='short',
            amount_usdt=8.0, leverage=3, estimated_price=2.0
        )
        mock_exchange.fetch_orders.side_effect = Exception("no data")
        event = ledger.record_external_close(
            symbol='WLD-USDT-SWAP', side='short',
            entry_price=2.0, amount_usdt=8.0, leverage=3,
        )
        assert event['source'] == 'estimated'
        assert event['reconcile_status'] == 'pending'


class TestPersistence:
    def test_events_persist_across_instances(self, tmp_dir, mock_exchange):
        events_path = os.path.join(tmp_dir, 'events.jsonl')
        lifecycle_path = os.path.join(tmp_dir, 'lifecycle.json')

        ledger1 = LiveLedger(mock_exchange, events_path, lifecycle_path)
        ledger1.record_open(
            order_id='o1', symbol='BTC-USDT-SWAP', side='long',
            amount_usdt=10.0, leverage=3, estimated_price=100.0
        )

        # 新实例应加载 lifecycle
        ledger2 = LiveLedger(mock_exchange, events_path, lifecycle_path)
        lc = ledger2.get_lifecycle('BTC-USDT-SWAP')
        assert lc is not None
        assert lc['status'] == 'open'

    def test_lifecycle_survives_reload(self, tmp_dir, mock_exchange):
        events_path = os.path.join(tmp_dir, 'events.jsonl')
        lifecycle_path = os.path.join(tmp_dir, 'lifecycle.json')

        ledger = LiveLedger(mock_exchange, events_path, lifecycle_path)
        ledger.record_open(
            order_id='o1', symbol='ETH-USDT-SWAP', side='short',
            amount_usdt=5.0, leverage=2, estimated_price=3000.0
        )
        # 直接读文件验证
        with open(lifecycle_path) as f:
            data = json.load(f)
        assert len(data) == 1
        key = list(data.keys())[0]
        assert data[key]['symbol'] == 'ETH-USDT-SWAP'


class TestRecordAdd:
    """record_add lifecycle 维护测试"""

    def test_add_updates_avg_entry_and_total(self, tmp_dir, mock_exchange):
        """加仓后 lifecycle 更新加权均价和总保证金"""
        events_path = os.path.join(tmp_dir, 'events.jsonl')
        lifecycle_path = os.path.join(tmp_dir, 'lifecycle.json')
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=events_path,
            lifecycle_path=lifecycle_path,
        )

        # 开仓 100 USDT @ 100.5
        mock_exchange.fetch_order.return_value = {
            'id': 'o1', 'average': 100.5, 'filled': 1.0,
            'fee': {'cost': 0.05, 'currency': 'USDT'}, 'status': 'closed',
        }
        ledger.record_open('o1', 'BTC-USDT-SWAP', 'long', 100.0, 5, 100.5)

        # 加仓 50 USDT @ 102.0
        mock_exchange.fetch_order.return_value = {
            'id': 'o2', 'average': 102.0, 'filled': 0.5,
            'fee': {'cost': 0.03, 'currency': 'USDT'}, 'status': 'closed',
        }
        ev = ledger.record_add('o2', 'BTC-USDT-SWAP', 'long', 50.0, 5, 102.0)

        assert ev['event_type'] == 'add'

        # 验证 lifecycle
        with open(lifecycle_path) as f:
            data = json.load(f)
        assert len(data) == 1
        lc = list(data.values())[0]
        assert lc['adds_count'] == 1
        assert lc['total_amount_usdt'] == 150.0
        expected_avg = (100.5 * 100 + 102.0 * 50) / 150
        assert abs(lc['avg_entry_price'] - expected_avg) < 0.001
        assert lc['events'] == ['open', 'add']
        assert lc['status'] == 'open'

    def test_add_without_open_falls_back_to_record_open(self, tmp_dir, mock_exchange):
        """无 open lifecycle 时 fallback 到 record_open"""
        events_path = os.path.join(tmp_dir, 'events.jsonl')
        lifecycle_path = os.path.join(tmp_dir, 'lifecycle.json')
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=events_path,
            lifecycle_path=lifecycle_path,
        )

        mock_exchange.fetch_order.return_value = {
            'id': 'o1', 'average': 50.0, 'filled': 1.0,
            'fee': {'cost': 0.02, 'currency': 'USDT'}, 'status': 'closed',
        }
        ev = ledger.record_add('o1', 'ETH-USDT-SWAP', 'short', 30.0, 3, 50.0)
        assert ev['event_type'] == 'open'

        with open(lifecycle_path) as f:
            data = json.load(f)
        assert len(data) == 1

    def test_full_lifecycle_open_add_close(self, tmp_dir, mock_exchange):
        """完整 open -> add -> close lifecycle 只有一个 lifecycle"""
        events_path = os.path.join(tmp_dir, 'events.jsonl')
        lifecycle_path = os.path.join(tmp_dir, 'lifecycle.json')
        ledger = LiveLedger(
            exchange=mock_exchange,
            events_path=events_path,
            lifecycle_path=lifecycle_path,
        )

        # open
        mock_exchange.fetch_order.return_value = {
            'id': 'o1', 'average': 200.0, 'filled': 1.0,
            'fee': {'cost': 0.1, 'currency': 'USDT'}, 'status': 'closed',
        }
        ledger.record_open('o1', 'SOL-USDT-SWAP', 'long', 100.0, 10, 200.0)

        # add
        mock_exchange.fetch_order.return_value = {
            'id': 'o2', 'average': 195.0, 'filled': 0.5,
            'fee': {'cost': 0.05, 'currency': 'USDT'}, 'status': 'closed',
        }
        ledger.record_add('o2', 'SOL-USDT-SWAP', 'long', 50.0, 10, 195.0)

        # close
        mock_exchange.fetch_order.return_value = {
            'id': 'o3', 'average': 210.0, 'filled': 1.5,
            'fee': {'cost': 0.15, 'currency': 'USDT'}, 'status': 'closed',
        }
        ledger.record_close('o3', 'SOL-USDT-SWAP', 'long', 200.0, 150.0, 10, 210.0)

        with open(lifecycle_path) as f:
            data = json.load(f)
        assert len(data) == 1
        lc = list(data.values())[0]
        assert lc['status'] == 'closed'
        assert lc['events'] == ['open', 'add', 'close']
        assert lc['adds_count'] == 1
        assert lc['total_amount_usdt'] == 150.0
