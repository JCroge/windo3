"""cf-choppy-neutral-tp1-floor-ab: choppy+neutral 多单卡 TP1 口径地板的反事实 A/B（observability-only write-only）。

对决策磁带 accept 流做 ladder toggle 两臂复盘——baseline=replay(ladder_rr_enabled=True)
（= live 现状，lever2 默认开，自检复现 live accept）vs CF=replay(ladder_rr_enabled=False)
（floor gate 改比 TP1 口径 effective_rr_tp1）。CF 臂因 rr_below_floor 翻 reject = "TP1 地板会拒掉"。
主桶 choppy+neutral，旁路 mixed+neutral。两结算桶统一 resolve_counterfactual+klines TP1
保守口径结算净 R，cf_honesty_gate(min_sample=30) 薄样本拒答。

红线：observability-only write-only —— 输出严禁任何交易决策/风控路径消费；绝不下单/改 config。
"""
import asyncio
import json
import os
import sqlite3
from collections import Counter, defaultdict

from utils.decision_replay import replay_decision
from utils.counterfactual_pnl import resolve_counterfactual
from utils.cf_honesty_gate import summarize_bucket

TAPE = "data/decision_replay_tape.jsonl"
KL1 = "data/klines_1s.db"
KL = "data/klines.db"
LIFECYCLE = "data/live_position_lifecycle.json"

LADDER_ON = {"ladder_rr_enabled": True}    # = live 现状(lever2 默认开)，baseline 自检锚
LADDER_OFF = {"ladder_rr_enabled": False}  # = CF：floor gate 比 TP1 口径

EARLY_WARNING_TEXT = (
    "EARLY_WARNING: choppy+neutral TP1-floor rejected bucket is strongly negative "
    "but still below honesty threshold; do not auto-change live config."
)


def _is_accept(action):
    return action in ("open_long", "open_short")


def load_tape_accepts(path=TAPE):
    accepts = []
    if not os.path.exists(path):
        return accepts
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if (r.get("decision") == "accept" and r.get("replayable")
                and r.get("state_snapshot_before_decision")):
            accepts.append(r)
    return accepts


def scope_filter(records, regime):
    """主桶 regime=choppy / 旁路 regime=mixed；均要求 trend.direction=neutral + 多单。"""
    out = []
    for r in records:
        if r.get("regime_state") != regime:
            continue
        trend = (r.get("tech_analysis") or {}).get("trend") or {}
        if trend.get("direction") != "neutral":
            continue
        side = ((r.get("trade_decision_output") or {}).get("plan") or {}).get("side")
        if side != "long":
            continue
        out.append(r)
    return out


def _reject_reason(decision):
    if not isinstance(decision, dict):
        return "hold_other"
    b = ((decision.get("attribution") or {}).get("blocked_by")) or decision.get("reject_reason")
    return str(b).split(":")[0] if b else "hold_other"


async def classify_accepts(records, *, replay_fn=replay_decision):
    """ladder-toggle 两臂复盘分类。

    baseline=replay(LADDER_ON) 非 accept → baseline_mismatch 排除；
    cf=replay(LADDER_OFF)：accept→survives_tp1_floor；
      reject & reason==rr_below_floor → tp1_floor_rejected；其它 reject → other_flip（不结算）。
    """
    tp1_floor_rejected, survives, other_flip = [], [], []
    mismatch = 0
    reasons = Counter()
    for rec in records:
        baseline = await replay_fn(rec, LADDER_ON)
        if not _is_accept((baseline or {}).get("action")):
            mismatch += 1
            continue
        cf = await replay_fn(rec, LADDER_OFF)
        if _is_accept((cf or {}).get("action")):
            survives.append(rec)
            continue
        reason = _reject_reason(cf)
        if reason == "rr_below_floor":
            tp1_floor_rejected.append(rec)
            reasons[reason] += 1
        else:
            other_flip.append(rec)
            reasons[reason] += 1
    return {"tp1_floor_rejected": tp1_floor_rejected, "survives_tp1_floor": survives,
            "other_flip": other_flip, "mismatch": mismatch,
            "rejected_reasons": dict(reasons)}


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


def extract_settle_fields(rec):
    plan = (rec.get("trade_decision_output") or {}).get("plan") or {}
    side = plan.get("side")
    entry = plan.get("entry_ref")
    sl = plan.get("stop_loss")
    tp = plan.get("take_profit") or []
    if not (side and entry and sl and tp):
        return None
    is_long = (side == "long")
    sl_dist = (entry - sl) / entry if is_long else (sl - entry) / entry
    tp1_dist = (tp[0] - entry) / entry if is_long else (entry - tp[0]) / entry
    if sl_dist <= 0 or tp1_dist <= 0:
        return None
    return {"symbol": rec.get("symbol"), "_side": side, "_created": rec.get("timestamp"),
            "_sl_dist": sl_dist, "_tp1_dist": tp1_dist,
            "_plan": {"side": side, "entry_price": entry, "created_at": rec.get("timestamp"),
                      "stop_loss": sl, "take_profit": tp}}


def dedup_clusters(items, gap_sec=3600):
    by_key = defaultdict(list)
    for x in items:
        by_key[(x["symbol"], x["_side"])].append(x)
    clusters = []
    for key, lst in by_key.items():
        lst.sort(key=lambda z: z["_created"])
        last = None
        for it in lst:
            if last is None or it["_created"] - last > gap_sec:
                clusters.append(it)
            last = it["_created"]
    return clusters


def settle_clusters(clusters, *, load_bars_fn=load_bars, resolve_fn=resolve_counterfactual):
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


def early_warning(settle, verdict):
    if verdict.get("verdict") != "INSUFFICIENT_SAMPLE":
        return False
    resolved = settle.get("resolved") or 0
    if resolved <= 0:
        return False
    return (
        verdict.get("n", 0) >= 15
        and resolved >= 20
        and settle.get("net_R", 0.0) / resolved <= -0.50
        and settle.get("sl", 0) >= 10
        and settle.get("tp", 0) <= 2
    )


def fuzzy_join_real_pnl(clusters, lifecycle, window=600):
    by_sym = defaultdict(list)
    for v in lifecycle.values():
        if isinstance(v, dict) and v.get("reconcile_status") == "matched":
            by_sym[(v.get("symbol"), v.get("side"))].append(v)
    out = []
    for cl in clusters:
        cands = by_sym.get((cl["symbol"], cl["_side"]), [])
        hit = None
        for v in cands:
            op = v.get("opened_at")
            if op is not None and cl["_created"] <= op <= cl["_created"] + window:
                if hit is None or op < hit.get("opened_at"):
                    hit = v
        if hit is not None:
            out.append({"symbol": cl["symbol"], "real_pnl": hit.get("total_realized_pnl"),
                        "fuzzy": True})
    return out


def _settle_bucket_records(records):
    fields = [f for f in (extract_settle_fields(r) for r in records) if f]
    clusters = dedup_clusters(fields)
    settle = settle_clusters(clusters)
    return clusters, settle, bucket_verdict(settle)


def _print_bucket(name, clusters, settle, v):
    print(f"\n--- {name}桶 ---")
    print(f"  簇去重: {len(clusters)} | 可结算 {settle['resolved']}(无 klines 跳过 {settle['nodata']})")
    print(f"  tp={settle['tp']} sl={settle['sl']} expired={settle['expired']}")
    print(f"  含亏单净 R(TP1 保守): {settle['net_R']:+.2f} over {settle['resolved']} 簇"
          + (f" → {settle['net_R']/settle['resolved']:+.3f} R/簇" if settle['resolved'] else ""))
    print(f"  诚实门裁定: {v['verdict']}  (n={v['n']})")


def _run_scope(accepts, regime, label, lifecycle):
    recs = scope_filter(accepts, regime=regime)
    cls = asyncio.run(classify_accepts(recs))
    print(f"\n========== {label}（regime={regime}+neutral 多单）==========")
    print(f"scope accept: {len(recs)} | baseline 自检: 忠实 "
          f"{len(cls['tp1_floor_rejected']) + len(cls['survives_tp1_floor']) + len(cls['other_flip'])}"
          f" / 失真排除 {cls['mismatch']}")
    print(f"TP1 地板拒掉(rr_below_floor): {len(cls['tp1_floor_rejected'])} | "
          f"卡 TP1 仍过: {len(cls['survives_tp1_floor'])} | "
          f"非地板翻转(排除结算): {len(cls['other_flip'])} | 拒因 {cls['rejected_reasons']}")
    for name, recs2 in [("tp1_floor_rejected(避开)", cls["tp1_floor_rejected"]),
                        ("survives_tp1_floor(保留)", cls["survives_tp1_floor"])]:
        clusters, settle, v = _settle_bucket_records(recs2)
        _print_bucket(name, clusters, settle, v)
        if name.startswith("tp1_floor_rejected"):
            if early_warning(settle, v):
                print(f"  {EARLY_WARNING_TEXT}")
            joined = fuzzy_join_real_pnl(clusters, lifecycle)
            if joined:
                rp = sum(j["real_pnl"] or 0 for j in joined)
                print(f"  [sanity] 模糊 join 到 {len(joined)} 笔真实开仓, 真实净 PnL {rp:+.2f}U")


def main():
    accepts = load_tape_accepts()
    lifecycle = json.load(open(LIFECYCLE)) if os.path.exists(LIFECYCLE) else {}
    print("=== cf-choppy-neutral-tp1-floor-ab: choppy+neutral 多单卡 TP1 地板反事实 ===")
    print(f"replayable accept 总数: {len(accepts)}")
    _run_scope(accepts, "choppy", "主桶 choppy+neutral", lifecycle)
    _run_scope(accepts, "mixed", "旁路 mixed+neutral", lifecycle)
    print("\n注: 诚实门 min_sample=30 不下调；薄样本 INSUFFICIENT_SAMPLE 时净 R 仅 suggestive。")
    print("    判据(tp1_floor_rejected 净 R/簇 << 0 且诚实门通过 → 收紧对此原型 +EV)仅两桶诚实门通过时成立。")
    print("    klines 覆盖受限(klines_1s 近 ~数日 ~数十标的)无覆盖簇已跳过并计数。observability-only。")


if __name__ == "__main__":
    main()
