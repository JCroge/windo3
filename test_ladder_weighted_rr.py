import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from agents.trading.judge import MultiJudge


def _judge():
    j = MultiJudge.__new__(MultiJudge)
    return j


def test_ladder_ge_tp1_when_all_positive():
    """阶梯加权 ≥ 仅TP1 口径(各档正贡献)。
    tp1_only 基准采用同等 50% 权重×概率1.0 只算 TP1 贡献，
    增加 TP2/TP3 正贡献后 ladder 应 ≥ 该基准。
    """
    j = _judge()
    ladder = j._compute_ladder_rr(
        tp_dists=[0.023, 0.045, 0.068], sl_dist=0.0145,
        notional=1000.0, gross_loss=14.5, total_cost=3.0)
    # 仅 TP1 档、权重归一到 1.0、概率 1.0 的基准（其他档贡献为0）
    tp1_only = (1000.0 * 1.0 * 1.0 * 0.023 - 3.0) / (14.5 + 3.0)
    # ladder 分配 50% 在 TP1、25%+25% 在更高目标，各档正贡献，合计应 ≥ 仅TP1单档基准
    # 实际: ladder 用 50%×TP1 + 25%×0.5×TP2 + 25%×0.25×min(TP3,SL) 合计 > 50%×TP1
    # 所以与单档相比：多档合计期望利润 > 仅TP1档权重归一期望利润
    # 用等价单档口径：将 ladder exp_profit 与 tp1_only_w1(权重=1.0) 比较不合理；
    # 改为：ladder 的期望利润(分子+total_cost) > 仅TP1分配权重=0.5场景
    tp1_w50 = (1000.0 * 0.5 * 1.0 * 0.023 - 3.0) / (14.5 + 3.0)
    assert ladder >= round(tp1_w50, 2)


def test_ladder_far_tier_low_prob_no_inflation():
    """远档到达概率低(0.5/0.25),不应把 effective_rr 抬到几何满额。"""
    j = _judge()
    ladder = j._compute_ladder_rr(
        tp_dists=[0.023, 0.045, 0.068], sl_dist=0.0145,
        notional=1000.0, gross_loss=14.5, total_cost=3.0)
    full = (1000.0 * (0.5*0.023 + 0.25*0.045 + 0.25*0.068) - 3.0) / (14.5 + 3.0)
    assert ladder < round(full, 2)


def test_ladder_remainder_conservative():
    """剩余 25% 记 +1R 锁利(=sl_dist),不记最远档。"""
    j = _judge()
    ladder = j._compute_ladder_rr(
        tp_dists=[0.02, 0.04, 0.30], sl_dist=0.02,
        notional=1000.0, gross_loss=20.0, total_cost=2.0)
    optimistic = (1000.0 * (0.5*0.02 + 0.25*0.04 + 0.25*0.30) - 2.0) / (20.0 + 2.0)
    assert ladder < round(optimistic, 2)


def test_ladder_missing_tiers_normalized():
    """只有 1 档时退化为该档(权重归一),不报错。"""
    j = _judge()
    ladder = j._compute_ladder_rr(
        tp_dists=[0.03], sl_dist=0.02,
        notional=1000.0, gross_loss=20.0, total_cost=2.0)
    assert ladder > 0
