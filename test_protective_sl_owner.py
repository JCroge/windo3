"""保护单 owner 单一入口单测 (FR-001 / FR-002 / FR-003)。

覆盖 docs/audit_remediation_20260528_acceptance.md:
- AC-P0-001/002/003: EarlyReview 经由 ContractExecutor.move_protective_sl,
  替换失败保留旧 stop_loss
- AC-P0-004/005/006: _replace_protective_sl cancel failure fail-closed
  (在 test_partial_tp_lifecycle.py::TestProtectiveSlSingleEntry 已覆盖,
  此处仅做 move_protective_sl 调用矩阵)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _run_async(coro):
    """asyncio.run 会清空全局 event loop 影响 pytest-asyncio 后续测试,
    用临时 loop 跑完后恢复原 policy 默认 (None)。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


from executor import ContractExecutor


def _make_executor() -> ContractExecutor:
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger('test_protective_sl_owner')
    ex.exchange_id = 'okx'
    ex.testnet = True
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
    ex.positions_file = '/tmp/_test_protective_sl_positions.json'
    ex._sl_check_failures = {}
    ex._sl_max_failures = 5
    ex._last_sl_update = {}
    ex._okx_pos_mode = 'net_mode'
    ex._okx_pos_mode_source = 'test'
    ex._halted_symbols = {}
    ex._exit_lock_mu = threading.Lock()
    ex._exit_locks = {}
    return ex


def _local_long(ex, sl=95.0):
    ex.positions['BTC-USDT'] = {
        'symbol': 'BTC-USDT', 'side': 'long',
        'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
        'leverage': 1,
        'stop_loss': sl, 'original_sl': sl,
        'take_profit': 110.0,
        'sl_order_id': 'old-algo', 'sl_algo_id': 'old-algo',
        'sl_algo_clord_id': 'old-clord',
        'sl_sync_state': 'active', 'protection_state': 'protected',
        'tp_filled': 0,
    }


class TestMoveProtectiveSL:
    """FR-001: ContractExecutor.move_protective_sl 公开入口契约。"""

    def test_success_updates_local_stop_loss_and_returns_ok(self):
        """AC-P0-003: 成功替换后 root executor 同步更新本地 stop_loss
        + sl_algo_id + protection_state。"""
        ex = _make_executor()
        _local_long(ex)
        ex._save_positions = MagicMock()
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex._place_protective_sl = MagicMock(return_value='new-algo')

        result = ex.move_protective_sl(
            'BTC-USDT', 96.0, reason='early_review_tighten',
        )

        assert result['ok'] is True
        assert result['operation'] == 'move_protective_sl'
        assert result['old_sl_algo_id'] == 'old-algo'
        assert result['new_sl_algo_id'] == 'new-algo'
        assert result['old_stop_loss'] == 95.0
        assert result['new_stop_loss'] == 96.0
        assert result['cancel_ok'] is True
        assert result['place_ok'] is True
        assert result['sl_sync_state'] == 'active'
        assert result['protection_state'] == 'protected'
        assert result['halt_required'] is False
        assert result['reason'] == 'early_review_tighten'

        pos = ex.positions['BTC-USDT']
        assert pos['stop_loss'] == 96.0
        assert pos['sl_algo_id'] == 'new-algo'
        assert pos['protection_state'] == 'protected'
        assert pos['last_protection_update_reason'] == 'early_review_tighten'
        ex._save_positions.assert_called()

    def test_cancel_failure_keeps_old_local_stop_loss(self):
        """AC-P0-002: 撤旧 SL 失败时本地 stop_loss 必须保持旧值,
        不能因为本地代码以为'已收紧'而误认为有保护。"""
        ex = _make_executor()
        ex.testnet = False
        _local_long(ex, sl=95.0)
        ex._save_positions = MagicMock()
        ex._halt_symbol = MagicMock()
        ex._cancel_protective_sl = MagicMock(return_value=False)
        ex._place_protective_sl = MagicMock()

        result = ex.move_protective_sl(
            'BTC-USDT', 96.0, reason='early_review_tighten',
        )

        assert result['ok'] is False
        assert result['halt_required'] is True
        # 本地 stop_loss 必须保持 95.0,而不是 96.0
        pos = ex.positions['BTC-USDT']
        assert pos['stop_loss'] == 95.0
        assert pos['sl_sync_state'] == 'failed'
        assert pos['protection_state'] == 'unknown'
        assert pos['last_protection_error'] == 'sl_cancel_failed'
        # 没有调用 _place
        ex._place_protective_sl.assert_not_called()
        # live OKX 触发 halt
        ex._halt_symbol.assert_called_once()
        assert ex._halt_symbol.call_args.kwargs.get('reason') == 'sl_cancel_failed'

    def test_place_failure_keeps_old_local_stop_loss(self):
        """撤旧成功但新 SL 挂单失败时,本地 stop_loss 也必须保持旧值。"""
        ex = _make_executor()
        ex.testnet = False
        _local_long(ex, sl=95.0)
        ex._save_positions = MagicMock()
        ex._halt_symbol = MagicMock()
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex._place_protective_sl = MagicMock(return_value=None)

        result = ex.move_protective_sl(
            'BTC-USDT', 96.0, reason='early_review_tighten',
        )

        assert result['ok'] is False
        assert result['halt_required'] is True
        pos = ex.positions['BTC-USDT']
        assert pos['stop_loss'] == 95.0
        assert pos['sl_sync_state'] == 'failed'
        assert pos['protection_state'] == 'unknown'
        assert pos['last_protection_error'] == 'sl_place_failed'
        ex._halt_symbol.assert_called_once()
        assert ex._halt_symbol.call_args.kwargs.get('reason') == 'sl_replace_failed'

    def test_missing_position_returns_position_missing(self):
        ex = _make_executor()
        result = ex.move_protective_sl('BTC-USDT', 96.0, reason='x')
        assert result['ok'] is False
        assert result['reason'] == 'position_missing'

    def test_invalid_new_sl_returns_invalid(self):
        ex = _make_executor()
        _local_long(ex)
        result = ex.move_protective_sl('BTC-USDT', 0.0, reason='x')
        assert result['ok'] is False
        assert result['reason'] == 'invalid_new_sl'

        result = ex.move_protective_sl('BTC-USDT', None, reason='x')
        assert result['ok'] is False
        assert result['reason'] == 'invalid_new_sl'

    def test_short_side_works(self):
        """short 持仓也必须能走同一入口。"""
        ex = _make_executor()
        ex.positions['ETH-USDT'] = {
            'symbol': 'ETH-USDT', 'side': 'short',
            'entry_price': 2000.0, 'amount': 0.5, 'amount_usdt': 1000.0,
            'leverage': 1,
            'stop_loss': 2050.0, 'original_sl': 2050.0,
            'take_profit': 1900.0,
            'sl_order_id': 'old', 'sl_algo_id': 'old',
            'sl_sync_state': 'active', 'protection_state': 'protected',
        }
        ex._save_positions = MagicMock()
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex._place_protective_sl = MagicMock(return_value='new')

        result = ex.move_protective_sl(
            'ETH-USDT', 2030.0, reason='early_review_tighten',
        )
        assert result['ok'] is True
        assert ex.positions['ETH-USDT']['stop_loss'] == 2030.0


class TestEarlyReviewWiring:
    """FR-001: agent 层 _early_review 必须走 move_protective_sl,
    不得直接修改 pos['stop_loss']。"""

    def _make_agent(self):
        from agents.trading.executor import MultiExecutor
        agent = MultiExecutor.__new__(MultiExecutor)
        agent.logger = logging.getLogger('test_early_review')
        agent.config = {'early_review_enabled': True}
        agent.executor = MagicMock()
        agent.executor.positions = {}
        agent._early_review_times = {}
        return agent

    def test_early_review_calls_move_protective_sl_when_tightening(self):
        """AC-P0-001: -0.5R + 20min 时调用 move_protective_sl 一次。"""
        agent = self._make_agent()
        pos = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'stop_loss': 95.0,
        }
        # 当前价 97.0:pnl_dist=-3, sl_dist=5, R=-0.6
        agent.executor.exchange.fetch_ticker = MagicMock(
            return_value={'last': 97.0}
        )
        agent.executor.move_protective_sl = MagicMock(
            return_value={'ok': True, 'reason': 'ok',
                          'protection_state': 'protected'}
        )

        _run_async(agent._early_review('BTC-USDT', pos, minutes_held=25))

        agent.executor.move_protective_sl.assert_called_once()
        call = agent.executor.move_protective_sl.call_args
        assert call.args[0] == 'BTC-USDT'
        # 新 SL = entry - sl_dist*0.7 = 100 - 3.5 = 96.5
        assert abs(call.args[1] - 96.5) < 1e-9
        assert 'early_review_tighten' in call.kwargs.get('reason', '')

    def test_early_review_does_not_directly_modify_stop_loss(self):
        """AC-P0-002: move_protective_sl 失败时,_early_review 不得自己改 pos。
        本测试通过验证 agent 不调用 _save_positions 也不写 pos['stop_loss']
        来保证。"""
        agent = self._make_agent()
        pos = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'stop_loss': 95.0,
        }
        agent.executor.exchange.fetch_ticker = MagicMock(
            return_value={'last': 97.0}
        )
        agent.executor.move_protective_sl = MagicMock(
            return_value={'ok': False, 'reason': 'sl_cancel_failed',
                          'protection_state': 'unknown',
                          'halt_required': True}
        )
        agent.executor._save_positions = MagicMock()

        _run_async(agent._early_review('BTC-USDT', pos, minutes_held=25))

        # 本地 pos['stop_loss'] 没被 agent 改
        assert pos['stop_loss'] == 95.0
        # _save_positions 不被 agent 直接调用 (root executor 内部会处理)
        agent.executor._save_positions.assert_not_called()

    def test_early_review_throttled_within_120s(self):
        agent = self._make_agent()
        agent._early_review_times['_er_BTC-USDT'] = time.time()
        pos = {'symbol': 'BTC-USDT', 'side': 'long',
               'entry_price': 100.0, 'stop_loss': 95.0}
        agent.executor.move_protective_sl = MagicMock()
        _run_async(agent._early_review('BTC-USDT', pos, minutes_held=25))
        agent.executor.move_protective_sl.assert_not_called()

    def test_early_review_short_path_works(self):
        agent = self._make_agent()
        pos = {'symbol': 'ETH-USDT', 'side': 'short',
               'entry_price': 2000.0, 'stop_loss': 2050.0}
        # 价格 2030: pnl_dist=-30, sl_dist=50, R=-0.6
        agent.executor.exchange.fetch_ticker = MagicMock(
            return_value={'last': 2030.0}
        )
        agent.executor.move_protective_sl = MagicMock(
            return_value={'ok': True, 'reason': 'ok',
                          'protection_state': 'protected'}
        )
        _run_async(agent._early_review('ETH-USDT', pos, minutes_held=25))
        agent.executor.move_protective_sl.assert_called_once()
        new_sl = agent.executor.move_protective_sl.call_args.args[1]
        # 新 SL = entry + sl_dist*0.7 = 2000 + 35 = 2035
        assert abs(new_sl - 2035.0) < 1e-9

    def test_early_review_below_threshold_no_op(self):
        agent = self._make_agent()
        pos = {'symbol': 'BTC-USDT', 'side': 'long',
               'entry_price': 100.0, 'stop_loss': 95.0}
        # 价格 99: R=-0.2 (>-0.5,不应触发)
        agent.executor.exchange.fetch_ticker = MagicMock(
            return_value={'last': 99.0}
        )
        agent.executor.move_protective_sl = MagicMock()
        _run_async(agent._early_review('BTC-USDT', pos, minutes_held=25))
        agent.executor.move_protective_sl.assert_not_called()
