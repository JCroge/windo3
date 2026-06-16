"""反事实组合状态机（L3b）：维护扰动后的 CF 持仓/slot/资金/EV/cooldown/daily-stop，
独立于真实系统。CF 开仓用 L1 resolve_counterfactual 估算退出 + 反馈。
observability-only —— 严禁交易决策路径 import/调用本模块。"""
from collections import defaultdict, deque
from utils.counterfactual_pnl import resolve_counterfactual
from utils.archetype_cooldown import ArchetypeCooldown


def _utc_day(ts):
    return int(ts // 86400)


class CounterfactualPortfolio:
    def __init__(self, initial_equity=1000.0, max_slots=3, price_loader=None,
                 daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3, window_sec=86400,
                 rolling_window_size=20):
        self.equity = float(initial_equity)
        self.max_slots = max_slots
        self.price_loader = price_loader
        self.daily_pnl_hard_stop = daily_pnl_hard_stop
        self.consecutive_loss_limit = consecutive_loss_limit
        self.window_sec = window_sec
        self._open = {}
        self._recent_wins = 0
        self._total_completed_trades = 0
        self._cf_cooldown = ArchetypeCooldown(enabled=True, logger=None)
        self._daily_pnl = defaultdict(float)
        self._consec_losses = 0
        self._halted_days = set()
        self.realized = []
        self.rolling_window_size = rolling_window_size
        # CF 自身已结算结果的滚动窗口(win=True/loss=False), 与 live Reviewer 同语义。
        # 只吃 CF 自己的结算; 序列起点的暖启动播种由 _seed_cf_prior 负责(后续任务接入)。
        self._cf_win_window = deque(maxlen=rolling_window_size)

    def open_symbols(self):
        return set(self._open.keys())

    def slot_count(self):
        return len(self._open)

    def _day_halted(self, ts):
        return _utc_day(ts) in self._halted_days

    def apply_decision(self, decision, created_at, funding_rate=0.0, regime=None):
        action = (decision or {}).get("action")
        if action not in ("open_long", "open_short"):
            return False
        symbol = decision.get("symbol")
        if symbol is None or symbol in self._open:
            return False
        if self.slot_count() >= self.max_slots or self._day_halted(created_at):
            return False
        plan = decision.get("plan") or {}
        side = "long" if action == "open_long" else "short"
        rec = {"symbol": symbol, "side": side, "entry_price": plan.get("entry_ref"),
               "stop_loss": plan.get("stop_loss"), "take_profit": plan.get("take_profit") or [],
               "leverage": plan.get("leverage", 1), "size_usdt": plan.get("size_usdt"),
               "created_at": created_at, "funding_rate": funding_rate}
        bars = self.price_loader(symbol, created_at, self.window_sec) if self.price_loader else []
        r = resolve_counterfactual(rec, bars, max_hold_sec=self.window_sec)
        archetype = self._cf_cooldown.classify(decision.get("attribution") or {})
        self._open[symbol] = {"resolved_ts": created_at + r.hold_hours * 3600.0,
                              "net_usdt": r.net_usdt if r.net_usdt is not None else 0.0,
                              "archetype": archetype, "created_at": created_at}
        return True

    def resolve_due(self, now):
        due = sorted([(p["resolved_ts"], s) for s, p in self._open.items()
                      if p["resolved_ts"] <= now])
        for _, symbol in due:
            p = self._open.pop(symbol)
            net = p["net_usdt"]
            self.equity += net
            self.realized.append(net)
            self._total_completed_trades += 1
            self._cf_win_window.append(net > 0)
            if net > 0:
                self._recent_wins += 1
                self._consec_losses = 0
            else:
                self._consec_losses += 1
            self._cf_cooldown.record_result(p["archetype"], net)
            day = _utc_day(p["resolved_ts"])
            self._daily_pnl[day] += net
            if (self._daily_pnl[day] <= self.daily_pnl_hard_stop
                    or self._consec_losses >= self.consecutive_loss_limit):
                self._halted_days.add(day)

    def to_snapshot(self, regime_snapshot=None):
        return {
            "_open_positions": list(self._open.keys()),
            "_pending_open_symbols": [], "_pending_open_ts": {},
            "_position_slots": {s: "main" for s in self._open},
            "_pending_open_slots": {},
            "_archetype_cooldown": {"_history": dict(self._cf_cooldown._history),
                                    "_cooldown_until": dict(self._cf_cooldown._cooldown_until)},
            "_recent_wins": self._recent_wins,
            "_total_completed_trades": self._total_completed_trades,
            "_recent_win_rate": (sum(self._cf_win_window) / len(self._cf_win_window)
                                 if self._cf_win_window else None),
            "_probe_short_active": None, "_probe_short_sl_count": 0,
            "_probe_short_cooldown_until": 0.0,
            "_symbol_state": {}, "_available_balance": self.equity,
            "_regime_manager": regime_snapshot or {"effective_regime": "mixed", "confidence": 50}}
