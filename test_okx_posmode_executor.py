"""OKX PosMode 执行兼容单元测试

覆盖：
- AC-A1 参数构造矩阵（net_mode / long_short_mode）
- 仓位归一化（_normalize_okx_position / _fetch_okx_position_state）
- 拒单复核（51169/51205 → already_flat / external_closed / still_open / direction_conflict）
- close/reduce 在交易所无仓 / 数量收敛 / 方向冲突 / 仍有仓位时的本地行为

不依赖真实 OKX。所有 exchange 行为通过 MagicMock 注入。
"""
import os
import sys
import time
import logging
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from executor import ContractExecutor, _is_okx_position_reject


def _make_executor(pos_mode='net_mode', exchange_id='okx'):
    """构造一个最小化 ContractExecutor 实例，跳过真实 init。"""
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger('test_okx_posmode_executor')
    ex.exchange_id = exchange_id
    ex.testnet = True
    ex.leverage = 1
    ex.exchange = MagicMock()
    ex.exchange.amount_to_precision = lambda symbol, amount: round(float(amount), 6)
    ex.exchange.market = MagicMock(return_value={
        'contractSize': 1, 'limits': {'amount': {'min': 1e-8}}
    })
    ex.exchange.fetch_ticker = MagicMock(return_value={'last': 100.0})
    ex.positions = {}
    ex.idempotency = None
    ex.balance_adapter = None
    ex.ledger = None
    ex.caps = None
    ex.risk_manager = MagicMock()
    ex.risk_manager.max_trade_amount = 10
    ex.risk_manager.check_can_trade = MagicMock(return_value=(True, 'ok'))
    ex.positions_file = '/tmp/_test_positions.json'
    ex._sl_check_failures = {}
    ex._last_sl_update = {}
    ex._okx_pos_mode = pos_mode if exchange_id == 'okx' else None
    ex._okx_pos_mode_source = 'test'
    ex._halted_symbols = {}
    return ex


# ----------------------------------------------------------------------
# AC-A1 参数构造矩阵
# ----------------------------------------------------------------------

class TestOpenParams:
    def test_net_open_long(self):
        ex = _make_executor('net_mode')
        p = ex._build_open_order_params('long')
        assert p == {'posSide': 'net'}
        assert 'reduceOnly' not in p

    def test_net_open_short(self):
        ex = _make_executor('net_mode')
        p = ex._build_open_order_params('short')
        assert p == {'posSide': 'net'}

    def test_long_short_open_long(self):
        ex = _make_executor('long_short_mode')
        p = ex._build_open_order_params('long')
        assert p == {'posSide': 'long'}
        assert 'reduceOnly' not in p

    def test_long_short_open_short(self):
        ex = _make_executor('long_short_mode')
        p = ex._build_open_order_params('short')
        assert p == {'posSide': 'short'}

    def test_open_with_clord_and_attach(self):
        ex = _make_executor('net_mode')
        p = ex._build_open_order_params(
            'long', clord_id='abc',
            attach_algo=[{'slTriggerPx': '99', 'slOrdPx': '-1'}],
        )
        assert p['posSide'] == 'net'
        assert p['clOrdId'] == 'abc'
        assert p['attachAlgoOrds'] == [{'slTriggerPx': '99', 'slOrdPx': '-1'}]
        assert 'reduceOnly' not in p

    def test_non_okx_keeps_reduceonly_false(self):
        ex = _make_executor('net_mode', exchange_id='binance')
        p = ex._build_open_order_params('long')
        assert p == {'reduceOnly': False}


class TestCloseParams:
    def test_net_close_long(self):
        ex = _make_executor('net_mode')
        p = ex._build_close_order_params({'side': 'long'})
        assert p == {'posSide': 'net', 'reduceOnly': True}

    def test_net_close_short(self):
        ex = _make_executor('net_mode')
        p = ex._build_close_order_params({'side': 'short'})
        assert p == {'posSide': 'net', 'reduceOnly': True}

    def test_long_short_close_long(self):
        ex = _make_executor('long_short_mode')
        p = ex._build_close_order_params({'side': 'long'})
        assert p == {'posSide': 'long'}
        assert 'reduceOnly' not in p

    def test_long_short_close_short(self):
        ex = _make_executor('long_short_mode')
        p = ex._build_close_order_params({'side': 'short'})
        assert p == {'posSide': 'short'}
        assert 'reduceOnly' not in p

    def test_non_okx_keeps_reduceonly_true(self):
        ex = _make_executor('net_mode', exchange_id='binance')
        p = ex._build_close_order_params({'side': 'long'})
        assert p == {'reduceOnly': True}


class TestAlgoParams:
    def test_standalone_sl_net_mode(self):
        ex = _make_executor('net_mode')
        p = ex._build_okx_algo_params({'side': 'long'}, sl_trigger=99)
        assert p['side'] == 'sell'  # 反向
        assert p['posSide'] == 'net'
        assert p['slTriggerPx'] == '99'
        assert p['slOrdPx'] == '-1'
        assert 'reduceOnly' not in p

    def test_standalone_sl_long_short_long(self):
        ex = _make_executor('long_short_mode')
        p = ex._build_okx_algo_params({'side': 'long'}, sl_trigger=99)
        assert p['side'] == 'sell'
        assert p['posSide'] == 'long'  # 被保护方向
        assert 'reduceOnly' not in p

    def test_standalone_sl_long_short_short(self):
        ex = _make_executor('long_short_mode')
        p = ex._build_okx_algo_params({'side': 'short'}, sl_trigger=110)
        assert p['side'] == 'buy'
        assert p['posSide'] == 'short'

    def test_attach_algo_payload(self):
        # partial TP lifecycle: OKX 开仓 attach 只允许 SL,不允许 TP
        ex = _make_executor('net_mode')
        attach = ex._build_okx_attach_algo(99, 110)
        assert attach == [{
            'slTriggerPx': '99', 'slOrdPx': '-1',
        }]
        # 传入 tp 也必须被忽略,不得出现任何 tp 字段
        only_sl = ex._build_okx_attach_algo(99, None)
        assert only_sl == [{'slTriggerPx': '99', 'slOrdPx': '-1'}]
        # 只传 tp 不传 sl: 无可挂保护单,返回 None
        assert ex._build_okx_attach_algo(None, 110) is None
        assert ex._build_okx_attach_algo(None, None) is None


# ----------------------------------------------------------------------
# 仓位归一化
# ----------------------------------------------------------------------

class TestPositionNormalization:
    def test_normalize_net_mode(self):
        ex = _make_executor('net_mode')
        raw = {
            'symbol': 'NEAR/USDT:USDT',
            'side': 'long',
            'contracts': 5,
            'entryPrice': 2.0,
            'leverage': 3,
            'info': {
                'instId': 'NEAR-USDT-SWAP',
                'posSide': 'net',
                'pos': '5',
                'availPos': '5',
                'avgPx': '2.0',
                'lever': '3',
            },
        }
        n = ex._normalize_okx_position(raw)
        assert n['symbol'] == 'NEAR-USDT-SWAP'
        assert n['side'] == 'long'
        assert n['pos_side'] == 'net'
        assert n['contracts'] == 5.0
        assert n['available_contracts'] == 5.0

    def test_normalize_long_short_mode(self):
        ex = _make_executor('long_short_mode')
        raw = {
            'symbol': 'BTC/USDT:USDT', 'side': 'short',
            'contracts': 0.1, 'entryPrice': 67000, 'leverage': 5,
            'info': {
                'instId': 'BTC-USDT-SWAP', 'posSide': 'short',
                'pos': '-0.1', 'availPos': '0.05', 'avgPx': '67000', 'lever': '5',
            },
        }
        n = ex._normalize_okx_position(raw)
        assert n['side'] == 'short'
        assert n['pos_side'] == 'short'
        assert n['available_contracts'] == 0.05

    def test_normalize_zero_position_returns_none(self):
        ex = _make_executor('net_mode')
        raw = {'contracts': 0, 'info': {'pos': '0'}, 'symbol': 'NEAR/USDT:USDT'}
        assert ex._normalize_okx_position(raw) is None

    def test_fetch_position_state_matches_unified_symbol(self):
        ex = _make_executor('net_mode')
        ex.exchange.fetch_positions = MagicMock(return_value=[{
            'symbol': 'NEAR/USDT:USDT', 'side': 'long', 'contracts': 5,
            'entryPrice': 2.0, 'leverage': 3,
            'info': {'instId': 'NEAR-USDT-SWAP', 'posSide': 'net',
                     'pos': '5', 'availPos': '5', 'avgPx': '2.0', 'lever': '3'},
        }])
        n = ex._fetch_okx_position_state('NEAR-USDT-SWAP')
        assert n is not None
        assert n['symbol'] == 'NEAR-USDT-SWAP'

    def test_fetch_position_state_returns_none_when_absent(self):
        ex = _make_executor('net_mode')
        ex.exchange.fetch_positions = MagicMock(return_value=[])
        assert ex._fetch_okx_position_state('NEAR-USDT-SWAP') is None


# ----------------------------------------------------------------------
# AC-A2 拒单复核
# ----------------------------------------------------------------------

class TestRejectReconciliation:
    def test_error_code_detection(self):
        assert _is_okx_position_reject('51169: ...') is True
        assert _is_okx_position_reject('51205 Reduce Only is not available') is True
        assert _is_okx_position_reject('51112 ...') is True
        assert _is_okx_position_reject('51008 insufficient') is False
        assert _is_okx_position_reject('') is False

    def test_close_reject_already_flat(self):
        ex = _make_executor('net_mode')
        ex.positions['NEAR-USDT-SWAP'] = {
            'symbol': 'NEAR-USDT-SWAP', 'side': 'long', 'amount': 5,
            'amount_usdt': 10, 'leverage': 3, 'entry_price': 2.0,
            'stop_loss': 1.9, 'take_profit': 2.1,
        }
        ex.exchange.fetch_positions = MagicMock(return_value=[])
        ex._save_positions = lambda: None
        review = ex._handle_okx_close_reject('NEAR-USDT-SWAP', '51205 ...', action='close')
        assert review['status'] == 'external_closed'
        assert 'NEAR-USDT-SWAP' not in ex.positions
        assert any(d['symbol'] == 'NEAR-USDT-SWAP' for d in ex._removed_positions_data)

    def test_close_reject_still_open(self):
        ex = _make_executor('net_mode')
        ex.positions['NEAR-USDT-SWAP'] = {
            'symbol': 'NEAR-USDT-SWAP', 'side': 'long', 'amount': 5,
            'amount_usdt': 10, 'leverage': 3, 'entry_price': 2.0,
            'stop_loss': 1.9, 'take_profit': 2.1,
        }
        ex.exchange.fetch_positions = MagicMock(return_value=[{
            'symbol': 'NEAR/USDT:USDT', 'side': 'long', 'contracts': 5,
            'entryPrice': 2.0, 'leverage': 3,
            'info': {'instId': 'NEAR-USDT-SWAP', 'posSide': 'net',
                     'pos': '5', 'availPos': '5', 'avgPx': '2.0', 'lever': '3'},
        }])
        ex._save_positions = lambda: None
        review = ex._handle_okx_close_reject('NEAR-USDT-SWAP', '51169 ...', action='close')
        assert review['status'] == 'still_open'
        # 本地仓位必须保留
        assert 'NEAR-USDT-SWAP' in ex.positions
        # symbol halt 必须开启
        assert ex.is_symbol_halted('NEAR-USDT-SWAP')

    def test_close_reject_direction_conflict(self):
        ex = _make_executor('net_mode')
        ex.positions['NEAR-USDT-SWAP'] = {
            'symbol': 'NEAR-USDT-SWAP', 'side': 'long', 'amount': 5,
            'amount_usdt': 10, 'leverage': 3, 'entry_price': 2.0,
            'stop_loss': 1.9, 'take_profit': 2.1,
        }
        ex.exchange.fetch_positions = MagicMock(return_value=[{
            'symbol': 'NEAR/USDT:USDT', 'side': 'short', 'contracts': 5,
            'entryPrice': 2.0, 'leverage': 3,
            'info': {'instId': 'NEAR-USDT-SWAP', 'posSide': 'net',
                     'pos': '-5', 'availPos': '5', 'avgPx': '2.0', 'lever': '3'},
        }])
        ex._save_positions = lambda: None
        review = ex._handle_okx_close_reject('NEAR-USDT-SWAP', '51205 ...', action='close')
        assert review['status'] == 'direction_conflict'
        assert 'NEAR-USDT-SWAP' in ex.positions  # 不删本地
        assert ex.is_symbol_halted('NEAR-USDT-SWAP')

    def test_close_reject_already_flat_no_local(self):
        ex = _make_executor('net_mode')
        ex.exchange.fetch_positions = MagicMock(return_value=[])
        review = ex._handle_okx_close_reject('NEAR-USDT-SWAP', '51205 ...', action='close')
        assert review['status'] == 'already_flat'


# ----------------------------------------------------------------------
# close_position / reduce_position 行为验证（重点：不再无脑删本地）
# ----------------------------------------------------------------------

class TestClosePositionFlow:
    def _seed_executor(self, pos_mode='net_mode'):
        ex = _make_executor(pos_mode)
        ex.positions['NEAR-USDT-SWAP'] = {
            'symbol': 'NEAR-USDT-SWAP', 'side': 'long', 'amount': 5,
            'amount_usdt': 10, 'leverage': 3, 'entry_price': 2.0,
            'stop_loss': 1.9, 'take_profit': 2.1, 'sl_order_id': None,
        }
        ex._save_positions = lambda: None
        ex._estimate_close_pnl_local = lambda *a, **kw: 0.0
        return ex

    def test_close_already_flat_clears_local(self):
        ex = self._seed_executor('net_mode')
        ex.exchange.fetch_positions = MagicMock(return_value=[])
        result = ex.close_position('NEAR-USDT-SWAP')
        assert result is None
        assert 'NEAR-USDT-SWAP' not in ex.positions
        # create_order 不应被调用（既然没仓）
        ex.exchange.create_order.assert_not_called()

    def test_close_clamps_amount_to_available(self):
        ex = self._seed_executor('net_mode')
        ex.exchange.fetch_positions = MagicMock(return_value=[{
            'symbol': 'NEAR/USDT:USDT', 'side': 'long', 'contracts': 5,
            'entryPrice': 2.0, 'leverage': 3,
            'info': {'instId': 'NEAR-USDT-SWAP', 'posSide': 'net',
                     'pos': '5', 'availPos': '3', 'avgPx': '2.0', 'lever': '3'},
        }])
        ex.exchange.create_order = MagicMock(return_value={'id': 'closed-1', 'status': 'closed'})
        result = ex.close_position('NEAR-USDT-SWAP')
        # 调用参数 amount 应被收敛到 3
        call = ex.exchange.create_order.call_args
        assert call.kwargs['amount'] == 3.0
        # net_mode 必须 posSide=net + reduceOnly=True
        assert call.kwargs['params']['posSide'] == 'net'
        assert call.kwargs['params']['reduceOnly'] is True

    def test_close_long_short_mode_no_reduceonly(self):
        ex = self._seed_executor('long_short_mode')
        ex.exchange.fetch_positions = MagicMock(return_value=[{
            'symbol': 'NEAR/USDT:USDT', 'side': 'long', 'contracts': 5,
            'entryPrice': 2.0, 'leverage': 3,
            'info': {'instId': 'NEAR-USDT-SWAP', 'posSide': 'long',
                     'pos': '5', 'availPos': '5', 'avgPx': '2.0', 'lever': '3'},
        }])
        ex.exchange.create_order = MagicMock(return_value={'id': 'closed-1', 'status': 'closed'})
        ex.close_position('NEAR-USDT-SWAP')
        params = ex.exchange.create_order.call_args.kwargs['params']
        assert params['posSide'] == 'long'
        assert 'reduceOnly' not in params

    def test_close_reject_does_not_delete_when_position_still_open(self):
        ex = self._seed_executor('net_mode')
        # fetch_positions 第一次（close 前的复核）有仓，create_order 抛 51169
        ex.exchange.fetch_positions = MagicMock(return_value=[{
            'symbol': 'NEAR/USDT:USDT', 'side': 'long', 'contracts': 5,
            'entryPrice': 2.0, 'leverage': 3,
            'info': {'instId': 'NEAR-USDT-SWAP', 'posSide': 'net',
                     'pos': '5', 'availPos': '5', 'avgPx': '2.0', 'lever': '3'},
        }])
        ex.exchange.create_order = MagicMock(side_effect=Exception('51169 ...'))
        result = ex.close_position('NEAR-USDT-SWAP')
        assert result is None
        # 本地仓位仍保留
        assert 'NEAR-USDT-SWAP' in ex.positions
        # symbol halt 触发
        assert ex.is_symbol_halted('NEAR-USDT-SWAP')


class TestReducePositionFlow:
    def _seed(self, pos_mode='net_mode'):
        ex = _make_executor(pos_mode)
        ex.positions['INJ-USDT-SWAP'] = {
            'symbol': 'INJ-USDT-SWAP', 'side': 'long', 'amount': 10,
            'amount_usdt': 20, 'leverage': 5, 'entry_price': 12.5,
            'stop_loss': 12.0, 'take_profit': 13.5, 'sl_order_id': None,
        }
        ex._save_positions = lambda: None
        return ex

    def test_reduce_clamps_to_available(self):
        ex = self._seed('net_mode')
        ex.exchange.fetch_positions = MagicMock(return_value=[{
            'symbol': 'INJ/USDT:USDT', 'side': 'long', 'contracts': 10,
            'entryPrice': 12.5, 'leverage': 5,
            'info': {'instId': 'INJ-USDT-SWAP', 'posSide': 'net',
                     'pos': '10', 'availPos': '4', 'avgPx': '12.5', 'lever': '5'},
        }])
        ex.exchange.create_order = MagicMock(return_value={'id': 'r-1'})
        # 50%=5，但 available=4，应收敛
        ex.reduce_position('INJ-USDT-SWAP', 0.5)
        # FR-05: 普通减仓后会再调一次 create_order 重挂 SL,reduce 是第一次
        reduce_call = ex.exchange.create_order.call_args_list[0]
        assert reduce_call.kwargs['amount'] == 4.0
        assert reduce_call.kwargs['params']['posSide'] == 'net'
        assert reduce_call.kwargs['params']['reduceOnly'] is True

    def test_reduce_already_flat(self):
        ex = self._seed('net_mode')
        ex.exchange.fetch_positions = MagicMock(return_value=[])
        ex.exchange.create_order = MagicMock()
        result = ex.reduce_position('INJ-USDT-SWAP', 0.3)
        assert result is None
        assert 'INJ-USDT-SWAP' not in ex.positions
        ex.exchange.create_order.assert_not_called()

    def test_reduce_long_short_mode_no_reduceonly(self):
        ex = self._seed('long_short_mode')
        ex.exchange.fetch_positions = MagicMock(return_value=[{
            'symbol': 'INJ/USDT:USDT', 'side': 'long', 'contracts': 10,
            'entryPrice': 12.5, 'leverage': 5,
            'info': {'instId': 'INJ-USDT-SWAP', 'posSide': 'long',
                     'pos': '10', 'availPos': '10', 'avgPx': '12.5', 'lever': '5'},
        }])
        ex.exchange.create_order = MagicMock(return_value={'id': 'r-1'})
        ex.reduce_position('INJ-USDT-SWAP', 0.3)
        params = ex.exchange.create_order.call_args.kwargs['params']
        assert params['posSide'] == 'long'
        assert 'reduceOnly' not in params


# ----------------------------------------------------------------------
# can_open_new / fail-closed 行为
# ----------------------------------------------------------------------

class TestPosModeGate:
    def test_can_open_new_when_known(self):
        for mode in ('net_mode', 'long_short_mode'):
            ex = _make_executor(mode)
            assert ex.can_open_new_okx() is True

    def test_can_open_new_unknown_blocks(self):
        ex = _make_executor('net_mode')
        ex._okx_pos_mode = None
        assert ex.can_open_new_okx() is False

    def test_non_okx_always_allowed(self):
        ex = _make_executor('net_mode', exchange_id='binance')
        assert ex.can_open_new_okx() is True


# ----------------------------------------------------------------------
# place_stop_loss_order：OKX 走 algo + posSide
# ----------------------------------------------------------------------

class TestStopLossOrder:
    def test_okx_sl_uses_algo(self):
        ex = _make_executor('net_mode')
        ex.exchange.create_order = MagicMock(return_value={'id': 'algo-1'})
        sid = ex.place_stop_loss_order('NEAR-USDT-SWAP', 'long', 1.9, 5)
        assert sid == 'algo-1'
        call = ex.exchange.create_order.call_args
        assert call.kwargs['type'] == 'conditional'
        assert call.kwargs['side'] == 'sell'  # 反向
        params = call.kwargs['params']
        assert params['posSide'] == 'net'
        assert params['slTriggerPx'] == '1.9'
        assert params['slOrdPx'] == '-1'
        assert 'reduceOnly' not in params

    def test_okx_sl_long_short_mode_uses_pos_direction(self):
        ex = _make_executor('long_short_mode')
        ex.exchange.create_order = MagicMock(return_value={'id': 'algo-2'})
        ex.place_stop_loss_order('NEAR-USDT-SWAP', 'short', 2.1, 5)
        params = ex.exchange.create_order.call_args.kwargs['params']
        assert params['posSide'] == 'short'
        assert ex.exchange.create_order.call_args.kwargs['side'] == 'buy'
        assert 'reduceOnly' not in params

    def test_non_okx_keeps_legacy_path(self):
        ex = _make_executor('net_mode', exchange_id='binance')
        ex.exchange.create_order = MagicMock(return_value={'id': 'sl-x'})
        ex.place_stop_loss_order('NEAR-USDT-SWAP', 'long', 1.9, 5)
        params = ex.exchange.create_order.call_args.kwargs['params']
        assert params['reduceOnly'] is True
        assert params['stopPrice'] == 1.9
