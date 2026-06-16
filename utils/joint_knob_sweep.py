"""多旋钮联合扫描 + 交互效应检验（L4 扩展）：笛卡尔积扫 L3b + 2-way 交互项
量化协同/可加/拮抗 + 多维孤峰守卫方向推荐。
observability-only —— 严禁交易决策路径 import；推荐绝不自动改线上 config。"""
import itertools

from utils.sequential_perturbation import (run_arm, _gate_of_recorded,
                                           _summarize_arm, _FIDELITY_NOTE)


async def sweep_grid(records, knob_grids, price_loader, *, baseline_config=None,
                     fidelity_threshold=0.8, initial_equity=1000.0, max_slots=3,
                     daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3):
    """多旋钮笛卡尔积扫描。baseline 臂只跑一次复用（fidelity 是 baseline 属性）。
    每个组合作为多 key perturbed_config 跑一个 perturbed 臂。"""
    recs = sorted(records, key=lambda r: r.get("timestamp", 0))
    kw = dict(price_loader=price_loader, initial_equity=initial_equity, max_slots=max_slots,
              daily_pnl_hard_stop=daily_pnl_hard_stop, consecutive_loss_limit=consecutive_loss_limit)
    base_cfg = dict(baseline_config or {})

    base = await run_arm(recs, base_cfg, **kw)
    agree = sum(1 for d, r in zip(base["decisions"], recs)
                if d["gate"] == _gate_of_recorded(r))
    fidelity = agree / len(recs) if recs else 0.0
    out_meta = {"baseline_fidelity": fidelity, "sequence_len": len(recs),
                "fidelity_note": _FIDELITY_NOTE,
                "baseline_cf_open_count": base["cf_open_count"]}
    if fidelity < fidelity_threshold:
        return {"combos": [], "untrustworthy": True, **out_meta}

    base_summary = _summarize_arm(base, initial_equity)
    knob_keys = list(knob_grids.keys())
    combos = []
    for values in itertools.product(*[knob_grids[k] for k in knob_keys]):
        perturbed_config = dict(zip(knob_keys, values))
        pert = await run_arm(recs, perturbed_config, **kw)
        p_summary = _summarize_arm(pert, initial_equity)
        delta = {"net_pnl": p_summary["net_pnl"] - base_summary["net_pnl"],
                 "win_rate": p_summary["win_rate"] - base_summary["win_rate"],
                 "max_drawdown": p_summary["max_drawdown"] - base_summary["max_drawdown"]}
        div = sum(1 for b, p in zip(base["decisions"], pert["decisions"])
                  if b["gate"] != p["gate"])
        combos.append({"combo": perturbed_config, "delta": delta,
                       "divergence_ratio": div / len(recs) if recs else 0.0,
                       "perturbed_cf_open_count": pert["cf_open_count"]})
    return {"combos": combos, "untrustworthy": False,
            "baseline_summary": base_summary, **out_meta}


def _non_base_axes(combo, base_values):
    """combo 中取值偏离 base 的旋钮 key 列表。"""
    return [k for k, v in combo.items() if base_values.get(k) != v]


def _delta_of(grid_result, combo):
    for c in grid_result["combos"]:
        if c["combo"] == combo:
            return c["delta"]
    return None


def compute_interactions(grid_result, base_values, *, actionable_min_pnl=0.0,
                         value_penalty_k=0.1):
    """对每个 2-轴联合点算 interaction = Δ(a,b) − Δ(a,base) − Δ(base,b)，
    判定 synergy/additive/antagonism；(base,base) delta≈0 自检。"""
    combos = grid_result["combos"]
    m = len(combos)
    threshold = actionable_min_pnl * (1 + value_penalty_k * m)

    # 自检锚点：(base,base) 组合
    base_combo = dict(base_values)
    anchor_delta = _delta_of(grid_result, base_combo)
    anchor_tol = max(threshold, 1e-9)
    anchor_ok = anchor_delta is not None and abs(anchor_delta["net_pnl"]) <= anchor_tol

    interactions = []
    for c in combos:
        combo = c["combo"]
        non_base = _non_base_axes(combo, base_values)
        if len(non_base) != 2:
            if len(non_base) == 1:                       # edge: 纯单旋钮效果
                interactions.append({"combo": combo, "interaction": None,
                                     "classification": "edge"})
            elif len(non_base) >= 3:                      # 高阶: 首发不做
                interactions.append({"combo": combo, "interaction": None,
                                     "classification": "skipped:higher_order"})
            continue
        ka, kb = non_base
        d_ab = c["delta"]["net_pnl"]
        edge_a = _delta_of(grid_result, {**base_values, ka: combo[ka]})
        edge_b = _delta_of(grid_result, {**base_values, kb: combo[kb]})
        if edge_a is None or edge_b is None:
            interactions.append({"combo": combo, "interaction": None,
                                 "classification": "skipped:missing_edge"})
            continue
        inter = d_ab - edge_a["net_pnl"] - edge_b["net_pnl"]
        if abs(inter) <= threshold:
            cls = "additive"
        elif inter > 0:
            cls = "synergy"
        else:
            cls = "antagonism"
        interactions.append({"combo": combo, "interaction": inter, "classification": cls,
                             "delta_ab": d_ab, "delta_a": edge_a["net_pnl"],
                             "delta_b": edge_b["net_pnl"]})
    return {"interactions": interactions, "anchor_ok": anchor_ok,
            "effective_threshold": threshold,
            "fidelity_note": grid_result.get("fidelity_note", _FIDELITY_NOTE)}


def _confidence_nd(best, baseline_fidelity, sequence_len):
    fid = baseline_fidelity or 0.0
    div = best.get("divergence_ratio") or 0.0
    n = sequence_len or 0
    div_factor = max(0.0, 1.0 - max(0.0, div - 0.5))
    sample_factor = 1.0 if n >= 100 else (0.6 if n >= 30 else 0.0)
    return round(fid * div_factor * sample_factor, 3)


def _axis_neighbors(combo, knob_grids):
    """网格上沿每个轴 ±1 step 的相邻组合（曼哈顿距离=1）。"""
    out = []
    for k, vals in knob_grids.items():
        if combo[k] not in vals:
            continue
        i = vals.index(combo[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                nb = dict(combo)
                nb[k] = vals[j]
                out.append(nb)
    return out


def recommend_direction_nd(grid_result, base_values, *, knob_grids=None,
                           min_sample=30, actionable_min_pnl=0.0, value_penalty_k=0.1,
                           coherence_frac=0.5):
    """多维轴邻居孤峰守卫 + 门槛随网格点数收紧。证据不足拒答不杜撰。"""
    combos = grid_result["combos"]
    note = grid_result.get("fidelity_note")
    base = {"all_combos": combos, "fidelity_note": note, "tested_count": len(combos)}
    if grid_result.get("untrustworthy"):
        return {**base, "verdict": "no_actionable_direction", "reason": "untrustworthy"}
    seq = grid_result.get("sequence_len", 0)
    trustworthy = [c for c in combos
                   if seq >= min_sample and c.get("delta") is not None]
    if not trustworthy:
        return {**base, "verdict": "no_actionable_direction", "reason": "no_trustworthy_combos"}

    m = len(combos)
    effective_min = actionable_min_pnl * (1 + value_penalty_k * m)
    ranked = sorted(trustworthy, key=lambda c: c["delta"]["net_pnl"], reverse=True)
    best = ranked[0]
    if best["delta"]["net_pnl"] <= effective_min:
        return {**base, "verdict": "no_actionable_direction", "reason": "below_threshold",
                "effective_min_pnl": effective_min}

    # 推导 knob_grids（每轴取值集合，保序）若未显式传
    if knob_grids is None:
        knob_grids = {}
        for c in combos:
            for k, v in c["combo"].items():
                knob_grids.setdefault(k, [])
                if v not in knob_grids[k]:
                    knob_grids[k].append(v)
        for k in knob_grids:
            knob_grids[k].sort()

    bp = best["delta"]["net_pnl"]
    neighbor_combos = _axis_neighbors(best["combo"], knob_grids)
    nb_deltas = [c["delta"]["net_pnl"] for c in trustworthy
                 if c["combo"] in neighbor_combos]
    coherent = any(d >= bp * coherence_frac for d in nb_deltas) if nb_deltas else False
    if not coherent:
        return {**base, "verdict": "no_actionable_direction", "reason": "isolated_spike",
                "isolated_spike": True}
    return {**base, "verdict": "recommend", "recommended_combo": best["combo"],
            "delta_net_pnl": bp,
            "confidence": _confidence_nd(best, grid_result.get("baseline_fidelity"), seq),
            "baseline_fidelity": grid_result.get("baseline_fidelity"),
            "divergence_ratio": best.get("divergence_ratio"), "sample": seq}
