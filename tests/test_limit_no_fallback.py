"""ContractExecutor._execute_limit_order 的 no_fallback / timeout_sec 行为契约。

数据回测验收：21 笔真实样本，pullback policy 净 +23.03U；其中 missed=2 笔属于
"未成交即放弃"，不应该再像旧路径那样在 30s 后改走市价 fallback。
"""
import logging
import threading
import time
from unittest.mock import MagicMock

import pytest

from executor import ContractExecutor


def _make_executor() -> ContractExecutor:
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger('test_limit_no_fallback')
    ex.exchange_id = 'okx'
    ex.testnet = True
    ex.leverage = 1
    ex.exchange = MagicMock()
    ex.exchange.amount_to_precision = lambda s, a: round(float(a), 6)
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
    ex._sl_check_failures = {}
    ex._sl_max_failures = 5
    ex._last_sl_update = {}
    ex._okx_pos_mode = 'net_mode'
    ex._okx_pos_mode_source = 'test'
    ex._halted_symbols = {}
    ex._exit_lock_mu = threading.Lock()
    ex._exit_locks = {}
    ex._pending_drift_alerts = []
    return ex


def _stub_open_unfilled(ex):
    """create_order 返回 open 订单；fetch_order 永远 'open'。"""
    ex.exchange.create_order = MagicMock(return_value={'id': 'lim-1'})
    ex.exchange.fetch_order = MagicMock(return_value={'status': 'open'})
    ex.exchange.cancel_order = MagicMock(return_value={'id': 'lim-1', 'status': 'canceled'})


def _patch_time(monkeypatch, durations):
    """让 time.time 序列推进 durations，每次调用返回下一个值；time.sleep 立即返回。"""
    state = {'i': 0}
    def _t():
        i = state['i']
        state['i'] = min(i + 1, len(durations) - 1)
        return durations[i]
    monkeypatch.setattr(time, 'time', _t)
    monkeypatch.setattr(time, 'sleep', lambda *_a, **_k: None)


def test_no_fallback_true_returns_none_and_enqueues_alert(monkeypatch):
    """pullback policy: 超时未成交 → cancel + return None，不发市价单，发 pullback_unfilled 告警。"""
    ex = _make_executor()
    _stub_open_unfilled(ex)
    # 仅推进 time，超过 deadline 即可
    _patch_time(monkeypatch, [0.0, 0.0, 0.0, 5.0, 100.0, 200.0, 300.0])

    out = ex._execute_limit_order(
        symbol='BTC-USDT', side='long', size_usdt=30.0,
        current_price=100.0, entry_zone=[99.5, 99.5],
        leverage=5, tp_sl_params=None, clord_id=None,
        orig_plan={'entry_ref': 100.0, 'sl_pct': 0.01, 'tp_pct': [0.02]},
        timeout_sec=10, no_fallback=True,
    )

    # 1. 不下市价单
    create_calls = ex.exchange.create_order.call_args_list
    assert len(create_calls) == 1
    assert create_calls[0].kwargs.get('type') == 'limit'

    # 2. 撤销限价单
    ex.exchange.cancel_order.assert_called_once()

    # 3. 返回 None
    assert out is None

    # 4. 入队 pullback_unfilled
    pending = ex._pending_drift_alerts
    assert len(pending) == 1
    assert pending[0]['type'] == 'pullback_unfilled'
    assert pending[0]['symbol'] == 'BTC-USDT'
    assert pending[0]['side'] == 'long'
    assert pending[0]['timeout_sec'] == 10


def test_no_fallback_false_keeps_legacy_market_fallback(monkeypatch):
    """老路径：no_fallback 默认 False，超时后走 Gate 2 + 市价 fallback。"""
    ex = _make_executor()
    _stub_open_unfilled(ex)
    _patch_time(monkeypatch, [0.0, 0.0, 0.0, 5.0, 100.0, 200.0, 300.0])

    # 让 Gate 2 不触发（无 orig_plan 即跳过 Gate 2）
    out = ex._execute_limit_order(
        symbol='BTC-USDT', side='long', size_usdt=30.0,
        current_price=100.0, entry_zone=[99.5, 99.5],
        leverage=5, tp_sl_params=None, clord_id=None,
        orig_plan=None,
        timeout_sec=10, no_fallback=False,
    )

    # 应有两次 create_order：limit + market fallback
    create_calls = ex.exchange.create_order.call_args_list
    assert len(create_calls) == 2
    types = [c.kwargs.get('type') for c in create_calls]
    assert types == ['limit', 'market']

    # 不入队 pullback_unfilled
    types_in_alerts = [a.get('type') for a in ex._pending_drift_alerts]
    assert 'pullback_unfilled' not in types_in_alerts

    # 返回 fallback 市价单元组
    assert out is not None
    amount, price, order_id = out
    assert price == pytest.approx(100.0)


def test_default_timeout_30s_unchanged_for_legacy_callers(monkeypatch):
    """默认 timeout_sec=30 与旧行为一致；这里只验证参数默认值不变。"""
    ex = _make_executor()
    _stub_open_unfilled(ex)
    # 让 deadline 立刻超过：第一次 t=0，第二次 t=31 > 0+30 → 跳出循环
    _patch_time(monkeypatch, [0.0, 31.0, 31.0, 31.0])

    out = ex._execute_limit_order(
        symbol='BTC-USDT', side='long', size_usdt=30.0,
        current_price=100.0, entry_zone=[99.5, 99.5],
        leverage=5, tp_sl_params=None, clord_id=None,
        orig_plan=None,
    )
    # 老路径仍然 fall through 到市价 fallback
    types = [c.kwargs.get('type') for c in ex.exchange.create_order.call_args_list]
    assert 'market' in types
    assert out is not None


def test_filled_in_window_returns_fill_no_alert(monkeypatch):
    """限价在窗口内成交 → 返回成交元组，不发 pullback_unfilled 告警。"""
    ex = _make_executor()
    ex.exchange.create_order = MagicMock(return_value={'id': 'lim-1'})
    ex.exchange.fetch_order = MagicMock(return_value={
        'status': 'closed', 'average': 99.5, 'filled': 1.5,
    })
    ex.exchange.cancel_order = MagicMock()
    _patch_time(monkeypatch, [0.0, 0.0, 5.0, 5.0])

    out = ex._execute_limit_order(
        symbol='BTC-USDT', side='long', size_usdt=30.0,
        current_price=100.0, entry_zone=[99.5, 99.5],
        leverage=5, tp_sl_params=None, clord_id=None,
        orig_plan=None, timeout_sec=10, no_fallback=True,
    )

    assert out is not None
    amount, price, oid = out
    assert price == pytest.approx(99.5)
    assert oid == 'lim-1'
    ex.exchange.cancel_order.assert_not_called()
    types_in_alerts = [a.get('type') for a in ex._pending_drift_alerts]
    assert 'pullback_unfilled' not in types_in_alerts
