"""分批止盈生命周期收敛单测。

覆盖 docs/partial_tp_lifecycle_acceptance.md 中的自动化 case:
- AC-A1: OKX 开仓 attach 不含 TP (已在 test_okx_posmode_executor.py 覆盖)
- AC-A2: TP1 后不再被 legacy scalar take_profit 全平 (本文件)
- AC-A3: tp_filled 仅在 reduce 成功后更新 (待实现)
- AC-A10: long/short 对称回归 (本文件)
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


def _make_executor() -> ContractExecutor:
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger('test_partial_tp_lifecycle')
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
    ex.positions_file = '/tmp/_test_partial_tp_positions.json'
    ex._sl_check_failures = {}
    ex._sl_max_failures = 5
    ex._last_sl_update = {}
    ex._okx_pos_mode = 'net_mode'
    ex._okx_pos_mode_source = 'test'
    ex._halted_symbols = {}
    ex._exit_lock_mu = threading.Lock()
    ex._exit_locks = {}
    return ex


def _set_price(ex, price: float):
    ex._fetch_price_robust = MagicMock(return_value=price)


class TestLegacyTakeProfitGate:
    """AC-A2: TP1 已减仓后,下一轮不得因 take_profit==tp1 触发全平。"""

    def test_long_tp1_filled_then_above_tp1_no_full_close(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 0.5, 'amount_usdt': 50.0,
            'leverage': 1,
            'stop_loss': 95.0, 'original_sl': 95.0,
            'take_profit': 110.0,
            'take_profit_levels': [110.0, 120.0, 130.0],
            'tp_filled': 1,
            'highest_price': 111.0, 'lowest_price': 100.0,
            'atr_pct': 0.02,
        }
        _set_price(ex, 111.0)
        result = ex.check_stop_loss_take_profit('BTC-USDT')
        assert result != 'take_profit', \
            f'TP1 已减仓后不得返回 take_profit 全平,实得 {result}'

    def test_short_tp1_filled_then_below_tp1_no_full_close(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'short',
            'entry_price': 100.0, 'amount': 0.5, 'amount_usdt': 50.0,
            'leverage': 1,
            'stop_loss': 105.0, 'original_sl': 105.0,
            'take_profit': 90.0,
            'take_profit_levels': [90.0, 80.0, 70.0],
            'tp_filled': 1,
            'highest_price': 100.0, 'lowest_price': 89.0,
            'atr_pct': 0.02,
        }
        _set_price(ex, 89.0)
        result = ex.check_stop_loss_take_profit('BTC-USDT')
        assert result != 'take_profit', \
            f'short TP1 已减仓后不得返回 take_profit 全平,实得 {result}'

    def test_long_first_hit_returns_partial_tp_1(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 0.5, 'amount_usdt': 50.0,
            'leverage': 1,
            'stop_loss': 95.0, 'original_sl': 95.0,
            'take_profit': 110.0,
            'take_profit_levels': [110.0, 120.0, 130.0],
            'tp_filled': 0,
            'highest_price': 100.0, 'lowest_price': 100.0,
            'atr_pct': 0.02,
            'sl_order_id': None,
        }
        ex._move_sl = MagicMock()
        ex._save_positions = MagicMock()
        _set_price(ex, 110.5)
        result = ex.check_stop_loss_take_profit('BTC-USDT')
        assert result == 'partial_tp_1', \
            f'TP1 首次命中应返回 partial_tp_1,实得 {result}'

    def test_legacy_scalar_tp_still_fires_when_no_levels(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 0.5, 'amount_usdt': 50.0,
            'leverage': 1,
            'stop_loss': 95.0, 'original_sl': 95.0,
            'take_profit': 110.0,
            'highest_price': 100.0, 'lowest_price': 100.0,
            'atr_pct': 0.02,
        }
        _set_price(ex, 110.5)
        result = ex.check_stop_loss_take_profit('BTC-USDT')
        assert result == 'take_profit', \
            f'无 take_profit_levels 的 legacy 持仓仍应走 scalar TP 全平,实得 {result}'

    def test_stop_loss_fires_regardless_of_tp_levels(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 0.5, 'amount_usdt': 50.0,
            'leverage': 1,
            'stop_loss': 95.0, 'original_sl': 95.0,
            'take_profit': 110.0,
            'take_profit_levels': [110.0, 120.0, 130.0],
            'tp_filled': 1,
            'highest_price': 111.0, 'lowest_price': 95.0,
            'atr_pct': 0.02,
        }
        _set_price(ex, 94.5)
        result = ex.check_stop_loss_take_profit('BTC-USDT')
        assert result == 'stop_loss'


class TestTpFilledOnlyAdvancesAfterReduce:
    """AC-A3: reduce 失败时 tp_filled 不得递增,SL 不得被锁利位前移。"""

    def _base_long(self, ex):
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
            'leverage': 1,
            'stop_loss': 95.0, 'original_sl': 95.0,
            'take_profit': 110.0,
            'take_profit_levels': [110.0, 120.0, 130.0],
            'tp_filled': 0,
            'highest_price': 110.5, 'lowest_price': 100.0,
            'atr_pct': 0.02,
            'sl_order_id': None,
        }

    def test_update_trailing_does_not_mutate_state(self):
        ex = _make_executor()
        self._base_long(ex)
        ex._move_sl = MagicMock()
        ex._save_positions = MagicMock()
        result = ex._update_trailing('BTC-USDT', ex.positions['BTC-USDT'], 110.5)
        assert result == 'partial_tp_1'
        assert ex.positions['BTC-USDT']['tp_filled'] == 0, \
            'TP1 信号阶段不得提前推进 tp_filled'
        assert ex.positions['BTC-USDT']['stop_loss'] == 95.0, \
            'TP1 信号阶段不得提前移动 SL'
        ex._move_sl.assert_not_called()

    def test_reduce_failure_keeps_tp_filled_zero(self):
        ex = _make_executor()
        self._base_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex.exchange.create_order = MagicMock(side_effect=Exception('mock reject'))
        ex._handle_okx_close_reject = MagicMock(return_value={'status': 'still_open'})
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex._replace_protective_sl = MagicMock(return_value=True)
        ex._save_positions = MagicMock()
        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)
        # FR-3A: reduce reject 后返回结构化 result(ok=False),不再 None
        assert result is not None and result.get('ok') is False
        assert result.get('reduce_ok') is False
        assert ex.positions['BTC-USDT']['tp_filled'] == 0, \
            'reduce 失败时 tp_filled 必须保持 0'
        assert ex.positions['BTC-USDT']['stop_loss'] == 95.0, \
            'reduce 失败时 SL 不得被锁利位前移'

    def test_reduce_success_advances_tp_filled_and_moves_sl(self):
        ex = _make_executor()
        self._base_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex.exchange.create_order = MagicMock(return_value={'id': 'ord1'})
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 110.5})
        ex.exchange.market = MagicMock(return_value={
            'contractSize': 1, 'limits': {'amount': {'min': 1e-8}},
        })
        captured = {}
        def fake_replace(symbol, position, new_sl):
            captured['new_sl'] = new_sl
            position['sl_algo_id'] = 'algo-new'
            position['protection_state'] = 'protected'
            position['sl_sync_state'] = 'active'
            return True
        ex._replace_protective_sl = fake_replace
        ex._save_positions = MagicMock()
        ex.risk_manager.record_trade = MagicMock()
        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)
        assert result is not None and result.get('ok') is True
        assert ex.positions['BTC-USDT']['tp_filled'] == 1, \
            'reduce 成功后 tp_filled 必须推进到 1'
        # entry=100, R=(100-95)/100=0.05, TP1 SL = 100*(1+0.05*0.5) = 102.5
        assert abs(captured['new_sl'] - 102.5) < 1e-6, \
            f'TP1 锁利 SL 应在 entry+0.5R=102.5,实得 {captured["new_sl"]}'
        assert result.get('protective_update_state') == 'protected'

    def test_short_reduce_success_moves_sl_down(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'short',
            'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
            'leverage': 1,
            'stop_loss': 105.0, 'original_sl': 105.0,
            'take_profit': 90.0,
            'take_profit_levels': [90.0, 80.0, 70.0],
            'tp_filled': 0,
            'highest_price': 100.0, 'lowest_price': 89.5,
            'atr_pct': 0.02,
            'sl_order_id': None,
        }
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'short', 'available_contracts': 1.0,
        })
        ex.exchange.create_order = MagicMock(return_value={'id': 'ord2'})
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 89.5})
        ex.exchange.market = MagicMock(return_value={
            'contractSize': 1, 'limits': {'amount': {'min': 1e-8}},
        })
        captured = {}
        def fake_replace(symbol, position, new_sl):
            captured['new_sl'] = new_sl
            position['sl_algo_id'] = 'algo-new'
            position['protection_state'] = 'protected'
            position['sl_sync_state'] = 'active'
            return True
        ex._replace_protective_sl = fake_replace
        ex._save_positions = MagicMock()
        ex.risk_manager.record_trade = MagicMock()
        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)
        assert result is not None and result.get('ok') is True
        assert ex.positions['BTC-USDT']['tp_filled'] == 1
        # short: entry=100, R=0.05, TP1 SL = 100*(1-0.05*0.5)=97.5
        assert abs(captured['new_sl'] - 97.5) < 1e-6, \
            f'short TP1 锁利 SL 应在 entry-0.5R=97.5,实得 {captured["new_sl"]}'
        assert result.get('protective_update_state') == 'protected'

    def test_normal_reduce_without_tp_advance_keeps_tp_filled(self):
        ex = _make_executor()
        self._base_long(ex)
        ex.positions['BTC-USDT']['tp_filled'] = 0
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex.exchange.create_order = MagicMock(return_value={'id': 'ord3'})
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 100.0})
        ex.exchange.market = MagicMock(return_value={
            'contractSize': 1, 'limits': {'amount': {'min': 1e-8}},
        })
        captured = {}
        def fake_replace(symbol, position, new_sl):
            captured['new_sl'] = new_sl
            position['sl_algo_id'] = 'algo-resized'
            position['protection_state'] = 'protected'
            position['sl_sync_state'] = 'active'
            return True
        ex._replace_protective_sl = fake_replace
        ex._save_positions = MagicMock()
        ex.risk_manager.record_trade = MagicMock()
        result = ex.reduce_position('BTC-USDT', 0.3)
        assert result is not None and result.get('ok') is True
        assert ex.positions['BTC-USDT']['tp_filled'] == 0, \
            '不传 tp_advance 的减仓(RiskGuard)不得改 tp_filled'
        # FR-3A: 普通 reduce 也必须 resize residual SL,使用原 stop_loss
        assert abs(captured['new_sl'] - 95.0) < 1e-6, \
            f'普通 reduce 应保持原 SL=95.0,实得 {captured.get("new_sl")}'


class TestExitLock:
    """AC-A6: 同 symbol 退出动作必须串行,partial_tp / risk_reduce / local_stop
    并发到达时,只允许第一个进入,其他 exit_locked。"""

    def _make_long(self, ex):
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
            'sl_order_id': None,
        }

    def test_acquire_release_cycle(self):
        ex = _make_executor()
        a, _ = ex._try_acquire_exit_lock('X', 'close', 'aid-1')
        assert a == 'acquired'
        # 第二个不同动作必须被拒
        b, holder = ex._try_acquire_exit_lock('X', 'reduce', 'aid-2')
        assert b == 'locked'
        assert holder['kind'] == 'close' and holder['action_id'] == 'aid-1'
        # 同 action_id 重入返回 reentrant
        c, _ = ex._try_acquire_exit_lock('X', 'close', 'aid-1')
        assert c == 'reentrant'
        # 释放后再次可获取
        ex._release_exit_lock('X', 'aid-1')
        d, _ = ex._try_acquire_exit_lock('X', 'reduce', 'aid-2')
        assert d == 'acquired'

    def test_release_only_self(self):
        ex = _make_executor()
        ex._try_acquire_exit_lock('X', 'close', 'aid-1')
        # 用错的 action_id 释放不应解锁
        ex._release_exit_lock('X', 'aid-other')
        a, _ = ex._try_acquire_exit_lock('X', 'reduce', 'aid-2')
        assert a == 'locked'

    def test_close_blocks_concurrent_reduce(self):
        ex = _make_executor()
        self._make_long(ex)
        # 手动占用锁(模拟另一动作正在跑)
        ex._exit_locks['BTC-USDT'] = {
            'kind': 'close', 'action_id': 'other-close',
            'started_at': 0,
        }
        ex.exchange.create_order = MagicMock(return_value={'id': 'should-not-fire'})
        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)
        assert result is None, 'reduce 应被 exit_locked 拒绝'
        ex.exchange.create_order.assert_not_called()
        # 仓位状态未被污染
        assert ex.positions['BTC-USDT']['tp_filled'] == 0

    def test_reduce_blocks_concurrent_close(self):
        ex = _make_executor()
        self._make_long(ex)
        ex._exit_locks['BTC-USDT'] = {
            'kind': 'partial_tp_1', 'action_id': 'tp1-running',
            'started_at': 0,
        }
        ex.exchange.create_order = MagicMock(return_value={'id': 'should-not-fire'})
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 100.0})
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        result = ex.close_position('BTC-USDT')
        assert result is None, 'close 应被 exit_locked 拒绝'
        ex.exchange.create_order.assert_not_called()
        assert 'BTC-USDT' in ex.positions, 'close 被拒后本地仓位不得删除'

    def test_lock_released_on_success(self):
        ex = _make_executor()
        self._make_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex.exchange.create_order = MagicMock(return_value={'id': 'ord-ok'})
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 110.5})
        ex.exchange.market = MagicMock(return_value={
            'contractSize': 1, 'limits': {'amount': {'min': 1e-8}},
        })
        ex._replace_protective_sl = MagicMock(return_value=True)
        ex._save_positions = MagicMock()
        ex.risk_manager.record_trade = MagicMock()
        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)
        assert result is not None and result.get('ok') is True
        # 释放后下一动作必须能拿锁
        a, _ = ex._try_acquire_exit_lock('BTC-USDT', 'reduce', 'next')
        assert a == 'acquired'

    def test_lock_released_on_failure(self):
        ex = _make_executor()
        self._make_long(ex)
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex.exchange.create_order = MagicMock(side_effect=Exception('boom'))
        ex._handle_okx_close_reject = MagicMock(return_value={'status': 'still_open'})
        ex._replace_protective_sl = MagicMock(return_value=False)
        ex._save_positions = MagicMock()
        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)
        # FR-3A: reduce reject 后返回结构化 result(ok=False),不再返回 None
        assert result is not None and result.get('ok') is False
        assert result.get('reduce_ok') is False
        assert result.get('reason') == 'reduce_rejected'
        # 异常路径也应释放锁
        a, _ = ex._try_acquire_exit_lock('BTC-USDT', 'reduce', 'next')
        assert a == 'acquired'

    def test_explicit_action_id_idempotent_reentry(self):
        ex = _make_executor()
        self._make_long(ex)
        ex._exit_locks['BTC-USDT'] = {
            'kind': 'partial_tp_1', 'action_id': 'aid-tp1',
            'started_at': 0,
        }
        ex.exchange.create_order = MagicMock(return_value={'id': 'should-not-fire'})
        # 同 action_id 重入: 返回 None(idempotent),不触发新订单
        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1, action_id='aid-tp1')
        assert result is None
        ex.exchange.create_order.assert_not_called()


class TestPrecisionAndDust:
    """AC-A9: 精度后 reduce_amount=0 不推进 tp_filled; dust 全平不留尾仓 SL。"""

    def test_zero_precision_reduce_does_not_advance_tp(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 0.0001, 'amount_usdt': 0.01,
            'leverage': 1,
            'stop_loss': 95.0, 'original_sl': 95.0,
            'take_profit': 110.0,
            'take_profit_levels': [110.0, 120.0, 130.0],
            'tp_filled': 0,
            'highest_price': 110.5, 'lowest_price': 100.0,
            'atr_pct': 0.02,
            'sl_order_id': None,
        }
        # precision 后变 0
        ex.exchange.amount_to_precision = lambda s, a: 0.0
        ex.exchange.create_order = MagicMock(return_value={'id': 'should-not-fire'})
        ex._replace_protective_sl = MagicMock(return_value=False)
        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)
        # FR-3A: zero-amount 早返回 ok=False/reason=reduce_amount_zero,不再 None
        assert result is not None and result.get('ok') is False
        assert result.get('reason') == 'reduce_amount_zero'
        assert ex.positions['BTC-USDT']['tp_filled'] == 0, \
            'reduce_amount 精度后为 0 不得推进 tp_filled'
        ex.exchange.create_order.assert_not_called()
        ex._replace_protective_sl.assert_not_called()
        # 锁也应释放
        a, _ = ex._try_acquire_exit_lock('BTC-USDT', 'reduce', 'next')
        assert a == 'acquired'

    def test_dust_remainder_full_closes_and_skips_tp_advance(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
            'leverage': 1,
            'stop_loss': 95.0, 'original_sl': 95.0,
            'take_profit': 110.0,
            'take_profit_levels': [110.0, 120.0, 130.0],
            'tp_filled': 0,
            'highest_price': 110.5, 'lowest_price': 100.0,
            'atr_pct': 0.02,
            'sl_order_id': None,
        }
        ex._fetch_okx_position_state = MagicMock(return_value={
            'side': 'long', 'available_contracts': 1.0,
        })
        ex.exchange.create_order = MagicMock(return_value={'id': 'ord-full'})
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': 110.5})
        # 把 min amount 调到很大,触发 dust 判定
        ex.exchange.market = MagicMock(return_value={
            'contractSize': 1, 'limits': {'amount': {'min': 10.0}},
        })
        ex._replace_protective_sl = MagicMock(return_value=True)
        ex._save_positions = MagicMock()
        ex.risk_manager.record_trade = MagicMock()
        result = ex.reduce_position('BTC-USDT', 0.5, tp_advance=1)
        assert result is not None and result.get('ok') is True
        assert result.get('protective_update_state') == 'dust_closed'
        # dust 路径下本地仓位被删除
        assert 'BTC-USDT' not in ex.positions
        # 已无尾仓,不应再调 _replace_protective_sl 重挂保护单
        ex._replace_protective_sl.assert_not_called()


class TestProtectiveSlSingleEntry:
    """FR-04: SL cancel/place 必须经由 _replace_protective_sl 单一入口。"""

    def _base(self, ex):
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
            'leverage': 1,
            'stop_loss': 95.0, 'original_sl': 95.0,
            'take_profit': 110.0,
            'sl_order_id': 'old-algo', 'sl_algo_id': 'old-algo',
            'sl_algo_clord_id': 'old-clord',
            'sl_sync_state': 'active', 'protection_state': 'protected',
            'highest_price': 100.0, 'lowest_price': 100.0,
            'tp_filled': 0, 'atr_pct': 0.02,
        }

    def test_replace_cancels_old_and_places_new(self):
        ex = _make_executor()
        self._base(ex)
        cancel_calls = []
        ex._cancel_protective_sl = lambda symbol, pos: cancel_calls.append(
            (symbol, pos.get('sl_algo_id'))
        ) or True
        ex._place_protective_sl = MagicMock(return_value='new-algo')
        ok = ex._replace_protective_sl('BTC-USDT', ex.positions['BTC-USDT'], 96.0)
        assert ok is True
        assert cancel_calls == [('BTC-USDT', 'old-algo')]
        ex._place_protective_sl.assert_called_once()
        kwargs = ex._place_protective_sl.call_args.kwargs
        assert kwargs['stop_price'] == 96.0
        assert kwargs['side'] == 'long'
        # F4-003: 新挂 SL 走 _make_owner_tag_clord_id,clord_id 必须通过 owner 判定。
        from executor import ContractExecutor
        assert kwargs['clord_id'] is not None
        assert ContractExecutor._is_owner_clord_id(kwargs['clord_id'])
        pos = ex.positions['BTC-USDT']
        assert pos['sl_algo_id'] == 'new-algo'
        assert pos['sl_algo_clord_id'] == kwargs['clord_id']
        assert pos['sl_sync_state'] == 'active'
        assert pos['protection_state'] == 'protected'

    def test_replace_failure_marks_unknown_and_halts_live(self):
        ex = _make_executor()
        ex.testnet = False
        self._base(ex)
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex._place_protective_sl = MagicMock(return_value=None)
        ex._halt_symbol = MagicMock()
        ok = ex._replace_protective_sl('BTC-USDT', ex.positions['BTC-USDT'], 96.0)
        assert ok is False
        pos = ex.positions['BTC-USDT']
        assert pos['sl_algo_id'] is None
        assert pos['protection_state'] == 'unknown'
        assert pos['sl_sync_state'] == 'failed'
        ex._halt_symbol.assert_called_once()
        assert ex._halt_symbol.call_args.kwargs.get('reason') == 'sl_replace_failed'

    def test_replace_failure_testnet_does_not_halt(self):
        ex = _make_executor()
        ex.testnet = True
        self._base(ex)
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex._place_protective_sl = MagicMock(return_value=None)
        ex._halt_symbol = MagicMock()
        ex._replace_protective_sl('BTC-USDT', ex.positions['BTC-USDT'], 96.0)
        ex._halt_symbol.assert_not_called()

    def test_cancel_failure_does_not_place_new_sl(self):
        """AC-P0-004: 撤旧失败必须立即返回,不下新 SL,避免双保护单。"""
        ex = _make_executor()
        ex.testnet = False
        self._base(ex)
        ex._cancel_protective_sl = MagicMock(return_value=False)
        ex._place_protective_sl = MagicMock(return_value='should-not-happen')
        ex._halt_symbol = MagicMock()
        ok = ex._replace_protective_sl('BTC-USDT', ex.positions['BTC-USDT'], 96.0)
        assert ok is False
        ex._place_protective_sl.assert_not_called()

    def test_cancel_failure_marks_failed_and_keeps_old_algo(self):
        """AC-P0-005: 撤旧失败写 sl_sync_state=failed/protection_state=unknown/
        last_protection_error=sl_cancel_failed;旧 algo_id 不能被覆盖
        (旧保护单仍可能在交易所有效)。"""
        ex = _make_executor()
        ex.testnet = False
        self._base(ex)
        ex._cancel_protective_sl = MagicMock(return_value=False)
        ex._place_protective_sl = MagicMock()
        ex._halt_symbol = MagicMock()
        ex._replace_protective_sl('BTC-USDT', ex.positions['BTC-USDT'], 96.0)
        pos = ex.positions['BTC-USDT']
        assert pos['sl_sync_state'] == 'failed'
        assert pos['protection_state'] == 'unknown'
        assert pos['last_protection_error'] == 'sl_cancel_failed'
        # 旧 algo_id 必须保留,因为旧保护单可能仍在交易所
        assert pos['sl_algo_id'] == 'old-algo'
        assert pos['sl_order_id'] == 'old-algo'

    def test_cancel_failure_live_okx_halts(self):
        """AC-P0-006: live OKX 撤旧失败必须 _halt_symbol(reason='sl_cancel_failed')。"""
        ex = _make_executor()
        ex.testnet = False
        ex.exchange_id = 'okx'
        self._base(ex)
        ex._cancel_protective_sl = MagicMock(return_value=False)
        ex._place_protective_sl = MagicMock()
        ex._halt_symbol = MagicMock()
        ex._replace_protective_sl('BTC-USDT', ex.positions['BTC-USDT'], 96.0)
        ex._halt_symbol.assert_called_once()
        assert ex._halt_symbol.call_args.kwargs.get('reason') == 'sl_cancel_failed'

    def test_cancel_failure_testnet_does_not_halt(self):
        """testnet 不 halt,只标 protection_state=unknown 等待运维。"""
        ex = _make_executor()
        ex.testnet = True
        ex.exchange_id = 'okx'
        self._base(ex)
        ex._cancel_protective_sl = MagicMock(return_value=False)
        ex._place_protective_sl = MagicMock()
        ex._halt_symbol = MagicMock()
        ex._replace_protective_sl('BTC-USDT', ex.positions['BTC-USDT'], 96.0)
        ex._halt_symbol.assert_not_called()
        assert ex.positions['BTC-USDT']['sl_sync_state'] == 'failed'

    def test_replace_success_clears_last_protection_error(self):
        """成功替换后必须清掉之前的 last_protection_error,避免误导后续诊断。"""
        ex = _make_executor()
        self._base(ex)
        ex.positions['BTC-USDT']['last_protection_error'] = 'previous_failure'
        ex._cancel_protective_sl = MagicMock(return_value=True)
        ex._place_protective_sl = MagicMock(return_value='new-algo')
        ex._replace_protective_sl('BTC-USDT', ex.positions['BTC-USDT'], 96.0)
        assert 'last_protection_error' not in ex.positions['BTC-USDT']

    def test_move_sl_skips_throttle_when_no_protection(self):
        """reduce 之后 sl_algo_id 被清空,_move_sl 必须立即重挂,不受 30s 节流。"""
        ex = _make_executor()
        self._base(ex)
        ex.positions['BTC-USDT']['sl_algo_id'] = None
        ex.positions['BTC-USDT']['sl_order_id'] = None
        ex._last_sl_update['BTC-USDT'] = 9999999999.0  # 假装刚更新过
        ex._replace_protective_sl = MagicMock(return_value=True)
        ex._save_positions = MagicMock()
        # change_pct=0 但无保护,应仍重挂
        ex._move_sl('BTC-USDT', ex.positions['BTC-USDT'], 95.0)
        ex._replace_protective_sl.assert_called_once()


class TestProtectionFailureFlow:
    """FR-05: 保护单失败时阻断 add/open。"""

    def test_add_blocked_when_protection_unknown(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
            'leverage': 1, 'stop_loss': 95.0,
            'protection_state': 'unknown',
        }
        ex.exchange.create_order = MagicMock()
        result = ex.add_to_position('BTC-USDT', 'long', size_pct=0.3)
        assert result is None
        ex.exchange.create_order.assert_not_called()

    def test_add_blocked_when_protection_local_fallback(self):
        ex = _make_executor()
        ex.positions['BTC-USDT'] = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
            'leverage': 1, 'stop_loss': 95.0,
            'protection_state': 'local_fallback',
        }
        ex.exchange.create_order = MagicMock()
        result = ex.add_to_position('BTC-USDT', 'long', size_pct=0.3)
        assert result is None
        ex.exchange.create_order.assert_not_called()


class TestAttachedSlVerification:
    def test_attached_sl_second_attempt_marks_protected_without_halt(self):
        ex = _make_executor()
        ex.exchange_id = "okx"
        ex.testnet = False
        ex._halt_symbol = MagicMock()
        ex._resolve_attached_sl_algo_id = MagicMock(side_effect=[None, "algo-1"])
        ex._list_pending_algos = MagicMock(return_value=[])

        algo_id = ex._verify_attached_sl_after_fill(
            "BTC-USDT-SWAP", "clord-1", attempts=2, sleep_sec=0
        )

        assert algo_id == "algo-1"
        ex._halt_symbol.assert_not_called()

    def test_attached_sl_fallback_matches_pending_algo(self):
        ex = _make_executor()
        ex.exchange_id = "okx"
        ex.testnet = False
        ex._resolve_attached_sl_algo_id = MagicMock(return_value=None)
        ex._list_pending_algos = MagicMock(return_value=[{
            "algoId": "algo-2",
            "algoClOrdId": "clord-2",
            "sl_trigger": "101.5",
            "tp_trigger": "",
        }])

        algo_id = ex._verify_attached_sl_after_fill(
            "BTC-USDT-SWAP", "clord-2", attempts=1, sleep_sec=0
        )

        assert algo_id == "algo-2"

    def test_attached_sl_missing_after_attempts_returns_none(self):
        ex = _make_executor()
        ex.exchange_id = "okx"
        ex.testnet = False
        ex._resolve_attached_sl_algo_id = MagicMock(return_value=None)
        ex._list_pending_algos = MagicMock(return_value=[])

        algo_id = ex._verify_attached_sl_after_fill(
            "BTC-USDT-SWAP", "clord-missing", attempts=2, sleep_sec=0
        )

        assert algo_id is None
        assert ex._resolve_attached_sl_algo_id.call_count == 2


class TestAlgoMigration:
    """AC-A7: 启动期/sync 时存量 OKX algo 迁移到 single-owner。"""

    def _local_long(self, ex):
        ex.positions['BTC-USDT-SWAP'] = {
            'symbol': 'BTC-USDT-SWAP', 'side': 'long',
            'entry_price': 100.0, 'amount': 1.0, 'amount_usdt': 100.0,
            'leverage': 1,
            'stop_loss': 95.0, 'original_sl': 95.0,
            'take_profit': 110.0,
            'sl_order_id': None, 'sl_algo_id': None,
            'sl_algo_clord_id': None,
            'sl_sync_state': 'unknown', 'protection_state': 'unprotected',
            'exit_owner': 'local_partial_tp_exchange_sl',
            'tp_filled': 0,
        }

    def test_cancel_residual_tp_and_match_sl(self):
        """AC-A7 主路径: 撤 TP algo,SL algo 归属本地。"""
        ex = _make_executor()
        self._local_long(ex)
        ex._save_positions = MagicMock()
        # 模拟 pending: 1 TP + 1 SL,均匹配 BTC-USDT-SWAP
        ex.exchange.private_get_trade_orders_algo_pending = MagicMock(
            return_value={'data': [
                {'algoId': 'tp-1', 'algoClOrdId': 'tp-clord',
                 'instId': 'BTC-USDT-SWAP', 'side': 'sell', 'posSide': 'net',
                 'tpTriggerPx': '110', 'slTriggerPx': '0',
                 'ordType': 'conditional'},
                {'algoId': 'sl-1', 'algoClOrdId': 'sl-clord',
                 'instId': 'BTC-USDT-SWAP', 'side': 'sell', 'posSide': 'net',
                 'tpTriggerPx': '0', 'slTriggerPx': '94',
                 'ordType': 'conditional'},
            ]}
        )
        cancelled = []
        ex.exchange.cancel_orders = MagicMock(
            side_effect=lambda ids, symbol, params=None: cancelled.append(
                {'ids': list(ids), 'symbol': symbol, 'params': params or {}}
            ) or [{'id': i, 'status': 'canceled'} for i in ids]
        )
        summary = ex._migrate_okx_algos_for_symbol('BTC-USDT-SWAP')
        # TP algo 被撤
        assert summary['cancelled_tp'] == 1
        assert any(
            'tp-1' in entry['ids'] for entry in cancelled
        )
        # SL algo 归属
        pos = ex.positions['BTC-USDT-SWAP']
        assert pos['sl_algo_id'] == 'sl-1'
        assert pos['sl_algo_clord_id'] == 'sl-clord'
        assert pos['protection_state'] == 'protected'
        assert pos['sl_sync_state'] == 'active'
        # SL trigger 同步到本地 stop_loss
        assert abs(pos['stop_loss'] - 94.0) < 1e-6
        assert summary['matched_sl'] == 'sl-1'

    def test_sl_algo_unresolved_halt_clears_when_migration_finds_sl(
        self, monkeypatch
    ):
        import utils.halt_state as hs_mod

        ex = _make_executor()
        ex.testnet = False
        self._local_long(ex)
        ex.positions["BTC-USDT-SWAP"]["protection_state"] = "unknown"
        ex._halted_symbols = {
            "BTC-USDT-SWAP": {"reason": "sl_algo_unresolved", "halted_at": 1.0}
        }
        halt_state = MagicMock()
        halt_state.auto_clear_if_reason.return_value = True
        monkeypatch.setattr(hs_mod, "get_halt_state", lambda: halt_state)
        ex._save_positions = MagicMock()
        ex.exchange.private_get_trade_orders_algo_pending = MagicMock(
            return_value={"data": [{
                "algoId": "sl-1",
                "algoClOrdId": "clord-1",
                "instId": "BTC-USDT-SWAP",
                "side": "sell",
                "tpTriggerPx": "0",
                "slTriggerPx": "94",
            }]}
        )

        summary = ex._migrate_okx_algos_for_symbol("BTC-USDT-SWAP")

        assert summary["matched_sl"] == "sl-1"
        assert ex.positions["BTC-USDT-SWAP"]["protection_state"] == "protected"
        assert "BTC-USDT-SWAP" not in ex._halted_symbols
        halt_state.auto_clear_if_reason.assert_called_once_with(
            "okx_sl_algo_unresolved:BTC-USDT-SWAP",
            cleared_by="self_heal:protection_resolved",
        )

    def test_missing_sl_halts_live(self):
        """本地有仓位但交易所无 SL,live 必须 halt。"""
        ex = _make_executor()
        ex.testnet = False
        self._local_long(ex)
        ex._save_positions = MagicMock()
        ex._halt_symbol = MagicMock()
        ex.exchange.private_get_trade_orders_algo_pending = MagicMock(
            return_value={'data': []}
        )
        summary = ex._migrate_okx_algos_for_symbol('BTC-USDT-SWAP')
        assert summary['missing_sl'] is True
        assert summary['halted'] is True
        ex._halt_symbol.assert_called_once()
        assert ex._halt_symbol.call_args.kwargs['reason'] == 'migrate_missing_sl'
        pos = ex.positions['BTC-USDT-SWAP']
        assert pos['protection_state'] == 'unknown'

    def test_missing_sl_testnet_does_not_halt(self):
        ex = _make_executor()
        ex.testnet = True
        self._local_long(ex)
        ex._save_positions = MagicMock()
        ex._halt_symbol = MagicMock()
        ex.exchange.private_get_trade_orders_algo_pending = MagicMock(
            return_value={'data': []}
        )
        summary = ex._migrate_okx_algos_for_symbol('BTC-USDT-SWAP')
        assert summary['missing_sl'] is True
        assert summary['halted'] is False
        ex._halt_symbol.assert_not_called()

    def test_multiple_sl_halts_and_cancels_all(self):
        ex = _make_executor()
        self._local_long(ex)
        ex._save_positions = MagicMock()
        ex._halt_symbol = MagicMock()
        ex.exchange.private_get_trade_orders_algo_pending = MagicMock(
            return_value={'data': [
                {'algoId': 'sl-a', 'instId': 'BTC-USDT-SWAP',
                 'side': 'sell', 'tpTriggerPx': '0', 'slTriggerPx': '94'},
                {'algoId': 'sl-b', 'instId': 'BTC-USDT-SWAP',
                 'side': 'sell', 'tpTriggerPx': '0', 'slTriggerPx': '93'},
            ]}
        )
        cancelled = []
        ex.exchange.cancel_orders = MagicMock(
            side_effect=lambda ids, symbol, params=None: cancelled.append(
                {'ids': list(ids), 'symbol': symbol, 'params': params or {}}
            ) or [{'id': i, 'status': 'canceled'} for i in ids]
        )
        summary = ex._migrate_okx_algos_for_symbol('BTC-USDT-SWAP')
        assert summary['halted'] is True
        ex._halt_symbol.assert_called_once()
        assert ex._halt_symbol.call_args.kwargs['reason'] == 'migrate_multiple_sl'
        assert ex.positions['BTC-USDT-SWAP']['protection_state'] == 'unknown'
        cancelled_ids = {i for entry in cancelled for i in entry['ids']}
        assert cancelled_ids == {'sl-a', 'sl-b'}

    def test_orphan_sl_without_local_position_is_cancelled(self):
        ex = _make_executor()
        ex._save_positions = MagicMock()
        # 本地无仓位
        ex.exchange.private_get_trade_orders_algo_pending = MagicMock(
            return_value={'data': [
                {'algoId': 'sl-orphan', 'instId': 'ETH-USDT-SWAP',
                 'side': 'sell', 'tpTriggerPx': '0', 'slTriggerPx': '1500'},
            ]}
        )
        cancelled = []
        ex.exchange.cancel_orders = MagicMock(
            side_effect=lambda ids, symbol, params=None: cancelled.append(
                {'ids': list(ids), 'symbol': symbol, 'params': params or {}}
            ) or [{'id': i, 'status': 'canceled'} for i in ids]
        )
        summary = ex._migrate_okx_algos_for_symbol('ETH-USDT-SWAP')
        assert summary['orphan_sl'] == 1
        assert any('sl-orphan' in entry['ids'] for entry in cancelled)

    def test_sl_side_conflict_halts(self):
        """本地 long,但 algo side=buy(应是 sell)→ 撤 + halt。"""
        ex = _make_executor()
        self._local_long(ex)
        ex._save_positions = MagicMock()
        ex._halt_symbol = MagicMock()
        ex.exchange.private_get_trade_orders_algo_pending = MagicMock(
            return_value={'data': [
                {'algoId': 'sl-bad', 'instId': 'BTC-USDT-SWAP',
                 'side': 'buy', 'tpTriggerPx': '0', 'slTriggerPx': '105'},
            ]}
        )
        ex.exchange.cancel_orders = MagicMock(
            side_effect=lambda ids, symbol, params=None: [
                {'id': i, 'status': 'canceled'} for i in ids
            ]
        )
        summary = ex._migrate_okx_algos_for_symbol('BTC-USDT-SWAP')
        assert summary['halted'] is True
        ex._halt_symbol.assert_called_once()
        assert ex._halt_symbol.call_args.kwargs['reason'] == 'migrate_sl_side_conflict'

    def test_non_okx_returns_empty_summary(self):
        ex = _make_executor()
        ex.exchange_id = 'binance'
        self._local_long(ex)
        summary = ex._migrate_okx_algos_for_symbol('BTC-USDT-SWAP')
        assert summary == {
            'symbol': 'BTC-USDT-SWAP',
            'cancelled_tp': 0,
            'matched_sl': None,
            'orphan_sl': 0,
            'missing_sl': False,
            'halted': False,
            'oco_replaced': 0,
            'foreign_algos': 0,
            'sidecar_protected_algos': 0,
        }

    def test_legacy_oco_replaced_with_pure_sl(self):
        """旧版 _build_okx_attach_algo 留下的 OCO 一体单(SL+TP)必须转成纯 SL。

        这是 2026-05-27 之前开仓的存量场景:OCO algo 同时带 slTriggerPx 和
        tpTriggerPx,新版 partial TP owner 必须接管 TP,所以 OCO 整撤后
        重挂纯 conditional SL,position.sl_algo_id 指向新 SL,
        protection_state=protected。
        """
        ex = _make_executor()
        ex.testnet = False
        self._local_long(ex)
        ex._save_positions = MagicMock()
        ex._halt_symbol = MagicMock()
        # 拉 conditional 返回空,oco 返回一条 OCO 一体单
        def _pending(params):
            ord_type = (params or {}).get('ordType')
            if ord_type == 'oco':
                return {'data': [
                    {'algoId': 'oco-old', 'algoClOrdId': 'oco-clord',
                     'instId': 'BTC-USDT-SWAP',
                     'side': 'sell', 'posSide': 'net',
                     'tpTriggerPx': '110', 'slTriggerPx': '94',
                     'ordType': 'oco'},
                ]}
            return {'data': []}
        ex.exchange.private_get_trade_orders_algo_pending = MagicMock(
            side_effect=_pending,
        )
        cancelled = []
        ex.exchange.cancel_orders = MagicMock(
            side_effect=lambda ids, symbol, params=None: cancelled.append(
                {'ids': list(ids), 'symbol': symbol, 'params': params or {}}
            ) or [{'id': i, 'status': 'canceled'} for i in ids]
        )
        # 新 SL 下单 mock:_replace_protective_sl 内部会调 create_order
        created = []
        ex.exchange.create_order = MagicMock(
            side_effect=lambda *args, **kwargs: created.append(
                {'args': args, 'kwargs': kwargs}
            ) or {'id': 'sl-new', 'info': {'algoId': 'sl-new',
                                            'algoClOrdId': 'sl-new-clord'}}
        )
        summary = ex._migrate_okx_algos_for_symbol('BTC-USDT-SWAP')
        assert summary['oco_replaced'] == 1, summary
        assert summary['halted'] is False
        ex._halt_symbol.assert_not_called()
        # 旧 OCO 必须被撤
        cancelled_ids = {i for entry in cancelled for i in entry['ids']}
        assert 'oco-old' in cancelled_ids
        # 撤单走 trigger=True
        assert any(
            entry['params'].get('trigger') is True for entry in cancelled
        )
        # 新 SL create_order 被调过
        ex.exchange.create_order.assert_called()
        # position.sl_algo_id 指向新 SL,protection_state=protected
        pos = ex.positions['BTC-USDT-SWAP']
        assert pos['sl_algo_id'] == 'sl-new'
        assert pos['protection_state'] == 'protected'
        assert pos['sl_sync_state'] == 'active'

    def test_oco_recovery_clears_protection_halt_after_global_exact_match(
        self, monkeypatch
    ):
        import utils.halt_state as hs_mod

        ex = _make_executor()
        ex.testnet = False
        self._local_long(ex)
        ex.positions['BTC-USDT-SWAP']['protection_state'] = 'unknown'
        ex._halted_symbols = {
            'BTC-USDT-SWAP': {'reason': 'sl_algo_unresolved', 'halted_at': 1.0}
        }
        ex._save_positions = MagicMock()
        ex._halt_symbol = MagicMock()

        def _pending(params):
            ord_type = (params or {}).get('ordType')
            if ord_type == 'oco':
                return {'data': [
                    {'algoId': 'oco-old', 'algoClOrdId': 'oco-clord',
                     'instId': 'BTC-USDT-SWAP',
                     'side': 'sell', 'posSide': 'net',
                     'tpTriggerPx': '110', 'slTriggerPx': '94',
                     'ordType': 'oco'},
                ]}
            return {'data': []}

        ex.exchange.private_get_trade_orders_algo_pending = MagicMock(
            side_effect=_pending,
        )
        ex.exchange.cancel_orders = MagicMock(
            return_value=[{'id': 'oco-old', 'status': 'canceled'}],
        )
        ex.exchange.create_order = MagicMock(
            return_value={'id': 'sl-new', 'info': {
                'algoId': 'sl-new',
                'algoClOrdId': 'sl-new-clord',
            }},
        )

        order = []
        halt_state = MagicMock()

        def auto_clear(expected, *, cleared_by):
            order.append('global')
            return True

        halt_state.auto_clear_if_reason.side_effect = auto_clear
        monkeypatch.setattr(hs_mod, 'get_halt_state', lambda: halt_state)
        real_clear_symbol_halt = ex.clear_symbol_halt

        def clear_symbol_halt(*args, **kwargs):
            order.append('local')
            return real_clear_symbol_halt(*args, **kwargs)

        ex.clear_symbol_halt = MagicMock(side_effect=clear_symbol_halt)

        summary = ex._migrate_okx_algos_for_symbol('BTC-USDT-SWAP')

        assert summary['oco_replaced'] == 1, summary
        assert summary['matched_sl'] == 'sl-new'
        assert ex.positions['BTC-USDT-SWAP']['protection_state'] == 'protected'
        assert 'BTC-USDT-SWAP' not in ex._halted_symbols
        assert order == ['global', 'local']
        halt_state.auto_clear_if_reason.assert_called_once_with(
            'okx_sl_algo_unresolved:BTC-USDT-SWAP',
            cleared_by='self_heal:protection_resolved',
        )
        ex.clear_symbol_halt.assert_called_once_with(
            'BTC-USDT-SWAP', source='self_heal:protection_resolved',
        )
        ex._halt_symbol.assert_not_called()


class TestAddPositionTpInvariant:
    """P1-01: add_to_position 重算 TP 必须经 _set_position_tp 收口，
    保证 take_profit == take_profit_levels[0]，加仓后不触发 tp_invariant_breach。"""

    def _open_long(self, ex, entry=100.0):
        pos = {
            'symbol': 'X-USDT-SWAP', 'side': 'long', 'amount': 1.0,
            'amount_usdt': 30.0, 'entry_price': entry,
            'stop_loss': entry * 0.95, 'original_sl': entry * 0.95,
            'take_profit': entry * 1.10,
            'take_profit_levels': [entry * 1.10, entry * 1.20],
            'tp_filled': 0, 'protection_state': 'protected', 'atr_pct': 0.02,
            'leverage': 1,
            'highest_price': entry, 'lowest_price': entry,
        }
        ex.positions['X-USDT-SWAP'] = pos
        return pos

    def _wire_add(self, ex, fill_price):
        # verified against executor.py:3080-3188
        ex.can_open_new_okx = lambda: True
        ex.is_symbol_halted = lambda s: False
        ex.get_balance = MagicMock(return_value=1000.0)
        ex.risk_manager.max_trade_amount = 30.0
        ex.risk_manager.check_can_trade = MagicMock(return_value=(True, ''))
        ex.balance_adapter = None  # → 走 exchange.fetch_balance()['USDT']['free']
        ex.exchange.fetch_balance = MagicMock(return_value={'USDT': {'free': 100000.0}})
        ex.exchange.set_leverage = MagicMock()
        ex.caps = None
        ex._build_open_order_params = MagicMock(return_value={})
        ex._replace_protective_sl = MagicMock()
        ex._save_positions = MagicMock()
        ex.idempotency = None
        ex.exchange.create_order = MagicMock(return_value={'id': 'ord1'})
        # add_to_position 取价入口 = exchange.fetch_ticker(symbol)['last']（3112-3113）
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': fill_price})

    def test_invariant_holds_after_add(self):
        ex = _make_executor()
        pos = self._open_long(ex, entry=100.0)
        self._wire_add(ex, fill_price=110.0)
        ex._halt_symbol = MagicMock()
        ex.add_to_position('X-USDT-SWAP', 'long', size_pct=1.0)
        assert pos['take_profit'] == pos['take_profit_levels'][0]
        # 加仓后跑止损轮询不得触发 tp_invariant_breach
        ex._update_trailing('X-USDT-SWAP', pos, pos['entry_price'])
        for call in ex._halt_symbol.call_args_list:
            assert call.kwargs.get('reason') != 'tp_invariant_breach'

    def test_add_after_partial_tp_fill(self):
        ex = _make_executor()
        pos = self._open_long(ex, entry=100.0)
        pos['tp_filled'] = 1  # TP1 已部分成交
        self._wire_add(ex, fill_price=112.0)
        ex._halt_symbol = MagicMock()
        ex.add_to_position('X-USDT-SWAP', 'long', size_pct=1.0)
        assert pos['tp_filled'] == 1
        assert pos['take_profit'] == pos['take_profit_levels'][0]
        ex._update_trailing('X-USDT-SWAP', pos, pos['entry_price'])
        for call in ex._halt_symbol.call_args_list:
            assert call.kwargs.get('reason') != 'tp_invariant_breach'

    def test_multi_level_ratios_preserved(self):
        ex = _make_executor()
        pos = self._open_long(ex, entry=100.0)  # levels=[110,120] → 距 10%/20%
        self._wire_add(ex, fill_price=120.0)
        ex._halt_symbol = MagicMock()
        ex.add_to_position('X-USDT-SWAP', 'long', size_pct=1.0)
        new_entry = pos['entry_price']
        levels = pos['take_profit_levels']
        assert abs((levels[0] - new_entry) / new_entry - 0.10) < 1e-9
        assert abs((levels[1] - new_entry) / new_entry - 0.20) < 1e-9
