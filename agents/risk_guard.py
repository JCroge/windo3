"""风控盯盘 Agent - 实时监控持仓和市场异常"""

import os
import asyncio
from dotenv import load_dotenv
from agents.base import BaseAgent
from risk_manager import RiskManager

load_dotenv()


class RiskGuardAgent(BaseAgent):
    name = "risk_guard"
    subscriptions = ["execution_result", "market_data"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.risk_manager = RiskManager()
        self._last_price = None
        self._price_history = []
        self._alert_cooldown = 300
        self._last_alert_time = 0

    async def setup(self):
        self.logger.info("风控盯盘Agent就绪")

    async def on_message(self, msg: dict):
        if msg['type'] == 'market_data':
            price = msg['payload'].get('latest_price')
            if price:
                self._update_price(price)

        elif msg['type'] == 'execution_result':
            status = msg['payload'].get('status')
            if status == 'executed':
                self.logger.info(f"[风控] 记录新交易: {msg['payload'].get('action')}")

    def _update_price(self, price: float):
        self._last_price = price
        self._price_history.append(price)
        if len(self._price_history) > 60:
            self._price_history = self._price_history[-60:]

    async def tick(self):
        await asyncio.sleep(10)

        if len(self._price_history) >= 5:
            await self._check_flash_crash()
            await self._check_volatility_spike()

    async def _check_flash_crash(self):
        if len(self._price_history) < 3:
            return

        recent = self._price_history[-3:]
        drop_pct = (recent[0] - recent[-1]) / recent[0] * 100

        if abs(drop_pct) > 3:
            import time
            now = time.time()
            if now - self._last_alert_time > self._alert_cooldown:
                self._last_alert_time = now
                direction = "暴跌" if drop_pct > 0 else "暴涨"
                self.logger.warning(f"[风控警报] 短期{direction} {abs(drop_pct):.1f}%!")
                await self.publish("risk_alert", {
                    "type": "flash_move",
                    "direction": direction,
                    "magnitude_pct": abs(drop_pct),
                    "action": "review_positions"
                })

    async def _check_volatility_spike(self):
        if len(self._price_history) < 10:
            return

        recent = self._price_history[-10:]
        max_p = max(recent)
        min_p = min(recent)
        volatility = (max_p - min_p) / min_p * 100

        if volatility > 5:
            self.logger.warning(f"[风控] 波动率异常: {volatility:.1f}% (10期)")
