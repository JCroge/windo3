"""AC-P0-004 to AC-P0-007: Halt/Resume state ownership tests"""
import json
import os
import time
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from utils.halt_state import HaltState, HALT_STATE_FILE


@pytest.fixture(autouse=True)
def clean_halt_state(tmp_path, monkeypatch):
    """Isolate halt state file for each test"""
    test_file = str(tmp_path / "halt_state.json")
    monkeypatch.setattr("utils.halt_state.HALT_STATE_FILE", test_file)
    yield test_file


def _run_async(coro):
    """Run async without polluting the global event loop"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestHaltStateFailClosed:
    """AC-P0-007: 状态文件损坏 fail-closed"""

    def test_corrupted_json_enters_halted(self, clean_halt_state):
        with open(clean_halt_state, 'w') as f:
            f.write("{invalid json content!!")
        state = HaltState()
        assert state.halted is True
        assert "state_load_failed" in state.reason
        assert state.reconciliation_pending is True
        assert state.can_open_new is False

    def test_normal_halted_state_preserved(self, clean_halt_state):
        with open(clean_halt_state, 'w') as f:
            json.dump({"halted": True, "reason": "daily_loss"}, f)
        state = HaltState()
        assert state.halted is True
        assert state.reason == "daily_loss"

    def test_normal_unhalted_state_preserved(self, clean_halt_state):
        with open(clean_halt_state, 'w') as f:
            json.dump({"halted": False, "reason": ""}, f)
        state = HaltState()
        assert state.halted is False
        assert state.can_open_new is True

    def test_missing_file_starts_unhalted(self, clean_halt_state):
        state = HaltState()
        assert state.halted is False
        assert state.can_open_new is True


class TestResumeOwnership:
    """AC-P0-004/005/006: Executor is the resume owner"""

    def test_executor_resume_with_reconciliation_pass(self):
        from agents.trading.executor import MultiExecutor
        executor = MultiExecutor.__new__(MultiExecutor)
        executor.logger = MagicMock()
        executor._trading_halted = True
        executor._halt_state = MagicMock()
        executor._halt_state.can_open_new = False
        executor._reconciler = None

        payload = {
            'command': 'resume', 'source': 'telegram',
            'reconciliation_result': {'status': 'matched'},
        }
        _run_async(executor._handle_resume('telegram', payload))
        executor._halt_state.confirm_resume.assert_called_once_with(
            resume_by='telegram', reconcile_ok=True
        )
        assert executor._trading_halted is False

    def test_executor_resume_reconciliation_fail_stays_halted(self):
        from agents.trading.executor import MultiExecutor
        executor = MultiExecutor.__new__(MultiExecutor)
        executor.logger = MagicMock()
        executor._trading_halted = True
        executor._halt_state = MagicMock()
        executor._halt_state.can_open_new = False
        executor._reconciler = MagicMock()
        executor._reconciler.reconcile.return_value = {
            'blocking_issues': [{'type': 'missing_in_exchange', 'symbol': 'BTC'}],
        }
        executor.executor = MagicMock()
        executor.executor.positions = {}

        payload = {'command': 'resume', 'source': 'telegram'}
        _run_async(executor._handle_resume('telegram', payload))
        executor._halt_state.confirm_resume.assert_called_once_with(
            resume_by='telegram', reconcile_ok=False
        )
        assert executor._trading_halted is True

    def test_executor_resume_no_reconciler_nonmatched_stays_halted(self):
        """P2-03: 无 reconciler + 非 matched → fail-closed 维持熔断（旧 else 分支是 fail-open 自动恢复）"""
        from agents.trading.executor import MultiExecutor
        executor = MultiExecutor.__new__(MultiExecutor)
        executor.logger = MagicMock()
        executor._trading_halted = True
        executor._halt_state = MagicMock()
        executor._halt_state.can_open_new = False
        executor._reconciler = None
        payload = {'command': 'resume', 'source': 'telegram'}  # 无 matched 对账结果
        _run_async(executor._handle_resume('telegram', payload))
        executor._halt_state.confirm_resume.assert_called_once_with(
            resume_by='telegram', reconcile_ok=False
        )
        assert executor._trading_halted is True

    def test_executor_resume_pnl_reconciler_no_attributeerror(self):
        """P2-03: _reconciler 是无 reconcile 方法的对象（像真实 PnL Reconciler），非 matched 不抛 AttributeError 且维持熔断"""
        from agents.trading.executor import MultiExecutor
        executor = MultiExecutor.__new__(MultiExecutor)
        executor.logger = MagicMock()
        executor._trading_halted = True
        executor._halt_state = MagicMock()
        executor._halt_state.can_open_new = False
        executor._reconciler = object()  # 无 reconcile 方法，模拟真实 PnL Reconciler
        executor.executor = MagicMock()
        payload = {'command': 'resume', 'source': 'telegram'}
        _run_async(executor._handle_resume('telegram', payload))  # 不得抛 AttributeError
        executor._halt_state.confirm_resume.assert_called_once_with(
            resume_by='telegram', reconcile_ok=False
        )
        assert executor._trading_halted is True

    def test_telegram_resume_does_not_directly_change_halt_state(self):
        """AC-P0-004: Telegram /resume 不直接最终改 HaltState"""
        from agents.trading.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.__new__(TelegramNotifier)
        notifier.logger = MagicMock()
        notifier._halt_state = MagicMock()
        notifier._halt_state.halted = True
        notifier._send_message = AsyncMock()
        notifier.publish = AsyncMock()
        notifier._run_reconciliation = AsyncMock(return_value=True)

        _run_async(notifier._cmd_resume())

        # Telegram should NOT call confirm_resume directly
        notifier._halt_state.confirm_resume.assert_not_called()
        notifier._halt_state.force_resume.assert_not_called()
        # Should publish system_command instead
        notifier.publish.assert_called_once()
        call_args = notifier.publish.call_args
        assert call_args[0][0] == "system_command"
        assert call_args[0][1]['command'] == 'resume'


class TestReconcilerBlockingAdvisory:
    """AC-P1-005: paper mismatch 不阻塞"""

    def test_paper_mismatch_does_not_block(self):
        from utils.position_reconciler import PositionReconciler

        class FakeExec:
            def get_all_positions(self):
                return {'BTC-USDT-SWAP': {'side': 'long'}}

        class FakeExchange:
            def fetch_positions(self):
                return [{'symbol': 'BTC/USDT:USDT', 'contracts': 1, 'side': 'long',
                         'notional': 1000, 'unrealizedPnl': 5}]

        reconciler = PositionReconciler(
            executor=FakeExec(), exchange=FakeExchange(), logger=MagicMock()
        )
        result = reconciler.reconcile(
            paper_positions={'BTC-USDT-SWAP': {'side': 'short'}}
        )
        assert result['status'] == 'matched'
        assert len(result['advisory_issues']) == 1
        assert result['advisory_issues'][0]['type'] == 'paper_live_mismatch'
        assert len(result['blocking_issues']) == 0
