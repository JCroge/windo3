"""序列扰动 driver（L3b）：时间序重放磁带 + CF 组合状态机 → 一臂结果。
observability-only —— CF 决策绝不进真实 bus；严禁交易决策路径 import 本模块。"""
from utils.decision_replay import replay_decision
from utils.cf_portfolio import CounterfactualPortfolio


def _inject_cf_state(record, cf):
    """把 CF 组合【实时累计】状态注入 record 供 L2 replay_decision 还原真实 _make_decision。

    regime 取录制快照（市场状态固定，CF 不重算 regime）。EV gate / cooldown 读的
    战绩直接用 CF 自身累计（cf.to_snapshot）——CF 在序列起点已被 _seed_cf_prior 灌入
    录制初始先验，之后各臂用【自己估算的 CF 结果】累计。两臂从同一先验各自级联，
    故 baseline_fidelity 真实反映 CF-sim 跟不跟得住现实，perturbed 的级联不被掩盖。
    （绝不 per-record 注入 reality 当时的演化计数——那会人为抬高 fidelity 并掩盖级联。）"""
    recorded_snap = record.get("state_snapshot_before_decision") or {}
    snap = cf.to_snapshot(regime_snapshot=recorded_snap.get("_regime_manager"))
    # 保留录制的 per-symbol 决策输入上下文(trend_streak/last_tech 等市场状态——CF 无法重建)，
    # 镜像上面的 _regime_manager 透传；空 {} 会让 Judge 信号强度路径误判"信号不足"→ hold。
    # 还原的是市场决策输入(非 reality 的 EV/胜率战绩累计)，不触 L3b 反模式。
    snap["_symbol_state"] = recorded_snap.get("_symbol_state") or {}
    new_rec = dict(record)
    new_rec["state_snapshot_before_decision"] = snap
    new_rec["replayable"] = True
    return new_rec


def _seed_cf_prior(cf, first_record):
    """序列起点用第一条 record 录制的滚动战绩 + cooldown 作为 CF 的【固定初始先验】
    （= 磁带窗口之前的真实战绩），之后各臂用自己累计的 CF 结果叠加。
    EV gate 起步不退冷启动 40% 先验，且两臂共享同一先验使 delta 干净。"""
    snap = (first_record or {}).get("state_snapshot_before_decision") or {}
    cf._recent_wins = snap.get("_recent_wins", 0) or 0
    cf._total_completed_trades = snap.get("_total_completed_trades", 0) or 0
    # 用录制滚动胜率暖启动 CF 窗口(= 磁带窗口前真实滚动率), 破 EV gate 冷启动死锁;
    # CF 自身结算结果之后 FIFO 逐步挤出合成种子。
    rate = snap.get("_recent_win_rate")
    cf._cf_win_window.clear()
    if rate is not None:
        n = cf.rolling_window_size
        wins = round(float(rate) * n)
        cf._cf_win_window.extend([True] * wins + [False] * (n - wins))
    ac = snap.get("_archetype_cooldown") or {}
    # 原地更新（保留 ArchetypeCooldown._history 的 defaultdict(list) 类型，
    # 否则 record_result 的 self._history[k].append 会 KeyError）
    cf._cf_cooldown._history.clear()
    cf._cf_cooldown._history.update(ac.get("_history", {}))
    cf._cf_cooldown._cooldown_until.clear()
    cf._cf_cooldown._cooldown_until.update(ac.get("_cooldown_until", {}))


async def run_arm(records, config, price_loader, *, initial_equity=1000.0, max_slots=3,
                  daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3):
    recs = sorted(records, key=lambda r: r.get("timestamp", 0))
    cf = CounterfactualPortfolio(initial_equity=initial_equity, max_slots=max_slots,
                                 price_loader=price_loader, daily_pnl_hard_stop=daily_pnl_hard_stop,
                                 consecutive_loss_limit=consecutive_loss_limit)
    if recs:
        _seed_cf_prior(cf, recs[0])  # 固定初始先验（磁带窗口前真实战绩），之后 CF 自累计
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
        decisions.append({"timestamp": ts, "symbol": rec.get("symbol"),
                          "action": action, "gate": _gate_of_replayed(decision)})
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


def _summarize_arm(arm, initial_equity):
    """单臂 PnL summary（从 build_delta_report 的 _summ 提取，供联合扫描复用）。"""
    rl = arm["realized"]
    wins = sum(1 for x in rl if x > 0)
    return {"net_pnl": arm["final_equity"] - initial_equity, "trades": len(rl),
            "win_rate": wins / len(rl) if rl else 0.0,
            "max_drawdown": _max_drawdown(arm["equity_curve"])}


def _decision_class(action):
    return "accept" if action in ("open_long", "open_short") else "reject"


def _gate_of_replayed(decision):
    """回放决策触达的 gate: 开仓=accept; 否则取 attribution.blocked_by 冒号前前缀。"""
    action = (decision or {}).get("action")
    if action in ("open_long", "open_short"):
        return "accept"
    blocked = ((decision or {}).get("attribution") or {}).get("blocked_by")
    if blocked:
        return str(blocked).split(":")[0]
    return "hold_other"


def _gate_of_recorded(record):
    """录制决策触达的 gate: accept; 否则取 reject_reason 冒号前前缀。"""
    if (record or {}).get("decision") == "accept":
        return "accept"
    rr = ((record or {}).get("trade_decision_output") or {}).get("reject_reason")
    if rr:
        return str(rr).split(":")[0]
    return "hold_other"


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
                if d["gate"] == _gate_of_recorded(r))
    fidelity = agree / len(recs) if recs else 0.0
    meta = {"perturbed_knobs": dict(perturbed_config or {}), "baseline_fidelity": fidelity,
            "sequence_len": len(recs), "fidelity_note": _FIDELITY_NOTE}
    if fidelity < fidelity_threshold:
        meta["untrustworthy"] = True
        return {"baseline": None, "perturbed": None, "delta": None, "metadata": meta}
    meta["untrustworthy"] = False
    pert = await run_arm(recs, perturbed_config, **kw)
    div = sum(1 for b, p in zip(base["decisions"], pert["decisions"]) if b["gate"] != p["gate"])
    meta["divergence_ratio"] = div / len(recs) if recs else 0.0
    meta["baseline_cf_open_count"] = base["cf_open_count"]
    meta["perturbed_cf_open_count"] = pert["cf_open_count"]

    b_s, p_s = _summarize_arm(base, initial_equity), _summarize_arm(pert, initial_equity)
    delta = {"net_pnl": p_s["net_pnl"] - b_s["net_pnl"], "win_rate": p_s["win_rate"] - b_s["win_rate"],
             "max_drawdown": p_s["max_drawdown"] - b_s["max_drawdown"]}
    return {"baseline": b_s, "perturbed": p_s, "delta": delta, "metadata": meta}
