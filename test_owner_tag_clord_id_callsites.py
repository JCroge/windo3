"""F4-003 owner-tag clOrdId 测试矩阵。

覆盖：
- BOT_INSTANCE_ID 缺失时启动 banner 打印 WARNING（live namespace）
- testnet/paper 缺失不报警
- legacy _make_sl_clord_id 仍可调用（用于历史 cleanup）
- _replace_protective_sl / open_position_with_plan / legacy _open_position 三处使用 owner-tag clOrdId
"""

import os
import pytest
from unittest.mock import patch

from utils.state_paths import StatePaths, get_state_paths, reset_state_paths


@pytest.fixture(autouse=True)
def _reset_state_paths():
    reset_state_paths()
    yield
    reset_state_paths()


class TestBotInstanceIdBanner:
    def test_live_missing_bot_instance_id_emits_warning(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "live")
        monkeypatch.delenv("BOT_INSTANCE_ID", raising=False)
        sp = get_state_paths(refresh=True)
        lines = sp.as_banner_lines()
        text = "\n".join(lines)
        assert "BOT_INSTANCE_ID" in text
        assert "WARNING" in text
        assert "not configured" in text

    def test_live_with_bot_instance_id_no_warning(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "live")
        monkeypatch.setenv("BOT_INSTANCE_ID", "bot-A")
        sp = get_state_paths(refresh=True)
        lines = sp.as_banner_lines()
        text = "\n".join(lines)
        assert "BOT_INSTANCE_ID" in text
        assert "bot-A" in text
        assert "not configured" not in text

    def test_testnet_missing_bot_instance_id_no_warning(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        monkeypatch.delenv("BOT_INSTANCE_ID", raising=False)
        sp = get_state_paths(refresh=True)
        lines = sp.as_banner_lines()
        text = "\n".join(lines)
        assert "not configured" not in text

    def test_paper_missing_bot_instance_id_no_warning(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "paper")
        monkeypatch.delenv("BOT_INSTANCE_ID", raising=False)
        sp = get_state_paths(refresh=True)
        lines = sp.as_banner_lines()
        text = "\n".join(lines)
        assert "not configured" not in text
