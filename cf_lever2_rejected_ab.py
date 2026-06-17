"""trend-entry-rr-fidelity 杠杆② 忠实 A/B(rejected_signal_events 流)。

observability-only —— 输出严禁任何交易决策路径消费;绝不自动改线上 config。

方法:
1. 取 rejected_signal_events 中 reject_reason=rr_below_floor 的被拒单(杠杆②目标人群)。
2. 从记录的 effective_rr(TP1-only)反推成本率 c(notional 在比值里抵消,无需仓位规模):
     recorded_rr = (tp1_dist - c)/(sl_dist + c)  →  c = (tp1_dist - recorded_rr*sl_dist)/(recorded_rr + 1)
3. 用 c 算 ladder effective_rr = (Σ w_i*dist_i - c)/(sl_dist + c),w=[.5,.25,.25],剩余档封顶 +1R。
4. flip = ladder_rr >= 地板(1.50)。
5. 对 flip 单按 (symbol,side) 趋势簇去重(>1h 间隔=新簇),每簇取最早记录为代表。
6. 用 klines resolve_counterfactual 出 outcome,算**含亏单**净 R 期望(保守 TP1 口径:tp→+tp1_dist/sl_dist, sl→-1, expired→0)。
"""
import json
import os
import re
import sqlite3
from collections import defaultdict

from utils.counterfactual_pnl import resolve_counterfactual

EVENTS = "data/rejected_signal_events.jsonl"
KL1 = "data/klines_1s.db"
KL = "data/klines.db"
W = (0.50, 0.25, 0.25)


def load_bars(db, sym, created, window=86400):
    if not db or not os.path.exists(db):
        return []
    conn = sqlite3.connect(db)
    try:
        lo, hi = int(created * 1000), int((created + window) * 1000)
        rows = conn.execute(
            "SELECT open_time,high,low,close FROM klines WHERE symbol=? "
            "AND open_time>=? AND open_time<=? ORDER BY open_time",
            (sym, lo, hi)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{"open_time": t, "high": h, "low": l, "close": c} for t, h, l, c in rows]


def ladder_rr(tp_dists, sl_dist, c):
    """ladder effective_rr,用反推的成本率 c。notional 抵消。"""
    if not tp_dists or sl_dist <= 0:
        return None
    weights = list(W[:len(tp_dists)])
    wsum = sum(weights)
    weights = [w / wsum for w in weights]
    Wsum = 0.0
    for i, d in enumerate(tp_dists):
        dd = min(d, sl_dist) if i == 2 else d
        Wsum += weights[i] * dd
    denom = sl_dist + c
    return (Wsum - c) / denom if denom > 0 else None


def main():
    # 收集 rr_below_floor 被拒单
    recs = []
    for line in open(EVENTS):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("event_type") != "rejected_plan_created":
            continue
        r = e.get("record") or {}
        m = re.match(r"rr_below_floor:([0-9.]+)<([0-9.]+)", r.get("reject_reason", ""))
        if not m:
            continue
        floor = float(m.group(2))
        side = r.get("side")
        entry = r.get("entry_price")
        sl = r.get("stop_loss")
        tp = r.get("take_profit") or []
        rec_rr = r.get("effective_rr")
        if not (entry and sl and tp and rec_rr and side):
            continue
        is_long = (side == "long")
        sl_dist = (entry - sl) / entry if is_long else (sl - entry) / entry
        tp_dists = [((t - entry) / entry if is_long else (entry - t) / entry) for t in tp]
        if sl_dist <= 0 or tp_dists[0] <= 0:
            continue
        # 反推成本率 c
        c = (tp_dists[0] - rec_rr * sl_dist) / (rec_rr + 1)
        if c < 0:
            c = 0.0
        lrr = ladder_rr(tp_dists, sl_dist, c)
        if lrr is None:
            continue
        recs.append({
            "symbol": r.get("symbol"), "side": side, "created": r.get("created_at"),
            "entry": entry, "sl": sl, "tp": tp, "floor": floor,
            "rec_rr": rec_rr, "ladder_rr": round(lrr, 3),
            "sl_dist": sl_dist, "tp1_dist": tp_dists[0],
            "flip": lrr >= floor, "record": r,
        })

    total = len(recs)
    flipped = [x for x in recs if x["flip"]]
    print(f"=== 杠杆② 忠实 A/B(rejected rr_below_floor)===")
    print(f"被拒单(rr_below_floor): {total}")
    print(f"经 ladder effective_rr 翻转(过地板)的记录: {len(flipped)}  ({len(flipped)/total*100:.1f}%)")

    # 趋势簇去重(symbol,side;>1h 间隔=新簇),取每簇最早记录
    by_key = defaultdict(list)
    for x in flipped:
        by_key[(x["symbol"], x["side"])].append(x)
    clusters = []
    for key, items in by_key.items():
        items.sort(key=lambda z: z["created"])
        last = None
        for it in items:
            if last is None or it["created"] - last > 3600:
                clusters.append(it)  # 簇代表
            last = it["created"]
    print(f"翻转单按趋势簇去重(>1h): {len(clusters)} 簇(来自 {len(by_key)} 个标的/方向)")

    # klines 结算每簇代表(含亏单),保守 TP1 口径净 R
    tp_n = sl_n = exp_n = nodata = 0
    net_R = 0.0
    detail = []
    for cl in clusters:
        bars = load_bars(KL1, cl["symbol"], cl["created"]) or load_bars(KL, cl["symbol"], cl["created"])
        if not bars:
            nodata += 1
            continue
        res = resolve_counterfactual(cl["record"], bars, source="tape")
        r_mult = 0.0
        if res.outcome == "tp":
            tp_n += 1
            r_mult = cl["tp1_dist"] / cl["sl_dist"]  # 保守:TP1 口径 R
        elif res.outcome == "sl":
            sl_n += 1
            r_mult = -1.0
        else:
            exp_n += 1
            r_mult = 0.0
        net_R += r_mult
        detail.append((cl["symbol"], cl["side"], res.outcome, round(r_mult, 2)))

    resolved = tp_n + sl_n + exp_n
    print(f"\n可结算簇: {resolved}(无 klines 覆盖跳过 {nodata})")
    print(f"  tp={tp_n}  sl={sl_n}  expired={exp_n}")
    if tp_n + sl_n:
        print(f"  成交簇胜率(tp/(tp+sl)): {tp_n/(tp_n+sl_n)*100:.1f}%")
    print(f"  含亏单净期望(保守 TP1 口径): {net_R:+.2f} R  over {resolved} 簇 "
          f"→ 人均 {net_R/resolved:+.3f} R/簇" if resolved else "  无可结算簇")
    print("\n  簇明细(symbol, side, outcome, R):")
    for d in sorted(detail, key=lambda z: z[3]):
        print(f"    {d[0]:<14}{d[1]:<6}{d[2]:<9}{d[3]:+.2f}")
    print("\n注:净 R 为保守 TP1 口径(tp→+tp1/sl, sl→-1, expired→0),未计阶梯 TP2/TP3 增益;")
    print("    klines_1s 仅覆盖近 ~3 天 18 标的,更早被拒单无覆盖被跳过(coverage 受限,已如实报)。")


if __name__ == "__main__":
    main()
