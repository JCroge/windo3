from unittest.mock import MagicMock
import pytest
from agents.base import BaseAgent


class _DummyAgent(BaseAgent):
    name = "dummy_setup_test"

    def __init__(self, fail: bool):
        super().__init__({})
        self._fail = fail

    async def on_message(self, msg):  # 满足 ABC
        pass

    async def setup(self):
        if self._fail:
            raise RuntimeError("boom")


async def test_setup_failure_logged_with_traceback_and_reraised():
    a = _DummyAgent(fail=True)
    a.logger = MagicMock()
    with pytest.raises(RuntimeError):
        await a.run()
    a.logger.critical.assert_called_once()
    msg = a.logger.critical.call_args[0][0]
    assert "setup 失败" in msg
    assert "Traceback" in msg  # traceback.format_exc() 输出含 "Traceback"


async def test_setup_success_does_not_log_critical():
    a = _DummyAgent(fail=False)
    a.logger = MagicMock()

    async def _noop():
        return

    a._message_loop = _noop      # 替换为立即返回，避免 run() 永久阻塞
    a._periodic_loop = _noop
    await a.run()
    a.logger.critical.assert_not_called()
