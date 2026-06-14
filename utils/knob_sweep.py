"""旋钮扫描 + 方向推荐（L4，反事实策略实验室收官）：单旋钮 grid 扫描 L3b
build_delta_report + 诚实门控 + 多重比较守卫 → 方向推荐或拒答。
observability-only —— 严禁交易决策路径 import；推荐绝不自动改线上 config。"""
from utils.sequential_perturbation import build_delta_report


async def sweep_knob(records, knob, values, price_loader, *, baseline_config=None,
                     fidelity_threshold=0.8, initial_equity=1000.0, max_slots=3,
                     daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3):
    base_cfg = dict(baseline_config or {})
    out = []
    for v in values:
        rep = await build_delta_report(
            records, base_cfg, {knob: v}, price_loader,
            initial_equity=initial_equity, max_slots=max_slots,
            fidelity_threshold=fidelity_threshold,
            daily_pnl_hard_stop=daily_pnl_hard_stop,
            consecutive_loss_limit=consecutive_loss_limit)
        meta = rep.get("metadata", {})
        out.append({"value": v, "delta": rep.get("delta"),
                    "baseline_fidelity": meta.get("baseline_fidelity"),
                    "untrustworthy": meta.get("untrustworthy", False),
                    "divergence_ratio": meta.get("divergence_ratio"),
                    "sequence_len": meta.get("sequence_len", 0),
                    "fidelity_note": meta.get("fidelity_note")})
    return out


def _confidence(best):
    fid = best.get("baseline_fidelity") or 0.0
    div = best.get("divergence_ratio") or 0.0
    n = best.get("sequence_len", 0)
    div_factor = max(0.0, 1.0 - max(0.0, div - 0.5))
    sample_factor = 1.0 if n >= 100 else (0.6 if n >= 30 else 0.0)
    return round(fid * div_factor * sample_factor, 3)


def _is_isolated_spike(best, trustworthy, coherence_frac=0.5):
    by_val = sorted(trustworthy, key=lambda r: r["value"])
    vals = [r["value"] for r in by_val]
    i = vals.index(best["value"])
    bp = best["delta"]["net_pnl"]
    if bp <= 0:
        return False
    neighbors = []
    if i > 0:
        neighbors.append(by_val[i - 1])
    if i < len(by_val) - 1:
        neighbors.append(by_val[i + 1])
    if not neighbors:
        return True
    coherent = any(nb["delta"]["net_pnl"] >= bp * coherence_frac for nb in neighbors)
    return not coherent


def recommend_direction(sweep_result, *, min_sample=30, actionable_min_pnl=0.0,
                        value_penalty_k=0.1, coherence_frac=0.5):
    """门控 + 排名 + 多重比较守卫（连贯趋势）→ recommend / no_actionable_direction。
    证据不足绝不杜撰方向。observability-only，绝不自动应用。"""
    note = next((r.get("fidelity_note") for r in sweep_result if r.get("fidelity_note")), None)
    base = {"all_values": sweep_result, "fidelity_note": note, "tested_count": len(sweep_result)}
    trustworthy = [r for r in sweep_result
                   if not r.get("untrustworthy") and (r.get("sequence_len", 0) >= min_sample)
                   and r.get("delta") is not None]
    if not trustworthy:
        return {**base, "verdict": "no_actionable_direction", "reason": "no_trustworthy_values"}
    ranked = sorted(trustworthy, key=lambda r: r["delta"]["net_pnl"], reverse=True)
    best = ranked[0]
    effective_min = actionable_min_pnl * (1 + value_penalty_k * len(sweep_result))
    if best["delta"]["net_pnl"] <= effective_min:
        return {**base, "verdict": "no_actionable_direction", "reason": "below_threshold",
                "effective_min_pnl": effective_min}
    if _is_isolated_spike(best, trustworthy, coherence_frac):
        return {**base, "verdict": "no_actionable_direction", "reason": "isolated_spike",
                "isolated_spike": True}
    return {**base, "verdict": "recommend", "recommended_value": best["value"],
            "delta_net_pnl": best["delta"]["net_pnl"], "confidence": _confidence(best),
            "baseline_fidelity": best.get("baseline_fidelity"),
            "divergence_ratio": best.get("divergence_ratio"), "sample": best.get("sequence_len")}
