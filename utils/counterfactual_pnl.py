"""被拒单反事实净 PnL：CostModel 真实成本 + K 线 SL/TP 触发判定 +
同根 SL-first 保守 + 偏差带 + 资金费近似标注。
observability-only：严禁交易决策读取。"""
from dataclasses import dataclass
from typing import Optional, List
from utils.cost_model import get_default_cost_model


@dataclass
class CfResult:
    outcome: str            # "tp" | "sl" | "expired"
    exit_price: float
    gross_return_pct: float
    net_usdt: Optional[float]
    net_return_pct: float
    price_ambiguous: bool
    funding_approx: bool
    hold_hours: float
    source: str             # "attribution_reconstructed" | "tape_exact"


def resolve_counterfactual(record: dict, bars: List[dict], *, max_hold_sec: int = 86400,
                           source: str = "attribution_reconstructed",
                           cost_model=None) -> CfResult:
    cm = cost_model or get_default_cost_model()
    side = record["side"]
    entry = float(record["entry_price"])
    sl = float(record.get("stop_loss") or 0)
    tp_list = record.get("take_profit") or []
    tp = float(tp_list[0]) if tp_list else 0
    created = float(record.get("created_at", 0))

    outcome, exit_price, ambiguous, resolved_t = "expired", entry, False, created
    for bar in bars:
        if (bar["open_time"] / 1000.0) - created > max_hold_sec:
            break
        hi, lo = float(bar["high"]), float(bar["low"])
        hit_sl = sl and (lo <= sl if side == "long" else hi >= sl)
        hit_tp = tp and (hi >= tp if side == "long" else lo <= tp)
        if hit_sl and hit_tp:                 # 同根冲突 → SL-first 保守
            outcome, exit_price, ambiguous = "sl", sl, True
            resolved_t = bar["open_time"] / 1000.0
            break
        if hit_sl:
            outcome, exit_price = "sl", sl
            resolved_t = bar["open_time"] / 1000.0
            break
        if hit_tp:
            outcome, exit_price = "tp", tp
            resolved_t = bar["open_time"] / 1000.0
            break
        exit_price = float(bar["close"])      # 过期 mark-to-market
        resolved_t = bar["open_time"] / 1000.0

    if side == "long":
        gross_pct = (exit_price - entry) / entry if entry else 0.0
    else:
        gross_pct = (entry - exit_price) / entry if entry else 0.0

    leverage = float(record.get("leverage") or 1)
    size_usdt = record.get("size_usdt")
    funding_rate = float(record.get("funding_rate") or 0.0)
    hold_hours = max(0.0, (resolved_t - created) / 3600.0)

    net_usdt = None
    if size_usdt is not None:
        notional = float(size_usdt) * leverage
        gross_usdt = notional * gross_pct
        cost = cm.round_trip_cost(notional=notional, funding_rate=funding_rate,
                                  hold_hours=hold_hours, side=side)
        net_usdt = gross_usdt - cost["total_cost"]
        net_return_pct = net_usdt / float(size_usdt) if size_usdt else gross_pct
    else:
        net_return_pct = gross_pct

    return CfResult(outcome=outcome, exit_price=exit_price,
                    gross_return_pct=gross_pct * 100,
                    net_usdt=net_usdt, net_return_pct=net_return_pct * 100,
                    price_ambiguous=ambiguous, funding_approx=(funding_rate != 0.0),
                    hold_hours=hold_hours, source=source)
