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
