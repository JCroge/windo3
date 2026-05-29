"""execution_result.v2 close cause + Judge SL hit 收敛单测 (FR-004)。

覆盖 docs/audit_remediation_20260528_acceptance.md:
- AC-P0-012: execution_result close cause 字段完整 (所有 close source 参数化)
- AC-P0-013: Judge 只对策略 SL 记 SL hit (local_stop_loss / exchange_sl)
- AC-P0-014: Judge 不把风控/全平/价格失败计为 SL
- AC-P0-015: 下游兼容旧 status (force_closed/closed_externally 仍可被识别)

以及 FR-003 close path 不直接 cancel 保护单的回归保障 (AC-P0-007/008/009/010/011)。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _run_async(coro):
    """asyncio.run 会清空全局 event loop 影响 pytest-asyncio 后续测试,
    用临时 loop 跑完后保留默认 policy。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


from agents.trading.executor import MultiExecutor


def _make_agent() -> MultiExecutor:
    agent = MultiExecutor.__new__(MultiExecutor)
    agent.logger = logging.getLogger('test_judge_close_cause')
    agent.config = {}
    agent.executor = MagicMock()
    agent.executor.positions = {}
    agent.executor._normalize_symbol = lambda s: s
    agent._sync_counter = 0
    agent._reconciler = None
    agent._trading_halted = False
    return agent


class TestExecutionResultCloseCause:
    """AC-P0-012: 所有 close source 必须输出 exit_reason/close_cause/
    is_strategy_stop/is_risk_forced。"""

    @pytest.mark.parametrize('source,reason,expected_exit_reason,is_strategy,is_risk', [
        ('local_stop', 'stop_loss', 'local_stop_loss', True, False),
        ('local_stop', 'take_profit', 'local_take_profit', False, False),
        ('local_stop', 'price_fetch_failed', 'price_fetch_failed', False, True),
        ('local_stop', 'partial_tp_1', 'partial_tp', False, False),
        ('risk_alert', 'emergency_close', 'risk_emergency', False, True),
        ('risk_alert', 'flash_move', 'risk_flash_move', False, True),
        ('risk_alert', 'position_danger', 'risk_position_danger', False, True),
        ('risk_alert', 'high_leverage_danger', 'risk_high_leverage_danger', False, True),
        ('risk_alert', 'trailing_stop', 'risk_trailing_stop', False, True),
        ('close_all', 'flash_move_market', 'system_close_all', False, True),
        ('close_all', '最大回撤触发', 'system_close_all', False, True),
        ('partial_tp', 'partial_tp_1', 'partial_tp', False, False),
        # PRD §6.2 #5: pending 阶段绝不计 SL hit
        ('external_close', 'external_pending', 'external_pending', False, False),
        # 历史 reason 兼容(fail-safe 同 external_pending)
        ('external_close', 'exchange_sl_tp_triggered', 'external_pending', False, False),
        # final 升级后明确归因
        ('external_close', 'exchange_sl', 'exchange_sl', True, False),
        ('external_close', 'exchange_tp', 'exchange_tp', False, False),
        ('external_close', 'unknown_external_close', 'external_unknown', False, False),
        ('executor_close', '', 'manual_close', False, False),
    ])
    def test_close_cause_classification(self, source, reason, expected_exit_reason,
                                         is_strategy, is_risk):
        agent = _make_agent()
        payload = agent._build_execution_result(
            status='force_closed', action='close', symbol='BTC-USDT',
            source=source, reason=reason, result={'pnl': -1.0},
        )
        assert payload['exit_reason'] == expected_exit_reason
        assert payload['close_cause']
        assert payload['is_strategy_stop'] is is_strategy
        assert payload['is_risk_forced'] is is_risk
        # 内层 result 镜像顶层
        assert payload['result']['exit_reason'] == expected_exit_reason
        assert payload['result']['is_strategy_stop'] is is_strategy
        assert payload['result']['is_risk_forced'] is is_risk

    def test_open_action_does_not_inject_close_cause(self):
        """open_long/open_short 不应被注入 close cause 字段。"""
        agent = _make_agent()
        payload = agent._build_execution_result(
            status='executed', action='open_long', symbol='BTC-USDT',
            source='executor_open', reason='ok',
        )
        assert 'exit_reason' not in payload
        assert 'close_cause' not in payload

    def test_protective_cleanup_state_default(self):
        agent = _make_agent()
        payload = agent._build_execution_result(
            status='force_closed', action='close', symbol='BTC-USDT',
            source='local_stop', reason='stop_loss',
            result={'pnl': -1.0},
        )
        assert 'protective_cleanup_state' in payload['result']

    def test_external_unknown_not_strategy_stop(self):
        agent = _make_agent()
        payload = agent._build_execution_result(
            status='closed_externally', action='close', symbol='BTC-USDT',
            source='external_close', reason='unknown_external_close',
            result={'pnl': 0.5},
        )
        assert payload['exit_reason'] == 'external_unknown'
        assert payload['is_strategy_stop'] is False


class TestJudgeRecordSlHit:
    """AC-P0-013/014: Judge 只对策略 SL 记 hit。"""

    def _make_judge(self):
        from agents.trading.judge import MultiJudge as Judge
        judge = Judge.__new__(Judge)
        judge.logger = logging.getLogger('test_judge_sl_hit')
        judge._states = {}
        judge._open_positions = set()
        judge._position_slots = {}
        judge._pending_open_symbols = set()
        judge._pending_open_ts = {}
        judge._pending_open_slots = {}
        judge._probe_short_active = None
        judge._probe_short_sl_count = 0
        judge._probe_short_cooldown_until = 0
        judge._probe_short_cooldown_hours = 12
        judge._counterfactual_ledger = MagicMock()
        judge._counterfactual_ledger._enabled = False
        judge._archetype_cooldown = MagicMock()
        judge._archetype_cooldown.classify = MagicMock(return_value='unknown')
        judge._archetype_cooldown.record_result = MagicMock()
        judge._state_dirty = False
        judge._persist_state = MagicMock()
        judge._record_sl_hit = MagicMock()
        judge._get_state = lambda s: judge._states.setdefault(s, {})
        judge._get_escalating_cooldown = MagicMock(return_value=300)
        return judge

    def _process_msg(self, judge, msg):
        from agents.trading.judge import MultiJudge as Judge
        _run_async(Judge.on_message(judge, msg))

    @pytest.mark.parametrize('exit_reason,is_strategy,expected_called', [
        ('local_stop_loss', True, True),
        ('exchange_sl', True, True),
        ('risk_emergency', False, False),
        ('risk_flash_move', False, False),
        ('risk_position_danger', False, False),
        ('system_close_all', False, False),
        ('price_fetch_failed', False, False),
        ('external_unknown', False, False),
        ('manual_close', False, False),
    ])
    def test_record_sl_hit_only_for_strategy_stop(self, exit_reason, is_strategy, expected_called):
        judge = self._make_judge()
        msg = {
            'type': 'execution_result',
            'symbol': 'BTC-USDT',
            'payload': {
                'status': 'force_closed',
                'action': 'close',
                'symbol': 'BTC-USDT',
                'direction': 'long',
                'exit_reason': exit_reason,
                'is_strategy_stop': is_strategy,
                'is_risk_forced': not is_strategy and exit_reason != 'manual_close',
                'result': {'pnl': -1.0},
            },
        }
        self._process_msg(judge, msg)
        if expected_called:
            judge._record_sl_hit.assert_called_once()
        else:
            judge._record_sl_hit.assert_not_called()

    def test_record_sl_hit_legacy_payload_no_strategy_stop(self):
        """AC-P0-015: 老 payload 不带 is_strategy_stop 时,默认不计 SL hit。
        这是 fail-safe:宁愿漏计也不要错误污染 cooldown。"""
        judge = self._make_judge()
        msg = {
            'type': 'execution_result',
            'symbol': 'BTC-USDT',
            'payload': {
                'status': 'force_closed',
                'action': 'close',
                'symbol': 'BTC-USDT',
                'direction': 'long',
                'result': {'pnl': -1.0},
            },
        }
        self._process_msg(judge, msg)
        judge._record_sl_hit.assert_not_called()

    def test_closed_externally_pending_does_not_record_sl(self):
        """PRD §6.2 #5 + §6.8 P1-d: closed_externally pending(pnl_is_final=False)
        即使 is_strategy_stop=True 也不能立即 _record_sl_hit,等 pnl_resolved 升级。"""
        judge = self._make_judge()
        msg = {
            'type': 'execution_result',
            'symbol': 'BTC-USDT',
            'payload': {
                'status': 'closed_externally',
                'action': 'close',
                'symbol': 'BTC-USDT',
                'direction': 'long',
                'exit_reason': 'external_pending',
                'close_cause': 'exchange_unknown_pending',
                'is_strategy_stop': False,
                'is_risk_forced': False,
                'result': {
                    'pnl': None,
                    'pnl_is_final': False,
                    'pnl_status': 'pending',
                },
            },
        }
        self._process_msg(judge, msg)
        judge._record_sl_hit.assert_not_called()

    def test_closed_externally_final_exchange_sl_records(self):
        """PRD §6.2 #5: closed_externally + final + is_strategy_stop=True → 计 SL hit。"""
        judge = self._make_judge()
        msg = {
            'type': 'execution_result',
            'symbol': 'BTC-USDT',
            'payload': {
                'status': 'closed_externally',
                'action': 'close',
                'symbol': 'BTC-USDT',
                'direction': 'long',
                'exit_reason': 'exchange_sl',
                'close_cause': 'exchange_sl',
                'is_strategy_stop': True,
                'is_risk_forced': False,
                'result': {
                    'pnl': -1.5,
                    'pnl_is_final': True,
                    'pnl_status': 'final',
                },
            },
        }
        self._process_msg(judge, msg)
        judge._record_sl_hit.assert_called_once()

    def test_closed_externally_final_exchange_tp_no_sl_hit(self):
        """AC-D2: final TP 不能计 SL cooldown。"""
        judge = self._make_judge()
        msg = {
            'type': 'execution_result',
            'symbol': 'BTC-USDT',
            'payload': {
                'status': 'closed_externally',
                'action': 'close',
                'symbol': 'BTC-USDT',
                'direction': 'long',
                'exit_reason': 'exchange_tp',
                'close_cause': 'exchange_tp',
                'is_strategy_stop': False,
                'is_risk_forced': False,
                'result': {
                    'pnl': 2.0,
                    'pnl_is_final': True,
                    'pnl_status': 'final',
                },
            },
        }
        self._process_msg(judge, msg)
        judge._record_sl_hit.assert_not_called()


class TestCloseDoesNotDirectlyCancel:
    """AC-P0-007/008/009/010: Agent close path 不再直接 cancel_order。"""

    def test_trade_decision_close_only_calls_close_position(self):
        agent = _make_agent()
        pos = {
            'symbol': 'BTC-USDT', 'side': 'long',
            'sl_order_id': 'algo-old', 'request_id': 'rq1',
            'amount_usdt': 100,
        }
        agent.executor.positions['BTC-USDT'] = pos
        agent.executor.get_all_positions = MagicMock(return_value={'BTC-USDT': pos})
        agent.executor.cancel_order = MagicMock()
        agent.executor.close_position = MagicMock(return_value={'pnl': 1.0})
        agent.publish = AsyncMock()

        _run_async(agent._close_all_positions('test_reason'))
        agent.executor.cancel_order.assert_not_called()
        agent.executor.close_position.assert_called_once_with('BTC-USDT')

    def test_risk_alert_emergency_close_only_calls_close_position(self):
        agent = _make_agent()
        agent.executor.positions['ETH-USDT'] = {
            'symbol': 'ETH-USDT', 'side': 'short',
            'sl_order_id': 'algo-old', 'request_id': 'rq3',
        }
        agent.executor.get_position = MagicMock(return_value=agent.executor.positions['ETH-USDT'])
        agent.executor.cancel_order = MagicMock()
        agent.executor.close_position = MagicMock(return_value={'pnl': -1.0})
        agent.publish = AsyncMock()

        _run_async(agent._handle_risk_alert({
            'type': 'emergency_close', 'symbol': 'ETH-USDT',
            'reason': 'emergency_test', 'scope': 'symbol',
        }))

        agent.executor.cancel_order.assert_not_called()
        agent.executor.close_position.assert_called_once_with('ETH-USDT')

    @pytest.mark.parametrize('alert_type', [
        'flash_move', 'position_danger', 'high_leverage_danger', 'trailing_stop',
    ])
    def test_risk_alert_others_do_not_cancel(self, alert_type):
        agent = _make_agent()
        agent.executor.positions['ETH-USDT'] = {
            'symbol': 'ETH-USDT', 'side': 'long',
            'sl_order_id': 'algo-x', 'request_id': 'rq',
        }
        agent.executor.get_position = MagicMock(return_value=agent.executor.positions['ETH-USDT'])
        agent.executor.cancel_order = MagicMock()
        agent.executor.close_position = MagicMock(return_value={'pnl': -1.0})
        agent.publish = AsyncMock()

        _run_async(agent._handle_risk_alert({
            'type': alert_type, 'symbol': 'ETH-USDT',
            'reason': alert_type, 'scope': 'symbol',
        }))

        agent.executor.cancel_order.assert_not_called()
