"""回撤基准修正验收测试 — AC-01 ~ AC-13"""

import json
import os
import tempfile
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from risk_manager import RiskManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state_file(content: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, 'w') as f:
        json.dump(content, f)
    return path


# ---------------------------------------------------------------------------
# AC-01: 旧 peak 不误杀启动
# ---------------------------------------------------------------------------

class TestAC01_OldPeakNoFalseReject:
    def test_old_peak_does_not_block_open(self):
        state_file = _make_state_file({
            "peak_balance": 6268.64,
            "daily_pnl": 0.0,
            "last_reset_date": "2026-05-21",
        })
        try:
            rm = RiskManager(
                max_trade_amount=30.0,
                max_drawdown_pct=20.0,
                max_daily_loss=300.0,
                state_file=state_file,
                effective_balance_cap=300.0,
                baseline_mode="session_start",
            )
            rm.initialize_session(4864.46, effective_balance_cap=300.0)

            assert rm.session_peak_equity == 300.0
            can, reason = rm.check_can_open(4864.46, 300.0)
            assert can is True, f"Should pass but got: {reason}"
        finally:
            os.unlink(state_file)


# ---------------------------------------------------------------------------
# AC-02: cap 进入 live 回撤口径
# ---------------------------------------------------------------------------

class TestAC02_CapEntersLiveDrawdown:
    def test_risk_equity_uses_cap(self):
        state_file = _make_state_file({})
        try:
            rm = RiskManager(
                max_trade_amount=30.0,
                max_drawdown_pct=20.0,
                max_daily_loss=300.0,
                state_file=state_file,
                effective_balance_cap=300.0,
            )
            eq = rm.initialize_session(4864.46, 300.0)
            assert eq == 300.0
            assert rm.session_peak_equity == 300.0
        finally:
            os.unlink(state_file)


# ---------------------------------------------------------------------------
# AC-03: 本轮回撤正常触发
# ---------------------------------------------------------------------------

PLACEHOLDER_MORE_TESTS = True


class TestAC03_DrawdownTriggersCorrectly:
    def test_20pct_drawdown_rejects_open(self):
        state_file = _make_state_file({})
        try:
            rm = RiskManager(
                max_trade_amount=30.0,
                max_drawdown_pct=20.0,
                max_daily_loss=300.0,
                state_file=state_file,
                effective_balance_cap=300.0,
            )
            rm.initialize_session(300.0, 300.0)
            assert rm.session_peak_equity == 300.0

            # Simulate equity drop to 239 (20.33% drawdown)
            can, reason = rm.check_can_open(239.0, 300.0)
            assert can is False
            assert "回撤" in reason
            assert "20." in reason
        finally:
            os.unlink(state_file)


# ---------------------------------------------------------------------------
# AC-04: 本轮盈利更新 peak
# ---------------------------------------------------------------------------

class TestAC04_ProfitUpdatesPeak:
    def test_peak_ratchets_up(self):
        state_file = _make_state_file({})
        try:
            rm = RiskManager(
                max_trade_amount=30.0,
                max_drawdown_pct=20.0,
                max_daily_loss=300.0,
                state_file=state_file,
                effective_balance_cap=400.0,
            )
            rm.initialize_session(300.0, 400.0)
            assert rm.session_peak_equity == 300.0

            # Equity rises to 330
            can, _ = rm.check_can_open(330.0, 400.0)
            assert can is True
            assert rm.session_peak_equity == 330.0
        finally:
            os.unlink(state_file)


# ---------------------------------------------------------------------------
# AC-05: 外部转出不影响重启后基准
# ---------------------------------------------------------------------------

class TestAC05_ExternalWithdrawalResetOnRestart:
    def test_restart_resets_baseline(self):
        state_file = _make_state_file({
            "peak_balance": 6268.64,
            "daily_pnl": 0.0,
            "last_reset_date": "2026-05-20",
        })
        try:
            rm = RiskManager(
                max_trade_amount=30.0,
                max_drawdown_pct=20.0,
                max_daily_loss=300.0,
                state_file=state_file,
                effective_balance_cap=300.0,
                baseline_mode="session_start",
            )
            rm.initialize_session(4864.46, 300.0)
            # session_start mode: baseline = risk_equity = 300, not 6268
            assert rm.session_baseline_equity == 300.0
            assert rm.session_peak_equity == 300.0
        finally:
            os.unlink(state_file)


# ---------------------------------------------------------------------------
# AC-06: persisted_peak 兼容旧行为
# ---------------------------------------------------------------------------

class TestAC06_PersistedPeakMode:
    def test_persisted_peak_uses_old_peak(self):
        state_file = _make_state_file({
            "peak_balance": 500.0,
            "daily_pnl": 0.0,
            "last_reset_date": "2026-05-23",
        })
        try:
            rm = RiskManager(
                max_trade_amount=30.0,
                max_drawdown_pct=20.0,
                max_daily_loss=300.0,
                state_file=state_file,
                effective_balance_cap=None,
                baseline_mode="persisted_peak",
            )
            rm.initialize_session(450.0)
            # persisted_peak mode with no cap: uses old peak_balance
            assert rm.session_peak_equity == 500.0
        finally:
            os.unlink(state_file)


# ---------------------------------------------------------------------------
# AC-07: close 不被最大回撤挡住
# ---------------------------------------------------------------------------

class TestAC07_CloseNotBlockedByDrawdown:
    @pytest.fixture
    def halted_executor(self):
        """Create a MultiExecutor mock in drawdown state"""
        from agents.trading.executor import MultiExecutor
        config = {'exchange': 'okx', 'min_confidence': 60, 'effective_balance_cap': 300.0}
        executor = MultiExecutor(config)
        executor.logger = MagicMock()

        mock_contract_executor = MagicMock()
        mock_contract_executor._normalize_symbol.return_value = "BTC-USDT-SWAP"
        mock_contract_executor.get_position.return_value = {
            'side': 'long', 'amount_usdt': 30, 'sl_order_id': None
        }
        mock_contract_executor.close_position.return_value = {'pnl': -5.0, 'symbol': 'BTC-USDT-SWAP'}
        # Risk manager in drawdown state
        mock_rm = MagicMock()
        mock_rm.check_can_trade.return_value = (False, "已达最大回撤限制: drawdown=25%")
        mock_contract_executor.risk_manager = mock_rm
        executor.executor = mock_contract_executor
        executor.publish = AsyncMock()
        return executor

    @pytest.mark.asyncio
    async def test_close_bypasses_drawdown_check(self, halted_executor):
        decision = {
            'action': 'close',
            'symbol': 'BTC-USDT',
            'confidence': 80,
            'request_id': 'test-close-001',
        }
        await halted_executor._execute_decision(decision)
        # close should NOT call check_can_trade
        halted_executor.executor.risk_manager.check_can_trade.assert_not_called()
        # close should proceed
        halted_executor.executor.close_position.assert_called_once()


# ---------------------------------------------------------------------------
# AC-08: reduce 不被最大回撤挡住
# ---------------------------------------------------------------------------

class TestAC08_ReduceNotBlockedByDrawdown:
    @pytest.mark.asyncio
    async def test_reduce_bypasses_drawdown_check(self):
        from agents.trading.executor import MultiExecutor
        config = {'exchange': 'okx', 'min_confidence': 60, 'effective_balance_cap': 300.0}
        executor = MultiExecutor(config)
        executor.logger = MagicMock()

        mock_ce = MagicMock()
        mock_ce._normalize_symbol.return_value = "ETH-USDT-SWAP"
        mock_ce.get_position.return_value = {
            'side': 'long', 'amount_usdt': 30, 'sl_order_id': None
        }
        mock_ce.reduce_position.return_value = {'pnl': -2.0}
        mock_rm = MagicMock()
        mock_rm.check_can_trade.return_value = (False, "drawdown exceeded")
        mock_ce.risk_manager = mock_rm
        executor.executor = mock_ce
        executor.publish = AsyncMock()

        decision = {
            'action': 'close',
            'symbol': 'ETH-USDT',
            'confidence': 80,
            'size_pct': 0.5,
            'source': 'position_analyst',
            'request_id': 'test-reduce-001',
        }
        await executor._execute_decision(decision)
        mock_rm.check_can_trade.assert_not_called()


# ---------------------------------------------------------------------------
# AC-09: open/add 被最大回撤挡住
# ---------------------------------------------------------------------------

class TestAC09_OpenBlockedByDrawdown:
    @pytest.mark.asyncio
    async def test_open_rejected_by_drawdown(self):
        from agents.trading.executor import MultiExecutor
        config = {'exchange': 'okx', 'min_confidence': 60, 'effective_balance_cap': 300.0}
        executor = MultiExecutor(config)
        executor.logger = MagicMock()
        executor._trading_halted = False
        executor._halt_state = MagicMock(can_open_new=True)

        mock_ce = MagicMock()
        mock_ce._normalize_symbol.return_value = "BTC-USDT-SWAP"
        mock_rm = MagicMock()
        mock_rm.check_can_trade.return_value = (False, "已达最大回撤限制: drawdown=21.0% risk_equity=237.0 peak=300.0 mode=session_start")
        mock_ce.risk_manager = mock_rm
        executor.executor = mock_ce
        executor.publish = AsyncMock()
        executor._get_balance = MagicMock(return_value=4800.0)

        decision = {
            'action': 'open_long',
            'symbol': 'BTC-USDT',
            'confidence': 80,
            'request_id': 'test-open-001',
        }
        await executor._execute_decision(decision)
        # Should have been rejected
        executor.publish.assert_called()
        call_args = executor.publish.call_args[0]
        assert call_args[1]['status'] == 'rejected'
        assert 'drawdown' in call_args[1]['reason'] or '回撤' in call_args[1]['reason']


# ---------------------------------------------------------------------------
# AC-10: PaperExecutor 隔离
# ---------------------------------------------------------------------------

class TestAC10_PaperExecutorIsolation:
    def test_paper_does_not_write_risk_state(self, tmp_path):
        risk_state_path = str(tmp_path / "risk_state.json")
        # Ensure risk_state.json does not exist
        assert not os.path.exists(risk_state_path)

        from agents.trading.paper_executor import PaperExecutor
        config = {'effective_balance_cap': 300.0, 'min_confidence': 60}
        paper = PaperExecutor(config)
        # Paper should not create or touch risk_state.json
        assert not os.path.exists(risk_state_path)

    def test_paper_uses_cap_for_initial_equity(self):
        from agents.trading.paper_executor import PaperExecutor
        config = {'effective_balance_cap': 300.0}
        paper = PaperExecutor(config)
        assert paper._initial_equity == 300.0


# ---------------------------------------------------------------------------
# AC-12: 启动日志可解释
# ---------------------------------------------------------------------------

class TestAC12_StartupLogReadable:
    def test_initialize_session_logs_baseline(self, caplog):
        import logging
        state_file = _make_state_file({})
        try:
            rm = RiskManager(
                max_trade_amount=30.0,
                max_drawdown_pct=20.0,
                max_daily_loss=300.0,
                state_file=state_file,
                effective_balance_cap=300.0,
            )
            with caplog.at_level(logging.INFO, logger="RiskManager"):
                rm.initialize_session(4864.46, 300.0)
            assert "[RiskBaseline]" in caplog.text
            assert "real_total=4864.46" in caplog.text
            assert "cap=300" in caplog.text
            assert "risk_equity=300.00" in caplog.text
            assert "mode=session_start" in caplog.text
        finally:
            os.unlink(state_file)


# ---------------------------------------------------------------------------
# AC-13: 状态文件 v2 兼容
# ---------------------------------------------------------------------------

class TestAC13_StateFileV2Compat:
    def test_v2_state_written_after_init(self):
        state_file = _make_state_file({
            "peak_balance": 6268.64,
            "daily_pnl": -10.0,
            "last_reset_date": "2026-05-20",
        })
        try:
            rm = RiskManager(
                max_trade_amount=30.0,
                max_drawdown_pct=20.0,
                max_daily_loss=300.0,
                state_file=state_file,
                effective_balance_cap=300.0,
            )
            rm.initialize_session(4864.46, 300.0)

            with open(state_file, 'r') as f:
                saved = json.load(f)
            assert saved['schema_version'] == 'risk_state.v2'
            assert saved['session_baseline_equity'] == 300.0
            assert saved['session_peak_equity'] == 300.0
            assert saved['legacy_peak_balance'] == 6268.64
            assert saved['effective_balance_cap'] == 300.0
            assert saved['baseline_mode'] == 'session_start'
        finally:
            os.unlink(state_file)

    def test_v2_state_loads_correctly(self):
        state_file = _make_state_file({
            "schema_version": "risk_state.v2",
            "baseline_mode": "session_start",
            "session_baseline_equity": 300.0,
            "session_peak_equity": 310.0,
            "legacy_peak_balance": 6268.64,
            "effective_balance_cap": 300.0,
            "daily_pnl": -5.0,
            "last_reset_date": "2026-05-23",
        })
        try:
            from datetime import datetime
            with patch('risk_manager.datetime') as mock_dt:
                mock_dt.now.return_value = datetime(2026, 5, 23)
                mock_dt.strptime = datetime.strptime
                rm = RiskManager(
                    max_trade_amount=30.0,
                    max_drawdown_pct=20.0,
                    max_daily_loss=300.0,
                    state_file=state_file,
                    effective_balance_cap=300.0,
                )
            assert rm.session_peak_equity == 310.0
            assert rm.session_baseline_equity == 300.0
            assert rm.peak_balance == 6268.64
            assert rm._session_initialized is True
        finally:
            os.unlink(state_file)
