"""智能交易执行 Agent - 消费Judge plan，支持动态杠杆/限价单/交易所止损/仓位同步"""

import os
import asyncio
from dotenv import load_dotenv
from agents.base import BaseAgent
from executor import ContractExecutor

load_dotenv()


class MultiExecutor(BaseAgent):
    name = "executor"
    subscriptions = ["trade_decision:*", "risk_alert", "daily_hard_stop_triggered"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.executor = None
        self.min_confidence = config.get('min_confidence', 60) if config else 60
        self._sync_counter = 0
        self._trading_halted = False

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
        self.logger.info(f"智能执行Agent就绪: {exchange_id}, 默认杠杆{leverage}x")

    async def on_message(self, msg: dict):
        if msg['type'] == 'daily_hard_stop_triggered':
            self._trading_halted = True
            self.logger.critical("[熔断] 停止接收新交易决策")
            return

        if msg['type'] == 'trade_decision':
            decision = msg['payload']
            symbol = msg.get('symbol') or decision.get('symbol')
            if symbol:
                decision['symbol'] = symbol
            await self._execute_decision(decision)
        elif msg['type'] == 'risk_alert':
            await self._handle_risk_alert(msg['payload'])

    async def _execute_decision(self, decision: dict):
        action = decision.get('action', 'hold')
        confidence = decision.get('confidence', 0)
        symbol = decision.get('symbol')
        plan = decision.get('plan')

        if self._trading_halted:
            self.logger.warning(f"[熔断] 拒绝执行: {symbol} {action}")
            return

        if action == 'hold' or not symbol:
            return

        if confidence < self.min_confidence:
            self.logger.info(f"[执行] {symbol} 跳过：置信度不足 ({confidence} < {self.min_confidence})")
            return

        balance = self._get_balance()
        if balance < 0:
            self.logger.warning(f"[执行] {symbol} 跳过：余额获取失败")
            return
        can_trade, reason = self.executor.risk_manager.check_can_trade(balance)
        if not can_trade:
            self.logger.warning(f"[执行] {symbol} 风控拒绝: {reason}")
            await self.publish("execution_result", {
                "status": "rejected", "reason": reason, "action": action, "symbol": symbol
            }, symbol=symbol)
            return

        position = self.executor.get_position(symbol)

        if action in ('open_long', 'open_short') and position is None:
            side = 'long' if action == 'open_long' else 'short'
            if plan:
                result = await self._execute_with_plan(symbol, side, plan)
            else:
                result = await self._execute_legacy(symbol, side, decision)

        elif action == 'close' and position is not None:
            if position.get('sl_order_id'):
                self.executor.cancel_order(symbol, position['sl_order_id'])
            result = self.executor.close_position(symbol)
            if result:
                self.logger.info(f"[执行] {symbol} 平仓 PnL={result.get('pnl', 0):.2f}")
        else:
            result = None

        if result:
            await self.publish("execution_result", {
                "status": "executed",
                "action": action,
                "symbol": symbol,
                "result": result,
                "confidence": confidence,
                "used_plan": plan is not None,
            }, symbol=symbol)

    async def _execute_with_plan(self, symbol: str, side: str, plan: dict) -> dict:
        """基于Judge plan智能执行（限价单可能阻塞30s，用线程池避免冻结事件循环）"""
        self.logger.info(
            f"[执行] {symbol} 智能开仓: {side}, "
            f"杠杆={plan.get('leverage')}x, "
            f"类型={plan.get('order_type')}, "
            f"仓位={plan.get('size_usdt')} USDT"
        )
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, self.executor.open_position_with_plan, symbol, side, plan
        )
        return result

    async def _execute_legacy(self, symbol: str, side: str, decision: dict) -> dict:
        """兼容旧格式（无plan时）"""
        size_pct = decision.get('size_pct', 0.5)
        max_amount = self.config.get('max_trade_amount', 10)
        amount = max_amount * size_pct

        loop = asyncio.get_event_loop()
        if side == 'long':
            result = await loop.run_in_executor(None, self.executor.open_long, symbol, amount)
        else:
            result = await loop.run_in_executor(None, self.executor.open_short, symbol, amount)

        if result:
            self.logger.info(f"[执行] {symbol} 旧模式开{side} {amount:.2f} USDT")
        return result

    async def _handle_risk_alert(self, alert: dict):
        """处理RiskGuard风险警报"""
        alert_type = alert.get('type', '')
        self.logger.warning(f"[风控警报] 收到: {alert_type}")

        if alert_type == 'flash_move':
            await self._close_all_positions("闪崩警报")

        elif alert_type == 'max_drawdown':
            await self._close_all_positions("最大回撤触发")

        elif alert_type in ('position_danger', 'high_leverage_danger', 'trailing_stop'):
            symbol = alert.get('symbol')
            if symbol and self.executor.get_position(symbol):
                self.logger.warning(f"[风控] {alert_type}: 平仓 {symbol}")
                pos = self.executor.positions.get(symbol)
                if pos and pos.get('sl_order_id'):
                    self.executor.cancel_order(symbol, pos['sl_order_id'])
                result = self.executor.close_position(symbol)
                if result:
                    await self.publish("execution_result", {
                        "status": "force_closed",
                        "symbol": symbol,
                        "reason": alert_type,
                        "result": result,
                    }, symbol=symbol)

        elif alert_type in ('portfolio_exposure', 'correlation_risk'):
            positions = self.executor.get_all_positions()
            if not positions:
                return
            largest_sym = max(positions, key=lambda s: positions[s].get('amount_usdt', 0))
            self.logger.warning(f"[风控] {alert_type}: 减仓 {largest_sym} 50%")
            result = self.executor.reduce_position(largest_sym, 0.5)
            if result:
                await self.publish("execution_result", {
                    "status": "risk_reduced",
                    "symbol": largest_sym,
                    "action": "reduce_50pct",
                    "trigger": alert_type,
                }, symbol=largest_sym)

        elif alert_type == 'stale_position':
            symbol = alert.get('symbol')
            self.logger.info(f"[风控] 持仓超时告警: {symbol} (仅日志，不自动执行)")

    async def _close_all_positions(self, reason: str):
        """全部平仓"""
        positions = self.executor.get_all_positions()
        for symbol, pos in positions.items():
            if pos.get('sl_order_id'):
                self.executor.cancel_order(symbol, pos['sl_order_id'])
            result = self.executor.close_position(symbol)
            if result:
                self.logger.warning(f"[风控平仓] {symbol} 因{reason}, PnL={result.get('pnl', 0):.2f}")
                await self.publish("execution_result", {
                    "status": "force_closed",
                    "symbol": symbol,
                    "reason": reason,
                    "result": result,
                }, symbol=symbol)

    def _get_balance(self) -> float:
        try:
            balance = self.executor.exchange.fetch_balance()
            return float(balance.get('total', {}).get('USDT', 0))
        except Exception as e:
            self.logger.error(f"获取余额失败: {e}")
            return -1.0

    async def tick(self):
        await asyncio.sleep(5)
        self._sync_counter += 1

        if self._sync_counter % 6 == 0:
            self.executor.sync_positions()

        self._check_all_positions()

    def _check_all_positions(self):
        """兜底止损检查（交易所条件单失败时的安全网）"""
        positions = self.executor.get_all_positions()
        for symbol in list(positions.keys()):
            trigger = self.executor.check_stop_loss_take_profit(symbol)
            if trigger:
                self.logger.info(f"[兜底] {symbol} 触发{trigger}，本地平仓")
                pos = self.executor.positions.get(symbol)
                if pos and pos.get('sl_order_id'):
                    self.executor.cancel_order(symbol, pos['sl_order_id'])
                self.executor.close_position(symbol)
