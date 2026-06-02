"""F-TG-003 /pnl + /pnl_id 测试矩阵。"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def _make_notifier_with_ledger(pending_events=None):
    """构造带 ledger mock 的 TelegramNotifier。"""
    from agents.trading.telegram_notifier import TelegramNotifier
    n = TelegramNotifier.__new__(TelegramNotifier)
    n.logger = MagicMock()
    n._chat_id = "12345"
    n._ledger = MagicMock()
    n._ledger.find_pending_external_closes.return_value = pending_events or []
    return n


class TestResolvePendingHelper:
    def test_resolve_one_candidate_returns_ok(self):
        n = _make_notifier_with_ledger([
            {"event_id": "e1", "symbol": "XLM-USDT-SWAP", "pnl_status": "pending"},
            {"event_id": "e2", "symbol": "BTC-USDT-SWAP", "pnl_status": "pending"},
        ])
        result = n._resolve_pending_for_pnl_correction(
            filter_fn=lambda ev: ev["symbol"] == "XLM-USDT-SWAP",
            label="symbol=XLM",
        )
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["event_id"] == "e1"

    def test_resolve_zero_candidates_returns_not_found(self):
        n = _make_notifier_with_ledger([
            {"event_id": "e1", "symbol": "BTC-USDT-SWAP"},
        ])
        result = n._resolve_pending_for_pnl_correction(
            filter_fn=lambda ev: ev["symbol"] == "XLM-USDT-SWAP",
            label="symbol=XLM",
        )
        assert result["status"] == "not_found"
        assert "XLM" in result["error_msg"]

    def test_resolve_multiple_candidates_returns_multiple(self):
        n = _make_notifier_with_ledger([
            {"event_id": "e1", "symbol": "XLM-USDT-SWAP"},
            {"event_id": "e2", "symbol": "XLM-USDT-SWAP"},
        ])
        result = n._resolve_pending_for_pnl_correction(
            filter_fn=lambda ev: ev["symbol"] == "XLM-USDT-SWAP",
            label="symbol=XLM",
        )
        assert result["status"] == "multiple"
        assert len(result["candidates"]) == 2

    def test_resolve_no_ledger_returns_error(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._ledger = None
        result = n._resolve_pending_for_pnl_correction(
            filter_fn=lambda ev: True,
            label="any",
        )
        assert result["status"] == "error"
        assert "ledger" in result["error_msg"]


class TestCmdPnl:
    @pytest.mark.asyncio
    async def test_pnl_one_candidate_writes_correction(self):
        n = _make_notifier_with_ledger([
            {
                "event_id": "e1", "symbol": "XLM-USDT-SWAP", "side": "long",
                "position_id": "pos-1", "entry_request_id": "req-1",
                "estimated_pnl": -0.5, "close_match_key": "K1",
                "pnl_status": "pending",
            }
        ])
        n._ledger.apply_pnl_resolution.return_value = {
            "event_id": "corr-1", "supersedes_event_id": "e1"
        }
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl(["XLM", "0.42"])

        n._ledger.apply_pnl_resolution.assert_called_once()
        resolution = n._ledger.apply_pnl_resolution.call_args[0][0]
        assert resolution["realized_pnl_net_usdt"] == 0.42
        assert resolution["pnl_source"] == "manual_tg_review"
        text = "\n".join(sent)
        assert "0.42" in text or "+0.4200" in text

    @pytest.mark.asyncio
    async def test_pnl_zero_candidate_rejects(self):
        n = _make_notifier_with_ledger([])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl(["XLM", "0.42"])

        n._ledger.apply_pnl_resolution.assert_not_called()
        text = "\n".join(sent)
        assert "未找到" in text or "not_found" in text.lower()

    @pytest.mark.asyncio
    async def test_pnl_multiple_candidate_lists_event_ids(self):
        n = _make_notifier_with_ledger([
            {"event_id": "abcdef12", "symbol": "XLM-USDT-SWAP"},
            {"event_id": "fedcba98", "symbol": "XLM-USDT-SWAP"},
        ])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl(["XLM", "0.42"])

        n._ledger.apply_pnl_resolution.assert_not_called()
        text = "\n".join(sent)
        assert "/pnl_id" in text
        assert "abcdef12" in text or "fedcba98" in text

    @pytest.mark.asyncio
    async def test_pnl_invalid_net_pnl_rejects(self):
        n = _make_notifier_with_ledger([])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl(["XLM", "abc"])

        n._ledger.find_pending_external_closes.assert_not_called()
        text = "\n".join(sent)
        assert "用法" in text or "usage" in text.lower()

    @pytest.mark.asyncio
    async def test_pnl_missing_args_rejects(self):
        n = _make_notifier_with_ledger([])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl(["XLM"])

        n._ledger.find_pending_external_closes.assert_not_called()
        text = "\n".join(sent)
        assert "用法" in text


class TestCmdPnlReason:
    @pytest.mark.asyncio
    async def test_pnl_with_reason_writes_field(self):
        n = _make_notifier_with_ledger([
            {"event_id": "e1", "symbol": "XLM-USDT-SWAP", "pnl_status": "pending"}
        ])
        n._ledger.apply_pnl_resolution.return_value = {"event_id": "c1"}
        sent = []
        n._send_message = AsyncMock()

        await n._cmd_pnl(["XLM", "0.42", "OKX", "bills", "late"])

        resolution = n._ledger.apply_pnl_resolution.call_args[0][0]
        assert "OKX bills late" in resolution["manual_correction_reason"]

    @pytest.mark.asyncio
    async def test_pnl_without_reason_uses_default(self):
        n = _make_notifier_with_ledger([
            {"event_id": "e1", "symbol": "XLM-USDT-SWAP", "pnl_status": "pending"}
        ])
        n._ledger.apply_pnl_resolution.return_value = {"event_id": "c1"}
        n._send_message = AsyncMock()

        await n._cmd_pnl(["XLM", "0.42"])

        resolution = n._ledger.apply_pnl_resolution.call_args[0][0]
        assert resolution["manual_correction_reason"]  # 非空


class TestCmdPnlId:
    @pytest.mark.asyncio
    async def test_pnl_id_exact_match_writes_correction(self):
        n = _make_notifier_with_ledger([
            {"event_id": "abc-123", "symbol": "XLM-USDT-SWAP",
             "pnl_status": "pending", "side": "long",
             "position_id": "pos-1"},
            {"event_id": "def-456", "symbol": "XLM-USDT-SWAP"},
        ])
        n._ledger.apply_pnl_resolution.return_value = {
            "event_id": "corr-1", "supersedes_event_id": "abc-123"
        }
        n._send_message = AsyncMock()

        await n._cmd_pnl_id(["abc-123", "0.42"])

        n._ledger.apply_pnl_resolution.assert_called_once()
        resolution = n._ledger.apply_pnl_resolution.call_args[0][0]
        assert resolution["position_id"] == "pos-1"  # 来自 abc-123,不是 def-456

    @pytest.mark.asyncio
    async def test_pnl_id_not_found_rejects(self):
        n = _make_notifier_with_ledger([
            {"event_id": "abc-123", "symbol": "XLM-USDT-SWAP"}
        ])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl_id(["zzz-999", "0.42"])

        n._ledger.apply_pnl_resolution.assert_not_called()
        text = "\n".join(sent)
        assert "zzz-999" in text or "未找到" in text

    @pytest.mark.asyncio
    async def test_pnl_id_invalid_net_pnl_rejects(self):
        n = _make_notifier_with_ledger([])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl_id(["abc-123", "abc"])

        n._ledger.find_pending_external_closes.assert_not_called()
        text = "\n".join(sent)
        assert "用法" in text

    @pytest.mark.asyncio
    async def test_pnl_id_missing_args_rejects(self):
        n = _make_notifier_with_ledger([])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl_id(["abc-123"])

        n._ledger.find_pending_external_closes.assert_not_called()
        text = "\n".join(sent)
        assert "用法" in text
