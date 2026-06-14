"""逐决策扰动回放引擎（L3a）：同一 record 用 baseline vs perturbed 旋钮跑两次
真实 _make_decision，量化决策翻转。复用 L2 replay_decision/compare_decision。
observability-only —— 严禁交易决策路径 import/调用本模块。"""
from utils.decision_replay import replay_decision, compare_decision, _DISCRETE_ATTR


def _decision_class(payload):
    a = (payload or {}).get("action")
    return "accept" if a in ("open_long", "open_short") else "reject"


def _gate_label_changed(baseline, perturbed):
    ba = (baseline or {}).get("attribution") or {}
    pa = (perturbed or {}).get("attribution") or {}
    return any(ba.get(f) != pa.get(f) for f in _DISCRETE_ATTR)


async def replay_with_perturbation(record, baseline_config, perturbed_config):
    if not record.get("replayable") or not record.get("state_snapshot_before_decision"):
        return {"status": "not_replayable", "flipped": False, "flip_kind": "not_replayable",
                "baseline_action": None, "perturbed_action": None, "diffs": []}

    baseline = await replay_decision(record, baseline_config)
    recorded_class = record.get("decision")
    if _decision_class(baseline) != recorded_class:
        return {"status": "baseline_mismatch", "flipped": False, "flip_kind": "baseline_mismatch",
                "baseline_action": (baseline or {}).get("action"),
                "perturbed_action": None, "diffs": []}

    perturbed = await replay_decision(record, perturbed_config)
    b_cls, p_cls = _decision_class(baseline), _decision_class(perturbed)
    diffs = compare_decision(baseline, perturbed)["diffs"] if (baseline and perturbed) else []

    if b_cls != p_cls:
        flip_kind = "reject_to_accept" if b_cls == "reject" else "accept_to_reject"
        flipped = True
    elif _gate_label_changed(baseline, perturbed):
        flip_kind = "gate_label_change"
        flipped = True
    else:
        flip_kind = "none"
        flipped = False

    return {"status": "ok", "flipped": flipped, "flip_kind": flip_kind,
            "baseline_action": (baseline or {}).get("action"),
            "perturbed_action": (perturbed or {}).get("action"), "diffs": diffs}


from collections import defaultdict
from utils.cf_honesty_gate import wilson_interval

_FIDELITY_NOTE = ("逐决策独立，不含级联（早期翻转改变后续状态留 L3b）；"
                  "只对非 LLM 旋钮确定（LLM 取录制内联输出）。")


async def build_perturbation_report(records, baseline_config, perturbed_config, *,
                                    min_sample=30, lowconf_sample=100):
    groups = defaultdict(list)
    skipped_nr = 0
    baseline_mismatch = 0
    for rec in records:
        r = await replay_with_perturbation(rec, baseline_config, perturbed_config)
        if r["status"] == "not_replayable":
            skipped_nr += 1
            continue
        if r["status"] == "baseline_mismatch":
            baseline_mismatch += 1
            continue
        key = (f"{(rec.get('trade_decision_output') or {}).get('reject_reason')}"
               f"|{rec.get('effective_regime') or rec.get('regime_state')}"
               f"|{rec.get('side')}")
        groups[key].append(r)
    buckets = {}
    for key, rs in groups.items():
        n = len(rs)
        flips = sum(1 for r in rs if r["flipped"])
        kinds = defaultdict(int)
        for r in rs:
            kinds[r["flip_kind"]] += 1
        bucket = {"n": n, "flip_count": flips, "flip_rate": flips / n if n else 0.0,
                  "flip_rate_ci": wilson_interval(flips, n), "flip_kinds": dict(kinds)}
        bucket["verdict"] = "INSUFFICIENT_SAMPLE" if n < min_sample else (
            "low_confidence" if n < lowconf_sample else "actionable")
        buckets[key] = bucket
    return {"buckets": buckets, "metadata": {
        "perturbed_knobs": dict(perturbed_config or {}),
        "skipped_not_replayable": skipped_nr,
        "baseline_mismatch_count": baseline_mismatch,
        "fidelity_note": _FIDELITY_NOTE}}
