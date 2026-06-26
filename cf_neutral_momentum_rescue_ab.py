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
