from utils.joint_knob_sweep import compute_interactions


def _gr(combos):
    """构造最小 grid_result：combos = list of (combo_dict, net_pnl)。"""
    return {"combos": [{"combo": c, "delta": {"net_pnl": p, "win_rate": 0.0, "max_drawdown": 0.0},
                        "divergence_ratio": 0.0} for c, p in combos],
            "baseline_fidelity": 0.95, "sequence_len": 200, "untrustworthy": False,
            "fidelity_note": "note"}


# base: rr=1.5, conf=60
BV = {"rr_floor_default": 1.5, "min_confidence": 60}


def test_additive_when_joint_equals_sum_of_edges():
    # edge_A = +4, edge_B = +6, joint ≈ 10 → 交互≈0 → additive
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),    # anchor
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 4.0),    # edge_A
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 6.0),    # edge_B
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 10.0)])  # joint
    out = compute_interactions(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert out["anchor_ok"] is True
    inter = next(i for i in out["interactions"] if i["combo"] == {"rr_floor_default": 1.3, "min_confidence": 40})
    assert abs(inter["interaction"]) < out["effective_threshold"]
    assert inter["classification"] == "additive"


def test_synergy_when_joint_exceeds_sum():
    # edge_A=+1, edge_B=+1, joint=+20 → 交互=+18 → synergy
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 1.0),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 1.0),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 20.0)])
    out = compute_interactions(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    inter = next(i for i in out["interactions"] if i["combo"] == {"rr_floor_default": 1.3, "min_confidence": 40})
    assert inter["interaction"] == 18.0
    assert inter["classification"] == "synergy"


def test_antagonism_when_joint_below_sum():
    # edge_A=+10, edge_B=+10, joint=0 → 交互=-20 → antagonism
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 10.0),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 10.0),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 0.0)])
    out = compute_interactions(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    inter = next(i for i in out["interactions"] if i["combo"] == {"rr_floor_default": 1.3, "min_confidence": 40})
    assert inter["interaction"] == -20.0
    assert inter["classification"] == "antagonism"


def test_anchor_fail_when_base_base_nonzero():
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 5.0),   # anchor 非零!
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 4.0),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 6.0),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 10.0)])
    out = compute_interactions(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert out["anchor_ok"] is False


def test_edge_combos_labeled_edge():
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),    # anchor
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 4.0),    # edge (1 axis)
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 6.0),    # edge (1 axis)
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 10.0)])  # joint
    out = compute_interactions(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    edges = [i for i in out["interactions"] if i["classification"] == "edge"]
    assert len(edges) == 2
    # anchor 不入列
    assert all(i["combo"] != {"rr_floor_default": 1.5, "min_confidence": 60} for i in out["interactions"])


def test_missing_edge_skipped():
    # joint (1.3,40) present but edge (1.5,40) absent → skipped:missing_edge
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),    # anchor
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 4.0),    # edge_A only
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 10.0)])  # joint, edge_B missing
    out = compute_interactions(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    inter = next(i for i in out["interactions"] if i["combo"] == {"rr_floor_default": 1.3, "min_confidence": 40})
    assert inter["classification"] == "skipped:missing_edge"


def test_higher_order_skipped():
    # 3 个非 base 轴 → skipped:higher_order（首发只做 2 轴 pairwise）
    bv3 = {"a": 0, "b": 0, "c": 0}
    gr = _gr([({"a": 0, "b": 0, "c": 0}, 0.0),
              ({"a": 1, "b": 1, "c": 1}, 5.0)])
    out = compute_interactions(gr, bv3, actionable_min_pnl=1.0, value_penalty_k=0.0)
    inter = next(i for i in out["interactions"] if i["combo"] == {"a": 1, "b": 1, "c": 1})
    assert inter["classification"] == "skipped:higher_order"


import asyncio
import utils.joint_knob_sweep as jks


class _FakeArm:
    """run_arm 返回结构的最小桩：按 config 决定 final_equity。"""
    @staticmethod
    def make(config):
        # baseline (空 config / 全 base) → equity 1000；每放宽一个旋钮 +5
        bump = 5.0 * len(config) if config else 0.0
        n = 4
        return {"final_equity": 1000.0 + bump, "realized": [1.0] * n,
                "equity_curve": [1000.0, 1000.0 + bump],
                "decisions": [{"gate": "accept" if config else "rr_below_floor"} for _ in range(n)],
                "cf_open_count": len(config)}


def test_sweep_grid_cartesian_and_baseline_reuse(monkeypatch):
    calls = []

    async def fake_run_arm(recs, config, price_loader, **kw):
        calls.append(dict(config))
        return _FakeArm.make(config)

    monkeypatch.setattr(jks, "run_arm", fake_run_arm)
    # _gate_of_recorded 桩：录制全 reject
    monkeypatch.setattr(jks, "_gate_of_recorded", lambda r: "rr_below_floor")

    recs = [{"timestamp": i, "symbol": "X"} for i in range(4)]
    grids = {"rr_floor_default": [1.5, 1.3], "min_confidence": [60, 40]}
    res = asyncio.run(jks.sweep_grid(recs, grids, price_loader=None,
                                     baseline_config={}, fidelity_threshold=0.0))
    # 笛卡尔积 = 2×2 = 4 组合
    assert len(res["combos"]) == 4
    # baseline 臂只跑 1 次：calls 中空 config（baseline）恰好 1 个
    baseline_calls = [c for c in calls if not c]
    assert len(baseline_calls) == 1
    # 总调用 = 1 baseline + 4 perturbed
    assert len(calls) == 5
    # 多 key perturbed_config 正确透传
    assert {"rr_floor_default": 1.3, "min_confidence": 40} in calls
    assert res["untrustworthy"] is False


def test_sweep_grid_untrustworthy_short_circuit(monkeypatch):
    async def fake_run_arm(recs, config, price_loader, **kw):
        return _FakeArm.make(config)
    monkeypatch.setattr(jks, "run_arm", fake_run_arm)
    # 录制 gate 与 baseline 回放永不一致 → fidelity = 0
    monkeypatch.setattr(jks, "_gate_of_recorded", lambda r: "NEVER_MATCH")

    recs = [{"timestamp": i, "symbol": "X"} for i in range(4)]
    grids = {"rr_floor_default": [1.5, 1.3], "min_confidence": [60, 40]}
    res = asyncio.run(jks.sweep_grid(recs, grids, price_loader=None,
                                     baseline_config={}, fidelity_threshold=0.8))
    assert res["untrustworthy"] is True
    assert res["combos"] == []


from utils.joint_knob_sweep import recommend_direction_nd


def test_recommend_coherent_neighbor():
    # best=(1.3,40) net=10；轴邻居 (1.5,40) net=6 同向 → 连贯 → recommend
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 5.0),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 6.0),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 10.0)])
    out = recommend_direction_nd(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert out["verdict"] == "recommend"
    assert out["recommended_combo"] == {"rr_floor_default": 1.3, "min_confidence": 40}


def test_recommend_isolated_spike():
    # best=(1.3,40) net=100；轴邻居都 ≈0 → 孤立尖刺 → 拒答
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 0.5),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 0.5),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 100.0)])
    out = recommend_direction_nd(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert out["verdict"] == "no_actionable_direction"
    assert out["reason"] == "isolated_spike"
    assert out.get("isolated_spike") is True


def test_recommend_below_threshold():
    # 全部 delta 都很小 → below_threshold
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 0.1),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 0.1),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 0.2)])
    out = recommend_direction_nd(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert out["verdict"] == "no_actionable_direction"
    assert out["reason"] == "below_threshold"


def test_recommend_reports_all_combos():
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 5.0),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 6.0),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 10.0)])
    out = recommend_direction_nd(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert "all_combos" in out and len(out["all_combos"]) == 4
    assert "fidelity_note" in out
