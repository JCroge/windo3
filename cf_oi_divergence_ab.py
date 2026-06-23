"""OI 背离 edge 反事实回测（observability-only，严禁决策路径读取）。

动机：边缘筛查(前向收益)发现现策略 MA 趋势块无 edge(t≈0)，唯一冒出正 edge 的
独立信号是 money_flow.oi_price_divergence（OI 价格背离），加 whale 确认更强、~1h 短持。
本驱动用决策磁带的信号 + klines_1s.db 的真实 SL/TP 路径退出 + 真实 CostModel 成本，
严格验证/证伪该 edge：
  - 多条候选规则(OI 单独 / OI+whale / 多空分开) + trend_follow 基线对照
  - SL/TP × max_hold 参数扫描
  - 簇去重(同 symbol+side 在 cluster_sec 内只开一次，模拟单仓)
  - 训练/测试分半(edge 须两半同号才算稳健)
  - cf_honesty_gate 诚实门(Wilson + bootstrap CI + 薄样本拒答)
  - 多重比较显式标注(跑了 N 个变体，名义显著需相应收紧)

仅读 data/decision_replay_tape.jsonl + data/klines_1s.db，不下单、不改 config、不进决策。
用法: python3 cf_oi_divergence_ab.py
"""
import json
import os
import sqlite3
import math
from collections import defaultdict

from utils.counterfactual_pnl import resolve_counterfactual
from utils.cf_honesty_gate import summarize_bucket

TAPE = "data/decision_replay_tape.jsonl"
KLINES_1S = "data/klines_1s.db"


# ── 信号抽取 ──
def _g(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def load_tape():
    recs = []
    if not os.path.exists(TAPE):
        return recs
    for line in open(TAPE):
        try:
            r = json.loads(line)
        except Exception:
            continue
        ss = _g(r, "state_snapshot_before_decision", "_symbol_state", "last_tech") or {}
        if not ss:
            continue
        price = r.get("price_at_decision")
        if not price:
            continue
        recs.append({
            "sym": r.get("symbol"),
            "ts": r.get("timestamp") or 0,
            "price": float(price),
            "oi_div": _g(ss, "money_flow", "oi_price_divergence"),
            "whale": _g(ss, "microstructure", "whale_direction"),
            "rsi_div": _g(ss, "momentum", "rsi_divergence"),
            "rule_long": _g(ss, "rule_signal", "entry_long"),
            "rule_short": _g(ss, "rule_signal", "entry_short"),
            "ma_long": _g(ss, "rule_signal", "ma_aligned_long"),
            "ma_short": _g(ss, "rule_signal", "ma_aligned_short"),
            "trend_dir": _g(ss, "trend", "direction"),
            "strength": _g(ss, "trend", "strength"),
            "regime": _g(r, "state_snapshot_before_decision", "_regime_manager", "effective_regime")
            or r.get("regime_state"),
        })
    return recs


# ── 候选决策规则: record -> +1 long / -1 short / 0 skip ──
def r_oi(r):
    if r["oi_div"] == "bullish":
        return 1
    if r["oi_div"] == "bearish":
        return -1
    return 0


def r_oi_whale(r):
    """OI 背离 + whale 同向确认（whale 中性也放行，反向则否决）"""
    d = r_oi(r)
    if d == 0:
        return 0
    w = 1 if r["whale"] == "accumulating" else -1 if r["whale"] == "distributing" else 0
    if w == -d:  # whale 明确反向 → 否决
        return 0
    return d


def r_oi_whale_strict(r):
    """OI 背离 AND whale 必须同向（最强确认）"""
    d = r_oi(r)
    w = 1 if r["whale"] == "accumulating" else -1 if r["whale"] == "distributing" else 0
    return d if (d != 0 and d == w) else 0


def r_oi_long_only(r):
    return 1 if r_oi(r) == 1 else 0


def r_trend_follow(r):
    """现策略核心 MA 趋势(对照基线)"""
    if r["rule_long"] or r["ma_long"]:
        return 1
    if r["rule_short"] or r["ma_short"]:
        return -1
    if r["trend_dir"] == "bullish" and (r["strength"] or 0) > 70:
        return 1
    if r["trend_dir"] == "bearish" and (r["strength"] or 0) > 70:
        return -1
    return 0


RULES = [
    ("trend_follow(基线对照)", r_trend_follow),
    ("OI背离(单独)", r_oi),
    ("OI背离+whale不反向", r_oi_whale),
    ("OI背离 AND whale同向", r_oi_whale_strict),
    ("OI背离 仅多单", r_oi_long_only),
]


# ── klines 载入(全量进内存，按符号) ──
def load_all_klines():
    K = defaultdict(list)
    if not os.path.exists(KLINES_1S):
        return K
    conn = sqlite3.connect(KLINES_1S)
    for sym, t, h, l, c in conn.execute(
        "SELECT symbol,open_time,high,low,close FROM klines ORDER BY symbol,open_time"
    ):
        K[sym].append({"open_time": t, "high": h, "low": l, "close": c})
    conn.close()
    return K


def bars_after(K, sym, created, window_sec):
    arr = K.get(sym)
    if not arr:
        return []
    lo = int(created * 1000)
    hi = int((created + window_sec) * 1000)
    # 线性过滤(arr 已排序)；数据量小可接受
    return [b for b in arr if lo <= b["open_time"] <= hi]


# ── 单笔结算: 给规则方向构造 plan，固定 SL/TP%，真实路径退出，返回净 R ──
def settle(K, r, direction, sl_pct, tp_pct, max_hold_sec, size_usdt=100.0, lev=1.0):
    entry = r["price"]
    side = "long" if direction == 1 else "short"
    if side == "long":
        sl = entry * (1 - sl_pct)
        tp = entry * (1 + tp_pct)
    else:
        sl = entry * (1 + sl_pct)
        tp = entry * (1 - tp_pct)
    bars = bars_after(K, r["sym"], r["ts"], max_hold_sec + 60)
    if len(bars) < 5:
        return None
    cf_rec = {
        "symbol": r["sym"], "side": side, "entry_price": entry,
        "stop_loss": sl, "take_profit": [tp], "leverage": lev,
        "size_usdt": size_usdt, "funding_rate": 0.0, "created_at": r["ts"],
    }
    res = resolve_counterfactual(cf_rec, bars, max_hold_sec=max_hold_sec)
    if res.net_usdt is None:
        return None
    risk = size_usdt * lev * sl_pct
    return (res.net_usdt / risk) if risk > 0 else None


# ── 簇去重: 同 symbol+side 在 cluster_sec 内只开一次(模拟单仓) ──
def fire_signals(rows, rule_fn, cluster_sec):
    last = {}  # (sym, dir) -> ts
    fired = []
    for r in sorted(rows, key=lambda x: x["ts"]):
        d = rule_fn(r)
        if d == 0:
            continue
        key = (r["sym"], d)
        if key in last and r["ts"] - last[key] < cluster_sec:
            continue
        last[key] = r["ts"]
        fired.append((r, d))
    return fired


def agg(samples):
    if not samples:
        return None
    n = len(samples)
    m = sum(samples) / n
    wins = sum(1 for x in samples if x > 0)
    return {"n": n, "mean_R": m, "total_R": sum(samples), "wr": wins / n, "wins": wins}


def main():
    print("=" * 92)
    print("OI 背离 edge 反事实回测 (真实 SL/TP 路径 + CostModel; observability-only)")
    print("=" * 92)
    rows = load_tape()
    print(f"磁带可用记录: {len(rows)}  |  klines 载入中...")
    K = load_all_klines()
    print(f"klines 符号: {len(K)}")
    if not rows or not K:
        print("→ 数据不足，拒答。")
        return

    mid = sorted(r["ts"] for r in rows)[len(rows) // 2]
    CLUSTER_SEC = 3600  # 同 symbol+side 1h 内只开一次

    # 参数网格
    GRID = [
        (0.010, 0.010, 3600), (0.015, 0.015, 7200), (0.010, 0.020, 7200),
        (0.015, 0.020, 7200), (0.020, 0.020, 14400),
    ]
    variant_count = len(RULES) * len(GRID)
    print(f"\n变体总数 = {len(RULES)} 规则 × {len(GRID)} 参数 = {variant_count}")
    print(f"⚠ 多重比较: 跑 {variant_count} 个变体，名义 95% 显著需收紧到 ~{1 - 0.05/variant_count:.4f}"
          f"(Bonferroni)，单个 actionable 须谨慎当探索性。\n")

    best = []
    for rname, rfn in RULES:
        print(f"\n{'─'*92}\n规则: {rname}")
        print(f"{'SL%/TP%/hold':<16}{'n':>5}{'胜率':>7}{'净R/笔':>9}{'总R':>8}"
              f"{'  诚实门':<16}{'  训练R/笔':>9}{'  测试R/笔':>9}{'  稳健'}")
        for sl_pct, tp_pct, hold in GRID:
            fired = fire_signals(rows, rfn, CLUSTER_SEC)
            all_R, tr_R, te_R = [], [], []
            for r, d in fired:
                v = settle(K, r, d, sl_pct, tp_pct, hold)
                if v is None:
                    continue
                all_R.append(v)
                (tr_R if r["ts"] < mid else te_R).append(v)
            a = agg(all_R)
            if not a:
                continue
            summ = summarize_bucket(wins=a["wins"], losses=a["n"] - a["wins"], net_usdt_samples=all_R)
            verdict = summ["verdict"]
            tr = agg(tr_R); te = agg(te_R)
            tr_m = tr["mean_R"] if tr else float("nan")
            te_m = te["mean_R"] if te else float("nan")
            robust = "✓两半同号" if (tr and te and tr_m > 0 and te_m > 0) else (
                "✗两半异号" if (tr and te and tr_m * te_m < 0) else "·")
            tag = f"{int(sl_pct*1000)/10}/{int(tp_pct*1000)/10}/{hold//3600}h"
            print(f"{tag:<16}{a['n']:>5}{a['wr']*100:>6.1f}%{a['mean_R']:>+8.3f}{a['total_R']:>+8.2f}"
                  f"  {verdict:<14}{tr_m:>+9.3f}{te_m:>+9.3f}  {robust}")
            if verdict != "INSUFFICIENT_SAMPLE" and a["mean_R"] > 0 and robust == "✓两半同号":
                best.append((rname, tag, a, summ, tr_m, te_m))

    print("\n" + "=" * 92)
    print("通过门槛的候选(非薄样本 + 净R为正 + 训练/测试两半同号):")
    if not best:
        print("  无。当前样本下没有任何 OI 变体能同时过诚实门+两半稳健 → 诚实拒答(edge 未确认)。")
    else:
        for rname, tag, a, summ, tr_m, te_m in sorted(best, key=lambda x: -x[2]["mean_R"]):
            print(f"  ✅ {rname} @ {tag}: n={a['n']} 胜率{a['wr']*100:.1f}% 净{a['mean_R']:+.3f}R/笔 "
                  f"总{a['total_R']:+.2f}R | 训练{tr_m:+.3f}/测试{te_m:+.3f} | "
                  f"PnL_CI={summ['net_pnl_ci']} 门={summ['verdict']}")
    print("\n注: net R = net_usdt /(size×lev×sl%); CostModel 真实成本; SL-first 保守同根冲突。")
    print("    observability-only —— 仅量化，不据此自动改 config；上 live 前须走 comet + 影子前向验证。")


if __name__ == "__main__":
    main()
