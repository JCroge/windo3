"""ev-decouple-forward-ab: 复核胜率解耦放行单的前向期望（observability-only write-only）。

对决策磁带 accept 流做 gate-toggle 两臂复盘——baseline=replay(ev_winrate_gate_enabled=False)
（= live 现配置，自检复现 live accept）vs 反事实=replay(ev_winrate_gate_enabled=True)
（= 06-18 前旧胜率门）。旧门翻 reject = "解耦放行"。两桶（解耦放行 vs 双门皆过）统一
resolve_counterfactual+klines TP1 保守口径结算前向净 R，cf_honesty_gate 薄样本拒答。

红线：observability-only write-only —— 输出严禁任何交易决策/风控路径消费；绝不自动改线上 config。
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

GATE_OFF = {"ev_winrate_gate_enabled": False}   # = live 现配置(baseline 自检锚)
GATE_ON = {"ev_winrate_gate_enabled": True}     # = 06-18 前旧胜率门(反事实)


def _is_accept(action):
    return action in ("open_long", "open_short")


def _reject_reason(decision):
    if not isinstance(decision, dict):
        return "hold_other"
    b = ((decision.get("attribution") or {}).get("blocked_by")) or decision.get("reject_reason")
    return str(b).split(":")[0] if b else "hold_other"


async def classify_accepts(records, *, replay_fn=replay_decision):
    """对 accept 记录 gate-toggle 分类。返回 decouple_admitted / both_pass / mismatch。"""
    decouple_admitted = []
    both_pass = []
    mismatch = 0
    reasons = Counter()
    for rec in records:
        baseline = await replay_fn(rec, GATE_OFF)
        if not _is_accept((baseline or {}).get("action")):    # 复盘失真 → 排除
            mismatch += 1
            continue
        cf = await replay_fn(rec, GATE_ON)
        if not _is_accept((cf or {}).get("action")):
            decouple_admitted.append(rec)
            reasons[_reject_reason(cf)] += 1
        else:
            both_pass.append(rec)
    return {"decouple_admitted": decouple_admitted, "both_pass": both_pass,
            "mismatch": mismatch, "admitted_reject_reasons": dict(reasons)}


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


def settle_clusters(clusters, *, load_bars_fn=load_bars, resolve_fn=resolve_counterfactual):
    """每簇代表用 klines+resolve_counterfactual 结算, TP1 保守 R(含亏单)。"""
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


def dedup_clusters(items, gap_sec=3600):
    """同 (symbol,_side) 按 _created 排序, 间隔 > gap_sec 为新簇, 取每簇最早代表。"""
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


def extract_settle_fields(rec):
    """从磁带 accept 记录提取结算所需字段；缺关键字段返回 None。"""
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
            "_sl_dist": sl_dist, "_tp1_dist": tp1_dist, "_plan": plan}


def fuzzy_join_real_pnl(admitted_clusters, lifecycle, window=600):
    """解耦放行簇 symbol+side, opened_at ∈ [created, created+window] 取最近 lifecycle。

    无 request_id → 模糊匹配；pending/external_close 不计入。
    """
    by_sym = defaultdict(list)
    for v in lifecycle.values():
        if isinstance(v, dict) and v.get("reconcile_status") == "matched":
            by_sym[(v.get("symbol"), v.get("side"))].append(v)
    out = []
    for cl in admitted_clusters:
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


def bucket_verdict(settle):
    """诚实门裁定(min_sample=30 不下调)。net_usdt_samples 用 R 序列(口径一致)。"""
    return summarize_bucket(wins=settle["tp"], losses=settle["sl"],
                            net_usdt_samples=settle["r_samples"],
                            min_sample=30, lowconf_sample=100)


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


def _settle_bucket_records(records):
    """记录 → 提取结算字段 → 簇去重 → 结算 + 诚实门。"""
    fields = [f for f in (extract_settle_fields(r) for r in records) if f]
    clusters = dedup_clusters(fields)
    settle = settle_clusters(clusters)
    return clusters, settle, bucket_verdict(settle)


def main():
    accepts = load_tape_accepts()
    cls = asyncio.run(classify_accepts(accepts))
    da, bp = cls["decouple_admitted"], cls["both_pass"]
    print("=== ev-decouple-forward-ab: 胜率解耦放行单前向期望复核 ===")
    print(f"replayable accept: {len(accepts)} | baseline 自检: 忠实 "
          f"{len(da) + len(bp)} / 失真排除 {cls['mismatch']}")
    print(f"解耦放行(旧胜率门会拒): {len(da)} | 双门皆过: {len(bp)} "
          f"| 拒因 {cls['admitted_reject_reasons']}")

    lifecycle = json.load(open(LIFECYCLE)) if os.path.exists(LIFECYCLE) else {}
    for name, recs in [("解耦放行", da), ("双门皆过", bp)]:
        clusters, settle, v = _settle_bucket_records(recs)
        print(f"\n--- {name}桶 ---")
        print(f"  簇去重: {len(clusters)} | 可结算 {settle['resolved']}(无 klines 跳过 {settle['nodata']})")
        print(f"  tp={settle['tp']} sl={settle['sl']} expired={settle['expired']}")
        print(f"  含亏单净 R(TP1 保守): {settle['net_R']:+.2f} over {settle['resolved']} 簇"
              + (f" → {settle['net_R']/settle['resolved']:+.3f} R/簇" if settle['resolved'] else ""))
        print(f"  诚实门裁定: {v['verdict']}  (n={v['n']})")
        if name == "解耦放行":
            joined = fuzzy_join_real_pnl(clusters, lifecycle)
            if joined:
                rp = sum(j["real_pnl"] or 0 for j in joined)
                print(f"  [sanity] 模糊 join 到 {len(joined)} 笔真实开仓, 真实净 PnL {rp:+.2f}U"
                      f"(无 request_id 模糊匹配/pending 不计)")
    print("\n注: 诚实门 min_sample=30 不下调；薄样本裁定 INSUFFICIENT_SAMPLE 时净 R 仅 suggestive 不作结论。")
    print("    判据(解耦放行净R << 双门皆过且<0 → 解耦放行亏损单)仅在两桶诚实门通过时成立。")
    print("    klines 覆盖受限(klines_1s 近 ~数日 ~24 标的)无覆盖簇已跳过并计数。observability-only。")


if __name__ == "__main__":
    main()
