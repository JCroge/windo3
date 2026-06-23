"""伪共振降权 (pseudo-resonance-downweight, 病根1a) 单测。

覆盖：config 四段式、MA 块同向封顶数学、开关回退、归因。
"""
from utils.config_loader import DEFAULTS, HARD_LIMITS
from agents.trading.judge import MultiJudge


# ─── Task 1: config ───
def test_config_defaults():
    assert DEFAULTS['pseudo_resonance_downweight_enabled'] is False  # 默认OFF保守起步
    assert DEFAULTS['ma_bloc_cap'] == 50  # 缓进起步(目标45,据CF回放收)
    assert HARD_LIMITS['ma_bloc_cap'] == (0, 100)


# ─── Task 2: MA 块封顶数学 ───
def _j(enabled=True, cap=50):
    j = MultiJudge.__new__(MultiJudge)
    j._pseudo_resonance_downweight_enabled = enabled
    j._ma_bloc_cap = cap
    return j


def test_bloc_cap_same_dir():
    assert _j(cap=45)._cap_ma_bloc(35 + 18 + 10) == 45  # 同向超cap削


def test_bloc_under_cap():
    assert _j(cap=45)._cap_ma_bloc(30) == 30


def test_bloc_internal_offset():
    assert _j(cap=45)._cap_ma_bloc(35 - 10) == 25  # 内部反向抵消后未超


def test_bloc_disabled_passthrough():
    assert _j(enabled=False, cap=45)._cap_ma_bloc(63) == 63  # 关闭=线性不封顶


def test_bloc_negative():
    assert _j(cap=45)._cap_ma_bloc(-63) == -45


def test_bloc_zero():
    assert _j(cap=45)._cap_ma_bloc(0) == 0
