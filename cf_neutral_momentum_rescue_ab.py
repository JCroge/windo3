"""cf-neutral-momentum-rescue-ab: path_evidence 阀门双重失效测量(observability-only write-only)。

体制空仓硬门 _classify_regime_flat_gate 的 path_evidence 救援阀门因 sym_dir=='bullish' +
strength>=60(neutral 结构封顶 ~50,是 bullish 的隐式代理)双重失效,从未触发。本驱动以
**信号口径**测量:对决策磁带 population(regime∈{choppy,mixed} & direction=='neutral')按
方向无关谓词(daily/htf bias 看多 + 12h 真实在涨 + 不在区间顶部,不读 strength)分 A(命中)/
B(对照)两桶,合成标准化退出(entry=决策价, sl/tp=策略典型几何),经 resolve_counterfactual+
klines_1s TP1 保守结算净 R,cf_honesty_gate(min_sample=30)分桶裁定。A 显著正 & B 不显著正 →
谓词有判别力、阀门值得放宽;A≈B 或皆负 → 救援无 edge。不实例化 Judge、不 replay、不下单/改 config。

红线:observability-only write-only —— 严禁任何交易决策/风控路径 import。
"""
import json
import os
import sqlite3
import statistics
from collections import defaultdict

from utils.counterfactual_pnl import resolve_counterfactual
from utils.cf_honesty_gate import summarize_bucket

TAPE = "data/decision_replay_tape.jsonl"
KL1 = "data/klines_1s.db"
KL = "data/klines.db"

PRE12H_GRID = [0.02, 0.03, 0.05]
RANGEPOS_GRID = [0.85, 0.92]


def load_population(path=TAPE):
    out = []
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not r.get("replayable"):
            continue
        if r.get("regime_state") not in ("choppy", "mixed"):
            continue
        trend = (r.get("tech_analysis") or {}).get("trend") or {}
        if trend.get("direction") != "neutral":
            continue
        out.append(r)
    return out


def rescue_predicate(rec, pre12h_min, range_pos_max):
    tech = rec.get("tech_analysis") or {}
    trend = tech.get("trend") or {}
    ectx = tech.get("entry_context") or {}
    bias_up = (trend.get("daily_bias") == "bullish"
               or trend.get("higher_tf_bias") == "bullish")
    pre12h = ectx.get("pre_12h_return_pct")
    range_pos = ectx.get("position_in_24h_range")
    if pre12h is None or range_pos is None:
        return False
    return bool(bias_up and pre12h >= pre12h_min and range_pos <= range_pos_max)


def derive_strategy_geometry(path=TAPE):
    """从磁带 choppy-long accept 的真实 plan 取 median sl_dist/tp1_dist。"""
    sl_ds, tp_ds = [], []
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("decision") != "accept" or r.get("regime_state") != "choppy":
                continue
            plan = (r.get("trade_decision_output") or {}).get("plan") or {}
            if plan.get("side") != "long":
                continue
            entry = plan.get("entry_ref")
            sl = plan.get("stop_loss")
            tp = plan.get("take_profit") or []
            if not (entry and sl and tp):
                continue
            sl_dist = (entry - sl) / entry
            tp1_dist = (tp[0] - entry) / entry
            if sl_dist > 0 and tp1_dist > 0:
                sl_ds.append(sl_dist)
                tp_ds.append(tp1_dist)
    if sl_ds and tp_ds:
        return statistics.median(sl_ds), statistics.median(tp_ds)
    return 0.015, 0.0225  # 回退 R:R≈1.5


def synthesize_settle_fields(rec, sl_dist, tp1_dist):
    if sl_dist <= 0 or tp1_dist <= 0:
        return None
    entry = rec.get("price_at_decision")
    if not entry:
        return None
    entry = float(entry)
    created = rec.get("timestamp")
    sl = entry * (1 - sl_dist)
    tp1 = entry * (1 + tp1_dist)
    return {"symbol": rec.get("symbol"), "_side": "long", "_created": created,
            "_sl_dist": sl_dist, "_tp1_dist": tp1_dist,
            "_plan": {"side": "long", "entry_price": entry, "created_at": created,
                      "stop_loss": sl, "take_profit": [tp1]}}


def load_bars(db, sym, created, window=86400):
    if not db or not os.path.exists(db) or created is None:
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


def dedup_clusters(items, gap_sec=3600):
    by_key = defaultdict(list)
    for x in items:
        by_key[(x["symbol"], x["_side"])].append(x)
    clusters = []
    for _key, lst in by_key.items():
        lst.sort(key=lambda z: z["_created"])
        last = None
        for it in lst:
            if last is None or it["_created"] - last > gap_sec:
                clusters.append(it)
            last = it["_created"]
    return clusters


def settle_clusters(clusters, *, load_bars_fn=None, resolve_fn=resolve_counterfactual):
    load_bars_fn = load_bars_fn or load_bars
    tp = sl = exp = nodata = 0
    net_R = 0.0
    r_samples = []
    for cl in clusters:
        bars = load_bars_fn(KL1, cl["symbol"], cl.get("_created")) or \
            load_bars_fn(KL, cl["symbol"], cl.get("_created"))
        if not bars:
            nodata += 1
            continue
        res = resolve_fn(cl["_plan"], bars, source="tape")
        if res.outcome == "tp":
            tp += 1
            r = cl["_tp1_dist"] / cl["_sl_dist"]
        elif res.outcome == "sl":
            sl += 1
            r = -1.0
        else:
            exp += 1
            r = 0.0
        net_R += r
        r_samples.append(r)
    return {"tp": tp, "sl": sl, "expired": exp, "nodata": nodata,
            "resolved": tp + sl + exp, "net_R": net_R, "r_samples": r_samples}


def bucket_verdict(settle):
    return summarize_bucket(wins=settle["tp"], losses=settle["sl"],
                            net_usdt_samples=settle["r_samples"],
                            min_sample=30, lowconf_sample=100)


def settle_records(records, sl_dist, tp1_dist):
    fields = [f for f in (synthesize_settle_fields(r, sl_dist, tp1_dist)
                          for r in records) if f]
    clusters = dedup_clusters(fields)
    settle = settle_clusters(clusters)
    return clusters, settle, bucket_verdict(settle)


def _print_bucket(name, clusters, settle, v):
    rr = (f" → {settle['net_R']/settle['resolved']:+.3f} R/簇"
          if settle["resolved"] else "")
    print(f"    [{name}] 簇 {len(clusters)} | 可结算 {settle['resolved']}"
          f"(无 klines 跳过 {settle['nodata']}) | tp={settle['tp']} sl={settle['sl']} "
          f"exp={settle['expired']} | 净R {settle['net_R']:+.2f}{rr} | 诚实门 {v['verdict']}(n={v['n']})")


def _run_grid(population, sl_dist, tp1_dist, exit_label):
    print(f"\n========== 退出假设: {exit_label} "
          f"(sl_dist={sl_dist:.4f} tp1_dist={tp1_dist:.4f}, R:R={tp1_dist/sl_dist:.2f}) ==========")
    for pre12h_min in PRE12H_GRID:
        for range_pos_max in RANGEPOS_GRID:
            a = [r for r in population if rescue_predicate(r, pre12h_min, range_pos_max)]
            b = [r for r in population if not rescue_predicate(r, pre12h_min, range_pos_max)]
            print(f"\n  --- 谓词 pre12h≥{pre12h_min} range_pos≤{range_pos_max} "
                  f"| A命中 {len(a)} / B对照 {len(b)} ---")
            ca, sa, va = settle_records(a, sl_dist, tp1_dist)
            cb, sb, vb = settle_records(b, sl_dist, tp1_dist)
            _print_bucket("A 救援候选", ca, sa, va)
            _print_bucket("B 对照", cb, sb, vb)


def main():
    population = load_population()
    n_choppy = sum(1 for r in population if r.get("regime_state") == "choppy")
    n_mixed = sum(1 for r in population if r.get("regime_state") == "mixed")
    med_sl, med_tp = derive_strategy_geometry()
    print("=== cf-neutral-momentum-rescue-ab: path_evidence 阀门双重失效测量(信号口径)===")
    print(f"population(choppy/mixed + neutral 方向): {len(population)} "
          f"(choppy {n_choppy} / mixed {n_mixed})")
    print(f"策略典型几何(choppy-long accept median): sl_dist={med_sl:.4f} tp1_dist={med_tp:.4f}")
    exit_assumptions = [
        (med_sl, med_tp, "策略中位"),
        (0.015, 0.0225, "固定 R:R=1.5"),
        (0.010, 0.0150, "更紧 SL"),
    ]
    for sl_dist, tp1_dist, label in exit_assumptions:
        _run_grid(population, sl_dist, tp1_dist, label)
    print("\n注: 诚实门 min_sample=30 不下调;n<30 INSUFFICIENT 时净 R 仅 suggestive。")
    print("    判据: A 净R/簇 显著>0 且 B 不显著>0 且 A 诚实门通过 → 谓词有判别力、阀门值得放宽(另起 change)。")
    print("    A≈B 或皆 ≤0 或 A INSUFFICIENT → 救援无 edge,结案。")
    print("    klines 覆盖受限(klines_1s 近 ~数日 ~数十标的)无覆盖簇已跳过并计数。observability-only。")


if __name__ == "__main__":
    main()
