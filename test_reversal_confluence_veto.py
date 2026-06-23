"""反转合流否决 (restore-llm-rsi-veto-power) 单测。

覆盖：config 四段式、helper 合流判定、defer 路由 + 归因。
"""
from utils.config_loader import DEFAULTS, HARD_LIMITS
from agents.trading.judge import MultiJudge


# ─── Task 1: config 四段式 ───
def test_reversal_veto_config_defaults():
    # 默认 OFF：潜伏护栏，不改线上行为（真实磁带验证 0% 触发，见 verify 报告）
    assert DEFAULTS['llm_rsi_reversal_veto_enabled'] is False
    assert DEFAULTS['reversal_veto_min_llm_confidence'] == 0
    assert HARD_LIMITS['reversal_veto_min_llm_confidence'] == (0, 100)


# ─── Task 2: helper ───
def _judge(enabled=True, min_conf=0):
    j = MultiJudge.__new__(MultiJudge)
    j._reversal_veto_enabled = enabled
    j._reversal_veto_min_llm_confidence = min_conf
    return j


def _tech(div):
    return {'momentum': {'rsi_divergence': div}}


def test_veto_confluence_long():
    j = _judge()
    assert j._reversal_confluence_veto('open_long', 'open_short', _tech('bearish_div')) == 'reversal_confluence'


def test_veto_confluence_short():
    j = _judge()
    assert j._reversal_confluence_veto('open_short', 'open_long', _tech('bullish_div')) == 'reversal_confluence'


def test_veto_only_llm_no_div():
    j = _judge()
    assert j._reversal_confluence_veto('open_long', 'open_short', _tech(None)) is None


def test_veto_only_div_no_llm():
    j = _judge()
    assert j._reversal_confluence_veto('open_long', 'hold', _tech('bearish_div')) is None


def test_veto_disabled():
    j = _judge(enabled=False)
    assert j._reversal_confluence_veto('open_long', 'open_short', _tech('bearish_div')) is None


def test_veto_min_llm_confidence_gate():
    j = _judge(min_conf=50)
    # LLM 反向但置信不足 → 不触发
    assert j._reversal_confluence_veto('open_long', 'open_short', _tech('bearish_div'), llm_confidence=40) is None
    # 置信达标 → 触发
    assert j._reversal_confluence_veto('open_long', 'open_short', _tech('bearish_div'), llm_confidence=60) == 'reversal_confluence'


def test_veto_same_direction_no_trigger():
    j = _judge()
    # LLM 同向不触发
    assert j._reversal_confluence_veto('open_long', 'open_long', _tech('bearish_div')) is None


# ─── Task 3: defer 路由 ───
def _judge_route():
    j = MultiJudge.__new__(MultiJudge)
    j._reversal_veto_enabled = True
    j._reversal_veto_min_llm_confidence = 0
    j._long_live_pullback_timeout_hours = 4
    import logging
    j.logger = logging.getLogger('test')
    j._st = {}
    j._get_state = lambda s: j._st
    # 最小化 _rejection_attribution（真实方法依赖较多，测试用桩返回基础 dict）
    j._rejection_attribution = lambda action, plan, reason, tech=None: {'blocked_by': reason}
    return j


def test_route_defer_sets_state_and_decision():
    j = _judge_route()
    dec = j._route_reversal_veto_defer('BTC-USDT', 'open_long', 100.0, 40.0,
                                       _tech('bearish_div'), 'open_short')
    assert dec['action'] == 'hold'
    attr = dec['attribution']
    assert attr['reversal_veto_triggered'] is True
    assert attr['reversal_veto_deferred_dir'] == 'open_long'
    assert attr['reversal_veto_rsi_div'] == 'bearish_div'
    assert attr['reversal_veto_llm_action'] == 'open_short'
    st = j._st['deferred_entry']
    assert st['entry_type'] == 'deferred_reversal_veto'
    assert st['target_price'] < 100.0  # 多单等回调向下


def test_route_defer_short_target_above():
    j = _judge_route()
    dec = j._route_reversal_veto_defer('BTC-USDT', 'open_short', 100.0, -40.0,
                                       _tech('bullish_div'), 'open_long')
    st = j._st['deferred_entry']
    assert st['target_price'] > 100.0  # 空单等回调向上
    assert dec['attribution']['reversal_veto_deferred_dir'] == 'open_short'


# ─── Task 4: 主路径接线（静态源码断言，与既有 judge 测试同范式）───
def test_main_path_wires_veto_before_quality_gate():
    import inspect
    from agents.trading import judge as judge_mod
    src = inspect.getsource(judge_mod.MultiJudge._make_decision)
    assert '_reversal_confluence_veto(' in src
    assert '_route_reversal_veto_defer(' in src
    # veto 调用后须紧跟主路径质量门（veto 在主路径开仓终点、质量门之前）
    veto_idx = src.index('_reversal_confluence_veto(')
    assert src.find('reject_reason = self._open_quality_rejection(', veto_idx) != -1


# ─── Task 5: deferred 边界（红线：单点收口，deferred 不重复挂）───
def test_deferred_path_does_not_reapply_veto():
    import inspect
    from agents.trading import judge as judge_mod
    src = inspect.getsource(judge_mod.MultiJudge._make_decision)
    # 主路径恰好一次 veto 调用（deferred 再分发不重复）
    assert src.count('_reversal_confluence_veto(') == 1
    assert src.count('_route_reversal_veto_defer(') == 1
    # 边界注释存在
    assert 'reversal-confluence-veto 边界' in src


# ─── Task 6: 放行路径默认 reversal_veto_triggered=false ───
def test_passthrough_attribution_default_false():
    from test_long_entry_position_guard import _make_judge, _make_tech
    j = _make_judge()
    attr = j._build_attribution(_make_tech(), 'open_long', 50.0, None, None, 'main')
    assert attr['reversal_veto_triggered'] is False


# ─── Task 7: banner 显示开关 ───
def test_banner_shows_reversal_veto():
    from utils.config_loader import format_banner, DEFAULTS
    banner = format_banner(dict(DEFAULTS))
    assert '反转合流否决' in banner
