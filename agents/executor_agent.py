"""交易执行 Agent - 接收决策并执行合约交易"""

import os
from dotenv import load_dotenv
from agents.base import BaseAgent
from executor import ContractExecutor
from risk_manager import RiskManager

load_dotenv()


class ExecutorAgent(BaseAgent):
    name = "executor"
    subscriptions = ["trade_decision"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.executor = None
        self.risk_manager = None
        self.min_confidence = 60

    async def setup(self):
        exchange_id = self.config.get('exchange', 'okx')
        leverage = self.config.get('leverage', 3)

        self.executor = ContractExecutor(
            exchange_id=exchange_id,
            api_key=os.getenv('OKX_API_KEY') if exchange_id == 'okx' else os.getenv('BINANCE_API_KEY'),
            secret=os.getenv('OKX_SECRET') if exchange_id == 'okx' else os.getenv('BINANCE_SECRET'),
            password=os.getenv('OKX_PASSWORD') if exchange_id == 'okx' else None,
            testnet=self.config.get('use_testnet', False),
            leverage=leverage
        )

        self.risk_manager = self.executor.risk_manager
        self.logger.info(f"执行Agent就绪: {exchange_id} {leverage}x杠杆")

    async def on_message(self, msg: dict):
        if msg['type'] != 'trade_decision':
            return

        decision = msg['payload']
        await self._execute_decision(decision)

    async def _execute_decision(self, decision: dict):
        action = decision.get('action', 'hold')
        confidence = decision.get('confidence', 0)
        symbol = decision.get('symbol', self.config.get('symbol'))
        size_pct = decision.get('size_pct', 0.5)

        if action == 'hold':
            return

        if confidence < self.min_confidence:
            self.logger.info(f"[执行] 跳过：置信度不足 ({confidence} < {self.min_confidence})")
            return

        balance = self._get_balance()
        can_trade, reason = self.risk_manager.check_can_trade(balance)
        if not can_trade:
            self.logger.warning(f"[执行] 风控拒绝: {reason}")
            await self.publish("execution_result", {
                "status": "rejected", "reason": reason, "action": action
            })
            return

        max_amount = self.config.get('max_trade_amount', 10)
        amount = max_amount * size_pct

        result = None
        position = self.executor.get_position(symbol)

        if action == 'open_long' and position is None:
            result = self.executor.open_long(symbol, amount)
            self.logger.info(f"[执行] 开多 {symbol} {amount:.2f} USDT → {result}")

        elif action == 'open_short' and position is None:
            result = self.executor.open_short(symbol, amount)
            self.logger.info(f"[执行] 开空 {symbol} {amount:.2f} USDT → {result}")

        elif action == 'close' and position is not None:
            result = self.executor.close_position(symbol)
            self.logger.info(f"[执行] 平仓 {symbol} → {result}")

        if result:
            await self.publish("execution_result", {
                "status": "executed",
                "action": action,
                "symbol": symbol,
                "amount": amount,
                "result": result,
                "confidence": confidence,
            })

    def _get_balance(self) -> float:
        try:
            balance = self.executor.exchange.fetch_balance()
            return float(balance.get('total', {}).get('USDT', 0))
        except Exception as e:
            self.logger.error(f"获取余额失败: {e}")
            return 0.0

    async def tick(self):
        import asyncio
        await asyncio.sleep(5)

        symbol = self.config.get('symbol')
        position = self.executor.get_position(symbol)
        if position:
            trigger = self.executor.check_stop_loss_take_profit(symbol)
            if trigger:
                self.logger.info(f"[风控] 触发{trigger}，自动平仓")
                result = self.executor.close_position(symbol)
                await self.publish("execution_result", {
                    "status": "stop_triggered",
                    "trigger": trigger,
                    "result": result,
                })
