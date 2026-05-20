"""实盘订单事件账本 — 以交易所真实成交为准的 PnL 记录系统

每次开仓/减仓/平仓后调用 exchange.fetch_order 获取真实成交均价和手续费，
写入 JSONL 事件流 + 持仓生命周期聚合。
"""

import json
import os
import time
import uuid
import datetime
from typing import Optional, Dict, List, Any


class LiveLedger:

    def __init__(self, exchange, events_path: str = "data/live_order_events.jsonl",
                 lifecycle_path: str = "data/live_position_lifecycle.json",
                 logger=None):
        self.exchange = exchange
        self.events_path = events_path
        self.lifecycle_path = lifecycle_path
        self.logger = logger
        self._lifecycle: Dict[str, dict] = self._load_lifecycle()
        os.makedirs(os.path.dirname(events_path) or '.', exist_ok=True)

    # ── Public API ──────────────────────────────────────────────────────────

    def record_open(self, order_id: str, symbol: str, side: str,
                    amount_usdt: float, leverage: int,
                    estimated_price: float) -> dict:
        fill = self._fetch_fill(order_id, symbol, estimated_price)
        position_id = f"{symbol}-{uuid.uuid4().hex[:8]}-{side}"

        event = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "position_id": position_id,
            "symbol": symbol,
            "event_type": "open",
            "side": side,
            "order_id": order_id,
            "fill_price": fill["fill_price"],
            "filled_amount": fill.get("filled_amount"),
            "fee": fill["fee"],
            "fee_currency": fill.get("fee_currency", "USDT"),
            "amount_usdt": amount_usdt,
            "leverage": leverage,
            "realized_pnl": 0.0,
            "source": fill["source"],
        }
        self._write_event(event)
        self._open_lifecycle(event)
        return event

    def record_add(self, order_id: str, symbol: str, side: str,
                   amount_usdt: float, leverage: int,
                   estimated_price: float) -> dict:
        """加仓记录 — 追加到已有 open lifecycle，不新建"""
        fill = self._fetch_fill(order_id, symbol, estimated_price)
        position_id = self._find_open_position_id(symbol, side)

        if not position_id:
            return self.record_open(order_id, symbol, side,
                                    amount_usdt, leverage, estimated_price)

        event = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "position_id": position_id,
            "symbol": symbol,
            "event_type": "add",
            "side": side,
            "order_id": order_id,
            "fill_price": fill["fill_price"],
            "filled_amount": fill.get("filled_amount"),
            "fee": fill["fee"],
            "fee_currency": fill.get("fee_currency", "USDT"),
            "amount_usdt": amount_usdt,
            "leverage": leverage,
            "realized_pnl": 0.0,
            "source": fill["source"],
        }
        self._write_event(event)
        self._update_lifecycle_pnl(event)
        return event

    def record_reduce(self, order_id: str, symbol: str, side: str,
                      entry_price: float, reduce_usdt: float, leverage: int,
                      estimated_price: float) -> dict:
        fill = self._fetch_fill(order_id, symbol, estimated_price)
        pnl = self._calc_realized_pnl(side, entry_price, fill["fill_price"],
                                       reduce_usdt, leverage, fill["fee"])
        position_id = self._find_open_position_id(symbol, side)

        event = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "position_id": position_id,
            "symbol": symbol,
            "event_type": "reduce",
            "side": side,
            "order_id": order_id,
            "fill_price": fill["fill_price"],
            "filled_amount": fill.get("filled_amount"),
            "fee": fill["fee"],
            "fee_currency": fill.get("fee_currency", "USDT"),
            "amount_usdt": reduce_usdt,
            "leverage": leverage,
            "realized_pnl": pnl,
            "source": fill["source"],
        }
        self._write_event(event)
        self._update_lifecycle_pnl(event)
        return event

    def record_close(self, order_id: str, symbol: str, side: str,
                     entry_price: float, amount_usdt: float, leverage: int,
                     estimated_price: float,
                     close_type: str = "close") -> dict:
        fill = self._fetch_fill(order_id, symbol, estimated_price)
        pnl = self._calc_realized_pnl(side, entry_price, fill["fill_price"],
                                       amount_usdt, leverage, fill["fee"])
        position_id = self._find_open_position_id(symbol, side)

        event = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "position_id": position_id,
            "symbol": symbol,
            "event_type": close_type,
            "side": side,
            "order_id": order_id,
            "fill_price": fill["fill_price"],
            "filled_amount": fill.get("filled_amount"),
            "fee": fill["fee"],
            "fee_currency": fill.get("fee_currency", "USDT"),
            "amount_usdt": amount_usdt,
            "leverage": leverage,
            "realized_pnl": pnl,
            "source": fill["source"],
        }
        self._write_event(event)
        self._close_lifecycle(event)
        return event

    def record_external_close(self, symbol: str, side: str,
                              entry_price: float, amount_usdt: float,
                              leverage: int,
                              order_info: Optional[dict] = None) -> dict:
        """交易所条件单/外部平仓 — 尝试从最近订单获取真实数据"""
        fill_price = entry_price
        fee = 0.0
        source = "estimated"
        order_id = None

        if order_info:
            fill_price = order_info.get("average") or order_info.get("price") or entry_price
            fee = self._extract_fee(order_info)
            order_id = order_info.get("id")
            source = "okx_order"

        if not order_info:
            fetched = self._fetch_recent_close_order(symbol, side)
            if fetched:
                fill_price = fetched.get("average") or fill_price
                fee = self._extract_fee(fetched)
                order_id = fetched.get("id")
                source = "okx_order"

        pnl = self._calc_realized_pnl(side, entry_price, fill_price,
                                       amount_usdt, leverage, fee)
        position_id = self._find_open_position_id(symbol, side)

        event = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "position_id": position_id,
            "symbol": symbol,
            "event_type": "external_close",
            "side": side,
            "order_id": order_id,
            "fill_price": fill_price,
            "filled_amount": None,
            "fee": fee,
            "fee_currency": "USDT",
            "amount_usdt": amount_usdt,
            "leverage": leverage,
            "realized_pnl": pnl,
            "source": source,
            "reconcile_status": "matched" if source != "estimated" else "pending",
        }
        self._write_event(event)
        self._close_lifecycle(event)
        return event

    def daily_realized_pnl(self, date_str: Optional[str] = None,
                           exclude_paper: bool = True) -> float:
        """计算指定日期的已实现 PnL（默认今天 UTC）"""
        if date_str is None:
            date_str = datetime.datetime.utcnow().strftime('%Y-%m-%d')

        total = 0.0
        for event in self._read_events():
            if event.get("paper"):
                continue
            event_date = datetime.datetime.utcfromtimestamp(
                event.get("ts", 0)).strftime('%Y-%m-%d')
            if event_date == date_str:
                total += event.get("realized_pnl", 0.0)
        return round(total, 4)

    def get_lifecycle(self, symbol: str, side: str = None) -> Optional[dict]:
        """获取指定标的的当前/最近生命周期"""
        for pid, lc in reversed(list(self._lifecycle.items())):
            if lc["symbol"] == symbol:
                if side is None or lc["side"] == side:
                    return lc
        return None

    def get_all_closed_lifecycles(self) -> List[dict]:
        return [lc for lc in self._lifecycle.values() if lc["status"] == "closed"]

    # ── Internal: Fill Fetching ─────────────────────────────────────────────

    def _fetch_fill(self, order_id: str, symbol: str,
                    estimated_price: float) -> dict:
        """从交易所获取真实成交数据，失败时降级为估算"""
        try:
            order = self.exchange.fetch_order(order_id, symbol)
            avg_price = order.get("average")
            if avg_price and float(avg_price) > 0:
                fee_info = order.get("fee") or {}
                fee_cost = float(fee_info.get("cost", 0) or 0)
                return {
                    "fill_price": float(avg_price),
                    "fee": abs(fee_cost),
                    "fee_currency": fee_info.get("currency", "USDT"),
                    "filled_amount": float(order.get("filled", 0) or 0),
                    "source": "okx_fill",
                }
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[Ledger] fetch_order 失败 ({symbol} {order_id}): {e}")

        return {
            "fill_price": estimated_price,
            "fee": 0.0,
            "fee_currency": "USDT",
            "filled_amount": None,
            "source": "estimated",
        }

    def _fetch_recent_close_order(self, symbol: str, side: str) -> Optional[dict]:
        """查询最近的平仓订单（用于外部平仓场景）"""
        try:
            close_side = 'sell' if side == 'long' else 'buy'
            orders = self.exchange.fetch_orders(
                symbol, since=int((time.time() - 300) * 1000), limit=10
            )
            for o in reversed(orders):
                if (o.get("side") == close_side and
                    o.get("reduceOnly", False) and
                    o.get("status") == "closed"):
                    return o
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[Ledger] fetch_recent_close 失败 ({symbol}): {e}")
        return None

    # ── Internal: PnL Calculation ───────────────────────────────────────────

    def _calc_realized_pnl(self, side: str, entry_price: float,
                           exit_price: float, amount_usdt: float,
                           leverage: int, fee: float) -> float:
        """计算真实已实现 PnL（含手续费）"""
        if entry_price <= 0:
            return 0.0
        notional = amount_usdt * leverage
        if side == 'long':
            gross = (exit_price - entry_price) / entry_price * notional
        else:
            gross = (entry_price - exit_price) / entry_price * notional
        net = gross - fee
        return round(net, 4)

    def _extract_fee(self, order_info: dict) -> float:
        fee_info = order_info.get("fee") or {}
        return abs(float(fee_info.get("cost", 0) or 0))

    # ── Internal: Event I/O ─────────────────────────────────────────────────

    def _write_event(self, event: dict) -> None:
        try:
            with open(self.events_path, 'a') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')
        except Exception as e:
            if self.logger:
                self.logger.error(f"[Ledger] 写入事件失败: {e}")

    def _read_events(self) -> List[dict]:
        events = []
        if not os.path.exists(self.events_path):
            return events
        try:
            with open(self.events_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        return events

    def read_events_since(self, since_ts: float) -> List[dict]:
        """读取指定时间戳之后的所有事件"""
        return [ev for ev in self._read_events() if ev.get("ts", 0) >= since_ts]

    # ── Internal: Lifecycle Management ──────────────────────────────────────

    def _open_lifecycle(self, event: dict) -> None:
        pid = event["position_id"]
        self._lifecycle[pid] = {
            "position_id": pid,
            "symbol": event["symbol"],
            "side": event["side"],
            "opened_at": event["ts"],
            "closed_at": None,
            "status": "open",
            "entry_price": event["fill_price"],
            "avg_entry_price": event["fill_price"],
            "total_amount_usdt": event.get("amount_usdt", 0),
            "adds_count": 0,
            "events": ["open"],
            "total_realized_pnl": 0.0,
            "total_fee": event["fee"],
            "source": event["source"],
            "reconcile_status": "matched" if event["source"] != "estimated" else "pending",
        }
        self._save_lifecycle()

    def _update_lifecycle_pnl(self, event: dict) -> None:
        pid = event.get("position_id")
        if pid and pid in self._lifecycle:
            lc = self._lifecycle[pid]
            lc["events"].append(event["event_type"])
            lc["total_realized_pnl"] = round(
                lc["total_realized_pnl"] + event["realized_pnl"], 4)
            lc["total_fee"] = round(lc["total_fee"] + event["fee"], 4)
            if event["event_type"] == "add":
                old_total = lc.get("total_amount_usdt", 0)
                add_amount = event.get("amount_usdt", 0)
                new_total = old_total + add_amount
                if new_total > 0:
                    old_avg = lc.get("avg_entry_price", lc.get("entry_price", 0))
                    lc["avg_entry_price"] = round(
                        (old_avg * old_total + event["fill_price"] * add_amount) / new_total, 8)
                lc["total_amount_usdt"] = round(new_total, 4)
                lc["adds_count"] = lc.get("adds_count", 0) + 1
            if event["source"] == "estimated":
                lc["reconcile_status"] = "pending"
            self._save_lifecycle()

    def _close_lifecycle(self, event: dict) -> None:
        pid = event.get("position_id")
        if pid and pid in self._lifecycle:
            lc = self._lifecycle[pid]
            lc["events"].append(event["event_type"])
            lc["total_realized_pnl"] = round(
                lc["total_realized_pnl"] + event["realized_pnl"], 4)
            lc["total_fee"] = round(lc["total_fee"] + event["fee"], 4)
            lc["closed_at"] = event["ts"]
            lc["status"] = "closed"
            if event["source"] == "estimated":
                lc["reconcile_status"] = "pending"
            elif lc.get("reconcile_status") != "pending":
                lc["reconcile_status"] = "matched"
            self._save_lifecycle()

    def _find_open_position_id(self, symbol: str, side: str) -> Optional[str]:
        """查找当前 open 状态的 position_id"""
        for pid, lc in self._lifecycle.items():
            if lc["symbol"] == symbol and lc["side"] == side and lc["status"] == "open":
                return pid
        return None

    def _load_lifecycle(self) -> dict:
        if not os.path.exists(self.lifecycle_path):
            return {}
        try:
            with open(self.lifecycle_path, 'r') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_lifecycle(self) -> None:
        try:
            from utils.atomic_io import atomic_write_json
            atomic_write_json(self.lifecycle_path, self._lifecycle)
        except Exception as e:
            if self.logger:
                self.logger.error(f"[Ledger] 保存生命周期失败: {e}")
