"""Tests for Telegram alert routing — pullback_unfilled / paper_unfilled.

Covers risk-alert-routing spec Req1 (critical_types) + Req2 (source-based prefix),
all 6 scenarios.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from agents.trading.telegram_notifier import TelegramNotifier


def _tg(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'x')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '1')
    tg = TelegramNotifier({})
    tg._send_message = AsyncMock()
    return tg


# ---------------------------------------------------------------------------
# Step 7.1 — pullback_unfilled (live) sends [实盘] message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pullback_unfilled_live_prefix(monkeypatch):
    tg = _tg(monkeypatch)
    msg = {'payload': {'type': 'pullback_unfilled', 'source': 'executor',
                       'symbol': 'WLD-USDT', 'side': 'short',
                       'limit_price': 0.4045, 'request_id': 'R1',
                       'timeout_sec': 1800}}
    await tg._handle_risk_alert(msg)
    tg._send_message.assert_called_once()
    text = tg._send_message.call_args[0][0]
    assert '[实盘]' in text
    assert 'WLD-USDT' in text
    assert 'R1' in text
    # Either 区间 or 限价 line should show the price
    assert '0.4045' in text or '限价' in text


# ---------------------------------------------------------------------------
# Step 7.2 — paper_unfilled (paper) sends [模拟] message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paper_unfilled_paper_prefix(monkeypatch):
    tg = _tg(monkeypatch)
    msg = {'payload': {'type': 'paper_unfilled', 'source': 'paper_executor',
                       'symbol': 'WLD-USDT', 'side': 'short',
                       'entry_zone': [0.4043, 0.4047], 'request_id': 'R1',
                       'timeout_sec': 1800}}
    await tg._handle_risk_alert(msg)
    tg._send_message.assert_called_once()
    text = tg._send_message.call_args[0][0]
    assert '[模拟]' in text


# ---------------------------------------------------------------------------
# Step 7.3 — paper_unfilled with subtype=no_tick uses 行情失联 variant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paper_unfilled_no_tick_variant(monkeypatch):
    tg = _tg(monkeypatch)
    msg = {'payload': {'type': 'paper_unfilled', 'source': 'paper_executor',
                       'symbol': 'WLD-USDT', 'side': 'short',
                       'entry_zone': [0.4043, 0.4047], 'request_id': 'R1',
                       'timeout_sec': 1800, 'subtype': 'no_tick'}}
    await tg._handle_risk_alert(msg)
    tg._send_message.assert_called_once()
    text = tg._send_message.call_args[0][0]
    assert '行情失联' in text


# ---------------------------------------------------------------------------
# Step 7.4 — pullback_unfilled missing source defaults to live + warning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pullback_unfilled_missing_source_fail_safe(monkeypatch):
    tg = _tg(monkeypatch)
    tg.logger = MagicMock()
    msg = {'payload': {'type': 'pullback_unfilled',
                       'symbol': 'WLD-USDT', 'side': 'short',
                       'entry_zone': [0.4043, 0.4047], 'request_id': 'R1',
                       'timeout_sec': 1800}}
    await tg._handle_risk_alert(msg)
    tg._send_message.assert_called_once()
    text = tg._send_message.call_args[0][0]
    assert '[实盘]' in text
    tg.logger.warning.assert_called()


# ---------------------------------------------------------------------------
# Step 7.5 — unknown alert type does not send (regression guard)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_type_does_not_send(monkeypatch):
    tg = _tg(monkeypatch)
    msg = {'payload': {'type': 'some_random_type', 'symbol': 'X'}}
    await tg._handle_risk_alert(msg)
    tg._send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Step 7.6 — paper and live alerts in sequence yield two distinguishable messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paper_and_live_unfilled_distinguished(monkeypatch):
    tg = _tg(monkeypatch)
    base = {'symbol': 'WLD-USDT', 'side': 'short',
            'entry_zone': [0.4043, 0.4047], 'request_id': 'R1', 'timeout_sec': 1800}
    await tg._handle_risk_alert({'payload': {'type': 'pullback_unfilled',
                                              'source': 'executor', **base}})
    await tg._handle_risk_alert({'payload': {'type': 'paper_unfilled',
                                              'source': 'paper_executor', **base}})
    assert tg._send_message.call_count == 2
    text_live = tg._send_message.call_args_list[0][0][0]
    text_paper = tg._send_message.call_args_list[1][0][0]
    assert '[实盘]' in text_live
    assert '[模拟]' in text_paper
    assert text_live != text_paper
