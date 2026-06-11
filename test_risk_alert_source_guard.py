"""P2-06: live executor _handle_risk_alert 必须以 source 守卫拒绝 paper 来源事件。

paper 与 live 共用 risk_alert topic；隔离 MUST 结构性（source 守卫），
不得依赖 paper alert type 恰好不在 live 白名单内。
"""
from __future__ import annotations

import os
import sys

import pytest
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestPaperSourceGuard:
    def _make(self):
        from agents.trading.executor import MultiExecutor
        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor._normalize_symbol = lambda s: "X-USDT-SWAP"
        ex.executor.get_position = MagicMock(return_value={'request_id': 'r1'})
        ex.executor.positions = {"X-USDT-SWAP": {'request_id': 'r1'}}
        ex.executor.close_position = MagicMock(return_value={'ok': True})
        ex.publish = AsyncMock()
        ex._build_execution_result = MagicMock(return_value={})
        return ex

    @pytest.mark.asyncio
    async def test_paper_source_alert_does_not_trigger_live_close(self):
        ex = self._make()
        await ex._handle_risk_alert({
            'type': 'emergency_close', 'symbol': 'X-USDT-SWAP',
            'source': 'paper_executor',
        })
        ex.executor.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_live_source_alert_still_processed(self):
        ex = self._make()
        await ex._handle_risk_alert({
            'type': 'emergency_close', 'symbol': 'X-USDT-SWAP',
            'source': 'portfolio_risk_guard',
        })
        ex.executor.close_position.assert_called_once()
