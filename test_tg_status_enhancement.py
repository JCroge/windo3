"""F-TG-004 测试矩阵：agent_health.json 路径 + Orchestrator 写入 + TG /status 增强。"""

import json
import os
import time
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class TestStatePathsAgentHealth:
    def test_live_path(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "live")
        from utils.state_paths import StatePaths, reset_state_paths
        reset_state_paths()
        paths = StatePaths.for_namespace("live")
        assert paths.agent_health == "data/agent_health.json"

    def test_testnet_path(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import StatePaths, reset_state_paths
        reset_state_paths()
        paths = StatePaths.for_namespace("testnet")
        assert paths.agent_health == "data/testnet_agent_health.json"

    def test_paper_path(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "paper")
        from utils.state_paths import StatePaths, reset_state_paths
        reset_state_paths()
        paths = StatePaths.for_namespace("paper")
        assert paths.agent_health == "data/paper_agent_health.json"

    def test_banner_includes_agent_health(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "live")
        from utils.state_paths import StatePaths, reset_state_paths
        reset_state_paths()
        paths = StatePaths.for_namespace("live")
        lines = paths.as_banner_lines()
        text = "\n".join(lines)
        assert "agent_health" in text
        assert "data/agent_health.json" in text


class TestMultiExecutorPublishHaltsSnapshot:
    @pytest.mark.asyncio
    async def test_publishes_halts_snapshot_with_payload(self):
        from agents.trading.executor import MultiExecutor

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor.get_halted_symbols.return_value = {
            "XLM-USDT-SWAP": {"reason": "sl_replace_failed", "halted_at": 100.0}
        }

        await ex._publish_halts_snapshot()

        topics = [t for t, _ in published]
        assert "halts_snapshot" in topics
        snap = next(p for t, p in published if t == "halts_snapshot")
        assert snap["halted_symbols"] == {
            "XLM-USDT-SWAP": {"reason": "sl_replace_failed", "halted_at": 100.0}
        }
        assert "ts" in snap

    @pytest.mark.asyncio
    async def test_publish_halts_snapshot_handles_exception(self):
        """publish 失败时 logger.warning,不抛。"""
        from agents.trading.executor import MultiExecutor

        async def failing_publish(*args, **kwargs):
            raise RuntimeError("bus down")

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = failing_publish
        ex.executor = MagicMock()
        ex.executor.get_halted_symbols.return_value = {}

        # 不抛
        await ex._publish_halts_snapshot()
        ex.logger.warning.assert_called()


class TestOrchestratorWritesAgentHealth:
    def _make_orchestrator(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        # 切换 cwd 到 tmp_path 让 data/ 写入临时目录
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        from agents.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch.logger = MagicMock()
        orch._tasks = []
        orch._research_agents = []
        orch._trading_agents = []
        orch._latest_halts_snapshot = {}
        return orch

    def test_write_agent_health_creates_file_with_schema(self, tmp_path, monkeypatch):
        orch = self._make_orchestrator(tmp_path, monkeypatch)
        # mock task: done=False (alive)
        t1 = MagicMock()
        t1.done.return_value = False
        t2 = MagicMock()
        t2.done.return_value = False
        t3 = MagicMock()
        t3.done.return_value = False
        orch._tasks = [t1, t2, t3]
        orch._research_agents = [object()] * 5
        orch._trading_agents = [object()] * 10
        orch._latest_halts_snapshot = {"XLM-USDT-SWAP": {"reason": "x"}}

        orch._write_agent_health()

        path = "data/testnet_agent_health.json"
        assert os.path.exists(path)
        with open(path) as f:
            health = json.load(f)
        assert health["agents_registered"] == 15
        assert health["tasks_alive"] == 3
        assert health["tasks_failed"] == 0
        assert health["halted_symbols"] == {"XLM-USDT-SWAP": {"reason": "x"}}
        assert "ts" in health
        assert "bus_dlq_size" in health  # 0 if no bus

    def test_write_agent_health_counts_failed_tasks(self, tmp_path, monkeypatch):
        orch = self._make_orchestrator(tmp_path, monkeypatch)
        # 2 个 alive, 1 个 done with exception, 1 个 done without exception
        t_alive_1 = MagicMock()
        t_alive_1.done.return_value = False
        t_alive_2 = MagicMock()
        t_alive_2.done.return_value = False
        t_failed = MagicMock()
        t_failed.done.return_value = True
        t_failed.exception.return_value = RuntimeError("boom")
        t_finished = MagicMock()
        t_finished.done.return_value = True
        t_finished.exception.return_value = None
        orch._tasks = [t_alive_1, t_alive_2, t_failed, t_finished]

        orch._write_agent_health()

        with open("data/testnet_agent_health.json") as f:
            health = json.load(f)
        assert health["tasks_alive"] == 2
        assert health["tasks_failed"] == 1

    def test_write_agent_health_ignores_cancelled_tasks(self, tmp_path, monkeypatch):
        orch = self._make_orchestrator(tmp_path, monkeypatch)
        t_alive = MagicMock()
        t_alive.done.return_value = False
        t_cancelled = MagicMock()
        t_cancelled.done.return_value = True
        t_cancelled.exception.side_effect = asyncio.CancelledError()
        orch._tasks = [t_alive, t_cancelled]

        orch._write_agent_health()

        with open("data/testnet_agent_health.json") as f:
            health = json.load(f)
        assert health["tasks_alive"] == 1
        assert health["tasks_failed"] == 0

    def test_write_agent_health_failure_does_not_raise(self, tmp_path, monkeypatch):
        orch = self._make_orchestrator(tmp_path, monkeypatch)
        # 让 atomic_write_json 失败
        with patch("utils.atomic_io.atomic_write_json", side_effect=OSError("disk full")):
            orch._write_agent_health()  # 不抛
        orch.logger.warning.assert_called()

    def test_orchestrator_caches_halts_snapshot_event(self, tmp_path, monkeypatch):
        orch = self._make_orchestrator(tmp_path, monkeypatch)
        msg = {"type": "halts_snapshot", "payload": {
            "halted_symbols": {"X": {"reason": "y"}}, "ts": 1.0
        }}
        # _on_halts_snapshot 是新方法
        orch._on_halts_snapshot(msg)
        assert orch._latest_halts_snapshot == {"X": {"reason": "y"}}

    def test_write_agent_health_reads_dlq_from_bus(self, tmp_path, monkeypatch):
        orch = self._make_orchestrator(tmp_path, monkeypatch)
        # mock MessageBus._dead_letter
        from agents.message_bus import MessageBus
        bus = MessageBus.get_instance()
        # 强制注入 _dead_letter
        from collections import deque
        bus._dead_letter = deque(["x", "y", "z"])
        try:
            orch._write_agent_health()
            with open("data/testnet_agent_health.json") as f:
                health = json.load(f)
            assert health["bus_dlq_size"] == 3
        finally:
            bus._dead_letter.clear()


class TestStatusEnhancement:
    def _make_notifier(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._chat_id = "12345"
        n._start_time = time.time() - 3600  # 1h uptime
        n._daily_summary = {"trades": 0, "pnl": 0.0, "alerts": 0}
        return n

    @pytest.mark.asyncio
    async def test_status_includes_agents_line(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({
                "ts": time.time(),
                "agents_registered": 17,
                "tasks_alive": 17,
                "tasks_failed": 0,
                "halted_symbols": {},
                "bus_dlq_size": 0,
            }, f)
        # 同步建必要的状态文件让 _cmd_status 不崩
        for fn in ("testnet_positions.json", "testnet_riskguard_state.json", "testnet_halt_state.json"):
            with open(f"data/{fn}", "w") as f:
                json.dump({}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_status()

        text = "\n".join(sent)
        assert "Agents" in text
        assert "17" in text  # agents_registered

    @pytest.mark.asyncio
    async def test_status_includes_per_symbol_halt_line_zero(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({
                "agents_registered": 17, "tasks_alive": 17, "tasks_failed": 0,
                "halted_symbols": {}, "bus_dlq_size": 0,
            }, f)
        for fn in ("testnet_positions.json", "testnet_riskguard_state.json", "testnet_halt_state.json"):
            with open(f"data/{fn}", "w") as f:
                json.dump({}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_status()

        text = "\n".join(sent)
        assert "Per-symbol halt: 0" in text

    @pytest.mark.asyncio
    async def test_status_includes_per_symbol_halt_line_one(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({
                "agents_registered": 17, "tasks_alive": 17, "tasks_failed": 0,
                "halted_symbols": {"XLM-USDT-SWAP": {"reason": "x"}},
                "bus_dlq_size": 0,
            }, f)
        for fn in ("testnet_positions.json", "testnet_riskguard_state.json", "testnet_halt_state.json"):
            with open(f"data/{fn}", "w") as f:
                json.dump({}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_status()

        text = "\n".join(sent)
        assert "Per-symbol halt: 1" in text
        assert "XLM" in text

    @pytest.mark.asyncio
    async def test_status_truncates_many_halts(self, tmp_path, monkeypatch):
        # 7 个 halt
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        halts = {f"S{i}-USDT-SWAP": {"reason": "r"} for i in range(7)}
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({
                "agents_registered": 17, "tasks_alive": 17, "tasks_failed": 0,
                "halted_symbols": halts, "bus_dlq_size": 0,
            }, f)
        for fn in ("testnet_positions.json", "testnet_riskguard_state.json", "testnet_halt_state.json"):
            with open(f"data/{fn}", "w") as f:
                json.dump({}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_status()

        text = "\n".join(sent)
        assert "Per-symbol halt: 7" in text
        assert "+2" in text or "…" in text

    @pytest.mark.asyncio
    async def test_status_health_missing_falls_back(self, tmp_path, monkeypatch):
        """health.json 缺失时仍返回基础 status,health 行降级文案。"""
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        # 不创建 health.json
        for fn in ("testnet_positions.json", "testnet_riskguard_state.json", "testnet_halt_state.json"):
            with open(f"data/{fn}", "w") as f:
                json.dump({}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_status()

        text = "\n".join(sent)
        # 基础字段(运行时长 / 持仓 / 熔断)仍在
        assert "运行" in text
        # health 行降级
        assert "缺失" in text or "?" in text


class TestTelegramStatusHaltMatrix(TestStatusEnhancement):
    def _write_status_files(self, tmp_path, halt_state, riskguard_state, health=None):
        os.makedirs(tmp_path / "data", exist_ok=True)
        with open(tmp_path / "data/testnet_positions.json", "w") as f:
            json.dump({}, f)
        with open(tmp_path / "data/testnet_halt_state.json", "w") as f:
            json.dump(halt_state, f)
        with open(tmp_path / "data/testnet_riskguard_state.json", "w") as f:
            json.dump(riskguard_state, f)
        with open(tmp_path / "data/testnet_agent_health.json", "w") as f:
            json.dump(health or {
                "agents_registered": 17,
                "tasks_alive": 17,
                "tasks_failed": 0,
                "halted_symbols": {},
                "bus_dlq_size": 0,
            }, f)

    @pytest.mark.asyncio
    async def test_global_protection_halt_tactical_not_paused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        import utils.halt_state as hs_mod
        reset_state_paths()
        hs_mod._instance = None
        monkeypatch.chdir(tmp_path)
        self._write_status_files(
            tmp_path,
            {
                "halted": True,
                "reason": "okx_sl_algo_unresolved:WLD-USDT-SWAP",
                "reconciliation_pending": False,
                "reconciliation_result": None,
            },
            {
                "tactical_circuit": {
                    "daily_pnl": -2.6721,
                    "loss_streak": 1,
                    "pause_until": 0,
                    "pause_reason": "",
                }
            },
            health={"halted_symbols": {"WLD-USDT-SWAP": {"reason": "sl_algo_unresolved"}}},
        )
        n = self._make_notifier()
        sent = []

        async def fake_send(text):
            sent.append(text)

        n._send_message = fake_send

        await n._cmd_status()

        text = "\n".join(sent)
        assert "全局熔断: 是" in text
        assert "okx_sl_algo_unresolved:WLD-USDT-SWAP" in text
        assert "Per-symbol halt: 1" in text
        assert "Tactical circuit: 否" in text

    @pytest.mark.asyncio
    async def test_tactical_paused_global_clear(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        import utils.halt_state as hs_mod
        reset_state_paths()
        hs_mod._instance = None
        monkeypatch.chdir(tmp_path)
        self._write_status_files(
            tmp_path,
            {"halted": False, "reason": ""},
            {
                "tactical_circuit": {
                    "daily_pnl": -12.0,
                    "loss_streak": 3,
                    "pause_until": time.time() + 3600,
                    "pause_reason": "loss_streak",
                }
            },
        )
        n = self._make_notifier()
        sent = []

        async def fake_send(text):
            sent.append(text)

        n._send_message = fake_send

        await n._cmd_status()

        text = "\n".join(sent)
        assert "全局熔断: 否" in text
        assert "Tactical circuit: 是" in text
        assert "loss_streak" in text
