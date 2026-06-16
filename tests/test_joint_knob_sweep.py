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
