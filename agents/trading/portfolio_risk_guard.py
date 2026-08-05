"""组合级风控盯盘 Agent - 6维度实时风控 + 动态止损追踪"""

import asyncio
import time
from agents.base import BaseAgent
from utils.symbol import to_internal
from utils.halt_state import get_halt_state


class PortfolioRiskGuard(BaseAgent):
    name = "portfolio_risk_guard"
    subscriptions = ["execution_result", "market_data:*", "price_tick:*", "symbol_update", "daily_hard_stop_triggered", "system_command"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._positions = {}
        self._prices = {}
        self._price_history = {}
        self._alert_cooldown = 60
        self._last_alert_times = {}
        self._account_balance = 0.0
        self._effective_balance_cap = None
        if config:
            try:
                cap = config.get('effective_balance_cap')
                self._effective_balance_cap = float(cap) if cap is not None else None
            except (TypeError, ValueError):
                self._effective_balance_cap = None
        self._trading_halted = False
        self._halt_state = get_halt_state()
        from utils.state_paths import get_state_paths
        self._state_file = get_state_paths().riskguard_state

        self._max_portfolio_exposure = 25.0
        self._max_single_loss_pct_fallback = 50.0
        self._position_danger_buffer_pct = 5.0
        self._max_portfolio_drawdown_pct = 15.0
        self._flash_move_threshold = 3.0
        self._flash_move_window = 60
        self._high_leverage_threshold = 20
        self._high_leverage_loss_pct = 5.0
        self._correlation_exposure_limit = 2400.0
        self._stale_position_hours = 24
        self._tactical_daily_pnl = 0.0
        self._tactical_daily_date = self._tactical_day_key()
        self._tactical_loss_streak = 0
        self._tactical_pause_until = 0
        self._tactical_pause_reason = ''
        self._tactical_daily_loss_limit_usdt = (
            config.get('tactical_daily_loss_limit_usdt', -10.0) if config else -10.0
        )
        self._tactical_loss_streak_pause_count = (
            config.get('tactical_loss_streak_pause_count', 3) if config else 3
        )
        self._tactical_loss_streak_pause_minutes = (
            config.get('tactical_loss_streak_pause_minutes', 60) if config else 60
        )
        self._tactical_max_concurrent_calm = 2
        self._tactical_max_concurrent_high_vol = 1
        self._tactical_quality_window_trades = (
            config.get('tactical_quality_window_trades', 20) if config else 20
        )
        self._tactical_quality_results = []

    @staticmethod
    def _tactical_day_key(ts: float = None) -> str:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc) if ts is None else datetime.fromtimestamp(ts, timezone.utc)
        return now.date().isoformat()

    def _reset_tactical_daily_if_needed(self, ts: float = None):
        today = self._tactical_day_key(ts)
        if getattr(self, '_tactical_daily_date', '') != today:
            self._tactical_daily_date = today
            self._tactical_daily_pnl = 0.0

    def _tactical_circuit_state(self) -> dict:
        self._reset_tactical_daily_if_needed()
        return {
            'daily_date': getattr(self, '_tactical_daily_date', self._tactical_day_key()),
            'daily_pnl': getattr(self, '_tactical_daily_pnl', 0.0),
            'daily_loss_limit_usdt': getattr(self, '_tactical_daily_loss_limit_usdt', -10.0),
            'loss_streak': getattr(self, '_tactical_loss_streak', 0),
            'loss_streak_pause_count': getattr(self, '_tactical_loss_streak_pause_count', 3),
            'pause_until': getattr(self, '_tactical_pause_until', 0),
            'pause_reason': getattr(self, '_tactical_pause_reason', ''),
            'quality_results': getattr(self, '_tactical_quality_results', []),
        }

    def _tactical_open_count(self) -> int:
        return sum(1 for p in self._positions.values() if p.get('track') == 'tactical')

    def can_open_tactical(self, symbol: str, plan: dict, market_state: dict):
        if self._is_tactical_v2(plan):
            return False, 'tactical_v2_controller_required'
        self._reset_tactical_daily_if_needed()
        now = time.time()
        if now < getattr(self, '_tactical_pause_until', 0):
            return False, 'tactical_paused'
        if (getattr(self, '_tactical_daily_pnl', 0.0)
                <= getattr(self, '_tactical_daily_loss_limit_usdt', -10.0)):
            return False, 'tactical_daily_loss_limit'

        volatility = (market_state or {}).get('volatility', 'calm')
        cap = (getattr(self, '_tactical_max_concurrent_high_vol', 1)
               if volatility == 'high'
               else getattr(self, '_tactical_max_concurrent_calm', 2))
        if self._tactical_open_count() >= cap:
            return False, 'tactical_concurrency_full'
        return True, 'ok'

    def record_tactical_close(self, symbol: str, pnl: float, close_reason: str, event: dict):
        if self._is_tactical_v2(event):
            return False
        self._reset_tactical_daily_if_needed()
        self._tactical_daily_pnl = (
            getattr(self, '_tactical_daily_pnl', 0.0) + float(pnl or 0.0)
        )
        if pnl < 0:
            self._tactical_loss_streak = getattr(self, '_tactical_loss_streak', 0) + 1
        else:
            self._tactical_loss_streak = 0

        if self._tactical_loss_streak >= getattr(self, '_tactical_loss_streak_pause_count', 3):
            pause_minutes = getattr(self, '_tactical_loss_streak_pause_minutes', 60)
            self._tactical_pause_until = time.time() + pause_minutes * 60
            self._tactical_pause_reason = 'loss_streak'
        return True

    def record_tactical_execution_failure(self, symbol: str, reason: str, event: dict):
        if self._is_tactical_v2(event):
            return False
        pause_minutes = getattr(self, '_tactical_loss_streak_pause_minutes', 60)
        self._tactical_pause_until = time.time() + pause_minutes * 60
        self._tactical_pause_reason = reason or 'tactical_execution_failure'
        return True

    def record_tactical_quality_result(self, symbol: str, success: bool, event: dict = None):
        if self._is_tactical_v2(event or {}):
            return False
        window = getattr(self, '_tactical_quality_window_trades', 20)
        self._tactical_quality_results.append(bool(success))
        self._tactical_quality_results = self._tactical_quality_results[-window:]
        if len(self._tactical_quality_results) >= window:
            success_rate = sum(1 for x in self._tactical_quality_results if x) / window
            if success_rate < 0.5:
                pause_minutes = getattr(self, '_tactical_loss_streak_pause_minutes', 60)
                self._tactical_pause_until = time.time() + pause_minutes * 60
                self._tactical_pause_reason = 'tactical_quality_failure'
        return True

    @staticmethod
    def _is_tactical_v2(*sources: dict) -> bool:
        for source in sources:
            if not isinstance(source, dict):
                continue
            attribution = source.get('attribution') or {}
            if (
                source.get('strategy_owner') == 'tactical_v2'
                or source.get('exit_profile') == 'tactical_v2'
                or attribution.get('strategy_owner') == 'tactical_v2'
                or attribution.get('exit_profile') == 'tactical_v2'
            ):
                return True
        return False

    async def setup(self):
        self._load_state()
        if self._halt_state.halted:
            self._trading_halted = True
        self.logger.info("组合级风控Agent就绪 (6维度+trailing stop)")

    async def on_message(self, msg: dict):
        if msg['type'] == 'daily_hard_stop_triggered':
            await self._handle_daily_hard_stop(msg['payload'])
            return

        if msg['type'] == 'system_command':
            cmd = msg.get('payload', {}).get('command', '')
            if cmd == 'halt':
                self._trading_halted = True
                self._save_state()
            elif cmd == 'resume':
                self._trading_halted = False
                self._save_state()
            return

        # 熔断后仍需更新本地持仓 state——否则 emergency_close 后 execution_result 被丢弃，
        # _positions 残留幽灵持仓，下次重启从 state 加载继续误判。tick() 内部根据 halted 跳过新告警。

        if msg['type'] == 'symbol_update':
            return

        if msg['type'] == 'price_tick':
            symbol = msg.get('symbol') or msg['payload'].get('symbol')
            price = msg['payload'].get('price')
            if symbol and price:
                self._update_price(to_internal(symbol), price)
            return

        if msg['type'] == 'market_data':
            symbol = msg.get('symbol') or msg['payload'].get('symbol')
            price = msg['payload'].get('latest_price')
            if symbol and price:
                self._update_price(to_internal(symbol), price)

        elif msg['type'] == 'execution_result':
            await self._handle_execution_result(msg['payload'])
            self._save_state()

    async def _handle_execution_result(self, payload: dict):
        result = payload.get('result', {})
        # 优先使用result中的ccxt格式symbol，保持与positions.json一致
        symbol = result.get('symbol') or payload.get('symbol')
        if not symbol:
            return
        # 统一为系统内部规范 BASE-USDT（state dict 的唯一 key 形式）
        symbol = to_internal(symbol)
        status = payload.get('status')

        # 从执行结果中更新账户余额基准
        if 'balance' in result:
            self._account_balance = float(result['balance'])
        elif status == 'executed' and payload.get('action') in ('open_long', 'open_short'):
            # 开仓后余额 = 开仓前余额 - 保证金（size_usdt在统一风险预算中已是margin语义）
            margin = result.get('amount_usdt', 0)
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
                    # amount_usdt 语义=新总保证金。ContractExecutor.add_to_position 返回新总值；
                    # 兼容老返回（只有 add_amount_usdt 增量）：用累加。绝不允许用增量覆盖总值。
                    if 'amount_usdt' in result:
                        pos['amount_usdt'] = result['amount_usdt']
                    elif 'add_amount_usdt' in result:
                        pos['amount_usdt'] = pos.get('amount_usdt', 0) + result['add_amount_usdt']
                    pos['stop_loss'] = result.get('new_stop_loss') or result.get('stop_loss') or pos.get('stop_loss')
                    pos['take_profit'] = result.get('new_take_profit') or result.get('take_profit') or pos.get('take_profit')
                    self.logger.info(f"[风控] 加仓更新: {symbol} 新均价={pos['entry_price']:.4f} 新保证金={pos['amount_usdt']:.2f}")
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
                        "track": payload.get('track')
                                 or result.get('track')
                                 or (payload.get('attribution') or {}).get('track', 'main'),
                        "exit_profile": payload.get('exit_profile')
                                        or result.get('exit_profile')
                                        or (payload.get('attribution') or {}).get('exit_profile', 'trend_runner'),
                        "strategy_owner": payload.get('strategy_owner')
                                          or result.get('strategy_owner')
                                          or (payload.get('attribution') or {}).get('strategy_owner', 'main'),
                        "open_time": time.time(),
                        "highest_price": entry_price,
                        "lowest_price": entry_price,
                    }
                    self.logger.info(f"[风控] 记录持仓: {symbol} {side} lev={result.get('leverage')}x")
            elif action == 'close':
                pos = self._positions.get(symbol, {})
                if (not self._is_tactical_v2(payload, result, pos)
                        and (payload.get('track') == 'tactical'
                        or result.get('track') == 'tactical'
                        or (payload.get('attribution') or {}).get('track') == 'tactical'
                        or pos.get('track') == 'tactical')):
                    pnl = result.get('realized_pnl_net_usdt')
                    if pnl is None:
                        pnl = result.get('pnl', 0)
                    self.record_tactical_close(
                        symbol, float(pnl or 0.0),
                        payload.get('tactical_close_reason')
                        or result.get('tactical_close_reason')
                        or payload.get('reason', ''),
                        payload,
                    )
                if payload.get('reduce_origin'):
                    self.logger.info(f"[风控] {symbol} dust_closed,移除追踪 (来源 reduce 路径)")
                else:
                    self.logger.info(f"[风控] 移除持仓: {symbol}")
                self._positions.pop(symbol, None)

        elif status in ('force_closed', 'closed_externally'):
            pos = self._positions.get(symbol, {})
            if (not self._is_tactical_v2(payload, result, pos)
                    and (payload.get('track') == 'tactical'
                    or result.get('track') == 'tactical'
                    or (payload.get('attribution') or {}).get('track') == 'tactical'
                    or pos.get('track') == 'tactical')):
                pnl = result.get('realized_pnl_net_usdt')
                if pnl is None:
                    pnl = result.get('pnl', 0)
                self.record_tactical_close(symbol, float(pnl or 0.0), payload.get('reason', ''), payload)
            if symbol in self._positions:
                self.logger.info(f"[风控] {symbol} 外部平仓，移除追踪")
            self._positions.pop(symbol, None)

        # F4-001: rejected / reduce_failed 显式不缩敞口(防御性 early-return,无副作用)
        elif status in ('rejected', 'reduce_failed'):
            self.logger.debug(f"[风控] {symbol} reduce {status},不缩敞口 reason={payload.get('reason', '')}")
            return

        elif status == 'risk_reduced':
            if symbol in self._positions:
                reduce_pct = payload.get('reduce_pct', 0.5)
                self._positions[symbol]['amount_usdt'] *= (1 - reduce_pct)
            # F4-001: 减仓已成交但保护单异常 → 发独立 protection_failed risk_alert
            if payload.get('protection_failed'):
                if (not self._is_tactical_v2(
                            payload, result, self._positions.get(symbol, {})
                        )
                        and (payload.get('track') == 'tactical'
                        or (payload.get('attribution') or {}).get('track') == 'tactical'
                        or self._positions.get(symbol, {}).get('track') == 'tactical')):
                    self.record_tactical_execution_failure(symbol, 'protection_failed', payload)
                await self.publish('risk_alert', {
                    'type': 'protection_failed',
                    'symbol': symbol,
                    'protective_update_state': payload.get('protective_update_state', ''),
                    'request_id': payload.get('request_id', ''),
                })

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

        # 熔断期间不再发新告警，但持仓 state 仍由 on_message 跟随 execution_result 维护
        if self._trading_halted:
            if int(time.time()) % 60 == 0:
                self._save_state()
            return

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
        """维度1: 单仓浮亏监控（动态阈值 = SL距离×杠杆 + 缓冲）"""
        pos = self._positions[symbol]
        pnl_pct = self._calc_pnl_pct(pos, price)

        threshold = self._get_danger_threshold(pos)
        if pnl_pct < -threshold:
            if self._can_alert(f"position_danger:{symbol}"):
                self.logger.warning(
                    f"[风控] {symbol} 浮亏{pnl_pct:.1f}% > -{threshold:.1f}%（动态阈值）!"
                )
                await self.publish("risk_alert", {
                    "type": "position_danger",
                    "scope": "symbol",
                    "symbol": symbol,
                    "pnl_pct": pnl_pct,
                    "threshold": threshold,
                    "action": "close_position"
                }, symbol=symbol)

    def _get_danger_threshold(self, pos: dict) -> float:
        """计算单仓 position_danger 动态阈值。

        公式: SL距离% × 杠杆 + 缓冲(5%)
        兜底: 如果无法计算SL距离，使用 fallback(50%)
        """
        entry = pos.get('entry_price', 0)
        sl = pos.get('stop_loss', 0)
        leverage = pos.get('leverage', 1)

        if entry <= 0 or sl <= 0:
            return self._max_single_loss_pct_fallback

        sl_dist_pct = abs(entry - sl) / entry * 100
        if sl_dist_pct <= 0:
            return self._max_single_loss_pct_fallback

        threshold = sl_dist_pct * leverage + self._position_danger_buffer_pct
        return min(threshold, self._max_single_loss_pct_fallback)

    async def _check_portfolio_drawdown(self):
        """维度2: 组合回撤保护

        字段语义（统一风险预算下）：
        - pos['amount_usdt'] = 保证金 (margin)
        - _calc_pnl_pct 已乘 leverage，返回保证金收益率(%)
        - 总盈亏 = sum(margin × pnl_pct / 100)
        """
        total_pnl_usdt = 0.0
        for symbol, pos in self._positions.items():
            price = self._prices.get(symbol)
            if not price:
                continue
            pnl_pct = self._calc_pnl_pct(pos, price)
            margin = pos['amount_usdt']
            total_pnl_usdt += margin * pnl_pct / 100

        # 组合回撤按逻辑账户 cap 衡量；没有 cap 时才退回账户余额/保证金兜底。
        if self._effective_balance_cap and self._effective_balance_cap > 0:
            balance = self._effective_balance_cap
        elif self._account_balance > 0:
            balance = self._account_balance
        else:
            balance = sum(pos['amount_usdt'] for pos in self._positions.values()) or 20.0
        drawdown_pct = abs(total_pnl_usdt) / balance * 100
        if total_pnl_usdt < 0 and drawdown_pct > self._max_portfolio_drawdown_pct:
            if self._can_alert("max_drawdown"):
                self.logger.warning(
                    f"[风控] 组合回撤{drawdown_pct:.1f}% > {self._max_portfolio_drawdown_pct}%!"
                )
                await self.publish("risk_alert", {
                    "type": "max_drawdown",
                    "scope": "market",
                    "drawdown_pct": drawdown_pct,
                    "total_pnl_usdt": total_pnl_usdt,
                    "action": "close_all"
                })

    def _get_dynamic_flash_threshold(self, symbol: str) -> float:
        """基于近期波动率动态计算闪崩阈值：max(3%, 近1h波动率×0.5)"""
        history = self._price_history.get(symbol, [])
        if len(history) < 10:
            return self._flash_move_threshold
        now = time.time()
        recent = [p for t, p in history if t > now - 3600]
        if len(recent) < 5:
            return self._flash_move_threshold
        high = max(recent)
        low = min(recent)
        vol_1h = (high - low) / low * 100 if low > 0 else 0
        return max(self._flash_move_threshold, vol_1h * 0.5)

    async def _check_flash_move(self, symbol: str):
        """维度3: 闪崩检测（60秒窗口，动态阈值）"""
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

        threshold = self._get_dynamic_flash_threshold(symbol)
        if change_pct > threshold:
            price_up = last_price > first_price
            pos = self._positions.get(symbol)
            if pos:
                # 价格暴涨且持仓做多 = 盈利方向，不平仓；暴跌且做空同理
                pos_side = pos.get('side')
                if (price_up and pos_side == 'long') or (not price_up and pos_side == 'short'):
                    if self._can_alert(f"flash_move_favorable:{symbol}"):
                        self.logger.info(
                            f"[风控] {symbol} 闪动{change_pct:.1f}%但方向有利于持仓({pos_side})，不平仓"
                        )
                    return

            if self._can_alert(f"flash_move:{symbol}"):
                direction = "暴跌" if not price_up else "暴涨"
                self.logger.warning(
                    f"[风控] {symbol} {self._flash_move_window}s内{direction} {change_pct:.1f}%!"
                )
                await self.publish("risk_alert", {
                    "type": "flash_move",
                    "scope": "symbol",
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
                    "scope": "symbol",
                    "symbol": symbol,
                    "leverage": leverage,
                    "pnl_pct": pnl_pct,
                    "action": "close_position"
                }, symbol=symbol)

    async def _check_correlation_risk(self):
        """维度5: 关联性风险（同方向保证金敞口叠加）"""
        long_exposure = 0.0
        short_exposure = 0.0
        for pos in self._positions.values():
            # amount_usdt在统一风险预算中已是margin语义，不再除以leverage
            margin = pos['amount_usdt']
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
                    "scope": "market",
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
                        "scope": "symbol",
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
                    "scope": "symbol",
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
                "scope": "market",
                "symbol": symbol,
                "reason": f"daily_hard_stop_{reason}",
                "action": "close_position"
            }, symbol=symbol)

        self._trading_halted = True
        self._halt_state.halt(reason=f"daily_hard_stop_{reason}", triggered_by="portfolio_risk_guard")
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
            'tactical_circuit': self._tactical_circuit_state(),
        }

        try:
            from utils.atomic_io import atomic_write_json
            atomic_write_json(self._state_file, state)
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

                # 迁移：旧 state 可能用 ccxt 或 -SWAP 格式 key，统一为 BASE-USDT
                raw_positions = state.get('positions', {})
                raw_prices = state.get('prices', {})
                self._positions = {to_internal(k): v for k, v in raw_positions.items()}
                self._prices = {to_internal(k): v for k, v in raw_prices.items()}
                self._trading_halted = state.get('trading_halted', False)
                self._last_alert_times = state.get('last_alert_times', {})
                tactical = state.get('tactical_circuit') or {}
                if tactical:
                    today = self._tactical_day_key()
                    stored_day = tactical.get('daily_date') or today
                    self._tactical_daily_date = today
                    self._tactical_daily_pnl = (
                        float(tactical.get('daily_pnl', 0.0) or 0.0)
                        if stored_day == today else 0.0
                    )
                    self._tactical_loss_streak = int(tactical.get('loss_streak', 0) or 0)
                    self._tactical_pause_until = float(tactical.get('pause_until', 0) or 0)
                    self._tactical_pause_reason = tactical.get('pause_reason', '') or ''
                    quality = tactical.get('quality_results', [])
                    self._tactical_quality_results = list(quality) if isinstance(quality, list) else []

                if self._trading_halted:
                    self.logger.warning("系统处于熔断状态（从上次会话恢复）")
            except Exception as e:
                self.logger.error(f"加载状态失败: {e}")

        # 交叉验证：从positions.json补录RiskGuard不知道的持仓（统一为 internal 格式）
        from utils.state_paths import get_state_paths
        positions_file = get_state_paths().positions
        if os.path.exists(positions_file):
            try:
                with open(positions_file, 'r') as f:
                    executor_positions = json.load(f)
                for sym, pos in executor_positions.items():
                    internal = to_internal(sym)
                    if internal not in self._positions and 'stop_loss' in pos:
                        entry = pos.get('entry_price', 0)
                        self._positions[internal] = {
                            "symbol": internal,
                            "side": pos['side'],
                            "entry_price": entry,
                            "amount_usdt": pos.get('amount_usdt', 0),
                            "leverage": pos.get('leverage', 1),
                            "stop_loss": pos.get('stop_loss'),
                            "take_profit": pos.get('take_profit'),
                            "highest_price": entry if pos['side'] == 'long' else entry,
                            "lowest_price": entry if pos['side'] == 'short' else entry,
                        }
                        self.logger.info(f"RiskGuard补录持仓: {internal} ({pos['side']} {pos.get('leverage',1)}x)")
            except Exception as e:
                self.logger.warning(f"交叉验证positions.json失败: {e}")

        self.logger.info(f"RiskGuard状态已加载: {len(self._positions)}个持仓")

    @staticmethod
    def _normalize_key(key: str) -> str:
        """[已废弃] 保留用于向后兼容，建议直接用 utils.symbol.to_internal"""
        return to_internal(key)

    @staticmethod
    def _to_ccxt_key(symbol: str) -> str:
        """[已废弃] 保留用于向后兼容，state 不再用 ccxt 格式 key"""
        from utils.symbol import to_ccxt
        return to_ccxt(symbol)
