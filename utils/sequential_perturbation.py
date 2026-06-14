"""序列扰动 driver（L3b）：时间序重放磁带 + CF 组合状态机 → 一臂结果。
observability-only —— CF 决策绝不进真实 bus；严禁交易决策路径 import 本模块。"""
from utils.decision_replay import replay_decision
from utils.cf_portfolio import CounterfactualPortfolio


def _inject_cf_state(record, cf):
    """把 CF 组合实时状态注入 record 供 L2 replay_decision 还原真实 _make_decision。

    regime 取录制快照（CF 不重算 regime）。EV gate 读的滚动战绩（_recent_wins /
    _total_completed_trades）是【录制时的真实先验 + CF 序列内累计的增量】之和：
    CF 从空组合起步，序列第一笔决策时 CF 自身战绩为 0，必须叠加录制先验，
    否则 EV gate 退回冷启动 40% 先验 → 与真实系统决策发散（baseline 失真）。
    cooldown 同理叠加录制历史。这是先验叠加，非 to_snapshot 缺字段。"""
    recorded_snap = record.get("state_snapshot_before_decision") or {}
    regime = recorded_snap.get("_regime_manager")
    snap = cf.to_snapshot(regime_snapshot=regime)

    base_wins = recorded_snap.get("_recent_wins", 0) or 0
    base_total = recorded_snap.get("_total_completed_trades", 0) or 0
    wins = snap["_recent_wins"] + base_wins
    total = snap["_total_completed_trades"] + base_total
    snap["_recent_wins"] = wins
    snap["_total_completed_trades"] = total
    snap["_recent_win_rate"] = (wins / total) if total else None

    rec_ac = recorded_snap.get("_archetype_cooldown") or {}
    cf_ac = snap.get("_archetype_cooldown") or {"_history": {}, "_cooldown_until": {}}
    merged_hist = dict(rec_ac.get("_history", {}))
    merged_hist.update(cf_ac.get("_history", {}))
    merged_cd = dict(rec_ac.get("_cooldown_until", {}))
    merged_cd.update(cf_ac.get("_cooldown_until", {}))
    snap["_archetype_cooldown"] = {"_history": merged_hist, "_cooldown_until": merged_cd}

    new_rec = dict(record)
    new_rec["state_snapshot_before_decision"] = snap
    new_rec["replayable"] = True
    return new_rec


async def run_arm(records, config, price_loader, *, initial_equity=1000.0, max_slots=3,
                  daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3):
    recs = sorted(records, key=lambda r: r.get("timestamp", 0))
    cf = CounterfactualPortfolio(initial_equity=initial_equity, max_slots=max_slots,
                                 price_loader=price_loader, daily_pnl_hard_stop=daily_pnl_hard_stop,
                                 consecutive_loss_limit=consecutive_loss_limit)
    decisions = []
    cf_open_count = 0
    equity_curve = []
    for rec in recs:
        ts = rec.get("timestamp", 0)
        cf.resolve_due(ts)
        if rec.get("state_snapshot_before_decision"):
            injected = _inject_cf_state(rec, cf)
            decision = await replay_decision(injected, config)
        else:
            decision = None
        action = (decision or {}).get("action", "hold")
        decisions.append({"timestamp": ts, "symbol": rec.get("symbol"), "action": action})
        if decision:
            funding = (rec.get("state_snapshot_before_decision") or {}).get("_funding_rate", 0.0)
            opened = cf.apply_decision(decision, created_at=ts, funding_rate=funding, regime=None)
            if opened:
                cf_open_count += 1
        equity_curve.append(cf.equity)
    cf.resolve_due(float("inf"))
    return {"final_equity": cf.equity, "decisions": decisions, "cf_open_count": cf_open_count,
            "realized": list(cf.realized), "equity_curve": equity_curve + [cf.equity]}


def _max_drawdown(curve):
    peak = curve[0] if curve else 0.0
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    return mdd


def _decision_class(action):
    return "accept" if action in ("open_long", "open_short") else "reject"


_FIDELITY_NOTE = ("退出仅 SL/TP/24h（漏 trailing/partial/risk-close ~10-20%），误差沿序列累积；"
                  "两臂同估算 → 系统性偏差在 delta 抵消，结论以 delta 为主非绝对值。")


async def build_delta_report(records, baseline_config, perturbed_config, price_loader, *,
                             initial_equity=1000.0, max_slots=3, fidelity_threshold=0.8,
                             daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3):
    recs = sorted(records, key=lambda r: r.get("timestamp", 0))
    kw = dict(price_loader=price_loader, initial_equity=initial_equity, max_slots=max_slots,
              daily_pnl_hard_stop=daily_pnl_hard_stop, consecutive_loss_limit=consecutive_loss_limit)
    base = await run_arm(recs, baseline_config, **kw)
    agree = sum(1 for d, r in zip(base["decisions"], recs)
                if _decision_class(d["action"]) == r.get("decision"))
    fidelity = agree / len(recs) if recs else 0.0
    meta = {"perturbed_knobs": dict(perturbed_config or {}), "baseline_fidelity": fidelity,
            "sequence_len": len(recs), "fidelity_note": _FIDELITY_NOTE}
    if fidelity < fidelity_threshold:
        meta["untrustworthy"] = True
        return {"baseline": None, "perturbed": None, "delta": None, "metadata": meta}
    meta["untrustworthy"] = False
    pert = await run_arm(recs, perturbed_config, **kw)
    div = sum(1 for b, p in zip(base["decisions"], pert["decisions"]) if b["action"] != p["action"])
    meta["divergence_ratio"] = div / len(recs) if recs else 0.0
    meta["baseline_cf_open_count"] = base["cf_open_count"]
    meta["perturbed_cf_open_count"] = pert["cf_open_count"]

    def _summ(arm):
        rl = arm["realized"]
        wins = sum(1 for x in rl if x > 0)
        return {"net_pnl": arm["final_equity"] - initial_equity, "trades": len(rl),
                "win_rate": wins / len(rl) if rl else 0.0,
                "max_drawdown": _max_drawdown(arm["equity_curve"])}
    b_s, p_s = _summ(base), _summ(pert)
    delta = {"net_pnl": p_s["net_pnl"] - b_s["net_pnl"], "win_rate": p_s["win_rate"] - b_s["win_rate"],
             "max_drawdown": p_s["max_drawdown"] - b_s["max_drawdown"]}
    return {"baseline": b_s, "perturbed": p_s, "delta": delta, "metadata": meta}
