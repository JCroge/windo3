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
