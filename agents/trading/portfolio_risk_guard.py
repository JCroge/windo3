"""组合级风控盯盘 Agent - 6维度实时风控 + 动态止损追踪"""

import asyncio
import time
from agents.base import BaseAgent


class PortfolioRiskGuard(BaseAgent):
    name = "portfolio_risk_guard"
    subscriptions = ["execution_result", "market_data:*", "price_tick:*", "symbol_update", "daily_hard_stop_triggered"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._positions = {}
        self._prices = {}
        self._price_history = {}
        self._alert_cooldown = 60
        self._last_alert_times = {}
        self._account_balance = 0.0  # 动态更新，从execution_result中读取
        self._trading_halted = False
        self._state_file = 'data/riskguard_state.json'

        self._max_portfolio_exposure = 25.0
        self._max_single_loss_pct = 15.0
        self._max_portfolio_drawdown_pct = 10.0
        self._flash_move_threshold = 3.0
        self._flash_move_window = 60
        self._high_leverage_threshold = 20
        self._high_leverage_loss_pct = 5.0
        self._correlation_exposure_limit = 20.0
        self._stale_position_hours = 24

    async def setup(self):
        self._load_state()
        self.logger.info("组合级风控Agent就绪 (6维度+trailing stop)")

    async def on_message(self, msg: dict):
        if msg['type'] == 'daily_hard_stop_triggered':
            await self._handle_daily_hard_stop(msg['payload'])
            return

        if self._trading_halted:
            return

        if msg['type'] == 'symbol_update':
            return

        if msg['type'] == 'price_tick':
            symbol = msg.get('symbol') or msg['payload'].get('symbol')
            price = msg['payload'].get('price')
            if symbol and price:
                self._update_price(self._to_ccxt_key(symbol), price)
            return

        if msg['type'] == 'market_data':
            symbol = msg.get('symbol') or msg['payload'].get('symbol')
            price = msg['payload'].get('latest_price')
            if symbol and price:
                self._update_price(self._to_ccxt_key(symbol), price)

        elif msg['type'] == 'execution_result':
            self._handle_execution_result(msg['payload'])

    def _handle_execution_result(self, payload: dict):
        result = payload.get('result', {})
        # 优先使用result中的ccxt格式symbol，保持与positions.json一致
        symbol = result.get('symbol') or payload.get('symbol')
        if not symbol:
            return
        # 统一为不带-SWAP的格式（与开仓时一致）
        if symbol.endswith('-SWAP'):
            symbol = symbol[:-5]
        status = payload.get('status')

        # 从执行结果中更新账户余额基准
        if 'balance' in result:
            self._account_balance = float(result['balance'])
        elif status == 'executed' and payload.get('action') in ('open_long', 'open_short'):
            # 开仓后余额 = 开仓前余额 - 保证金
            size_usdt = result.get('amount_usdt', 0)
            leverage = result.get('leverage', 1)
            margin = size_usdt / leverage if leverage else size_usdt
            if self._account_balance > 0:
                self._account_balance = max(0, self._account_balance - margin)

        if status == 'executed':
            action = payload.get('action', '')
            if action in ('open_long', 'open_short'):
                side = 'long' if action == 'open_long' else 'short'
                entry_price = result.get('entry_price') or result.get('new_entry_price', 0)

                if payload.get('is_add') and symbol in self._positions:
                    # 加仓：增量更新（保留open_time/highest/lowest）
                    pos = self._positions[symbol]
                    pos['entry_price'] = result.get('new_entry_price', entry_price)
                    pos['amount_usdt'] = result.get('amount_usdt', pos.get('amount_usdt', 0))
                    pos['stop_loss'] = result.get('new_stop_loss') or result.get('stop_loss') or pos.get('stop_loss')
                    pos['take_profit'] = result.get('new_take_profit') or result.get('take_profit') or pos.get('take_profit')
                    self.logger.info(f"[风控] 加仓更新: {symbol} 新均价={pos['entry_price']:.4f}")
                else:
                    # 新开仓：完整记录
                    self._positions[symbol] = {
                        "symbol": symbol,
                        "side": side,
                        "entry_price": entry_price,
                        "amount_usdt": result.get('amount_usdt', 0),
                        "leverage": result.get('leverage', 1),
                        "stop_loss": result.get('stop_loss'),
                        "take_profit": result.get('take_profit'),
                        "open_time": time.time(),
                        "highest_price": entry_price,
                        "lowest_price": entry_price,
                    }
                    self.logger.info(f"[风控] 记录持仓: {symbol} {side} lev={result.get('leverage')}x")
            elif action == 'close':
                self._positions.pop(symbol, None)
                self.logger.info(f"[风控] 移除持仓: {symbol}")

        elif status in ('force_closed', 'closed_externally'):
            if symbol in self._positions:
                self.logger.info(f"[风控] {symbol} 外部平仓，移除追踪")
            self._positions.pop(symbol, None)

        elif status == 'risk_reduced':
            if symbol in self._positions:
                reduce_pct = payload.get('reduce_pct', 0.5)
                self._positions[symbol]['amount_usdt'] *= (1 - reduce_pct)

    def _update_price(self, symbol: str, price: float):
        self._prices[symbol] = price
        now = time.time()

        if symbol not in self._price_history:
            self._price_history[symbol] = []
        self._price_history[symbol].append((now, price))

        if len(self._price_history[symbol]) > 120:
            self._price_history[symbol] = self._price_history[symbol][-60:]

        if symbol in self._positions:
            pos = self._positions[symbol]
            if pos['side'] == 'long':
                if price > pos['highest_price']:
                    pos['highest_price'] = price
            else:
                if price < pos['lowest_price']:
                    pos['lowest_price'] = price

    async def tick(self):
        await asyncio.sleep(10)

        for symbol in list(self._positions.keys()):
            price = self._prices.get(symbol)
            if not price:
                continue
            await self._check_position_pnl(symbol, price)
            await self._check_high_leverage(symbol, price)
            await self._check_trailing_stop(symbol, price)

        await self._check_portfolio_drawdown()
        await self._check_correlation_risk()
        await self._check_stale_positions()

        for symbol in list(self._price_history.keys()):
            await self._check_flash_move(symbol)

        # 定期保存状态（每分钟）
        if int(time.time()) % 60 == 0:
            self._save_state()

    def _calc_pnl_pct(self, pos: dict, current_price: float) -> float:
        entry = pos['entry_price']
        if entry == 0:
            return 0.0
        leverage = pos.get('leverage', 1)
        if pos['side'] == 'long':
            return (current_price - entry) / entry * 100 * leverage
        else:
            return (entry - current_price) / entry * 100 * leverage

    async def _check_position_pnl(self, symbol: str, price: float):
        """维度1: 单仓浮亏监控"""
        pos = self._positions[symbol]
        pnl_pct = self._calc_pnl_pct(pos, price)

        if pnl_pct < -self._max_single_loss_pct:
            if self._can_alert(f"position_danger:{symbol}"):
                self.logger.warning(
                    f"[风控] {symbol} 浮亏{pnl_pct:.1f}% > -{self._max_single_loss_pct}%!"
                )
                await self.publish("risk_alert", {
                    "type": "position_danger",
                    "symbol": symbol,
                    "pnl_pct": pnl_pct,
                    "action": "close_position"
                }, symbol=symbol)

    async def _check_portfolio_drawdown(self):
        """维度2: 组合回撤保护"""
        total_pnl_usdt = 0.0
        for symbol, pos in self._positions.items():
            price = self._prices.get(symbol)
            if not price:
                continue
            pnl_pct = self._calc_pnl_pct(pos, price)
            margin = pos['amount_usdt'] / pos.get('leverage', 1)
            total_pnl_usdt += margin * pnl_pct / 100

        # 余额未初始化时用持仓保证金兜底
        balance = self._account_balance if self._account_balance > 0 else (
            sum(pos['amount_usdt'] / pos.get('leverage', 1) for pos in self._positions.values()) or 20.0
        )
        drawdown_pct = abs(total_pnl_usdt) / balance * 100
        if total_pnl_usdt < 0 and drawdown_pct > self._max_portfolio_drawdown_pct:
            if self._can_alert("max_drawdown"):
                self.logger.warning(
                    f"[风控] 组合回撤{drawdown_pct:.1f}% > {self._max_portfolio_drawdown_pct}%!"
                )
                await self.publish("risk_alert", {
                    "type": "max_drawdown",
                    "drawdown_pct": drawdown_pct,
                    "total_pnl_usdt": total_pnl_usdt,
                    "action": "close_all"
                })

    async def _check_flash_move(self, symbol: str):
        """维度3: 闪崩检测（60秒窗口）"""
        history = self._price_history.get(symbol, [])
        if len(history) < 2:
            return

        now = time.time()
        window_start = now - self._flash_move_window
        prices_in_window = [p for t, p in history if t >= window_start]

        if len(prices_in_window) < 2:
            return

        first_price = prices_in_window[0]
        last_price = prices_in_window[-1]
        change_pct = abs(last_price - first_price) / first_price * 100

        if change_pct > self._flash_move_threshold:
            if self._can_alert(f"flash_move:{symbol}"):
                direction = "暴跌" if last_price < first_price else "暴涨"
                self.logger.warning(
                    f"[风控] {symbol} {self._flash_move_window}s内{direction} {change_pct:.1f}%!"
                )
                await self.publish("risk_alert", {
                    "type": "flash_move",
                    "symbol": symbol,
                    "direction": direction,
                    "magnitude_pct": change_pct,
                    "action": "close_symbol"
                }, symbol=symbol)

    async def _check_high_leverage(self, symbol: str, price: float):
        """维度4: 高杠杆风险"""
        pos = self._positions[symbol]
        leverage = pos.get('leverage', 1)
        if leverage <= self._high_leverage_threshold:
            return

        pnl_pct = self._calc_pnl_pct(pos, price)
        raw_loss = abs(pnl_pct) / leverage

        if pnl_pct < 0 and raw_loss > self._high_leverage_loss_pct:
            if self._can_alert(f"high_lev:{symbol}"):
                self.logger.warning(
                    f"[风控] {symbol} 高杠杆{leverage}x + 浮亏{pnl_pct:.1f}%!"
                )
                await self.publish("risk_alert", {
                    "type": "high_leverage_danger",
                    "symbol": symbol,
                    "leverage": leverage,
                    "pnl_pct": pnl_pct,
                    "action": "close_position"
                }, symbol=symbol)

    async def _check_correlation_risk(self):
        """维度5: 关联性风险（同方向敞口叠加）"""
        long_exposure = 0.0
        short_exposure = 0.0
        for pos in self._positions.values():
            margin = pos['amount_usdt'] / pos.get('leverage', 1)
            if pos['side'] == 'long':
                long_exposure += margin
            else:
                short_exposure += margin

        max_dir_exposure = max(long_exposure, short_exposure)
        num_positions = len(self._positions)

        if num_positions >= 2 and max_dir_exposure > self._correlation_exposure_limit:
            if self._can_alert("correlation_risk"):
                direction = "多" if long_exposure > short_exposure else "空"
                self.logger.warning(
                    f"[风控] 同{direction}方向敞口{max_dir_exposure:.1f} > {self._correlation_exposure_limit}"
                )
                await self.publish("risk_alert", {
                    "type": "correlation_risk",
                    "direction": direction,
                    "exposure_usdt": max_dir_exposure,
                    "action": "reduce_exposure"
                })

    async def _check_stale_positions(self):
        """维度6: 持仓超时"""
        now = time.time()
        stale_threshold = self._stale_position_hours * 3600

        for symbol, pos in self._positions.items():
            age = now - pos.get('open_time', now)
            if age < stale_threshold:
                continue

            price = self._prices.get(symbol)
            if not price:
                continue

            pnl_pct = self._calc_pnl_pct(pos, price)
            if pnl_pct < 0:
                if self._can_alert(f"stale:{symbol}"):
                    hours = age / 3600
                    self.logger.warning(
                        f"[风控] {symbol} 持仓{hours:.0f}h且浮亏{pnl_pct:.1f}%"
                    )
                    await self.publish("risk_alert", {
                        "type": "stale_position",
                        "symbol": symbol,
                        "hours_held": hours,
                        "pnl_pct": pnl_pct,
                        "action": "warn_only"
                    }, symbol=symbol)

    async def _check_trailing_stop(self, symbol: str, price: float):
        """动态止损追踪"""
        pos = self._positions[symbol]
        entry = pos['entry_price']
        if entry == 0:
            return

        if pos['side'] == 'long':
            profit_pct = (price - entry) / entry * 100
            peak = pos['highest_price']
            if peak <= entry:
                return
            retrace_from_peak = (peak - price) / (peak - entry) * 100 if peak > entry else 0
        else:
            profit_pct = (entry - price) / entry * 100
            trough = pos['lowest_price']
            if trough >= entry:
                return
            retrace_from_peak = (price - trough) / (entry - trough) * 100 if trough < entry else 0

        if profit_pct < 3:
            return

        if profit_pct >= 10:
            trail_pct = 30
        elif profit_pct >= 5:
            trail_pct = 50
        else:
            trail_pct = 100

        if retrace_from_peak > trail_pct:
            if self._can_alert(f"trailing:{symbol}"):
                self.logger.warning(
                    f"[风控] {symbol} trailing stop触发: "
                    f"盈利{profit_pct:.1f}%回撤{retrace_from_peak:.0f}% > 阈值{trail_pct}%"
                )
                await self.publish("risk_alert", {
                    "type": "trailing_stop",
                    "symbol": symbol,
                    "profit_pct": profit_pct,
                    "retrace_pct": retrace_from_peak,
                    "action": "close_position"
                }, symbol=symbol)

    def _can_alert(self, key: str) -> bool:
        """告警冷却：关键告警10秒，普通告警60秒"""
        now = time.time()
        last = self._last_alert_times.get(key, 0)
        is_critical = any(k in key for k in ('flash_move', 'max_drawdown', 'position_danger'))
        cooldown = 10 if is_critical else self._alert_cooldown
        if now - last < cooldown:
            return False
        self._last_alert_times[key] = now
        return True

    async def _handle_daily_hard_stop(self, payload: dict):
        """处理daily hard stop触发"""
        reason = payload.get('reason')
        self.logger.critical(f"[熔断] Daily hard stop触发: {reason}")

        # 全平所有持仓
        for symbol in list(self._positions.keys()):
            await self.publish("risk_alert", {
                "type": "emergency_close",
                "symbol": symbol,
                "reason": f"daily_hard_stop_{reason}",
                "action": "close_position"
            }, symbol=symbol)

        self._trading_halted = True
        self._save_state()

    def _save_state(self):
        """保存持仓追踪状态"""
        import json
        import os

        state = {
            'positions': self._positions,
            'prices': self._prices,
            'trading_halted': self._trading_halted,
            'last_alert_times': self._last_alert_times,
        }

        os.makedirs('data', exist_ok=True)
        try:
            with open(self._state_file, 'w') as f:
                json.dump(state, f, indent=2)
            self.logger.info(f"RiskGuard状态已保存: {len(self._positions)}个持仓")
        except Exception as e:
            self.logger.error(f"保存状态失败: {e}")

    def _load_state(self):
        """加载持仓追踪状态，并与executor持仓交叉验证"""
        import json
        import os

        if os.path.exists(self._state_file):
            try:
                with open(self._state_file, 'r') as f:
                    state = json.load(f)

                self._positions = state.get('positions', {})
                self._prices = state.get('prices', {})
                self._trading_halted = state.get('trading_halted', False)
                self._last_alert_times = state.get('last_alert_times', {})

                if self._trading_halted:
                    self.logger.warning("系统处于熔断状态（从上次会话恢复）")
            except Exception as e:
                self.logger.error(f"加载状态失败: {e}")

        # 交叉验证：从positions.json补录RiskGuard不知道的持仓
        positions_file = 'data/positions.json'
        if os.path.exists(positions_file):
            try:
                with open(positions_file, 'r') as f:
                    executor_positions = json.load(f)
                # 构建已有持仓的base symbol集合（去掉格式差异）
                existing_bases = set()
                for k in self._positions:
                    existing_bases.add(self._normalize_key(k))
                for sym, pos in executor_positions.items():
                    base = self._normalize_key(sym)
                    if base not in existing_bases and 'stop_loss' in pos:
                        entry = pos.get('entry_price', 0)
                        self._positions[sym] = {
                            "symbol": sym,
                            "side": pos['side'],
                            "entry_price": entry,
                            "amount_usdt": pos.get('amount_usdt', 0),
                            "leverage": pos.get('leverage', 1),
                            "stop_loss": pos.get('stop_loss'),
                            "take_profit": pos.get('take_profit'),
                            "highest_price": entry if pos['side'] == 'long' else entry,
                            "lowest_price": entry if pos['side'] == 'short' else entry,
                        }
                        existing_bases.add(base)
                        self.logger.info(f"RiskGuard补录持仓: {sym} ({pos['side']} {pos.get('leverage',1)}x)")
                # 清理旧格式key（保留ccxt统一格式）
                keys_to_remove = [k for k in self._positions if '/' not in k and self._normalize_key(k) in existing_bases and any(
                    '/' in k2 and self._normalize_key(k2) == self._normalize_key(k) for k2 in self._positions if k2 != k
                )]
                for k in keys_to_remove:
                    del self._positions[k]
                    self.logger.info(f"RiskGuard清理旧格式key: {k}")
            except Exception as e:
                self.logger.warning(f"交叉验证positions.json失败: {e}")

        self.logger.info(f"RiskGuard状态已加载: {len(self._positions)}个持仓")

    @staticmethod
    def _normalize_key(key: str) -> str:
        """将不同格式的symbol统一为base（如ETH-USDT）用于比较"""
        # "ETH/USDT:USDT" → "ETH-USDT"
        # "ETH-USDT" → "ETH-USDT"
        key = key.split(':')[0]  # 去掉 :USDT
        key = key.replace('/', '-')  # / → -
        return key.upper()

    @staticmethod
    def _to_ccxt_key(symbol: str) -> str:
        """将任意格式转为ccxt统一格式（与positions.json一致）"""
        # "ETH-USDT" → "ETH/USDT:USDT"
        # "ETH/USDT:USDT" → "ETH/USDT:USDT" (不变)
        if '/' in symbol:
            return symbol
        parts = symbol.split('-')
        if len(parts) == 2:
            return f"{parts[0]}/{parts[1]}:{parts[1]}"
        return symbol
