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


# ─── Task 3: 归因 breakdown ───
def test_attribution_breakdown_fields():
    from test_long_entry_position_guard import _make_judge, _make_tech
    j = _make_judge(_pseudo_resonance_downweight_enabled=True, _ma_bloc_cap=45)
    # 强 MA 同源信号(rule entry_long + bullish 强 + htf bullish)触发封顶
    tech = _make_tech()
    tech['rule_signal'] = {'entry_long': 1}
    tech['trend'] = {'direction': 'bullish', 'strength': 90, 'higher_tf_bias': 'bullish'}
    j._trend_saturation_enabled = False
    _ = j._compute_score(tech)
    attr = j._build_attribution(tech, 'open_long', 50.0, None, None, 'main')
    assert 'ma_bloc_contribution' in attr
    assert 'independent_contribution' in attr
    assert attr['ma_bloc_capped'] is True  # 35+18+10=63 > cap45


def test_attribution_default_when_disabled():
    from test_long_entry_position_guard import _make_judge, _make_tech
    j = _make_judge(_pseudo_resonance_downweight_enabled=False, _ma_bloc_cap=45)
    j._trend_saturation_enabled = False
    _ = j._compute_score(_make_tech())
    attr = j._build_attribution(_make_tech(), 'open_long', 50.0, None, None, 'main')
    assert attr['ma_bloc_capped'] is False


# ─── Task 4: banner ───
def test_banner_shows_pseudo_resonance():
    from utils.config_loader import format_banner, DEFAULTS
    assert '伪共振降权' in format_banner(dict(DEFAULTS))
