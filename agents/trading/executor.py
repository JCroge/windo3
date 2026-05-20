"""智能交易执行 Agent - 消费Judge plan，支持动态杠杆/限价单/交易所止损/仓位同步"""

import os
import math
import numbers
import time
import asyncio
from dotenv import load_dotenv
from agents.base import BaseAgent
from executor import ContractExecutor
from utils.halt_state import get_halt_state
from utils.reconciliation import Reconciler

load_dotenv()


class MultiExecutor(BaseAgent):
    name = "executor"
    subscriptions = ["trade_decision:*", "risk_alert", "daily_hard_stop_triggered", "system_command"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.executor = None
        self.min_confidence = config.get('min_confidence', 60) if config else 60
        self._sync_counter = 0
        self._trading_halted = False
        self._open_fail_cooldown = {}  # {symbol: expire_timestamp}
        self._halt_state = get_halt_state()

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
        self._reconciler = None
        if self.executor.ledger:
            self._reconciler = Reconciler(
                self.executor.exchange, self.executor.ledger, logger=self.logger
            )
        self.logger.info(f"智能执行Agent就绪: {exchange_id}, 默认杠杆{leverage}x")

    async def on_message(self, msg: dict):
        if msg['type'] == 'daily_hard_stop_triggered':
            self._trading_halted = True
            reason = msg.get('payload', {}).get('reason', 'unknown')
            self._halt_state.halt(reason=reason, triggered_by="reviewer")
            self.logger.critical(f"[熔断] Daily Hard Stop 触发: {reason} — 停止新交易并全平持仓")
            await self._close_all_positions(f"daily_hard_stop_{reason}")
            return

        if msg['type'] == 'system_command':
            cmd = msg.get('payload', {}).get('command', '')
            source = msg.get('payload', {}).get('source', 'telegram')
            if cmd == 'halt':
                self._trading_halted = True
                self._halt_state.halt(reason="manual", triggered_by=source)
                self.logger.warning(f"[手动熔断] 通过{source}触发")
            elif cmd == 'resume':
                self._trading_halted = False
                self.logger.info(f"[解除熔断] 通过{source}触发")
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
        source = decision.get('source', '')
        size_pct = decision.get('size_pct', 1.0)

        if self._trading_halted or not self._halt_state.can_open_new:
            if action in ('open_long', 'open_short'):
                reason = "halted" if self._trading_halted else "reconciliation_pending"
                self.logger.warning(f"[熔断] 拒绝执行: {symbol} {action} ({reason})")
                await self.publish("execution_result", {
                    "status": "rejected", "reason": reason, "action": action, "symbol": symbol
                }, symbol=symbol)
                return
            # close 允许通过（保护性平仓）

        if action == 'hold' or not symbol:
            return

        if confidence < self.min_confidence:
            self.logger.info(f"[执行] {symbol} 跳过：置信度不足 ({confidence} < {self.min_confidence})")
            if action in ('open_long', 'open_short'):
                await self.publish("execution_result", {
                    "status": "rejected", "reason": "low_confidence",
                    "action": action, "symbol": symbol
                }, symbol=symbol)
            return

        # normalize symbol 确保与持仓key一致
        norm_symbol = self.executor._normalize_symbol(symbol)

        balance = self._get_balance()
        if balance < 0:
            self.logger.warning(f"[执行] {symbol} 跳过：余额获取失败")
            await self.publish("execution_result", {
                "status": "rejected", "reason": "balance_fetch_failed",
                "action": action, "symbol": symbol
            }, symbol=symbol)
            return
        can_trade, reason = self.executor.risk_manager.check_can_trade(balance)
        if not can_trade:
            self.logger.warning(f"[执行] {symbol} 风控拒绝: {reason}")
            await self.publish("execution_result", {
                "status": "rejected", "reason": reason, "action": action, "symbol": symbol
            }, symbol=symbol)
            return

        try:
            position = self.executor.get_position(norm_symbol)
        except Exception as e:
            self.logger.error(f"[执行] {symbol} 获取持仓失败: {e}")
            await self.publish("execution_result", {
                "status": "error", "reason": str(e), "action": action, "symbol": symbol
            }, symbol=symbol)
            return

        result = None

        if action in ('open_long', 'open_short') and position is None:
            # PA的add信号不应在无持仓时执行（持仓已被外部平仓）
            if source == 'position_analyst':
                self.logger.warning(f"[执行] {symbol} PA加仓信号但无持仓（已被外部平仓），忽略")
                await self.publish("execution_result", {
                    "status": "rejected", "reason": "no_position_for_add",
                    "action": action, "symbol": symbol
                }, symbol=symbol)
                return
            cooldown_until = self._open_fail_cooldown.get(norm_symbol, 0)
            if time.time() < cooldown_until:
                self.logger.info(f"[执行] {symbol} 开仓失败冷却中，跳过")
                await self.publish("execution_result", {
                    "status": "rejected", "reason": "open_cooldown",
                    "action": action, "symbol": symbol
                }, symbol=symbol)
                return
            side = 'long' if action == 'open_long' else 'short'
            try:
                if plan:
                    result = await self._execute_with_plan(symbol, side, plan)
                else:
                    result = await self._execute_legacy(symbol, side, decision)
            except Exception as e:
                self._open_fail_cooldown[norm_symbol] = time.time() + 120
                self.logger.error(f"[执行] {symbol} 开仓失败(120s冷却): {e}")
                await self.publish("execution_result", {
                    "status": "error", "reason": str(e), "action": action, "symbol": symbol
                }, symbol=symbol)
                return

        elif action in ('open_long', 'open_short') and position is not None and source == 'position_analyst':
            # 加仓：已有持仓，PositionAnalyst要求追加
            side = 'long' if action == 'open_long' else 'short'
            if position.get('side') != side:
                self.logger.warning(f"[执行] {symbol} 加仓方向冲突: 持仓{position['side']} vs 请求{side}，跳过")
                await self.publish("execution_result", {
                    "status": "rejected", "reason": "add_direction_conflict",
                    "action": "add", "symbol": symbol
                }, symbol=symbol)
                return
            cooldown_until = self._open_fail_cooldown.get(norm_symbol, 0)
            if time.time() < cooldown_until:
                self.logger.info(f"[执行] {symbol} 开仓失败冷却中，跳过加仓")
                await self.publish("execution_result", {
                    "status": "rejected", "reason": "add_cooldown",
                    "action": "add", "symbol": symbol
                }, symbol=symbol)
                return
            try:
                result = await self._execute_add_position(norm_symbol, side, position, size_pct)
            except Exception as e:
                self._open_fail_cooldown[norm_symbol] = time.time() + 120
                self.logger.error(f"[执行] {symbol} 加仓失败(120s冷却): {e}")
                await self.publish("execution_result", {
                    "status": "error", "reason": str(e), "action": "add", "symbol": symbol
                }, symbol=symbol)
                return

        elif action == 'close' and position is not None:
            try:
                if size_pct < 1.0 and source == 'position_analyst':
                    # 减仓：部分平仓
                    result = await asyncio.to_thread(
                        self.executor.reduce_position, norm_symbol, size_pct
                    )
                    if result:
                        self.logger.info(f"[执行] {symbol} 减仓{int(size_pct*100)}%")
                else:
                    # 全平
                    if position.get('sl_order_id'):
                        self.executor.cancel_order(norm_symbol, position['sl_order_id'])
                    result = self.executor.close_position(norm_symbol)
                    if result:
                        self.logger.info(f"[执行] {symbol} 平仓 PnL={result.get('pnl', 0):.2f}")
            except Exception as e:
                self.logger.error(f"[执行] {symbol} 平仓失败: {e}")
                await self.publish("execution_result", {
                    "status": "error", "reason": str(e), "action": action, "symbol": symbol
                }, symbol=symbol)
                return

        elif action in ('open_long', 'open_short') and position is not None:
            # Judge在已有持仓时发信号（非加仓），忽略
            self.logger.debug(f"[执行] {symbol} 已有持仓，忽略开仓信号(source={source})")
            return

        if result:
            payload = {
                "status": "executed",
                "action": action,
                "symbol": symbol,
                "result": result,
                "confidence": confidence,
                "used_plan": plan is not None,
            }
            # RQ-07: 传递归因字段
            attribution = decision.get('attribution')
            if attribution:
                payload['attribution'] = attribution
                result['attribution'] = attribution
            if source == 'position_analyst' and action in ('open_long', 'open_short'):
                payload["is_add"] = True
            if source == 'position_analyst' and action == 'close' and size_pct < 1.0:
                payload["status"] = "risk_reduced"
                payload["reduce_pct"] = size_pct
            await self.publish("execution_result", payload, symbol=symbol)

    async def _execute_with_plan(self, symbol: str, side: str, plan: dict) -> dict:
        """基于Judge plan智能执行（限价单可能阻塞30s，用线程池避免冻结事件循环）"""
        self.logger.info(
            f"[执行] {symbol} 智能开仓: {side}, "
            f"杠杆={plan.get('leverage')}x, "
            f"类型={plan.get('order_type')}, "
            f"仓位={plan.get('size_usdt')} USDT"
        )
        result = await asyncio.to_thread(
            self.executor.open_position_with_plan, symbol, side, plan
        )
        return result

    async def _execute_legacy(self, symbol: str, side: str, decision: dict) -> dict:
        """兼容旧格式（无plan时）"""
        size_pct = decision.get('size_pct', 0.5)
        max_amount = self.config.get('max_trade_amount', 10)
        amount = max_amount * size_pct

        if side == 'long':
            result = await asyncio.to_thread(self.executor.open_long, symbol, amount)
        else:
            result = await asyncio.to_thread(self.executor.open_short, symbol, amount)

        if result:
            self.logger.info(f"[执行] {symbol} 旧模式开{side} {amount:.2f} USDT")
        return result

    async def _execute_add_position(self, symbol: str, side: str, position: dict, size_pct: float) -> dict:
        """加仓：在已有持仓方向上追加仓位"""
        result = await asyncio.to_thread(
            self.executor.add_to_position, symbol, side, size_pct
        )
        if result:
            self.logger.info(
                f"[执行] {symbol} 加仓{int(size_pct*100)}%: "
                f"新增{result.get('add_amount_usdt', 0):.2f} USDT"
            )
        return result

    async def _handle_risk_alert(self, alert: dict):
        """处理RiskGuard风险警报"""
        alert_type = alert.get('type', '')
        scope = alert.get('scope', 'symbol')  # 'symbol' 或 'market'
        self.logger.warning(f"[风控警报] 收到: {alert_type} scope={scope}")

        if alert_type == 'emergency_close':
            # RiskGuard 兜底：daily_hard_stop 等场景的强平
            # 注：Executor 收到 daily_hard_stop_triggered 时已自行全平，此处为双保险
            symbol = alert.get('symbol')
            reason = alert.get('reason', 'emergency_close')
            norm_sym = self.executor._normalize_symbol(symbol) if symbol else None
            if not norm_sym:
                return
            position = self.executor.get_position(norm_sym)
            if position is None:
                self.logger.debug(f"[风控] {norm_sym} emergency_close 时持仓已不存在（主路径已平），忽略")
                return
            self.logger.critical(f"[风控兜底平仓] {norm_sym} 因 {reason}")
            pos = self.executor.positions.get(norm_sym)
            if pos and pos.get('sl_order_id'):
                self.executor.cancel_order(norm_sym, pos['sl_order_id'])
            result = self.executor.close_position(norm_sym)
            if result:
                await self.publish("execution_result", {
                    "status": "force_closed",
                    "symbol": symbol,
                    "reason": reason,
                    "result": result,
                }, symbol=symbol)
            return

        if alert_type == 'flash_move':
            symbol = alert.get('symbol')
            if scope == 'market':
                await self._close_all_positions("flash_move_market")
                return
            norm_sym = self.executor._normalize_symbol(symbol) if symbol else None
            if norm_sym and self.executor.get_position(norm_sym):
                self.logger.warning(f"[风控平仓] {norm_sym} 因闪崩警报")
                pos = self.executor.positions.get(norm_sym)
                if pos and pos.get('sl_order_id'):
                    self.executor.cancel_order(norm_sym, pos['sl_order_id'])
                result = self.executor.close_position(norm_sym)
                if result:
                    await self.publish("execution_result", {
                        "status": "force_closed",
                        "symbol": symbol,
                        "reason": "flash_move",
                        "result": result,
                    }, symbol=symbol)

        elif alert_type == 'max_drawdown':
            await self._close_all_positions("最大回撤触发")

        elif alert_type in ('position_danger', 'high_leverage_danger', 'trailing_stop'):
            symbol = alert.get('symbol')
            norm_sym = self.executor._normalize_symbol(symbol) if symbol else None
            if norm_sym and self.executor.get_position(norm_sym):
                self.logger.warning(f"[风控] {alert_type}: 平仓 {norm_sym}")
                pos = self.executor.positions.get(norm_sym)
                if pos and pos.get('sl_order_id'):
                    self.executor.cancel_order(norm_sym, pos['sl_order_id'])
                result = self.executor.close_position(norm_sym)
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
                    "result": {"pnl": result.get('realized_pnl', 0), "symbol": largest_sym},
                }, symbol=largest_sym)

        elif alert_type == 'stale_position':
            symbol = alert.get('symbol')
            self.logger.info(f"[风控] 持仓超时告警: {symbol} (仅日志，不自动执行)")

    async def _close_all_positions(self, reason: str):
        """全部平仓 — 并发执行，失败收集后告警

        设计说明：
        - 串行平仓 10-15s 内系统持续亏损，故改并发
        - 单个标的平仓失败不影响其他
        - 失败的标的写入日志告警（RiskGuard 兜底 emergency_close 会再次尝试）
        """
        positions = self.executor.get_all_positions()
        if not positions:
            self.logger.info(f"[全平] 无持仓需平仓 ({reason})")
            return

        self.logger.critical(f"[全平] 开始并发平仓 {len(positions)} 个持仓 ({reason})")

        async def close_one(symbol: str, pos: dict):
            try:
                if pos.get('sl_order_id'):
                    await asyncio.to_thread(self.executor.cancel_order, symbol, pos['sl_order_id'])
                result = await asyncio.to_thread(self.executor.close_position, symbol)
                if result:
                    self.logger.warning(f"[全平] {symbol} 已平仓, PnL={result.get('pnl', 0):.2f}")
                    await self.publish("execution_result", {
                        "status": "force_closed",
                        "symbol": symbol,
                        "reason": reason,
                        "result": result,
                    }, symbol=symbol)
                    return True, symbol, None
                else:
                    return False, symbol, "close_position returned None"
            except Exception as e:
                return False, symbol, str(e)

        tasks = [close_one(sym, pos) for sym, pos in positions.items()]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        failed = [(sym, err) for ok, sym, err in results if not ok]
        if failed:
            self.logger.error(f"[全平] {len(failed)} 个标的平仓失败，等待 RiskGuard 兜底: {failed}")
        else:
            self.logger.critical(f"[全平] 完成 ({reason}) — 全部 {len(positions)} 个持仓已平")

    def _get_balance(self) -> float:
        try:
            if self.executor and self.executor.balance_adapter:
                val = self.executor.balance_adapter.get_total()
            else:
                balance = self.executor.exchange.fetch_balance()
                from utils.balance_adapter import BalanceAdapter
                val = BalanceAdapter._parse(balance)[1]

            # 第六轮 P1-2：余额是下单前硬闸，必须是有限实数；拒绝 MagicMock/bool/NaN/Inf
            if isinstance(val, bool) or not isinstance(val, numbers.Real):
                self.logger.error(f"余额非实数类型: {type(val).__name__}")
                return -1.0
            result = float(val)
            if not math.isfinite(result):
                self.logger.error(f"余额非有限值: {result}")
                return -1.0
            return result
        except Exception as e:
            self.logger.error(f"获取余额失败: {e}")
            return -1.0

    async def tick(self):
        await asyncio.sleep(5)
        self._sync_counter += 1

        if self._sync_counter % 6 == 0:
            await asyncio.to_thread(self.executor.sync_positions)
            await self._notify_synced_positions()
            await self._notify_removed_positions()

        if self._reconciler and self._reconciler.should_run(interval_sec=600):
            await self._run_reconciliation()

        await self._check_all_positions()

    async def _run_reconciliation(self):
        """定时 PnL 对账：本地账本 vs 交易所账单"""
        try:
            report = await asyncio.to_thread(self._reconciler.run_and_report)
            if report:
                await self.publish("risk_alert", {
                    "type": "reconciliation_mismatch",
                    "message": report,
                    "timestamp": time.time(),
                })
        except Exception as e:
            self.logger.warning(f"[Reconciler] 对账异常: {e}")

    async def _notify_synced_positions(self):
        """将同步发现的新持仓通知RiskGuard"""
        newly_synced = self.executor.get_newly_synced()
        for pos in newly_synced:
            symbol = pos['symbol']
            action = 'open_long' if pos['side'] == 'long' else 'open_short'
            await self.publish("execution_result", {
                "status": "executed",
                "action": action,
                "symbol": symbol,
                "result": pos,
                "confidence": 0,
                "used_plan": False,
                "source": "sync",
            }, symbol=symbol)

    async def _notify_removed_positions(self):
        """通知下游：持仓已被交易所平仓（SL/TP触发），含真实PnL"""
        removed = self.executor.get_removed_symbols()
        removed_data = self.executor.get_removed_positions_data()
        data_map = {d['symbol']: d for d in removed_data}

        for symbol in removed:
            pos_data = data_map.get(symbol, {})
            pnl = self._get_external_close_pnl(symbol, pos_data)
            self.logger.info(f"[执行] {symbol} 被交易所平仓，PnL={pnl:+.3f} USDT")
            await self.publish("execution_result", {
                "status": "closed_externally",
                "action": "close",
                "symbol": symbol,
                "reason": "exchange_sl_tp_triggered",
                "result": {"pnl": pnl, "symbol": symbol,
                           "side": pos_data.get('side', ''),
                           "entry_price": pos_data.get('entry_price', 0),
                           "amount_usdt": pos_data.get('amount_usdt', 0)},
            }, symbol=symbol)

    def _get_external_close_pnl(self, symbol: str, pos_data: dict) -> float:
        """外部平仓 PnL：优先通过 ledger 查询交易所真实数据"""
        if not pos_data:
            return 0.0

        ledger = getattr(self.executor, 'ledger', None)
        if ledger:
            try:
                event = ledger.record_external_close(
                    symbol=symbol,
                    side=pos_data.get('side', 'long'),
                    entry_price=pos_data.get('entry_price', 0),
                    amount_usdt=pos_data.get('amount_usdt', 0),
                    leverage=pos_data.get('leverage', 1) or 1,
                )
                if event.get('source') != 'estimated':
                    return event['realized_pnl']
            except Exception:
                pass

        return self._estimate_close_pnl(pos_data)

    def _estimate_close_pnl(self, pos_data: dict) -> float:
        """从本地持仓数据估算平仓PnL
        优先用最后一次sync的unrealized_pnl（最接近真实值，~30s误差）
        降级用stop_loss作为exit price计算
        """
        if not pos_data:
            return 0.0

        # 优先：最后一次sync记录的浮动盈亏（最准确，因为sync周期30s）
        unrealized = pos_data.get('unrealized_pnl')
        if unrealized is not None and unrealized != 0:
            return round(unrealized, 4)

        # 降级：用stop_loss估算（假设SL触发）
        entry = pos_data.get('entry_price', 0)
        sl = pos_data.get('stop_loss', 0)
        side = pos_data.get('side', '')
        amount_usdt = pos_data.get('amount_usdt', 0)  # 保证金 (margin)
        leverage = pos_data.get('leverage', 1) or 1

        if not entry or not sl or not amount_usdt:
            return 0.0

        if side == 'long':
            pnl_pct = (sl - entry) / entry
        else:
            pnl_pct = (entry - sl) / entry

        # 保证金 × 价格变化率 × 杠杆 = 实际 PnL（与 ContractExecutor 内部平仓口径一致）
        pnl = amount_usdt * pnl_pct * leverage
        return round(pnl, 4)

    async def _check_all_positions(self):
        """兜底止损检查（交易所条件单失败时的安全网）+ RQ-04 早期复核"""
        positions = self.executor.get_all_positions()
        for symbol in list(positions.keys()):
            # RQ-04: 早期持仓复核（前30分钟内）
            pos = positions[symbol]
            early_review_enabled = self.config.get('early_review_enabled', True)
            if early_review_enabled:
                open_time = pos.get('open_time', 0)
                minutes_held = (time.time() - open_time) / 60 if open_time else 999
                if 0 < minutes_held <= 30:
                    early_action = await self._early_review(symbol, pos, minutes_held)
                    if early_action:
                        continue

            trigger = await asyncio.to_thread(self.executor.check_stop_loss_take_profit, symbol)
            if not trigger:
                continue

            if trigger == 'price_fetch_failed':
                self.logger.error(f"[兜底] {symbol} 价格获取连续失败，强制平仓保护资金!")
                pos = self.executor.positions.get(symbol)
                if pos and pos.get('sl_order_id'):
                    self.executor.cancel_order(symbol, pos['sl_order_id'])
                result = self.executor.close_position(symbol)
                if result:
                    await self.publish("execution_result", {
                        "status": "force_closed", "action": "close",
                        "symbol": symbol, "reason": trigger, "result": result,
                    }, symbol=symbol)

            elif trigger in ('partial_tp_1', 'partial_tp_2'):
                pct = 0.5 if trigger == 'partial_tp_1' else 0.25
                self.logger.info(f"[Trailing] {symbol} {trigger}，减仓{int(pct*100)}%")
                result = await asyncio.to_thread(self.executor.reduce_position, symbol, pct)
                if result:
                    await self.publish("execution_result", {
                        "status": "risk_reduced", "action": "close",
                        "symbol": symbol, "reason": trigger,
                        "result": result, "reduce_pct": pct,
                    }, symbol=symbol)

            else:
                self.logger.info(f"[兜底] {symbol} 触发{trigger}，本地平仓")
                pos = self.executor.positions.get(symbol)
                if pos and pos.get('sl_order_id'):
                    self.executor.cancel_order(symbol, pos['sl_order_id'])
                result = self.executor.close_position(symbol)
                if result:
                    await self.publish("execution_result", {
                        "status": "force_closed", "action": "close",
                        "symbol": symbol, "reason": trigger, "result": result,
                    }, symbol=symbol)

    async def _early_review(self, symbol: str, pos: dict, minutes_held: float) -> bool:
        """RQ-04: 早期持仓复核（入场后30分钟内）。

        返回 True 表示已执行平仓动作，False 表示无动作。
        """
        if not hasattr(self, '_early_review_times'):
            self._early_review_times = {}
        review_key = f"_er_{symbol}"
        last_review = self._early_review_times.get(review_key, 0)
        if time.time() - last_review < 120:
            return False

        entry_price = pos.get('entry_price', 0)
        stop_loss = pos.get('stop_loss', 0)
        side = pos.get('side', 'long')

        if not entry_price or not stop_loss:
            return False

        try:
            ticker = await asyncio.to_thread(self.executor.exchange.fetch_ticker, symbol)
            current_price = ticker['last']
        except Exception:
            return False

        sl_dist = abs(entry_price - stop_loss)
        if sl_dist == 0:
            return False

        pnl_dist = (current_price - entry_price) if side == 'long' else (entry_price - current_price)
        r_multiple = pnl_dist / sl_dist

        # 20min+: 达到 -0.5R → 收紧止损到 -0.7R 位置
        if minutes_held >= 20 and r_multiple <= -0.5:
            tighter_sl_dist = sl_dist * 0.7
            if side == 'long':
                new_sl = entry_price - tighter_sl_dist
                if new_sl > pos['stop_loss']:
                    self.logger.info(
                        f"[EarlyReview] {symbol} {minutes_held:.0f}min R={r_multiple:.2f}, "
                        f"收紧SL → {new_sl:.4f}"
                    )
                    pos['stop_loss'] = new_sl
                    self.executor.positions[symbol] = pos
                    self.executor._save_positions()
            else:
                new_sl = entry_price + tighter_sl_dist
                if new_sl < pos['stop_loss']:
                    self.logger.info(
                        f"[EarlyReview] {symbol} {minutes_held:.0f}min R={r_multiple:.2f}, "
                        f"收紧SL → {new_sl:.4f}"
                    )
                    pos['stop_loss'] = new_sl
                    self.executor.positions[symbol] = pos
                    self.executor._save_positions()

            self._early_review_times[review_key] = time.time()

        return False
