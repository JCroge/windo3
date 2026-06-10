"""Paper Trading Executor — 影子账户，与实盘 Executor 并行运行

设计目标：
- 订阅相同的 trade_decision:* 和 price_tick:*，与真实执行器零交互
- 模拟开仓/平仓/止损/止盈，维护影子账户余额
- 持仓和已完成交易持久化到 data/paper_*
- 不下任何真实订单，不查询交易所

mirror_risk 模式（默认 true）：
- 跟随全局熔断状态，熔断期间禁止新模拟开仓
- 拒绝时记录标的、动作、原因、来源
"""

import json
import os
import time
from typing import Optional, Dict

from agents.base import BaseAgent
from utils.halt_state import get_halt_state

PAPER_TRADES_FILE = "data/paper_trades.jsonl"
PAPER_POSITIONS_FILE = "data/paper_positions.json"
PAPER_EQUITY_FILE = "data/paper_equity.json"

DEFAULT_PAPER_LIMIT_TICK_STALENESS_SEC = 60


class PaperExecutor(BaseAgent):
    name = "paper_executor"
    subscriptions = [
        "trade_decision:*",
        "price_tick:*",
        "system_command",
    ]

    def __init__(self, config: dict = None):
        super().__init__(config)
        cap = (config or {}).get('effective_balance_cap')
        self._initial_equity = float(cap) if cap and cap > 0 else 1000.0
        self.min_confidence = (config or {}).get('min_confidence', 60)
        self._max_trade_amount = (config or {}).get('max_trade_amount', 10)
        self._mirror_risk = (config or {}).get('mirror_risk', True)

        self._books = {
            "realistic": {"positions": {}, "equity": self._initial_equity},
            "idealized": {"positions": {}, "equity": self._initial_equity},
        }
        self._latest_price: dict = {}
        self._halted: bool = False
        self._halt_state = get_halt_state()
        self._cost_model = None
        self._rejected_log: list = []
        self._pending_limits: Dict[str, dict] = {}
        self._tick_staleness_sec = float(
            (config or {}).get('paper_limit_tick_staleness_sec',
                               DEFAULT_PAPER_LIMIT_TICK_STALENESS_SEC)
        )
        self._latest_tick_ts: Dict[str, float] = {}

    @property
    def _positions(self) -> dict:
        """Realistic-book positions (proxy: preserves all legacy references)."""
        return self._books["realistic"]["positions"]

    @property
    def _equity(self) -> float:
        return self._books["realistic"]["equity"]

    @_equity.setter
    def _equity(self, value: float) -> None:
        self._books["realistic"]["equity"] = value

    async def setup(self):
        os.makedirs("data", exist_ok=True)
        self._load_state()
        try:
            from utils.cost_model import get_default_cost_model
            self._cost_model = get_default_cost_model()
        except Exception as e:
            self.logger.warning(f"[PaperExecutor] CostModel 加载失败，将用 0.2% 兜底: {e}")
            self._cost_model = None

        self.logger.info(
            f"[PaperExecutor] 影子账户启动 equity={self._equity:.2f} USDT "
            f"持仓={len(self._positions)}"
        )

    async def on_message(self, msg: dict):
        mtype = msg.get('type')
        if mtype == 'system_command':
            cmd = msg.get('payload', {}).get('command', '')
            if cmd == 'halt':
                self._halted = True
                self.logger.warning("[PaperExecutor] halt — 停止新开仓（已有持仓继续跟SL/TP）")
            elif cmd == 'resume':
                self._halted = False
                self.logger.info("[PaperExecutor] resume")
            return

        if mtype == 'price_tick':
            payload = msg.get('payload', {})
            symbol = msg.get('symbol') or payload.get('symbol')
            price = payload.get('price')
            if symbol and price:
                price_f = float(price)
                self._latest_price[symbol] = price_f
                self._latest_tick_ts[symbol] = time.time()
                if symbol in self._pending_limits:
                    await self._wait_paper_limit_fill(symbol, price_f)
                await self._check_sl_tp(symbol, price_f)
            return

        if mtype == 'trade_decision':
            decision = msg.get('payload', {})
            symbol = msg.get('symbol') or decision.get('symbol')
            if symbol:
                decision = dict(decision)
                decision['symbol'] = symbol
            await self._execute_decision(decision)

    async def _execute_decision(self, decision: dict):
        action = decision.get('action', 'hold')
        confidence = decision.get('confidence', 0)
        symbol = decision.get('symbol')
        plan = decision.get('plan')
        source = decision.get('source', '')
        size_pct = decision.get('size_pct', 1.0)

        if action == 'hold' or not symbol:
            return

        # 标准化 symbol（去 -SWAP 后缀，与上游 tech_analysis 一致）
        norm_symbol = self._normalize_symbol(symbol)
        position = self._positions.get(norm_symbol)

        if self._halted and action in ('open_long', 'open_short') and position is None:
            self._record_rejection(norm_symbol, action, "skip_halted", source)
            return

        # mirror_risk: 跟随全局熔断状态
        if self._mirror_risk and not self._halt_state.can_open_new:
            if action in ('open_long', 'open_short') and position is None:
                reason = "reconciliation_pending" if self._halt_state.reconciliation_pending else "global_halt"
                self._record_rejection(norm_symbol, action, reason, source)
                return

        if confidence < self.min_confidence and action in ('open_long', 'open_short'):
            return

        if action in ('open_long', 'open_short') and position is None:
            if symbol in self._pending_limits:
                # Already waiting on a limit fill — guard handled inside _open_paper
                self.logger.info(
                    f"[PAPER] {norm_symbol} {action} 跳过：已有 pending limit"
                )
                return
            if source == 'position_analyst':
                return
            await self._open_paper(norm_symbol, action, plan, decision)
        elif action in ('open_long', 'open_short') and position is not None:
            if source == 'position_analyst':
                await self._add_paper(norm_symbol, action, size_pct, position)
        elif action == 'close':
            if norm_symbol in self._pending_limits:
                self._pending_limits.pop(norm_symbol, None)
                self.logger.info(f"[PAPER] {norm_symbol} close 取消 pending limit")
                return
            if position is None:
                return
            if size_pct < 1.0 and source == 'position_analyst':
                await self._reduce_paper(norm_symbol, size_pct, position)
            else:
                await self._close_paper(norm_symbol, position, reason='signal_close')

    def _record_rejection(self, symbol: str, action: str, reason: str, source: str):
        """记录被拒绝的开仓请求（mirror_risk 模式下）"""
        record = {
            "ts": time.time(),
            "symbol": symbol,
            "action": action,
            "reason": reason,
            "source": source,
            "halt_reason": self._halt_state.reason,
        }
        self._rejected_log.append(record)
        if len(self._rejected_log) > 100:
            self._rejected_log = self._rejected_log[-50:]
        self.logger.info(f"[PAPER] {symbol} {action} 拒绝: {reason} (来源={source})")

    async def _open_paper(self, symbol: str, action: str, plan: Optional[dict], decision: dict):
        side = 'long' if action == 'open_long' else 'short'

        # Duplicate-pending guard (Req5 Scenario 1)
        if symbol in self._pending_limits:
            self.logger.info(
                f"[PAPER] {symbol} {action} 跳过：已有 pending limit"
            )
            return

        order_type = (plan or {}).get('order_type', 'market')
        entry_zone = (plan or {}).get('entry_zone') or []
        if (order_type == 'limit'
                and isinstance(entry_zone, (list, tuple))
                and len(entry_zone) >= 2
                and float(entry_zone[0] or 0) > 0
                and float(entry_zone[1] or 0) > 0):
            self._enqueue_pending_limit(symbol, side, action, plan, decision)
            return

        if order_type == 'limit':
            # entry_zone invalid/missing — fail-safe to market with warning per spec Req1 Scenario 3
            self.logger.warning(
                f"[PAPER] {symbol} order_type=limit but entry_zone invalid ({entry_zone!r}) "
                f"— falling back to market"
            )

        price = self._latest_price.get(symbol)
        if not price:
            price = (plan or {}).get('entry_zone', [0])[0] if plan else 0
            if not price:
                self.logger.warning(f"[PAPER] {symbol} 无价格快照，跳过开仓")
                return
        await self._open_paper_at_price(
            symbol=symbol, side=side, action=action,
            plan=plan, decision=decision,
            fill_price=price, entry_method='market',
        )

    def _enqueue_pending_limit(self, symbol: str, side: str, action: str,
                               plan: dict, decision: dict) -> None:
        """Queue a limit-order paper open until tick hit or timeout."""
        entry_zone = list(plan.get('entry_zone', []))
        low = min(entry_zone[0], entry_zone[1])
        high = max(entry_zone[0], entry_zone[1])
        timeout_sec = float(plan.get('limit_timeout_sec', 30))
        no_fallback = bool(plan.get('limit_no_fallback', False))
        now = time.time()
        self._pending_limits[symbol] = {
            'symbol': symbol,
            'side': side,
            'action': action,
            'plan': dict(plan),
            'decision': dict(decision),
            'entry_zone': [low, high],
            'created_at': now,
            'deadline': now + timeout_sec,
            'limit_no_fallback': no_fallback,
        }
        self.logger.info(
            f"[PAPER] {symbol} {side} 限价挂出 zone=[{low:.6g},{high:.6g}] "
            f"timeout={timeout_sec:.0f}s no_fallback={no_fallback}"
        )

    async def _wait_paper_limit_fill(self, symbol: str, tick_price: float) -> None:
        """Tick-driven limit fill check. Hit → midpoint fill + entry_method='limit_filled'."""
        pending = self._pending_limits.get(symbol)
        if not pending:
            return
        low, high = pending['entry_zone']
        if not (low <= tick_price <= high):
            return
        fill_price = (low + high) / 2.0
        plan = pending['plan']
        decision = pending['decision']
        # Pop BEFORE await so a failure inside _open_paper_at_price cannot leave a zombie pending entry
        self._pending_limits.pop(symbol, None)
        await self._open_paper_at_price(
            symbol=symbol, side=pending['side'], action=pending['action'],
            plan=plan, decision=decision,
            fill_price=fill_price, entry_method='limit_filled',
        )

    async def _scan_pending_limits(self) -> None:
        """Cleanup loop: resolve any pending limit whose deadline has elapsed."""
        if not self._pending_limits:
            return
        now = time.time()
        # Snapshot to allow mutation during iteration
        expired = [(sym, p) for sym, p in self._pending_limits.items()
                   if now >= p['deadline']]
        for symbol, pending in expired:
            await self._resolve_pending_timeout(symbol, pending, now)

    async def _resolve_pending_timeout(self, symbol: str, pending: dict, now: float) -> None:
        """Decision tree: no_fallback → reject; else fresh tick → market; stale → reject."""
        side = pending['side']
        plan = pending['plan']
        decision = pending['decision']
        no_fallback = pending['limit_no_fallback']
        request_id = decision.get('request_id', '')
        entry_zone = pending['entry_zone']

        self._pending_limits.pop(symbol, None)

        if no_fallback:
            self._record_paper_unfilled(symbol, side, request_id, entry_zone,
                                        reason='paper_unfilled')
            await self._publish_paper_unfilled(symbol, side, request_id,
                                               entry_zone, plan, subtype=None)
            return

        last_tick_ts = self._latest_tick_ts.get(symbol)
        latest_price = self._latest_price.get(symbol)
        stale = (last_tick_ts is None
                 or latest_price is None
                 or (now - last_tick_ts) > self._tick_staleness_sec)
        if stale:
            self._record_paper_unfilled(symbol, side, request_id, entry_zone,
                                        reason='paper_unfilled_no_tick')
            await self._publish_paper_unfilled(symbol, side, request_id,
                                               entry_zone, plan, subtype='no_tick')
            return

        # Fresh tick → market fallback
        await self._open_paper_at_price(
            symbol=symbol, side=side, action=pending['action'],
            plan=plan, decision=decision,
            fill_price=float(latest_price), entry_method='market',
        )
        self.logger.info(
            f"[PAPER] {symbol} {side} paper_limit_fallback_used 限价超时 fallback market @ {latest_price:.6f}"
        )

    def _record_paper_unfilled(self, symbol: str, side: str, request_id: str,
                               entry_zone: list, reason: str) -> None:
        record = {
            "ts": time.time(),
            "symbol": symbol,
            "side": side,
            "action": f"open_{side}",
            "reason": reason,
            "entry_method": "limit_unfilled",
            "entry_zone": list(entry_zone),
            "request_id": request_id,
            "halt_reason": self._halt_state.reason,
        }
        self._rejected_log.append(record)
        if len(self._rejected_log) > 100:
            self._rejected_log = self._rejected_log[-50:]
        self.logger.info(f"[PAPER] {symbol} {side} {reason} entry_zone={entry_zone}")

    async def _publish_paper_unfilled(self, symbol: str, side: str, request_id: str,
                                      entry_zone: list, plan: dict,
                                      subtype: Optional[str]) -> None:
        payload = {
            "type": "paper_unfilled",
            "source": "paper_executor",
            "symbol": symbol,
            "side": side,
            "entry_zone": list(entry_zone),
            "request_id": request_id,
            "timeout_sec": float(plan.get('limit_timeout_sec', 0)),
            "limit_no_fallback": bool(plan.get('limit_no_fallback', False)),
        }
        if subtype:
            payload["subtype"] = subtype
            payload["reason"] = "paper_unfilled_no_tick"
        try:
            await self.publish("risk_alert", payload, symbol=symbol)
        except Exception as e:
            self.logger.warning(f"[PAPER] {symbol} paper_unfilled publish failed: {e}")

    async def _open_paper_at_price(self, symbol: str, side: str, action: str,
                                   plan: Optional[dict], decision: dict,
                                   fill_price: float, entry_method: str) -> None:
        """Shared open path: builds position dict, persists, publishes event."""
        if plan:
            margin = float(plan.get('size_usdt', 0))
            leverage = int(plan.get('leverage', 3))
            sl = float(plan.get('stop_loss', 0))
            tp_levels = plan.get('tp_levels') or []
            if tp_levels:
                tp = float(tp_levels[0])
            else:
                tp_raw = plan.get('take_profit', 0)
                if isinstance(tp_raw, (list, tuple)):
                    tp = float(tp_raw[0]) if tp_raw else 0.0
                else:
                    tp = float(tp_raw or 0)
            atr_pct = plan.get('atr_pct', 0.02)
        else:
            margin = self._max_trade_amount * decision.get('size_pct', 0.5)
            leverage = int(self.config.get('leverage', 3))
            sl_dist = 0.025
            sl = fill_price * (1 - sl_dist) if side == 'long' else fill_price * (1 + sl_dist)
            tp = fill_price * (1 + sl_dist * 1.5) if side == 'long' else fill_price * (1 - sl_dist * 1.5)
            atr_pct = 0.02

        if margin <= 0 or (self._equity - self._locked_margin()) < margin:
            self.logger.warning(
                f"[PAPER] {symbol} 跳过：可用保证金不足 (margin={margin}, "
                f"free_equity={self._equity - self._locked_margin():.2f})"
            )
            return

        notional = margin * leverage
        entry_fee = self._fee(notional)
        pos = {
            'symbol': symbol, 'side': side,
            'entry_price': fill_price,
            'sl': sl, 'tp': tp,
            'margin': margin, 'leverage': leverage, 'notional': notional,
            'opened_at': time.time(), 'entry_fee': entry_fee,
            'atr_pct': atr_pct,
            'source': decision.get('source', 'judge'),
            'confidence': decision.get('confidence', 0),
            'request_id': decision.get('request_id', ''),
            'attribution': decision.get('attribution') or (plan or {}).get('attribution') or {},
            'entry_method': entry_method,
        }
        self._positions[symbol] = pos
        self._equity -= entry_fee
        self._persist_state()
        self.logger.info(
            f"[PAPER] OPEN {side.upper()} {symbol} @ {fill_price:.6f} "
            f"margin={margin:.2f} lev={leverage}x SL={sl:.6f} TP={tp:.6f} "
            f"entry_method={entry_method}"
        )
        await self.publish("paper_execution_result", {
            "status": "executed", "action": action, "symbol": symbol,
            "request_id": pos.get('request_id', ''), "result": dict(pos),
            "paper_equity": round(self._equity, 4),
            "locked_margin": round(self._locked_margin(), 4),
            "free_equity": round(self._equity - self._locked_margin(), 4),
        }, symbol=symbol)

    async def _close_paper(self, symbol: str, position: dict, reason: str, exit_price: Optional[float] = None):
        if exit_price is None:
            exit_price = self._latest_price.get(symbol, position['entry_price'])

        side = position['side']
        entry = position['entry_price']
        margin = position['margin']
        leverage = position['leverage']
        notional = position['notional']

        if side == 'long':
            pnl_pct = (exit_price - entry) / entry
        else:
            pnl_pct = (entry - exit_price) / entry

        gross_pnl = margin * pnl_pct * leverage
        exit_fee = self._fee(notional)
        hold_hours = (time.time() - position['opened_at']) / 3600

        self._equity += gross_pnl - exit_fee

        del self._positions[symbol]
        self._persist_state()

        trade_record = {
            **position,
            'exit_price': exit_price,
            'exit_reason': reason,
            'gross_pnl': round(gross_pnl, 4),
            'exit_fee': round(exit_fee, 4),
            'net_pnl': round(gross_pnl - position['entry_fee'] - exit_fee, 4),
            'hold_hours': round(hold_hours, 2),
            'closed_at': time.time(),
            'paper_equity_after': round(self._equity, 4),
        }
        trade_record['entry_method'] = position.get('entry_method', 'market')
        attr = position.get('attribution', {})
        if attr:
            trade_record['entry_regime'] = attr.get('entry_regime', 'unknown')
            trade_record['raw_regime'] = attr.get('raw_regime', 'unknown')
            trade_record['rr_policy'] = attr.get('rr_policy', '')
            trade_record['slot_type'] = attr.get('slot_type', 'main')
            trade_record['is_low_rr'] = attr.get('is_low_rr', False)
            trade_record['is_probe'] = attr.get('is_probe', False)
        self._append_trade(trade_record)
        self.logger.info(
            f"[PAPER] CLOSE {symbol} @ {exit_price:.6f} ({reason}) "
            f"PnL={trade_record['net_pnl']:+.4f} equity={self._equity:.2f}"
        )

        await self.publish("paper_execution_result", {
            "status": "closed",
            "action": "close",
            "symbol": symbol,
            "reason": reason,
            "result": trade_record,
            "entry_request_id": position.get('request_id', ''),
            "paper_equity": round(self._equity, 4),
            "locked_margin": round(self._locked_margin(), 4),
            "free_equity": round(self._equity - self._locked_margin(), 4),
        }, symbol=symbol)

    async def _add_paper(self, symbol: str, action: str, size_pct: float, position: dict):
        """加仓：按当前价加权平均"""
        side = 'long' if action == 'open_long' else 'short'
        if position['side'] != side:
            return  # 方向冲突
        price = self._latest_price.get(symbol)
        if not price:
            return

        add_margin = position['margin'] * size_pct
        if add_margin <= 0:
            return
        total_margin_cap = self._max_trade_amount * 2
        if position['margin'] + add_margin > total_margin_cap:
            add_margin = max(0, total_margin_cap - position['margin'])
            if add_margin <= 0:
                return

        leverage = position['leverage']
        add_notional = add_margin * leverage
        add_fee = self._fee(add_notional)

        free_equity = self._equity - self._locked_margin()
        if add_margin + add_fee > free_equity:
            self.logger.warning(f"[PAPER] {symbol} 加仓跳过：可用保证金不足 (need={add_margin+add_fee:.2f}, free={free_equity:.2f})")
            return

        # 加权均价
        old_qty = position['margin'] * leverage / position['entry_price']
        add_qty = add_notional / price
        new_qty = old_qty + add_qty
        new_entry = (old_qty * position['entry_price'] + add_qty * price) / new_qty

        # 保持原 SL/TP 距离比例
        old_sl_dist = abs(position['sl'] - position['entry_price']) / position['entry_price']
        old_tp_dist = abs(position['tp'] - position['entry_price']) / position['entry_price']
        new_sl = new_entry * (1 - old_sl_dist) if side == 'long' else new_entry * (1 + old_sl_dist)
        new_tp = new_entry * (1 + old_tp_dist) if side == 'long' else new_entry * (1 - old_tp_dist)

        position['margin'] += add_margin
        position['entry_price'] = new_entry
        position['notional'] = position['margin'] * leverage
        position['sl'] = new_sl
        position['tp'] = new_tp
        position['entry_fee'] += add_fee
        self._equity -= add_fee
        self._persist_state()
        self.logger.info(
            f"[PAPER] ADD {symbol} +{add_margin:.2f} → margin={position['margin']:.2f} "
            f"avg_entry={new_entry:.6f}"
        )

    async def _reduce_paper(self, symbol: str, size_pct: float, position: dict):
        """部分平仓：按 size_pct 比例兑现 PnL"""
        price = self._latest_price.get(symbol, position['entry_price'])
        side = position['side']
        entry = position['entry_price']
        leverage = position['leverage']

        reduce_margin = position['margin'] * size_pct
        reduce_notional = reduce_margin * leverage

        if side == 'long':
            pnl_pct = (price - entry) / entry
        else:
            pnl_pct = (entry - price) / entry

        partial_pnl = reduce_margin * pnl_pct * leverage
        partial_fee = self._fee(reduce_notional)
        net_partial = partial_pnl - partial_fee
        self._equity += net_partial

        position['margin'] -= reduce_margin
        position['notional'] = position['margin'] * leverage
        self._persist_state()
        reduce_record = {
            **{k: position[k] for k in ('symbol', 'side', 'leverage')},
            'entry_price': entry,
            'exit_price': price,
            'exit_reason': 'partial_reduce',
            'reduce_pct': size_pct,
            'gross_pnl': round(partial_pnl, 4),
            'net_pnl': round(net_partial, 4),
            'closed_at': time.time(),
            'paper_equity_after': round(self._equity, 4),
        }
        attr = position.get('attribution', {})
        if attr:
            reduce_record['entry_regime'] = attr.get('entry_regime', 'unknown')
            reduce_record['slot_type'] = attr.get('slot_type', 'main')
            reduce_record['is_low_rr'] = attr.get('is_low_rr', False)
            reduce_record['is_probe'] = attr.get('is_probe', False)
        self._append_trade(reduce_record)
        self.logger.info(f"[PAPER] REDUCE {symbol} {int(size_pct*100)}% PnL={net_partial:+.4f}")

    async def _check_sl_tp(self, symbol: str, price: float):
        pos = self._positions.get(symbol)
        if not pos:
            return
        side = pos['side']
        sl, tp = pos['sl'], pos['tp']

        if side == 'long':
            if price <= sl:
                await self._close_paper(symbol, pos, reason='sl', exit_price=sl)
            elif tp and price >= tp:
                await self._close_paper(symbol, pos, reason='tp', exit_price=tp)
        else:  # short
            if price >= sl:
                await self._close_paper(symbol, pos, reason='sl', exit_price=sl)
            elif tp and price <= tp:
                await self._close_paper(symbol, pos, reason='tp', exit_price=tp)

    def _fee(self, notional: float) -> float:
        if self._cost_model:
            return self._cost_model.entry_fee(notional, is_maker=False)
        return notional * 0.001  # 兜底 taker 0.1%

    def _normalize_symbol(self, symbol: str) -> str:
        if symbol.endswith('-SWAP'):
            return symbol[:-5]
        return symbol

    def _load_state(self):
        try:
            if os.path.exists(PAPER_POSITIONS_FILE):
                with open(PAPER_POSITIONS_FILE) as f:
                    loaded = json.load(f)
                    self._books["realistic"]["positions"].clear()
                    self._books["realistic"]["positions"].update(loaded)
            if os.path.exists(PAPER_EQUITY_FILE):
                with open(PAPER_EQUITY_FILE) as f:
                    data = json.load(f)
                    self._equity = float(data.get('equity', self._initial_equity))
        except Exception as e:
            self.logger.warning(f"[PaperExecutor] 状态加载失败: {e}")

    def _locked_margin(self) -> float:
        return sum(p['margin'] for p in self._positions.values())

    def _persist_state(self):
        try:
            from utils.atomic_io import atomic_write_json
            atomic_write_json(PAPER_POSITIONS_FILE, self._positions)
            locked = self._locked_margin()
            atomic_write_json(PAPER_EQUITY_FILE, {
                'equity': round(self._equity, 4),
                'locked_margin': round(locked, 4),
                'free_equity': round(self._equity - locked, 4),
                'initial_equity': self._initial_equity,
                'open_positions': len(self._positions),
                'updated_at': time.time(),
            })
        except Exception as e:
            self.logger.error(f"[PaperExecutor] 状态持久化失败: {e}")

    def _append_trade(self, record: dict):
        try:
            with open(PAPER_TRADES_FILE, 'a') as f:
                f.write(json.dumps(record, default=str) + '\n')
        except Exception as e:
            self.logger.error(f"[PaperExecutor] 写trade ledger失败: {e}")

    async def tick(self):
        import asyncio
        await asyncio.sleep(30)
        await self._scan_pending_limits()
        if int(time.time()) % 300 < 30:
            self.logger.info(
                f"[PaperExecutor] equity={self._equity:.2f} 持仓={len(self._positions)} "
                f"unrealized_pnl≈{self._unrealized_pnl():+.2f} "
                f"pending_limits={len(self._pending_limits)}"
            )

    def _unrealized_pnl(self) -> float:
        total = 0.0
        for sym, pos in self._positions.items():
            price = self._latest_price.get(sym)
            if not price:
                continue
            entry = pos['entry_price']
            if pos['side'] == 'long':
                pct = (price - entry) / entry
            else:
                pct = (entry - price) / entry
            total += pos['margin'] * pct * pos['leverage']
        return total
