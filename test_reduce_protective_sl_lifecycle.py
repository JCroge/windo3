"""AC3-P0-001..008 reduce_position 保护单生命周期定向单测.

参考: docs/audit_remediation_third_pass_20260528_acceptance.md §5.1
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
    ex.logger = logging.getLogger('test_reduce_protective_sl_lifecycle')
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
    ex.positions_file = '/tmp/_test_reduce_lifecycle_positions.json'
    ex._sl_check_failures = {}
    ex._sl_max_failures = 5
    ex._last_sl_update = {}
    ex._okx_pos_mode = 'net_mode'
    ex._okx_pos_mode_source = 'test'
    ex._halted_symbols = {}
    ex._exit_lock_mu = threading.Lock()
    ex._exit_locks = {}
    return ex


def _seed_long(ex):
    ex.positions['BTC-USDT'] = {
        'symbol': 'BTC-USDT', 'side': 'long',
        'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
        'leverage': 1,
        'stop_loss': 95.0, 'original_sl': 95.0,
        'take_profit': 110.0,
        'take_profit_levels': [110.0, 120.0, 130.0],
        'tp_filled': 0,
        'highest_price': 110.0, 'lowest_price': 100.0,
        'atr_pct': 0.02,
        'sl_order_id': 'old-algo',
        'sl_algo_id': 'old-algo',
        'sl_algo_clord_id': 'slBTCUSDT-old',
        'sl_sync_state': 'active',
        'protection_state': 'protected',
        'request_id': 'req-test',
    }


class TestAC3P0001CancelFailNoReduce:
    """AC3-P0-001: 撤旧 SL 失败时不发 reduce 订单."""

    def test_cancel_fail_blocks_create_order(self):
        ex = _make_executor()
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=False)
        ex.exchange.create_order = MagicMock()
        ex._save_positions = MagicMock()

        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        assert result is not None and result.get('ok') is False
        assert result.get('reason') == 'sl_cancel_failed'
        assert result.get('cancel_ok') is False
        assert result.get('reduce_ok') is False
        ex.exchange.create_order.assert_not_called()

    def test_cancel_fail_keeps_old_ids(self):
        ex = _make_executor()
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=False)
        ex.exchange.create_order = MagicMock()
        ex._save_positions = MagicMock()

        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        pos = ex.positions['BTC-USDT']
        # 旧 ID 必须保留以便人工对账
        assert pos['sl_algo_id'] == 'old-algo'
        assert pos['sl_order_id'] == 'old-algo'
        assert pos['sl_algo_clord_id'] == 'slBTCUSDT-old'
        assert result.get('old_sl_algo_id') == 'old-algo'
        assert 'old_sl_may_still_be_live' in result.get('warnings', [])


class TestAC3P0002CancelFailedState:
    """AC3-P0-002: 撤旧失败必须写 failed 状态."""

    def test_cancel_fail_marks_failed_state(self):
        ex = _make_executor()
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=False)
        ex._save_positions = MagicMock()

        ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        pos = ex.positions['BTC-USDT']
        assert pos['sl_sync_state'] == 'failed'
        assert pos['protection_state'] == 'unknown'
        assert pos.get('last_protection_error') == 'sl_cancel_failed'


class TestAC3P0003LiveCancelFailHalt:
    """AC3-P0-003: live OKX 撤旧失败必须 halt symbol."""

    def test_live_okx_halt_on_cancel_fail(self):
        ex = _make_executor(exchange_id='okx', testnet=False)
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=False)
        ex._halt_symbol = MagicMock()
        ex._save_positions = MagicMock()

        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        ex._halt_symbol.assert_called_once()
        call_kwargs = ex._halt_symbol.call_args
        assert call_kwargs.kwargs.get('reason') == 'sl_cancel_failed'
        assert result.get('halt_required') is True

    def test_testnet_no_halt_on_cancel_fail(self):
        ex = _make_executor(exchange_id='okx', testnet=True)
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=False)
        ex._halt_symbol = MagicMock()
        ex._save_positions = MagicMock()

        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        ex._halt_symbol.assert_not_called()
        assert result.get('halt_required') is False


class TestAC3P0004ReduceRejectRestore:
    """AC3-P0-004: 撤旧 ok + reduce reject → 尝试 restore 原 SL."""

    def test_reduce_reject_restores_old_sl(self):
        ex = _make_executor()
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex.exchange.create_order = MagicMock(side_effect=Exception('reduce reject'))

        captured = {}
        def fake_replace(symbol, position, new_sl):
            captured['restore_sl'] = new_sl
            position['sl_algo_id'] = 'restored-algo'
            position['protection_state'] = 'protected'
            position['sl_sync_state'] = 'active'
            return True
        ex._replace_protective_sl = fake_replace
        ex._save_positions = MagicMock()

        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        assert result is not None and result.get('ok') is False
        assert result.get('reduce_ok') is False
        assert result.get('reason') == 'reduce_rejected'
        assert captured.get('restore_sl') == 95.0
        assert result.get('protective_update_state') == 'restored_old_sl'
        # tp_filled 不得推进
        assert ex.positions['BTC-USDT']['tp_filled'] == 0

    def test_reduce_reject_restore_fail_halts_live(self):
        ex = _make_executor(exchange_id='okx', testnet=False)
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex.exchange.create_order = MagicMock(side_effect=Exception('reduce reject'))
        ex._replace_protective_sl = MagicMock(return_value=False)
        ex._halt_symbol = MagicMock()
        ex._save_positions = MagicMock()

        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        assert result.get('protective_update_state') == 'restore_failed'
        assert ex.positions['BTC-USDT']['protection_state'] == 'unknown'
        assert ex.positions['BTC-USDT']['sl_sync_state'] == 'failed'
        ex._halt_symbol.assert_called_once()
        assert ex._halt_symbol.call_args.kwargs.get('reason') == 'sl_restore_failed'
        assert result.get('halt_required') is True
        # tp_filled 不得推进
        assert ex.positions['BTC-USDT']['tp_filled'] == 0


class TestAC3P0005NormalReduceResidualProtection:
    """AC3-P0-005: 普通 risk reduce(无 tp_advance) 后剩余仓位也要保护."""

    def test_normal_reduce_replaces_residual_sl(self):
        ex = _make_executor()
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex.exchange.create_order = MagicMock(return_value={'id': 'r1'})
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 100.0})

        captured = {}
        def fake_replace(symbol, position, new_sl):
            captured['new_sl'] = new_sl
            position['sl_algo_id'] = 'new-algo'
            position['protection_state'] = 'protected'
            position['sl_sync_state'] = 'active'
            return True
        ex._replace_protective_sl = fake_replace
        ex._save_positions = MagicMock()
        ex.risk_manager.record_trade = MagicMock()

        # 普通 reduce: tp_advance=None
        result = ex.reduce_position('BTC-USDT', 0.3)

        assert result.get('ok') is True
        assert result.get('protective_update_state') == 'protected'
        # residual 必须保护(原 SL=95.0)
        assert captured.get('new_sl') == 95.0
        # tp_filled 不变
        assert ex.positions['BTC-USDT']['tp_filled'] == 0


class TestAC3P0006PartialTpProtectionFailed:
    """AC3-P0-006: partial TP reduce 成交但 SL replace fail → 不得报安全."""

    def test_partial_tp_replace_fail_blocks_safety(self):
        ex = _make_executor()
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex.exchange.create_order = MagicMock(return_value={'id': 'r1'})
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 110.0})
        ex._replace_protective_sl = MagicMock(return_value=False)
        ex._save_positions = MagicMock()
        ex.risk_manager.record_trade = MagicMock()

        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        # reduce 成交后 tp_filled 可以推进
        assert ex.positions['BTC-USDT']['tp_filled'] == 1
        # 但 protection_state 必须不是 protected
        assert ex.positions['BTC-USDT']['protection_state'] == 'unknown'
        assert ex.positions['BTC-USDT']['sl_sync_state'] == 'failed'
        assert ex.positions['BTC-USDT'].get('partial_tp_state') == 'protection_failed'
        assert result.get('protective_update_state') == 'replace_failed'
        # ok 必须为 False(reduce_ok=True 但 replace_ok=False → ok=False)
        assert result.get('ok') is False
        assert result.get('reduce_ok') is True
        assert result.get('replace_ok') is False

    def test_partial_tp_replace_fail_blocks_add_to_position(self):
        ex = _make_executor()
        _seed_long(ex)
        ex.positions['BTC-USDT']['protection_state'] = 'unknown'

        # add_to_position 必须因 protection_state != protected 拒绝
        # mock 必要的依赖
        ex.is_symbol_halted = MagicMock(return_value=False)
        ex.can_open_new_okx = MagicMock(return_value=True)
        ex._normalize_symbol = lambda s: s

        result = ex.add_to_position('BTC-USDT', 'long', 0.3)
        assert result is None


class TestAC3P0007DustFullClose:
    """AC3-P0-007: dust 全平不重挂 SL."""

    def test_dust_skip_replace(self):
        ex = _make_executor()
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex.exchange.create_order = MagicMock(return_value={'id': 'r1'})
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 110.0})
        # min amount 大到触发 dust 判定
        ex.exchange.market = MagicMock(return_value={
            'contractSize': 1, 'limits': {'amount': {'min': 10.0}},
        })
        ex._replace_protective_sl = MagicMock(return_value=True)
        ex._save_positions = MagicMock()
        ex.risk_manager.record_trade = MagicMock()

        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        assert result.get('ok') is True
        assert result.get('protective_update_state') == 'dust_closed'
        assert 'BTC-USDT' not in ex.positions
        ex._replace_protective_sl.assert_not_called()


class TestAC3P0008ExitLockCovered:
    """AC3-P0-008: exit lock 必须覆盖 reduce 的整个 protection update."""

    def test_concurrent_close_blocks_reduce(self):
        ex = _make_executor()
        _seed_long(ex)
        # 占用 close lock
        ex._exit_locks['BTC-USDT'] = {
            'kind': 'close', 'action_id': 'close-running',
            'started_at': 0,
        }
        ex._cancel_protective_sl = MagicMock()
        ex.exchange.create_order = MagicMock()

        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        assert result is None  # exit_locked 返回 None(idempotent)
        ex._cancel_protective_sl.assert_not_called()
        ex.exchange.create_order.assert_not_called()

    def test_lock_released_after_protection_update(self):
        ex = _make_executor()
        _seed_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex.exchange.create_order = MagicMock(return_value={'id': 'r1'})
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 110.0})
        ex._replace_protective_sl = MagicMock(return_value=True)
        ex._save_positions = MagicMock()
        ex.risk_manager.record_trade = MagicMock()

        ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)

        # 锁必须释放,允许下一动作
        a, _ = ex._try_acquire_exit_lock('BTC-USDT', 'reduce', 'next')
        assert a == 'acquired'
