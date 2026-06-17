"""fix-lever2-low-rr-sizing-tp1: 低 R:R 保护性缩仓判定用 TP1 口径（不被 lever2 阶梯松绑）。"""
from unittest import mock

from agents.trading.judge import MultiJudge


def _judge():
    j = MultiJudge.__new__(MultiJudge)
    j.logger = mock.MagicMock()
    j._low_rr_max_position_pct = 0.5
    j._low_rr_max_leverage = 5
    return j


def test_low_rr_sizing_scales_on_tp1_even_if_ladder_high():
    # lever2 开：阶梯 rr=1.7≥1.5，但 TP1 口径 rr=1.4<1.5 → 仍须保护性缩仓
    j = _judge()
    plan = {"size_usdt": 30.0, "leverage": 5,
            "effective_rr_tp1": 1.4, "effective_risk_reward_ratio": 1.7}
    scale = j._apply_low_rr_sizing(plan, plan["effective_rr_tp1"], True, "long_aligned_low_rr")
    assert scale is not None
    assert plan["size_usdt"] < 30.0          # 缩仓了
    assert plan["leverage"] <= j._low_rr_max_leverage
    assert plan["is_low_rr"] is True
    assert plan["slot_type"] == "low_rr_extra"


def test_low_rr_sizing_skips_when_tp1_high():
    # TP1 口径 rr=1.6≥1.5 → 不缩（本就不是 marginal 单）
    j = _judge()
    plan = {"size_usdt": 30.0, "leverage": 5, "effective_rr_tp1": 1.6}
    scale = j._apply_low_rr_sizing(plan, plan["effective_rr_tp1"], True, "long_aligned_low_rr")
    assert scale is None
    assert plan["size_usdt"] == 30.0         # 未缩


def test_low_rr_sizing_skips_non_low_rr_policy():
    j = _judge()
    plan = {"size_usdt": 30.0, "leverage": 5, "effective_rr_tp1": 1.3}
    scale = j._apply_low_rr_sizing(plan, 1.3, True, "default")
    assert scale is None
    assert plan["size_usdt"] == 30.0


def test_low_rr_sizing_skips_probe():
    j = _judge()
    plan = {"size_usdt": 30.0, "leverage": 5, "effective_rr_tp1": 1.3, "is_probe": True}
    scale = j._apply_low_rr_sizing(plan, 1.3, True, "long_aligned_low_rr")
    assert scale is None
    assert plan["size_usdt"] == 30.0
