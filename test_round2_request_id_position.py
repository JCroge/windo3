"""AC2-03 + AC2-04: Executor duplicate open rejected + request_id in result/position."""
from unittest.mock import MagicMock, AsyncMock, patch


class FakeExecutor:
    def _normalize_symbol(self, symbol):
        return symbol

    def get_position(self, symbol):
        return {'side': 'long', 'entry_price': 100, 'amount': 1, 'request_id': 'req-123'}

    def close_position(self, symbol):
        return {
            'symbol': symbol, 'side': 'long', 'entry_price': 100,
            'exit_price': 105, 'pnl': 5, 'pnl_pct': 5.0,
            'attribution': {}, 'entry_type': 'rule_signal',
            'entry_request_id': 'req-123',
        }

    risk_manager = MagicMock()
    risk_manager.check_can_trade = MagicMock(return_value=(True, ''))


class FakeExecutorNoPosition:
    def _normalize_symbol(self, symbol):
        return symbol

    def get_position(self, symbol):
        return None

    def open_position_with_plan(self, symbol, side, plan):
        return {
            'symbol': symbol, 'side': side, 'entry_price': 100,
            'amount': 0.01, 'leverage': 3, 'request_id': plan.get('request_id', ''),
        }

    risk_manager = MagicMock()
    risk_manager.check_can_trade = MagicMock(return_value=(True, ''))


def _make_executor_agent(executor_impl):
    from agents.trading.executor import MultiExecutor
    agent = MultiExecutor.__new__(MultiExecutor)
    agent.executor = executor_impl
    agent.config = {'max_trade_amount': 10}
    agent.logger = MagicMock()
    agent.publish = AsyncMock()
    agent._open_fail_cooldown = {}
    agent._trading_halted = False
    agent._halt_state = MagicMock()
    agent._halt_state.can_open_new = True
    agent.min_confidence = 60
    agent.balance_adapter = None
    agent._get_balance = MagicMock(return_value=1000.0)
    return agent


class TestDuplicateOpenRejected:
    async def test_position_exists_same_side_rejected(self):
        """AC2-03: open_long with existing long position publishes rejected."""
        agent = _make_executor_agent(FakeExecutor())
        decision = {
            'action': 'open_long', 'symbol': 'BTC-USDT',
            'confidence': 70, 'source': 'judge',
            'request_id': 'req-456', 'plan': {'leverage': 3},
        }
        await agent._execute_decision(decision)
        call_args = agent.publish.call_args
        payload = call_args[0][1]
        assert payload['status'] == 'rejected'
        assert payload['reason'] == 'position_exists_same_side'
        assert payload['request_id'] == 'req-456'
        assert payload['schema_version'] == 'execution_result.v2'

    async def test_position_exists_opposite_side_rejected(self):
        """AC2-03: open_short with existing long position publishes rejected."""
        agent = _make_executor_agent(FakeExecutor())
        decision = {
            'action': 'open_short', 'symbol': 'BTC-USDT',
            'confidence': 70, 'source': 'judge',
            'request_id': 'req-789', 'plan': {'leverage': 3},
        }
        await agent._execute_decision(decision)
        payload = agent.publish.call_args[0][1]
        assert payload['status'] == 'rejected'
        assert payload['reason'] == 'position_exists_opposite_side'

    async def test_open_success_result_has_request_id(self):
        """AC2-04: successful open writes request_id into result dict."""
        agent = _make_executor_agent(FakeExecutorNoPosition())
        decision = {
            'action': 'open_long', 'symbol': 'BTC-USDT',
            'confidence': 70, 'source': 'judge',
            'request_id': 'req-open-001',
            'plan': {'leverage': 3, 'size_usdt': 10, 'order_type': 'market'},
        }
        await agent._execute_decision(decision)
        payload = agent.publish.call_args[0][1]
        assert payload['status'] == 'executed'
        assert payload['request_id'] == 'req-open-001'
        assert payload['result']['request_id'] == 'req-open-001'
        assert payload['result']['entry_request_id'] == 'req-open-001'

    async def test_close_result_has_entry_request_id(self):
        """AC2-04: close result carries entry_request_id from position."""
        agent = _make_executor_agent(FakeExecutor())
        decision = {
            'action': 'close', 'symbol': 'BTC-USDT',
            'confidence': 70, 'source': 'position_analyst',
            'request_id': 'req-close-001', 'plan': None,
            'size_pct': 1.0,
        }
        await agent._execute_decision(decision)
        payload = agent.publish.call_args[0][1]
        assert payload['status'] == 'executed'
        result = payload['result']
        assert result['entry_request_id'] == 'req-123'
        assert result['exit_request_id'] == 'req-close-001'
