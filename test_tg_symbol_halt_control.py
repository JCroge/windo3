"""F-TG-001 + F-TG-002 测试矩阵：root executor halt API + TG 命令路径。"""

import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def _make_executor_stub():
    """构造可用于单测 _halted_symbols 的 ContractExecutor 实例(不连交易所)。"""
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = MagicMock()
    ex._halted_symbols = {}
    return ex


class TestClearSymbolHalt:
    def test_clear_all_returns_count_and_empties(self):
        ex = _make_executor_stub()
        ex._halted_symbols = {
            "A-USDT-SWAP": {"reason": "x", "halted_at": time.time()},
            "B-USDT-SWAP": {"reason": "y", "halted_at": time.time()},
        }
        n = ex.clear_symbol_halt(None)
        assert n == 2
        assert ex._halted_symbols == {}

    def test_clear_single_symbol(self):
        ex = _make_executor_stub()
        ex._halted_symbols = {
            "A-USDT-SWAP": {"reason": "x"},
            "B-USDT-SWAP": {"reason": "y"},
        }
        n = ex.clear_symbol_halt("A-USDT-SWAP")
        assert n == 1
        assert "A-USDT-SWAP" not in ex._halted_symbols
        assert "B-USDT-SWAP" in ex._halted_symbols

    def test_clear_missing_symbol_returns_zero(self):
        ex = _make_executor_stub()
        ex._halted_symbols = {"A-USDT-SWAP": {"reason": "x"}}
        n = ex.clear_symbol_halt("NOT-EXIST")
        assert n == 0
        assert ex._halted_symbols == {"A-USDT-SWAP": {"reason": "x"}}

    def test_clear_when_attribute_missing(self):
        from executor import ContractExecutor
        ex = ContractExecutor.__new__(ContractExecutor)
        ex.logger = MagicMock()
        # 不初始化 _halted_symbols
        n = ex.clear_symbol_halt(None)
        assert n == 0

    def test_clear_all_logs_audit_with_source(self):
        ex = _make_executor_stub()
        ex._halted_symbols = {
            "A-USDT-SWAP": {"reason": "x"},
            "B-USDT-SWAP": {"reason": "y"},
        }
        ex.clear_symbol_halt(None, source="telegram")
        ex.logger.info.assert_called_once()
        log_msg = ex.logger.info.call_args[0][0]
        assert "telegram" in log_msg
        assert "A-USDT-SWAP" in log_msg
        assert "B-USDT-SWAP" in log_msg
        assert "2" in log_msg  # count

    def test_clear_single_logs_audit_with_source_and_reason(self):
        ex = _make_executor_stub()
        ex._halted_symbols = {"XLM-USDT-SWAP": {"reason": "sl_replace_failed"}}
        ex.clear_symbol_halt("XLM-USDT-SWAP", source="resume_symbol")
        ex.logger.info.assert_called_once()
        log_msg = ex.logger.info.call_args[0][0]
        assert "resume_symbol" in log_msg
        assert "XLM-USDT-SWAP" in log_msg
        assert "sl_replace_failed" in log_msg

    def test_clear_zero_does_not_log(self):
        """清 0 项时不打 audit log,避免日志噪音。"""
        ex = _make_executor_stub()
        ex._halted_symbols = {"A-USDT-SWAP": {"reason": "x"}}
        ex.clear_symbol_halt("NOT-EXIST", source="telegram")
        ex.logger.info.assert_not_called()

    def test_clear_default_source_is_unknown(self):
        """source 不传时默认 'unknown',log 仍含此 placeholder。"""
        ex = _make_executor_stub()
        ex._halted_symbols = {"A-USDT-SWAP": {"reason": "x"}}
        ex.clear_symbol_halt(None)
        ex.logger.info.assert_called_once()
        assert "unknown" in ex.logger.info.call_args[0][0]


class TestGetHaltedSymbols:
    def test_returns_shallow_copy_top_level(self):
        ex = _make_executor_stub()
        ex._halted_symbols = {"X": {"reason": "a"}}
        snapshot = ex.get_halted_symbols()
        assert snapshot == {"X": {"reason": "a"}}
        # 顶层 add 不影响内部
        snapshot["NEW"] = {"reason": "z"}
        assert "NEW" not in ex._halted_symbols

    def test_returns_empty_when_attribute_missing(self):
        from executor import ContractExecutor
        ex = ContractExecutor.__new__(ContractExecutor)
        snapshot = ex.get_halted_symbols()
        assert snapshot == {}


class TestHandleResumeClearsHaltedSymbols:
    @pytest.mark.asyncio
    async def test_payload_matched_clears(self):
        """resume payload 含 reconciliation_result.matched 时清 _halted_symbols。"""
        from agents.trading.executor import MultiExecutor

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex.executor = MagicMock()
        ex.executor.get_halted_symbols.return_value = {"X-USDT-SWAP": {"reason": "r"}}
        ex.executor.clear_symbol_halt = MagicMock(return_value=1)

        await ex._handle_resume("telegram", {
            "reconciliation_result": {"status": "matched"}
        })

        # 必须传 source kwarg(Task 1 加的)
        ex.executor.clear_symbol_halt.assert_called_once()
        call_kwargs = ex.executor.clear_symbol_halt.call_args.kwargs
        assert "source" in call_kwargs or len(ex.executor.clear_symbol_halt.call_args.args) >= 2
        ex._halt_state.confirm_resume.assert_called_once()

    @pytest.mark.asyncio
    async def test_local_reconciler_matched_clears(self):
        """本地 reconciler 通过时清 _halted_symbols。"""
        from agents.trading.executor import MultiExecutor

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex._reconciler = MagicMock()
        ex._reconciler.reconcile.return_value = {"blocking_issues": []}
        ex.executor = MagicMock()
        ex.executor.positions = {}
        ex.executor.clear_symbol_halt = MagicMock(return_value=0)

        await ex._handle_resume("telegram", {})  # 无 payload, 走本地 reconciler

        ex.executor.clear_symbol_halt.assert_called_once()

    @pytest.mark.asyncio
    async def test_local_reconciler_blocking_does_not_clear(self):
        """本地 reconciler 报 blocking 时不清。"""
        from agents.trading.executor import MultiExecutor

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex._reconciler = MagicMock()
        ex._reconciler.reconcile.return_value = {
            "blocking_issues": [{"type": "x"}]
        }
        ex.executor = MagicMock()
        ex.executor.positions = {}
        ex.executor.clear_symbol_halt = MagicMock(return_value=0)

        await ex._handle_resume("telegram", {})

        ex.executor.clear_symbol_halt.assert_not_called()
        # halt 维持
        assert ex._trading_halted is True

    @pytest.mark.asyncio
    async def test_no_reconciler_clears(self):
        """无 reconciler 直接恢复路径清。"""
        from agents.trading.executor import MultiExecutor

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex._reconciler = None
        ex.executor = MagicMock()
        ex.executor.clear_symbol_halt = MagicMock(return_value=0)

        await ex._handle_resume("telegram", {})

        ex.executor.clear_symbol_halt.assert_called_once()


class TestForceResumeClearsWithAudit:
    @pytest.mark.asyncio
    async def test_force_resume_clears_and_publishes_audit_alert(self):
        """force_resume 同时清 _halted_symbols 并 publish risk_alert。"""
        from agents.trading.executor import MultiExecutor

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor.get_halted_symbols.return_value = {
            "XLM-USDT-SWAP": {"reason": "sl_replace_failed", "halted_at": 0}
        }
        ex.executor.clear_symbol_halt = MagicMock(return_value=1)

        msg = {"type": "system_command", "payload": {
            "command": "force_resume", "source": "telegram"
        }}
        await ex.on_message(msg)

        ex.executor.clear_symbol_halt.assert_called_once()
        # publish risk_alert
        types = [p.get("type") for t, p in published if t == "risk_alert"]
        assert "force_resume_cleared_symbol_halts" in types
        alert = next(p for t, p in published
                       if t == "risk_alert" and p.get("type") == "force_resume_cleared_symbol_halts")
        assert "XLM-USDT-SWAP" in str(alert.get("cleared_symbols", []))
        assert alert.get("source") == "telegram"

    @pytest.mark.asyncio
    async def test_force_resume_empty_halts_no_audit_alert(self):
        """force_resume 但 _halted_symbols 已空,不发 audit alert。"""
        from agents.trading.executor import MultiExecutor

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor.get_halted_symbols.return_value = {}
        ex.executor.clear_symbol_halt = MagicMock(return_value=0)

        msg = {"type": "system_command", "payload": {
            "command": "force_resume", "source": "telegram"
        }}
        await ex.on_message(msg)

        types = [p.get("type") for t, p in published if t == "risk_alert"]
        assert "force_resume_cleared_symbol_halts" not in types


class TestCmdHalts:
    def _make_notifier(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._chat_id = "12345"
        return n

    @pytest.mark.asyncio
    async def test_cmd_halts_no_halts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        import os
        os.makedirs("data", exist_ok=True)
        import json
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({"halted_symbols": {}}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_halts()
        assert any("无 per-symbol halt" in s for s in sent)

    @pytest.mark.asyncio
    async def test_cmd_halts_one_symbol(self, tmp_path, monkeypatch):
        import time as time_mod
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        import os
        os.makedirs("data", exist_ok=True)
        import json
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({
                "halted_symbols": {
                    "XLM-USDT-SWAP": {
                        "reason": "sl_replace_failed",
                        "halted_at": time_mod.time() - 3600,  # 1 hour ago
                    }
                }
            }, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_halts()
        text = "\n".join(sent)
        assert "XLM-USDT-SWAP" in text
        assert "sl_replace_failed" in text
        # 显示 1h something ago
        assert "1h" in text or "60m" in text

    @pytest.mark.asyncio
    async def test_cmd_halts_health_file_missing(self, tmp_path, monkeypatch):
        """health.json 缺失时返回降级文案,不抛错。"""
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        import os
        os.makedirs("data", exist_ok=True)
        # 不创建文件

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_halts()
        text = "\n".join(sent)
        # 缺失时按"空"处理(_read_agent_health 返回 None,fallback 空字典 → 走"无 halt"分支)
        assert "无 per-symbol halt" in text


class TestFormatElapsed:
    def test_seconds(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        assert TelegramNotifier._format_elapsed(45) == "45s"

    def test_minutes(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        assert TelegramNotifier._format_elapsed(120) == "2m"

    def test_hours(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        assert TelegramNotifier._format_elapsed(3700) == "1h1m"


class TestCmdResumeSymbolViaBus:
    @pytest.mark.asyncio
    async def test_resume_symbol_publishes_system_command(self):
        from agents.trading.telegram_notifier import TelegramNotifier

        published = []
        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._chat_id = "12345"
        n.publish = fake_publish
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_resume_symbol(["XLM"])

        topics = [t for t, _ in published]
        assert "system_command" in topics
        cmd_payload = next(p for t, p in published if t == "system_command")
        assert cmd_payload["command"] == "resume_symbol"
        assert cmd_payload["symbol"] == "XLM-USDT-SWAP"
        assert cmd_payload["source"] == "telegram"
        assert any("XLM" in s for s in sent)

    @pytest.mark.asyncio
    async def test_resume_symbol_no_args_returns_usage(self):
        from agents.trading.telegram_notifier import TelegramNotifier

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._chat_id = "12345"
        published = []
        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))
        n.publish = fake_publish
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_resume_symbol([])

        # 不发 system_command
        assert all(t != "system_command" for t, _ in published)
        # 给出用法
        assert any("用法" in s or "usage" in s.lower() for s in sent)


class TestExecutorAgentResumeSymbol:
    @pytest.mark.asyncio
    async def test_resume_symbol_calls_clear_and_publishes_cleared_alert(self):
        from agents.trading.executor import MultiExecutor

        published = []
        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor._normalize_symbol.return_value = "XLM-USDT-SWAP"
        ex.executor.clear_symbol_halt = MagicMock(return_value=1)

        msg = {"type": "system_command", "payload": {
            "command": "resume_symbol",
            "symbol": "XLM-USDT-SWAP",
            "source": "telegram",
        }}
        await ex.on_message(msg)

        ex.executor.clear_symbol_halt.assert_called_once()
        # 第一个位置参数应该是 normalized symbol
        args = ex.executor.clear_symbol_halt.call_args.args
        assert args[0] == "XLM-USDT-SWAP"
        types = [p.get("type") for t, p in published if t == "risk_alert"]
        assert "symbol_halt_cleared" in types

    @pytest.mark.asyncio
    async def test_resume_symbol_not_found_publishes_not_found_alert(self):
        from agents.trading.executor import MultiExecutor

        published = []
        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor._normalize_symbol.return_value = "X-USDT-SWAP"
        ex.executor.clear_symbol_halt = MagicMock(return_value=0)

        msg = {"type": "system_command", "payload": {
            "command": "resume_symbol",
            "symbol": "X-USDT-SWAP",
            "source": "telegram",
        }}
        await ex.on_message(msg)

        types = [p.get("type") for t, p in published if t == "risk_alert"]
        assert "symbol_halt_not_found" in types

    @pytest.mark.asyncio
    async def test_resume_symbol_empty_symbol_no_op(self):
        from agents.trading.executor import MultiExecutor

        published = []
        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor.clear_symbol_halt = MagicMock()

        msg = {"type": "system_command", "payload": {
            "command": "resume_symbol",
            "symbol": "",
            "source": "telegram",
        }}
        await ex.on_message(msg)

        # 不调 clear_symbol_halt
        ex.executor.clear_symbol_halt.assert_not_called()


class TestTelegramAlertSubscriptions:
    @pytest.mark.asyncio
    async def test_handles_symbol_halt_cleared(self):
        from agents.trading.telegram_notifier import TelegramNotifier

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._daily_summary = {"alerts": 0}
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        msg = {"type": "risk_alert", "payload": {
            "type": "symbol_halt_cleared",
            "symbol": "XLM-USDT-SWAP",
            "source": "telegram",
        }}
        await n._handle_risk_alert(msg)
        text = "\n".join(sent)
        assert "XLM-USDT-SWAP" in text
        assert "解除" in text or "cleared" in text.lower()

    @pytest.mark.asyncio
    async def test_handles_symbol_halt_not_found(self):
        from agents.trading.telegram_notifier import TelegramNotifier

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._daily_summary = {"alerts": 0}
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        msg = {"type": "risk_alert", "payload": {
            "type": "symbol_halt_not_found",
            "symbol": "XYZ-USDT-SWAP",
            "source": "telegram",
        }}
        await n._handle_risk_alert(msg)
        text = "\n".join(sent)
        assert "XYZ-USDT-SWAP" in text
        assert "没有" in text or "not_found" in text.lower() or "未" in text

    @pytest.mark.asyncio
    async def test_handles_force_resume_cleared(self):
        from agents.trading.telegram_notifier import TelegramNotifier

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._daily_summary = {"alerts": 0}
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        msg = {"type": "risk_alert", "payload": {
            "type": "force_resume_cleared_symbol_halts",
            "cleared_symbols": ["XLM-USDT-SWAP (sl_replace_failed)"],
            "source": "telegram",
        }}
        await n._handle_risk_alert(msg)
        text = "\n".join(sent)
        assert "XLM-USDT-SWAP" in text
        assert "force_resume" in text or "强制" in text
