"""P2-16: DLQ 增长 MUST 经 telegram_alert 主动告警，不得仅静默写 agent_health.json。
"""
from __future__ import annotations

import os
import sys

import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _make_orch():
    from agents.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.logger = MagicMock()
    orch._prev_dlq_size = 0
    published = []
    bus = MagicMock()

    async def pub(src, topic, payload, scope=None):
        published.append((topic, payload))
    bus.publish = pub
    orch.bus = bus
    return orch, published


@pytest.mark.asyncio
async def test_dlq_growth_alerts():
    orch, published = _make_orch()
    await orch._maybe_alert_dlq_growth(3)
    alerts = [p for t, p in published
              if t == 'telegram_alert' and p.get('type') == 'bus_dlq_growth']
    assert alerts, "DLQ 增长应发 telegram_alert"
    assert alerts[0]['dlq_size'] == 3
    assert alerts[0]['delta'] == 3
    assert orch._prev_dlq_size == 3


@pytest.mark.asyncio
async def test_dlq_no_growth_no_alert():
    orch, published = _make_orch()
    orch._prev_dlq_size = 5
    await orch._maybe_alert_dlq_growth(5)
    assert not [p for t, p in published if t == 'telegram_alert']
    assert orch._prev_dlq_size == 5
