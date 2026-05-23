"""AC-05: Executor拒单终态测试 — 所有open request必须产生终态事件"""
import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture
def executor():
    from agents.trading.executor import MultiExecutor
    ex = MultiExecutor.__new__(MultiExecutor)
    ex.executor = MagicMock()
    ex.executor._normalize_symbol = lambda s: s.replace('-USDT', '-USDT-SWAP')
    ex.executor.risk_manager = MagicMock()
    ex.executor.risk_manager.check_can_trade = MagicMock(return_value=(True, ''))
    ex.executor.get_position = MagicMock(return_value=None)
    ex._trading_halted = False
    ex._halt_state = MagicMock()
    ex._halt_state.can_open_new = True
    ex._open_fail_cooldown = {}
    ex.min_confidence = 60
    ex.publish = AsyncMock()
    ex.logger = MagicMock()
    ex.balance_adapter = None
    ex._get_balance = MagicMock(return_value=1000.0)
    return ex


class TestExecutorTerminalResult:
    @pytest.mark.asyncio
    async def test_none_result_publishes_rejected(self, executor):
        """底层返回None时必须发布rejected终态"""
        executor.executor.get_position = MagicMock(return_value=None)
        decision = {
            'action': 'open_long', 'symbol': 'BTC-USDT',
            'confidence': 70, 'plan': None, 'request_id': 'test-req-001',
        }
        with patch.object(executor, '_execute_legacy', new_callable=AsyncMock, return_value=None):
            await executor._execute_decision(decision)
        calls = [c for c in executor.publish.call_args_list
                 if c[0][0] == 'execution_result']
        assert len(calls) == 1
        payload = calls[0][0][1]
        assert payload['status'] == 'rejected'
        assert payload['reason'] == 'unknown_none_result'
        assert payload['request_id'] == 'test-req-001'

    @pytest.mark.asyncio
    async def test_halted_publishes_rejected(self, executor):
        """熔断时必须发布rejected"""
        executor._trading_halted = True
        decision = {
            'action': 'open_long', 'symbol': 'BTC-USDT',
            'confidence': 70, 'plan': None, 'request_id': 'test-req-002',
        }
        await executor._execute_decision(decision)
        calls = [c for c in executor.publish.call_args_list
                 if c[0][0] == 'execution_result']
        assert len(calls) == 1
        payload = calls[0][0][1]
        assert payload['status'] == 'rejected'
        assert payload['reason'] == 'halted'
        assert payload['request_id'] == 'test-req-002'
        assert payload['schema_version'] == 'execution_result.v2'

    @pytest.mark.asyncio
    async def test_low_confidence_publishes_rejected(self, executor):
        """置信度不足时必须发布rejected"""
        decision = {
            'action': 'open_short', 'symbol': 'ETH-USDT',
            'confidence': 30, 'plan': None, 'request_id': 'test-req-003',
        }
        await executor._execute_decision(decision)
        calls = [c for c in executor.publish.call_args_list
                 if c[0][0] == 'execution_result']
        assert len(calls) == 1
        payload = calls[0][0][1]
        assert payload['status'] == 'rejected'
        assert payload['reason'] == 'low_confidence'

    @pytest.mark.asyncio
    async def test_cooldown_publishes_rejected(self, executor):
        """冷却期内必须发布rejected"""
        norm = 'BTC-USDT-SWAP'
        executor._open_fail_cooldown[norm] = time.time() + 3600
        decision = {
            'action': 'open_long', 'symbol': 'BTC-USDT',
            'confidence': 70, 'plan': {'leverage': 3}, 'request_id': 'test-req-004',
        }
        await executor._execute_decision(decision)
        calls = [c for c in executor.publish.call_args_list
                 if c[0][0] == 'execution_result']
        assert len(calls) == 1
        payload = calls[0][0][1]
        assert payload['status'] == 'rejected'
        assert payload['reason'] == 'open_cooldown'

    @pytest.mark.asyncio
    async def test_success_has_schema_and_request_id(self, executor):
        """成功执行也必须有schema_version和request_id"""
        executor.executor.get_position = MagicMock(return_value=None)
        decision = {
            'action': 'open_long', 'symbol': 'BTC-USDT',
            'confidence': 70, 'plan': {'leverage': 3, 'size_usdt': 10},
            'request_id': 'test-req-005',
        }
        mock_result = {'entry_price': 50000, 'side': 'long'}
        with patch.object(executor, '_execute_with_plan', new_callable=AsyncMock, return_value=mock_result):
            await executor._execute_decision(decision)
        calls = [c for c in executor.publish.call_args_list
                 if c[0][0] == 'execution_result']
        assert len(calls) == 1
        payload = calls[0][0][1]
        assert payload['status'] == 'executed'
        assert payload['schema_version'] == 'execution_result.v2'
        assert payload['request_id'] == 'test-req-005'
