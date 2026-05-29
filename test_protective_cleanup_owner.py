"""AC3-P0-009..014 close cleanup owner-bound 定向单测.

参考: docs/audit_remediation_third_pass_20260528_acceptance.md §5.2
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from executor import ContractExecutor


def _make_executor(*, exchange_id: str = 'okx', testnet: bool = True) -> ContractExecutor:
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger('test_protective_cleanup_owner')
    ex.exchange_id = exchange_id
    ex.testnet = testnet
    ex.leverage = 1
    ex.exchange = MagicMock()
    ex.exchange.amount_to_precision = lambda s, a: round(float(a), 6)
    ex.exchange.market = MagicMock(return_value={
        'contractSize': 1, 'limits': {'amount': {'min': 1e-8}}
    })
    ex.positions = {}
    ex.idempotency = None
    ex.balance_adapter = None
    ex.ledger = None
    ex.caps = None
    ex.risk_manager = MagicMock()
    ex.positions_file = '/tmp/_test_cleanup_owner_positions.json'
    ex._sl_check_failures = {}
    ex._sl_max_failures = 5
    ex._last_sl_update = {}
    ex._okx_pos_mode = 'net_mode'
    ex._okx_pos_mode_source = 'test'
    ex._halted_symbols = {}
    ex._exit_lock_mu = threading.Lock()
    ex._exit_locks = {}
    return ex


def _seed_long(ex, *, sl_algo='123', sl_clord='caLiveBotBTC123abc'):
    ex.positions['BTC-USDT'] = {
        'symbol': 'BTC-USDT', 'side': 'long',
        'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
        'leverage': 1,
        'stop_loss': 95.0, 'original_sl': 95.0,
        'take_profit': 110.0,
        'tp_filled': 0,
        'highest_price': 110.0, 'lowest_price': 100.0,
        'atr_pct': 0.02,
        'sl_order_id': sl_algo,
        'sl_algo_id': sl_algo,
        'sl_algo_clord_id': sl_clord,
        'sl_sync_state': 'active',
        'protection_state': 'protected',
    }


@pytest.fixture(autouse=True)
def _set_owner_env(monkeypatch):
    """统一 namespace=live, bot_instance=Bot1,使 _is_owner_clord_id 可识别 caliveBot1...
    历史 sl... 前缀仍只能通过 exact sl_algo_clord_id 识别。
    """
    monkeypatch.setenv('STATE_NAMESPACE', 'live')
    monkeypatch.setenv('BOT_INSTANCE_ID', 'Bot1')
    yield


class TestAC3P0009KnownAlgoOnly:
    """AC3-P0-009: pending algos 含 known + foreign 时只取消 known."""

    def test_known_only_foreign_skipped(self):
        ex = _make_executor()
        _seed_long(ex, sl_algo='123', sl_clord='caliveBot1BTCUSDTabc')

        # mock 撤已知 SL
        ex._cancel_protective_sl = MagicMock(return_value=True)
        # pending algos: known(123) 已被前面撤掉,新出现 foreign(999) clord 是手工 manual-999
        ex._list_pending_algos = MagicMock(return_value=[
            {'algoId': '123', 'algoClOrdId': 'caliveBot1BTCUSDTabc'},
            {'algoId': '999', 'algoClOrdId': 'manual-999'},
        ])
        cancel_calls = []
        ex.exchange.cancel_orders = MagicMock(
            side_effect=lambda ids, sym, params=None: cancel_calls.append((tuple(ids), sym))
        )

        res = ex._cleanup_protective_orders_on_close('BTC-USDT', ex.positions['BTC-USDT'])

        # known(123) 由 _cancel_protective_sl 撤;cancel_orders 不应再撤 foreign(999)
        assert cancel_calls == []  # foreign 不被 cancel_orders 调用
        assert '999' in res['foreign_algo_ids']
        assert '123' in res['cancelled_algo_ids']
        assert res['state'] == 'foreign_algos_present'
        assert res['ok'] is False
        assert res['halt_required'] is True


class TestAC3P0010ExactClordOwner:
    """AC3-P0-010: algoId 不同但 algoClOrdId == 本地 sl_algo_clord_id 可取消."""

    def test_clord_match_owns_algo(self):
        ex = _make_executor()
        # local sl_algo_id 不存在,但 sl_algo_clord_id 已知
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
            'leverage': 1,
            'stop_loss': 95.0, 'original_sl': 95.0,
            'sl_order_id': None,
            'sl_algo_id': None,
            'sl_algo_clord_id': 'slBTCUSDT-old',
            'sl_sync_state': 'active',
            'protection_state': 'protected',
            'tp_filled': 0,
        }
        ex._cancel_protective_sl = MagicMock(return_value=True)  # known_sl_algo 为 None,不会调
        ex._list_pending_algos = MagicMock(return_value=[
            {'algoId': '777', 'algoClOrdId': 'slBTCUSDT-old'},
        ])
        ex.exchange.cancel_orders = MagicMock()

        res = ex._cleanup_protective_orders_on_close('BTC-USDT', ex.positions['BTC-USDT'])

        ex.exchange.cancel_orders.assert_called_once()
        args = ex.exchange.cancel_orders.call_args
        assert args.args[0] == ['777']
        assert '777' in res['cancelled_algo_ids']
        assert '777' in res['owned_algo_ids']
        assert res['state'] == 'cleaned'
        assert res['ok'] is True


class TestAC3P0011OwnerPrefix:
    """AC3-P0-011: 新 owner prefix 匹配可取消."""

    def test_owner_prefix_match(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 1.0,
            'sl_order_id': None, 'sl_algo_id': None,
            'sl_algo_clord_id': None,
            'sl_sync_state': 'unknown', 'protection_state': 'unknown',
            'tp_filled': 0,
        }
        ex._cancel_protective_sl = MagicMock(return_value=True)
        # 算 prefix: ca + live + Bot1 = caliveBot1
        ex._list_pending_algos = MagicMock(return_value=[
            {'algoId': '888', 'algoClOrdId': 'caliveBot1BTCUSDTrand123'},
        ])
        ex.exchange.cancel_orders = MagicMock()

        res = ex._cleanup_protective_orders_on_close('BTC-USDT', ex.positions['BTC-USDT'])

        ex.exchange.cancel_orders.assert_called_once()
        assert '888' in res['cancelled_algo_ids']
        assert res['state'] == 'cleaned'


class TestAC3P0012LegacySlPrefixNotSwept:
    """AC3-P0-012: 历史 'sl' 前缀不能泛化 sweep — 必须 exact clord 匹配."""

    def test_sl_prefix_alone_is_not_owner(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 1.0,
            'sl_order_id': None, 'sl_algo_id': None,
            'sl_algo_clord_id': 'slBTCUSDT-mine',  # 本地记录的 clord
            'sl_sync_state': 'active', 'protection_state': 'protected',
            'tp_filled': 0,
        }
        ex._cancel_protective_sl = MagicMock(return_value=True)
        # foreign algo 也是 sl 前缀但 clord 不匹配本地,不能被 sweep
        ex._list_pending_algos = MagicMock(return_value=[
            {'algoId': '500', 'algoClOrdId': 'slBTCUSDT-other-bot'},
        ])
        ex.exchange.cancel_orders = MagicMock()

        res = ex._cleanup_protective_orders_on_close('BTC-USDT', ex.positions['BTC-USDT'])

        ex.exchange.cancel_orders.assert_not_called()
        assert '500' in res['foreign_algo_ids']
        assert res['state'] == 'foreign_algos_present'
        assert res['halt_required'] is True


class TestAC3P0013ForeignBlocksOpen:
    """AC3-P0-013: cleanup 后 foreign algo 残留必须阻断新开仓(halt)."""

    def test_foreign_present_halts_live(self):
        ex = _make_executor(exchange_id='okx', testnet=False)
        _seed_long(ex, sl_algo='123', sl_clord='caliveBot1BTCabc')
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex._list_pending_algos = MagicMock(return_value=[
            {'algoId': '999', 'algoClOrdId': 'manual-foreign'},
        ])
        ex.exchange.cancel_orders = MagicMock()
        ex._halt_symbol = MagicMock()
        ex._save_positions = MagicMock()
        # 必要的 close 路径 mock
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 100.0})
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex.exchange.create_order = MagicMock(return_value={'id': 'close-order'})
        ex._build_close_order_params = MagicMock(return_value={})
        ex._estimate_close_pnl_local = MagicMock(return_value=0.0)
        ex.risk_manager.record_trade = MagicMock()

        result = ex.close_position('BTC-USDT')

        assert result is not None
        assert result.get('protective_cleanup_state') == 'foreign_algos_present'
        assert '999' in result.get('foreign_algo_ids', [])
        # halt 必须被触发
        ex._halt_symbol.assert_called_once()
        assert ex._halt_symbol.call_args.kwargs.get('reason') == 'foreign_algos_present'


class TestAC3P0014CleanupResultPassthrough:
    """AC3-P0-014: cleanup 回参必须透传到 close_position result."""

    def test_close_passthrough(self):
        ex = _make_executor()
        _seed_long(ex, sl_algo='123', sl_clord='caliveBot1BTCabc')
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex._list_pending_algos = MagicMock(return_value=[
            {'algoId': '999', 'algoClOrdId': 'manual-x'},
        ])
        ex.exchange.cancel_orders = MagicMock()
        ex._save_positions = MagicMock()
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 100.0})
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex.exchange.create_order = MagicMock(return_value={'id': 'close-order'})
        ex._build_close_order_params = MagicMock(return_value={})
        ex._estimate_close_pnl_local = MagicMock(return_value=0.0)
        ex.risk_manager.record_trade = MagicMock()

        result = ex.close_position('BTC-USDT')

        assert 'protective_cleanup' in result
        cleanup = result['protective_cleanup']
        assert cleanup['state'] == 'foreign_algos_present'
        assert '999' in cleanup['foreign_algo_ids']
        assert '123' in cleanup['cancelled_algo_ids']
        assert 'foreign_algo_not_cancelled' in cleanup['warnings']
        assert result.get('foreign_algo_ids') == cleanup['foreign_algo_ids']
        assert result.get('cleanup_warnings') == cleanup['warnings']


class TestNoneStateWhenNoAlgosAndNoLocal:
    """边界: 本地无 SL + pending 无 algo → state=none, ok=True."""

    def test_state_none(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 1.0,
            'sl_order_id': None, 'sl_algo_id': None,
            'sl_algo_clord_id': None,
            'tp_filled': 0,
        }
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex._list_pending_algos = MagicMock(return_value=[])

        res = ex._cleanup_protective_orders_on_close('BTC-USDT', ex.positions['BTC-USDT'])

        assert res['state'] == 'none'
        assert res['ok'] is True
        assert res['cancelled_algo_ids'] == []
