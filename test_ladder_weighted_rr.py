import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from agents.trading.judge import MultiJudge


def _judge():
    return MultiJudge.__new__(MultiJudge)


def _tp1_only(tp_dists, notional, gross_loss, total_cost):
    """旧口径:满仓在 TP1 离场。"""
    return round((notional * tp_dists[0] - total_cost) / (gross_loss + total_cost), 2)


def test_ladder_ge_tp1_when_all_positive():
    """阶梯加权必须 ≥ 真实旧 TP1-only 口径(各档正贡献)。不注水不反向。"""
    j = _judge()
    args = dict(tp_dists=[0.023, 0.045, 0.068], sl_dist=0.0145,
                notional=1000.0, gross_loss=14.5, total_cost=3.0)
    ladder = j._compute_ladder_rr(**args)
    baseline = _tp1_only(args['tp_dists'], args['notional'], args['gross_loss'], args['total_cost'])
    assert ladder >= baseline, f"ladder {ladder} < tp1_only {baseline} — 杠杆②反向了"


def test_ladder_hype_clears_aligned_floor():
    """HYPE 代表数:旧口径 1.14 过不了,阶梯口径应 >=1.30(过经杠杆①的对齐地板)。"""
    j = _judge()
    ladder = j._compute_ladder_rr(tp_dists=[0.023, 0.045, 0.068], sl_dist=0.0145,
                                  notional=1000.0, gross_loss=14.5, total_cost=3.0)
    assert ladder >= 1.30


def test_ladder_remainder_capped_at_1R():
    """剩余 25% 档封顶 +1R(=sl_dist),最远档极大时不被记满额。"""
    j = _judge()
    ladder = j._compute_ladder_rr(tp_dists=[0.02, 0.04, 0.30], sl_dist=0.02,
                                  notional=1000.0, gross_loss=20.0, total_cost=2.0)
    uncapped = round((1000.0 * (0.5*0.02 + 0.25*0.04 + 0.25*0.30) - 2.0) / (20.0 + 2.0), 2)
    assert ladder < uncapped


def test_ladder_missing_tiers_normalized():
    """只有 1 档时权重归一,退化为该档,不报错且为正。"""
    j = _judge()
    ladder = j._compute_ladder_rr(tp_dists=[0.03], sl_dist=0.02,
                                  notional=1000.0, gross_loss=20.0, total_cost=2.0)
    assert ladder > 0


def test_effective_rr_switch_off_uses_tp1():
    j = _judge()
    j._ladder_rr_enabled = False
    val = j._effective_rr_for_plan(tp_dists=[0.023, 0.045, 0.068], sl_dist=0.0145,
                                   notional=1000.0, gross_loss=14.5, total_cost=3.0)
    assert val == _tp1_only([0.023, 0.045, 0.068], 1000.0, 14.5, 3.0)


def test_effective_rr_switch_on_uses_ladder():
    j = _judge()
    j._ladder_rr_enabled = True
    args = dict(tp_dists=[0.023, 0.045, 0.068], sl_dist=0.0145,
                notional=1000.0, gross_loss=14.5, total_cost=3.0)
    assert j._effective_rr_for_plan(**args) == j._compute_ladder_rr(**args)
