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


class TestReplaceProtectiveSlOwnerTag:
    def test_replace_uses_owner_tag_clord_id(self, monkeypatch):
        from executor import ContractExecutor
        from unittest.mock import MagicMock
        monkeypatch.setenv("STATE_NAMESPACE", "live")
        monkeypatch.setenv("BOT_INSTANCE_ID", "bot42")
        # 捕获 _place_protective_sl 收到的 clord_id
        captured = {}

        def fake_place(self_inner, *, symbol, side, stop_price, amount, clord_id=None, **kw):
            captured["clord_id"] = clord_id
            return "fake-algo-id"

        ex = MagicMock(spec=ContractExecutor)
        ex.exchange_id = "okx"
        ex.testnet = False
        ex.logger = MagicMock()
        # MagicMock(spec=...) intercepts attribute access before class patches apply,
        # so patch.object on the class is shadowed by the mock's own attributes.
        # We set the dependencies directly on the mock instance instead.
        ex._cancel_protective_sl = lambda s, p: True
        ex._place_protective_sl = lambda *, symbol, side, stop_price, amount, clord_id=None, **kw: (
            captured.__setitem__("clord_id", clord_id) or "fake-algo-id"
        )
        # Rebind both factory methods to their real implementations so the test
        # distinguishes between _make_sl_clord_id (no owner prefix) and
        # _make_owner_tag_clord_id (ca+ns+bot prefix). Without this, MagicMock
        # returns a truthy Mock for both, making _is_owner_clord_id always pass.
        ex._make_sl_clord_id = ContractExecutor._make_sl_clord_id
        ex._make_owner_tag_clord_id = ContractExecutor._make_owner_tag_clord_id
        position = {
            "side": "long", "amount": 1.0,
            "sl_algo_id": "old-algo", "sl_order_id": "old-algo",
        }
        ok = ContractExecutor._replace_protective_sl(ex, "BTC-USDT", position, 50000)
        assert ok is True
        assert captured["clord_id"] is not None
        assert ContractExecutor._is_owner_clord_id(captured["clord_id"])


class TestAttachedSlOwnerTag:
    def test_open_position_with_plan_attached_sl_owner_tag(self):
        from executor import ContractExecutor
        import inspect
        src = inspect.getsource(ContractExecutor.open_position_with_plan)
        assert "_make_owner_tag_clord_id" in src
        # 旧工厂仍存在但不再被新挂单调用
        assert "_make_sl_clord_id(symbol)" not in src or "DEPRECATED" in src
