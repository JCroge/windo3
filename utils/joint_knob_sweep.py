"""多旋钮联合扫描 + 交互效应检验（L4 扩展）：笛卡尔积扫 L3b + 2-way 交互项
量化协同/可加/拮抗 + 多维孤峰守卫方向推荐。
observability-only —— 严禁交易决策路径 import；推荐绝不自动改线上 config。"""
import itertools

from utils.sequential_perturbation import (run_arm, _gate_of_recorded,
                                           _summarize_arm, _FIDELITY_NOTE)


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
    anchor_ok = anchor_delta is not None and abs(anchor_delta["net_pnl"]) <= threshold

    interactions = []
    for c in combos:
        combo = c["combo"]
        non_base = _non_base_axes(combo, base_values)
        if len(non_base) != 2:
            if len(non_base) >= 1:  # 非 anchor、非 edge 的高阶点
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
