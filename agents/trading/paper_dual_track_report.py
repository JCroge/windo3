"""Paper dual-track comparison: realistic vs idealized gap.

Pure functions over paper_trades.jsonl records. No agent, no bus, paper-only.
Never feeds live Reviewer metrics.
"""
import json
import time
from typing import Optional

PAPER_TRADES_FILE = "data/paper_trades.jsonl"


def _book_of(rec: dict) -> str:
    return rec.get("book", "realistic")  # legacy default


def _metrics(trades: list) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "win_pct": 0.0, "avg_net_pnl": 0.0,
                "total_net_pnl": 0.0, "max_drawdown": 0.0}
    pnls = [float(t.get("net_pnl", 0.0)) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in sorted(trades, key=lambda t: t.get("closed_at", 0.0)):
        cum += float(p.get("net_pnl", 0.0))
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return {
        "n": n,
        "win_pct": round(100.0 * wins / n, 2),
        "avg_net_pnl": round(total / n, 4),
        "total_net_pnl": round(total, 4),
        "max_drawdown": round(max_dd, 4),
    }


def compute_gap(trades: list, window_days: Optional[float] = None,
                min_trades: int = 10) -> dict:
    if window_days is not None:
        cutoff = time.time() - window_days * 86400
        trades = [t for t in trades if float(t.get("closed_at", 0.0)) >= cutoff]
    realistic = [t for t in trades if _book_of(t) == "realistic"]
    idealized = [t for t in trades if _book_of(t) == "idealized"]
    rm = _metrics(realistic)
    im = _metrics(idealized)
    return {
        "realistic": rm,
        "idealized": im,
        "limit_discipline_value": round(rm["total_net_pnl"] - im["total_net_pnl"], 4),
        "low_sample": rm["n"] < min_trades or im["n"] < min_trades,
        "window_days": window_days,
    }


def load_trades(path: str = PAPER_TRADES_FILE) -> list:
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        return []
    return rows


def format_gap(gap: dict) -> str:
    """Human-readable summary for logs / Telegram."""
    r, i = gap["realistic"], gap["idealized"]
    ldv = gap["limit_discipline_value"]
    verdict = "限价纪律净赚" if ldv > 0 else ("限价纪律净亏" if ldv < 0 else "持平")
    lines = [
        "📊 Paper 双轨对比 (realistic vs idealized)",
        f"realistic: n={r['n']} 胜率{r['win_pct']}% 总PnL{r['total_net_pnl']:+} 回撤{r['max_drawdown']}",
        f"idealized: n={i['n']} 胜率{i['win_pct']}% 总PnL{i['total_net_pnl']:+} 回撤{i['max_drawdown']}",
        f"limit_discipline_value = {ldv:+} ({verdict})",
    ]
    if gap["low_sample"]:
        lines.append("⚠️ 样本不足，误差大，仅供参考")
    return "\n".join(lines)
