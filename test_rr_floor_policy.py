"""验收测试：R:R Floor Policy 修复。

参考文档：
- docs/rr_floor_policy_prd.md
- docs/rr_floor_policy_acceptance.md

覆盖项目 AC-RR-01 .. AC-RR-09。
"""

import os
import sys
import time
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(__file__))

from agents.trading.judge import MultiJudge
from utils.config_loader import load_config, format_banner
from utils.market_regime import RegimeManager


# ───────────────────────────── helpers ─────────────────────────────

def _make_judge(regime: str = 'bullish', config_overrides: dict = None) -> MultiJudge:
    """Construct Judge attached to a RegimeManager pinned to `regime`."""
    config = {
        'max_trade_amount': 10,
        'short_regime_guard_enabled': True,
        'probe_short_enabled': False,
        'low_rr_slot_enabled': True,
        'low_rr_long_aligned_enabled': True,
        'rr_floor_default': 1.50,
        'rr_floor_long_bullish': 1.30,
        'rr_floor_long_aligned_choppy': 1.30,
        'rr_floor_short_bullish': 1.80,
        'probe_rr_floor': 1.30,
        'low_rr_max_leverage': 5,
        'low_rr_max_position_pct': 0.5,
        'min_deferred_signal_score': 45,
    }
    if config_overrides:
        config.update(config_overrides)

    judge = MultiJudge.__new__(MultiJudge)
    judge._short_regime_guard_enabled = config['short_regime_guard_enabled']
    judge._short_live_min_score = 55
    judge._short_live_min_rsi = 40
    judge._short_live_min_range_pos = 0.45
    judge._short_live_require_daily_bearish = True
    judge._short_live_min_htf_votes = 2
    judge._short_live_max_pre_move = -0.01
    judge._probe_short_enabled = config['probe_short_enabled']
    judge._low_rr_slot_enabled = config['low_rr_slot_enabled']
    judge._low_rr_long_aligned_enabled = config['low_rr_long_aligned_enabled']
    judge._rr_floor_default = config['rr_floor_default']
    judge._rr_floor_long_bullish = config['rr_floor_long_bullish']
    judge._rr_floor_long_aligned_choppy = config['rr_floor_long_aligned_choppy']
    judge._rr_floor_short_bullish = config['rr_floor_short_bullish']
    judge._probe_rr_floor = config['probe_rr_floor']
    judge._low_rr_max_leverage = config['low_rr_max_leverage']
    judge._low_rr_max_position_pct = config['low_rr_max_position_pct']
    judge._min_deferred_signal_score = config['min_deferred_signal_score']
    judge._probe_short_max_position_pct = 0.3
    judge._probe_short_max_leverage = 3
    judge._probe_short_cooldown_until = 0
    judge._probe_short_active = None
    judge._max_trade_amount = config['max_trade_amount']
    judge._symbol_tech_cache = {}
    judge._pending_open_slots = {}

    with patch.object(RegimeManager, '_load_state'):
        rm = RegimeManager({})
    rm._effective_regime = regime
    rm._raw_regime = regime
    rm._confidence = 80
    rm._last_changed_at = time.time() - 7200
    judge._regime_manager = rm

    class FakeLedger:
        _enabled = True
        def record_rejection(self, *a, **kw):
            pass
    judge._counterfactual_ledger = FakeLedger()
    judge.logger = MagicMock()
    return judge


def _tech(direction: str = 'bullish', htf: str = 'bullish', daily: str = 'bullish',
          block_long: bool = False, block_short: bool = False) -> dict:
    return {
        'trend': {
            'direction': direction,
            'higher_tf_bias': htf,
            'daily_bias': daily,
            'strength': 75,
        },
        'momentum': {'rsi': 55, 'atr_pct': 0.02},
        'entry_timing': {
            'tf_15m_confirm_long': not block_long,
            'tf_15m_confirm_short': not block_short and direction == 'bearish',
            'tf_15m_block_long': block_long,
            'tf_15m_block_short': block_short,
            'tf_15m_bias': direction,
        },
        'money_flow': {},
        'crowd': {'long_ratio': 0.5},
        'risk': {'liquidity_score': 50},
        'short_context': {'position_in_24h_range': 0.6, 'pre_12h_return_pct': 0.0},
        'indicators': {'rsi': 55},
    }


# ───────────────────────────── AC-RR-01 ─────────────────────────────

class TestAC01ConfigDefaults:

    def test_default_floor_values(self):
        cfg = load_config(strict_live_check=False)
        assert cfg['rr_floor_default'] == 1.50
        assert cfg['rr_floor_long_bullish'] == 1.30
        assert cfg['rr_floor_long_aligned_choppy'] == 1.30
        assert cfg['rr_floor_short_bullish'] == 1.80
        assert cfg['probe_rr_floor'] == 1.30

    def test_banner_lists_all_four_floors(self):
        cfg = load_config(strict_live_check=False)
        banner = format_banner(cfg)
        assert 'default=1.5' in banner
        assert 'long_bullish=1.3' in banner
        assert 'long_aligned_choppy=1.3' in banner
        assert 'probe=1.3' in banner


# ───────────────────────────── AC-RR-02 ─────────────────────────────

class TestAC02BullishLongLowRR:

    def test_low_rr_long_passes_in_bullish(self):
        judge = _make_judge('bullish')
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.45,
                'effective_risk_reward_ratio': 1.45}
        tech = _tech('bullish', 'bullish', 'bullish')

        result = judge._apply_regime_policy('INJ-USDT', 'open_long', plan, 50, tech)

        assert result is None
        assert plan['is_low_rr'] is True
        assert plan['slot_type'] == 'low_rr_extra'
        assert plan['rr_floor_used'] == 1.30
        assert plan['rr_policy'] == 'long_bullish_low_rr'


# ───────────────────────────── AC-RR-03 ─────────────────────────────

class TestAC03ChoppyAlignedLongPasses:

    @pytest.mark.parametrize('regime', ['choppy', 'mixed'])
    def test_choppy_aligned_long_passes(self, regime):
        judge = _make_judge(regime)
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.45,
                'effective_risk_reward_ratio': 1.45}
        tech = _tech('bullish', 'bullish', 'neutral')

        result = judge._apply_regime_policy('INJ-USDT', 'open_long', plan, 50, tech)

        assert result is None
        assert plan['is_low_rr'] is True
        assert plan['slot_type'] == 'low_rr_extra'
        assert plan['rr_floor_used'] == 1.30
        assert plan['rr_policy'] == 'long_aligned_low_rr'

    def test_choppy_long_passes_with_daily_bullish_only(self):
        """HTF 中性但 daily=bullish 仍算强一致。"""
        judge = _make_judge('choppy')
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.45,
                'effective_risk_reward_ratio': 1.45}
        tech = _tech('bullish', 'neutral', 'bullish')

        result = judge._apply_regime_policy('INJ-USDT', 'open_long', plan, 50, tech)

        assert result is None
        assert plan['rr_policy'] == 'long_aligned_low_rr'
        assert plan['rr_floor_used'] == 1.30


# ───────────────────────────── AC-RR-04 ─────────────────────────────

class TestAC04ChoppyNonAlignedRejected:

    def test_choppy_non_aligned_long_rejected(self):
        judge = _make_judge('choppy')
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.45,
                'effective_risk_reward_ratio': 1.45}
        # direction=neutral, htf/daily 都不是 bullish
        tech = _tech('neutral', 'neutral', 'neutral')

        result = judge._apply_regime_policy('INJ-USDT', 'open_long', plan, 50, tech)

        assert result is not None
        assert 'rr_below_floor' in result
        assert plan.get('slot_type') != 'low_rr_extra'
        assert plan['rr_floor_used'] == 1.50
        assert plan['rr_policy'] == 'default'

    def test_choppy_long_blocked_by_15m_block_long(self):
        """趋势一致但 15m 明确 block_long 时不许放行。"""
        judge = _make_judge('choppy')
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.45,
                'effective_risk_reward_ratio': 1.45}
        tech = _tech('bullish', 'bullish', 'bullish', block_long=True)

        result = judge._apply_regime_policy('INJ-USDT', 'open_long', plan, 50, tech)

        assert result is not None
        assert 'rr_below_floor' in result
        assert plan['rr_policy'] == 'default'


# ───────────────────────────── AC-RR-05 ─────────────────────────────

class TestAC05ShortDefaultNotRelaxed:

    @pytest.mark.parametrize('regime', ['choppy', 'mixed'])
    def test_short_in_choppy_still_blocked_at_default_floor(self, regime):
        judge = _make_judge(regime)
        # short guard only kicks in bullish; here we want pure default-floor effect.
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.45,
                'effective_risk_reward_ratio': 1.45}
        tech = _tech('bearish', 'bearish', 'bearish')

        result = judge._apply_regime_policy('INJ-USDT', 'open_short', plan, -60, tech)

        assert result is not None
        assert 'rr_below_floor' in result
        assert plan['rr_floor_used'] == 1.50
        assert plan['rr_policy'] == 'default'


# ───────────────────────────── AC-RR-06 ─────────────────────────────

class TestAC06BullishShortStrong:

    def test_short_in_bullish_blocked_at_1_70(self):
        judge = _make_judge('bullish')
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.70,
                'effective_risk_reward_ratio': 1.70}
        tech = _tech('bearish', 'bearish', 'bearish')
        # Short guard wraps R:R into is_short_allowed which uses 1.8 too.
        result = judge._apply_regime_policy('INJ-USDT', 'open_short', plan, -75, tech)

        assert result is not None
        # 任一 short_regime_guard / rr_below_floor 都符合 1.80 边界要求
        assert ('short_regime_guard' in result) or ('rr_below_floor' in result)


# ───────────────────────────── AC-RR-07 ─────────────────────────────

class TestAC07ProbePathConsistency:

    def test_probe_uses_probe_floor_via_select(self):
        judge = _make_judge('mixed')
        plan = {'is_probe': True, 'size_usdt': 5, 'leverage': 3,
                'effective_risk_reward_ratio': 1.35}
        tech = _tech('neutral', 'neutral', 'neutral')

        min_rr, policy, reason = judge._select_rr_floor('open_long', plan, tech, 50)
        assert min_rr == 1.30
        assert policy == 'probe'
        assert 'probe' in reason

    def test_probe_path_main_passes_135(self):
        """Probe 经主路径 _apply_regime_policy 不被 1.50 拦截。"""
        judge = _make_judge('bullish')
        plan = {'is_probe': True, 'slot_type': 'probe_long', 'size_usdt': 5,
                'leverage': 3, 'risk_reward_ratio': 1.35,
                'effective_risk_reward_ratio': 1.35}
        tech = _tech('bullish', 'bullish', 'bullish')

        result = judge._apply_regime_policy('INJ-USDT', 'open_long', plan, 50, tech)
        assert result is None
        assert plan['rr_floor_used'] == 1.30
        assert plan['rr_policy'] == 'probe'


# ───────────────────────────── AC-RR-08 ─────────────────────────────

class TestAC08PathConsistency:

    @pytest.mark.parametrize('regime,direction,score,rr', [
        ('bullish', 'open_long', 50, 1.45),
        ('choppy', 'open_long', 50, 1.45),
        ('mixed', 'open_long', 50, 1.45),
        ('bullish', 'open_short', -75, 2.0),
        ('choppy', 'open_short', -60, 1.45),
    ])
    def test_select_floor_matches_apply_regime(self, regime, direction, score, rr):
        """主路径与 deferred 路径都经过 _select_rr_floor，应得到相同 floor 与 policy。"""
        judge_a = _make_judge(regime)
        judge_b = _make_judge(regime)

        plan_a = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': rr,
                  'effective_risk_reward_ratio': rr}
        plan_b = dict(plan_a)
        tech = _tech(
            'bullish' if direction == 'open_long' else 'bearish',
            'bullish' if direction == 'open_long' else 'bearish',
            'bullish' if direction == 'open_long' else 'bearish',
        )

        # 直接 call _select_rr_floor（共享实现）
        min_rr_a, policy_a, _ = judge_a._select_rr_floor(direction, plan_a, tech, score)
        # _apply_regime_policy 写入 plan
        judge_b._apply_regime_policy('INJ-USDT', direction, plan_b, score, tech)
        min_rr_b = plan_b.get('rr_floor_used')
        policy_b = plan_b.get('rr_policy')

        assert min_rr_a == min_rr_b
        assert policy_a == policy_b


# ───────────────────────────── AC-RR-09 ─────────────────────────────

class TestAC09AttributionLogging:

    def test_rejection_attribution_carries_new_fields(self):
        judge = _make_judge('choppy')
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.45,
                'effective_risk_reward_ratio': 1.45}
        tech = _tech('neutral', 'neutral', 'neutral')

        # 触发拒绝并写入 plan 的 floor 字段
        judge._apply_regime_policy('INJ-USDT', 'open_long', plan, 50, tech)
        attr = judge._rejection_attribution(
            'open_long', plan, 'rr_below_floor:1.45', tech=tech
        )

        assert attr['rr_floor_used'] == 1.50
        assert attr['rr_floor_reason'].startswith('default')
        assert attr['symbol_trend'] == 'neutral'
        assert attr['symbol_higher_tf_bias'] == 'neutral'
        assert attr['symbol_daily_bias'] == 'neutral'
        assert attr['rr_policy'] == 'default'
        assert attr['entry_regime'] == 'choppy'
        assert attr['raw_regime'] == 'choppy'

    def test_rejection_attribution_for_aligned_long(self):
        """选择 long_aligned_low_rr 后通过的 plan，attribution 应反映新 policy。"""
        judge = _make_judge('choppy')
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.45,
                'effective_risk_reward_ratio': 1.45}
        tech = _tech('bullish', 'bullish', 'bullish')
        judge._apply_regime_policy('INJ-USDT', 'open_long', plan, 50, tech)

        attr = judge._rejection_attribution(
            'open_long', plan, 'unit_test', tech=tech
        )
        assert attr['rr_floor_used'] == 1.30
        assert attr['rr_policy'] == 'long_aligned_low_rr'
        assert attr['symbol_trend'] == 'bullish'
        assert attr['symbol_higher_tf_bias'] == 'bullish'

    def test_rejection_attribution_no_tech_uses_plan_snapshot(self):
        """生产中 _gate_and_publish_open 调用 _rejection_attribution 时不带 tech。
        plan 已携带 symbol_trend/htf/daily 快照，attribution 必须读 plan，
        不能 fallback 到 neutral 与 rr_floor_reason 字符串自相矛盾。"""
        judge = _make_judge('choppy')
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 1.45,
                'effective_risk_reward_ratio': 1.45}
        tech = _tech('bullish', 'bullish', 'bullish')

        judge._apply_regime_policy('INJ-USDT', 'open_long', plan, 50, tech)
        # 模拟 slot gate 阶段：tech 已不在调用栈中
        attr_no_tech = judge._rejection_attribution(
            'open_long', plan, 'low_rr_slot_full'
        )
        assert attr_no_tech['rr_policy'] == 'long_aligned_low_rr'
        assert attr_no_tech['rr_floor_used'] == 1.30
        assert attr_no_tech['symbol_trend'] == 'bullish'
        assert attr_no_tech['symbol_higher_tf_bias'] == 'bullish'
        assert attr_no_tech['symbol_daily_bias'] == 'bullish'
        assert 'sym_trend=bullish' in attr_no_tech['rr_floor_reason']


# ───────────────────────────── Task 2: path_evidence OR branch ─────────────────────────────

def _make_judge_path(regime='choppy', overrides=None):
    j = _make_judge(regime=regime, config_overrides=overrides)
    # 新字段在 _make_judge 中未设,显式补设以反映 Task 1 行为
    j._path_evidence_aligned_enabled = (overrides or {}).get('path_evidence_aligned_enabled', True)
    j._path_evidence_min_pre12h_return = 0.03
    j._path_evidence_max_range_pos = 0.92
    j._path_evidence_min_strength = 60
    return j


def _clean_trend_tech():
    """choppy regime 下的干净 long 趋势:bias 漏报(neutral),但路径证据明确。"""
    return {
        'trend': {'direction': 'bullish', 'strength': 70,
                  'higher_tf_bias': 'neutral', 'daily_bias': 'neutral'},
        'entry_timing': {'tf_15m_block_long': False},
        'entry_context': {'pre_12h_return_pct': 0.08, 'position_in_24h_range': 0.6,
                          'prev_daily_return_pct': 0.05},
    }


def test_path_evidence_grants_aligned_floor():
    j = _make_judge_path(regime='choppy')
    min_rr, policy, reason = j._select_rr_floor('open_long', {}, _clean_trend_tech(), score=60)
    assert min_rr == 1.30
    assert policy == 'long_aligned_path_evidence'


def test_path_evidence_real_choppy_not_granted():
    """方向反复/回撤大:pre_12h_return 为负 → 不授对齐地板。"""
    j = _make_judge_path(regime='choppy')
    tech = _clean_trend_tech()
    tech['entry_context']['pre_12h_return_pct'] = -0.02
    min_rr, policy, reason = j._select_rr_floor('open_long', {}, tech, score=60)
    assert min_rr == 1.50
    assert policy == 'default'


def test_path_evidence_overheated_not_granted():
    """追高(range_pos 过高)→ 不授对齐地板。"""
    j = _make_judge_path(regime='choppy')
    tech = _clean_trend_tech()
    tech['entry_context']['position_in_24h_range'] = 0.97
    min_rr, policy, reason = j._select_rr_floor('open_long', {}, tech, score=60)
    assert min_rr == 1.50


def test_path_evidence_switch_off_keeps_default():
    j = _make_judge_path(regime='choppy', overrides={'path_evidence_aligned_enabled': False})
    min_rr, policy, reason = j._select_rr_floor('open_long', {}, _clean_trend_tech(), score=60)
    assert min_rr == 1.50
    assert policy == 'default'


# ───────────────────────────── 回归守卫: path_evidence 必须与低RR缩仓家族同享待遇 ─────────────────────────────

def test_path_evidence_policy_in_low_rr_family():
    """long_aligned_path_evidence 必须与 long_aligned_low_rr 同享低RR缩仓/降杠杆/槽位待遇。
    回归守卫:防止新 policy 绕过低 R:R 风控(judge.py ~1480 和 ~3027 两处 low_rr_policies)。"""
    import re
    import pathlib
    src = (pathlib.Path(__file__).parent / 'agents/trading/judge.py').read_text()
    matches = re.findall(r"low_rr_policies = \{([^}]*)\}", src)
    # 两处 low_rr_policies 集合都必须包含该 policy
    assert len(matches) >= 2, f"期望至少 2 处 low_rr_policies 定义，实际找到 {len(matches)} 处"
    for occurrence in matches:
        assert 'long_aligned_path_evidence' in occurrence, (
            f"low_rr_policies 缺少 long_aligned_path_evidence: {occurrence!r}"
        )
        assert 'long_aligned_low_rr' in occurrence, (
            f"low_rr_policies 缺少 long_aligned_low_rr: {occurrence!r}"
        )
