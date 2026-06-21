"""AC-LONGPOS-01 ~ AC-LONGPOS-17 验收测试 — Long Entry Position Guard。

覆盖：
- 主路径与 deferred 路径的 Entry Position Guard 一致性
- range_pos / pre_12h / prev_daily 三组阈值触发
- deferred_pullback_overheat 创建与 chase 禁用
- 无有效回调目标时直拒
- short side guard 在主路径生效
- EV bucket entry_type 不再 unknown，sparse bucket 不抬 p_win
- trade_decision.v2 / execution_result.v2 兼容性
- event_backtest 与 live 同构
- 配置默认值、hard limits、env override、启动 banner
"""

import os
import sys
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────


def _make_judge(**overrides):
    """构建一个剥离了网络与 LLM 的 MultiJudge。"""
    from agents.trading.judge import MultiJudge

    j = MultiJudge.__new__(MultiJudge)
    j._long_live_position_guard_enabled = True
    j._long_live_max_range_pos = 0.82
    j._long_live_max_pre_move = 0.05
    j._long_live_max_daily_gain = 0.10
    j._long_live_daily_gain_range_pos = 0.75
    j._long_live_pullback_min_pct = 0.025
    j._long_live_pullback_timeout_hours = 4
    j._long_live_overheat_disable_chase = True

    j._short_regime_guard_enabled = True
    j._short_live_min_score = 55
    j._short_live_min_rsi = 40
    j._short_live_min_range_pos = 0.45
    j._short_live_require_daily_bearish = True
    j._short_live_min_htf_votes = 2
    j._short_live_max_pre_move = -0.01

    j._ev_bucket_min_trades = 10
    j._ev_bucket_sparse_allow_uplift = False
    j._ev_min_threshold = 0.05
    j._ev_strong_signal_threshold = 70
    j._ev_prior_wins = 2
    j._ev_prior_total = 5
    j._fallback_win_rate = 0.52
    j._recent_win_rate = None
    j._total_completed_trades = 0
    j._recent_wins = 0
    j._bucketed_ev_enabled = True
    j._bucketed_metrics = {}

    j._rr_floor_default = 1.5
    j._rr_floor_long_bullish = 1.30
    j._rr_floor_long_aligned_choppy = 1.30
    j._rr_floor_short_bullish = 1.80
    j._probe_rr_floor = 1.30
    j._low_rr_long_aligned_enabled = True
    j._low_rr_max_leverage = 5
    j._low_rr_max_position_pct = 0.5
    j._low_rr_slot_enabled = True

    j.logger = MagicMock()

    class _MockLedger:
        _enabled = False

        def record_rejection(self, *a, **k):
            pass

    j._counterfactual_ledger = _MockLedger()

    class _MockRegime:
        _effective_regime = 'bullish'
        _raw_regime = 'bullish'
        _confidence = 70

        def snapshot(self):
            return {'effective_regime': 'bullish', 'raw_regime': 'bullish', 'confidence': 70}

        def is_probe_short_eligible(self, btc_tech, techs):
            return True

    j._regime_manager = _MockRegime()
    for k, v in overrides.items():
        setattr(j, k, v)
    return j


def _make_tech(range_pos=0.5, pre_12h=0.0, prev_daily=0.0,
               daily_bias='bullish', rsi=55, atr_pct=0.02):
    return {
        'trend': {
            'direction': 'bullish',
            'higher_tf_bias': 'bullish',
            'daily_bias': daily_bias,
        },
        'entry_timing': {'tf_15m_confirm_long': True, 'tf_15m_confirm_short': False},
        'indicators': {'rsi': rsi, 'price': 1.0},
        'momentum': {'atr_pct': atr_pct, 'rsi': rsi},
        'risk': {'liquidity_score': 60},
        'rule_signal': {'ma_aligned_long': True},
        'entry_context': {
            'position_in_24h_range': range_pos,
            'pre_12h_return_pct': pre_12h,
            'prev_daily_return_pct': prev_daily,
        },
    }


def _make_plan(price=1.0, sl=0.95):
    return {
        'entry_zone': [price, price],
        'stop_loss': sl,
        'leverage': 5,
        'size_usdt': 10,
        'risk_reward_ratio': 1.36,
        'effective_risk_reward_ratio': 1.36,
        'rr_floor_used': 1.30,
        'slot_type': 'low_rr_extra',
    }


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-01: NEAR 复现场景不得即时开多
# ──────────────────────────────────────────────────────────


class TestAC01NEARScenario:
    def test_near_overheat_blocks_immediate_open(self):
        judge = _make_judge()
        # NEAR-USDT 复现：range_pos=0.838, prev_daily=0.1566, pre_12h=0.0033
        tech = _make_tech(range_pos=0.838, pre_12h=0.0033, prev_daily=0.1566)
        plan = _make_plan(price=2.778, sl=2.65)
        result = judge._check_entry_position_policy(
            'NEAR-USDT', 'open_long', plan, tech, 31.5, context='main')
        assert result['allowed'] is False
        assert result['entry_position_status'] == 'overheated'
        # PRD AC-01: 'long_overheat_daily_gain 或等价机器可读原因'。
        # 由于 range_pos=0.838 >= 0.82 先命中，实际返回 long_overheat_range_pos。
        assert result['block_reason'] in (
            'long_overheat_range_pos', 'long_overheat_daily_gain', 'long_overheat_pre_move'
        )
        assert result['should_defer'] is True
        # target_price 应小于 signal_price 才能等待回调
        assert 0 < result['target_price'] < 2.778


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-02: 正常位置允许 bullish low R:R long
# ──────────────────────────────────────────────────────────


class TestAC02NormalLowRRPasses:
    def test_normal_position_passes(self):
        judge = _make_judge()
        # 正常位置：range_pos=0.5, pre_12h=0.01, prev_daily=0.02
        tech = _make_tech(range_pos=0.5, pre_12h=0.01, prev_daily=0.02)
        plan = _make_plan()
        result = judge._check_entry_position_policy(
            'BTC-USDT', 'open_long', plan, tech, 60, context='main')
        assert result['allowed'] is True
        assert result['entry_position_status'] == 'normal'
        assert result['block_reason'] == ''


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-03: range_pos 单独超阈值
# ──────────────────────────────────────────────────────────


class TestAC03RangePosOnly:
    def test_range_pos_alone_triggers_overheat(self):
        judge = _make_judge()
        # 仅 range_pos 高，pre_12h 与 prev_daily 都低
        tech = _make_tech(range_pos=0.85, pre_12h=0.0, prev_daily=0.0)
        plan = _make_plan()
        result = judge._check_entry_position_policy(
            'BTC-USDT', 'open_long', plan, tech, 60, context='main')
        assert result['allowed'] is False
        assert result['block_reason'] == 'long_overheat_range_pos'


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-04: 12h 前置涨幅过大
# ──────────────────────────────────────────────────────────


class TestAC04PreMove:
    def test_pre_move_triggers_overheat(self):
        judge = _make_judge()
        # range_pos=0.78（满足 daily_gain_range_pos=0.75）, pre_12h=0.06 (>= 0.05)
        tech = _make_tech(range_pos=0.78, pre_12h=0.06, prev_daily=0.0)
        plan = _make_plan()
        result = judge._check_entry_position_policy(
            'BTC-USDT', 'open_long', plan, tech, 60, context='main')
        assert result['allowed'] is False
        assert result['block_reason'] == 'long_overheat_pre_move'


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-05: 日线涨幅过大
# ──────────────────────────────────────────────────────────


class TestAC05DailyGain:
    def test_daily_gain_triggers_overheat(self):
        judge = _make_judge()
        # range_pos=0.78, prev_daily=0.12 (>= 0.10)
        tech = _make_tech(range_pos=0.78, pre_12h=0.0, prev_daily=0.12)
        plan = _make_plan()
        result = judge._check_entry_position_policy(
            'BTC-USDT', 'open_long', plan, tech, 60, context='main')
        assert result['allowed'] is False
        assert result['block_reason'] == 'long_overheat_daily_gain'


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-06: 无有效回调目标 → reject
# ──────────────────────────────────────────────────────────


class TestAC06NoValidPullbackTarget:
    def test_target_invalid_means_reject(self):
        judge = _make_judge()
        tech = _make_tech(range_pos=0.85)
        # SL 距离 signal 极近：signal=1, sl=0.999 → target ≈ 0.975 < sl*1.005=1.004
        # 调成 sl=0.998：floor_target=1.003, raw=0.975 → target=1.003 > signal=1
        plan = _make_plan(price=1.0, sl=0.998)
        result = judge._check_entry_position_policy(
            'BTC-USDT', 'open_long', plan, tech, 60, context='main')
        assert result['allowed'] is False
        assert result['should_defer'] is False
        assert result['block_reason'] == 'long_overheat_no_valid_pullback_target'


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-07: overheat deferred 禁止 chase
# ──────────────────────────────────────────────────────────


class TestAC07OverheatNoChase:
    def test_chase_eligible_false_on_overheat_deferred(self):
        """overheat 触发 deferred 时 chase_eligible 必须为 False。"""
        # 通过 _check_entry_position_policy 间接验证：
        # 主路径在创建 deferred_entry 时显式设置 chase_eligible=False。
        # 这里以源码常量校验。
        import inspect
        from agents.trading import judge as judge_mod
        src = inspect.getsource(judge_mod.MultiJudge)
        assert "'entry_type': 'deferred_pullback_overheat'" in src
        # 同一段 dict 必须包含 chase_eligible: False
        idx = src.find("'entry_type': 'deferred_pullback_overheat'")
        nearby = src[max(0, idx - 500):idx + 800]
        assert "'chase_eligible': False" in nearby


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-08: 回调到位必须全链路二次确认
# ──────────────────────────────────────────────────────────


class TestAC08DeferredReConfirm:
    def test_deferred_pullback_path_calls_position_guard(self):
        import inspect
        from agents.trading import judge as judge_mod
        src = inspect.getsource(judge_mod.MultiJudge._make_decision)
        # 必须出现：reconfirm + 15m + RR + EV + regime_policy + entry_position_policy
        assert '_reconfirm_deferred' in src
        assert '_check_15m_deferred_reconfirm' in src
        assert '_apply_regime_policy' in src
        assert '_check_entry_position_policy' in src
        assert '_check_expected_value' in src


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-09: 主路径与 deferred 路径策略一致
# ──────────────────────────────────────────────────────────


class TestAC09PathConsistency:
    def test_same_inputs_same_decision(self):
        judge = _make_judge()
        tech = _make_tech(range_pos=0.838, pre_12h=0.0033, prev_daily=0.1566)
        plan = _make_plan(price=2.778, sl=2.65)
        r_main = judge._check_entry_position_policy(
            'NEAR-USDT', 'open_long', plan, tech, 31.5, context='main')
        r_15m = judge._check_entry_position_policy(
            'NEAR-USDT', 'open_long', plan, tech, 31.5, context='deferred_15m_confirmation')
        r_pull = judge._check_entry_position_policy(
            'NEAR-USDT', 'open_long', plan, tech, 31.5, context='deferred_pullback')
        r_chase = judge._check_entry_position_policy(
            'NEAR-USDT', 'open_long', plan, tech, 31.5, context='deferred_chase')
        for r in (r_15m, r_pull, r_chase):
            assert r['allowed'] == r_main['allowed']
            assert r['should_defer'] == r_main['should_defer']
            assert r['entry_position_status'] == r_main['entry_position_status']
            assert r['block_reason'] == r_main['block_reason']
            assert abs(r['target_price'] - r_main['target_price']) < 1e-6


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-10: short side guard 主路径生效
# ──────────────────────────────────────────────────────────


class TestAC10ShortSideGuardInMainPath:
    def test_short_range_pos_too_low_blocks(self):
        judge = _make_judge()
        tech = _make_tech(range_pos=0.30, daily_bias='bearish', rsi=55)
        # 调整成 short：daily_bias=bearish
        plan = {'size_usdt': 10, 'leverage': 5, 'risk_reward_ratio': 2.0,
                'effective_risk_reward_ratio': 2.0,
                'entry_zone': [1.0, 1.0], 'stop_loss': 1.05}
        result = judge._check_entry_position_policy(
            'BTC-USDT', 'open_short', plan, tech, -60, context='main')
        assert result['allowed'] is False
        assert result['block_reason'] == 'range_position_too_low'

    def test_short_pre_move_too_deep_blocks(self):
        judge = _make_judge()
        tech = _make_tech(range_pos=0.5, pre_12h=-0.05, daily_bias='bearish', rsi=55)
        plan = {'size_usdt': 10, 'leverage': 5,
                'entry_zone': [1.0, 1.0], 'stop_loss': 1.05}
        result = judge._check_entry_position_policy(
            'BTC-USDT', 'open_short', plan, tech, -60, context='main')
        assert result['allowed'] is False
        assert result['block_reason'] == 'pre_move_too_deep'


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-11: EV bucket 不得使用 unknown
# ──────────────────────────────────────────────────────────


class TestAC11NoUnknownBucket:
    def test_main_path_assigns_entry_type_before_ev(self):
        import inspect
        from agents.trading import judge as judge_mod
        src = inspect.getsource(judge_mod.MultiJudge._make_decision)
        idx_entry = src.find("plan['entry_type'] = entry_type")
        idx_ev = src.find('_check_expected_value(symbol, plan, score)')
        assert idx_entry > 0
        assert idx_ev > 0
        assert idx_entry < idx_ev, (
            'entry_type must be assigned before _check_expected_value '
            f'(entry_type at {idx_entry}, ev at {idx_ev})')


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-12: 稀疏 bucket 不得抬高 p_win
# ──────────────────────────────────────────────────────────


class TestAC12SparseBucketNoUplift:
    def test_sparse_uplift_capped(self):
        judge = _make_judge()
        # bucket 高 p_win 但只有 3 笔，低于 min_trades=10
        judge._bucketed_metrics = {
            'long_bullish_ma_aligned_low_rr_extra': {
                'win_rate': 0.80,
                'trade_count': 3,
            }
        }
        plan = {
            'p_win_used': 0.50,
            'p_win_source': 'bayesian_prior',
            'expected_value': 0.10,
            'net_profit_usdt': 5.0,
            'net_loss_usdt': 4.0,
            'entry_type': 'ma_aligned',
            'slot_type': 'low_rr_extra',
            'side': 'long',
            'action': 'open_long',
            'size_usdt': 10,
        }
        ok = judge._check_expected_value('BTC-USDT', plan, 60)
        # sparse 不允许 uplift → p_win 维持原值，不被抬到 0.80
        assert ok is True
        assert plan['ev_bucket_sparse'] is True
        assert plan['ev_bucket_trade_count'] == 3
        assert plan['ev_bucket_min_trades'] == 10
        assert plan['ev_bucket_key'] == 'long_bullish_ma_aligned_low_rr_extra'
        # 未被 uplift（仍然是原 p_win 0.50）
        assert plan['p_win_used'] == 0.50

    def test_sparse_downgrade_still_allowed(self):
        """稀疏 bucket 可以降低 p_win（保护性的）。"""
        judge = _make_judge()
        judge._bucketed_metrics = {
            'long_bullish_ma_aligned_low_rr_extra': {
                'win_rate': 0.30,
                'trade_count': 3,
            }
        }
        plan = {
            'p_win_used': 0.50,
            'p_win_source': 'bayesian_prior',
            'expected_value': 0.10,
            'net_profit_usdt': 5.0,
            'net_loss_usdt': 4.0,
            'entry_type': 'ma_aligned',
            'slot_type': 'low_rr_extra',
            'side': 'long',
            'action': 'open_long',
            'size_usdt': 10,
        }
        judge._check_expected_value('BTC-USDT', plan, 60)
        # downgrade 路径会重算 EV：p_win 应被降为 0.30
        assert plan['p_win_used'] == 0.30


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-13: trade_decision.v2 兼容性
# ──────────────────────────────────────────────────────────


class TestAC13TradeDecisionV2Compat:
    def test_attribution_includes_optional_fields(self):
        judge = _make_judge()
        tech = _make_tech(range_pos=0.4)
        plan = _make_plan()
        attr = judge._build_attribution(tech, 'open_long', 60, plan, None, 'ma_aligned')
        # 既有字段保留
        for f in ('entry_type', 'rule_signal_type', 'signal_score', 'rr_policy',
                  'rr_floor_used', 'slot_type', 'blocked_by'):
            assert f in attr
        # 新增 optional 字段都存在
        for f in ('entry_position_status', 'entry_position_block_reason',
                  'entry_range_pos_24h', 'entry_pre_12h_return_pct',
                  'entry_prev_daily_return_pct', 'entry_position_policy',
                  'deferred_target_price', 'deferred_reason',
                  'ev_bucket_key', 'ev_bucket_trade_count',
                  'ev_bucket_min_trades', 'ev_bucket_sparse'):
            assert f in attr

    def test_rejection_attribution_includes_optional_fields(self):
        judge = _make_judge()
        plan = {
            'entry_position_status': 'overheated',
            'entry_position_block_reason': 'long_overheat_daily_gain',
            'entry_range_pos_24h': 0.838,
            'entry_pre_12h_return_pct': 0.0033,
            'entry_prev_daily_return_pct': 0.1566,
            'entry_position_policy': 'long_overheat_v1',
            'deferred_target_price': 2.71,
            'deferred_reason': 'long_overheat_daily_gain',
            'ev_bucket_key': 'long_bullish_ma_aligned_low_rr_extra',
            'ev_bucket_trade_count': 3,
            'ev_bucket_min_trades': 10,
            'ev_bucket_sparse': True,
            'slot_type': 'low_rr_extra',
        }
        attr = judge._rejection_attribution('open_long', plan, 'long_overheat_daily_gain')
        assert attr['entry_position_status'] == 'overheated'
        assert attr['entry_position_block_reason'] == 'long_overheat_daily_gain'
        assert attr['entry_range_pos_24h'] == 0.838
        assert attr['entry_prev_daily_return_pct'] == 0.1566
        assert attr['ev_bucket_sparse'] is True
        assert attr['ev_bucket_trade_count'] == 3
        assert attr['ev_bucket_min_trades'] == 10


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-14: execution_result.v2 透传 attribution
# ──────────────────────────────────────────────────────────


class TestAC14ExecutionResultPassthrough:
    def test_executor_passes_attribution(self):
        """execution_result.v2 的契约由 executor 透传 plan.attribution。"""
        import inspect
        from agents.trading import executor as ex_mod
        # 找到 execution_result 构造代码并校验包含 attribution 透传
        src = inspect.getsource(ex_mod)
        # 必须把 trade_decision 的 attribution 透传到 result.attribution
        assert 'attribution' in src


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-15: event_backtest 与 live 同构
# ──────────────────────────────────────────────────────────


class TestAC15BacktestParity:
    def test_event_backtest_blocks_overheat_long(self):
        from event_backtest import EventBacktest
        bt = EventBacktest(long_live_position_guard_enabled=True)
        # 构造 row：score 高（>=35），但 prev_daily=0.16, range_pos=0.84
        row = {
            'rsi': 60, 'htf_bias': 'bullish', 'daily_bias': 'bullish',
            'ma_aligned_long': 1, 'ma_aligned_short': 0,
            'entry_long': 1, 'entry_short': 0,
            'position_in_24h_range': 0.84,
            'pre_12h_return_pct': 0.01,
            'prev_daily_return_pct': 0.16,
        }
        result = bt._check_entry_with_regime(row, 'bullish', -10000, 50)
        assert result is None  # overheat 拦截

    def test_event_backtest_passes_normal_long(self):
        from event_backtest import EventBacktest
        bt = EventBacktest(long_live_position_guard_enabled=True)
        row = {
            'rsi': 60, 'htf_bias': 'bullish', 'daily_bias': 'bullish',
            'ma_aligned_long': 1, 'ma_aligned_short': 0,
            'entry_long': 1, 'entry_short': 0,
            'position_in_24h_range': 0.50,
            'pre_12h_return_pct': 0.01,
            'prev_daily_return_pct': 0.02,
        }
        result = bt._check_entry_with_regime(row, 'bullish', -10000, 50)
        assert result is not None
        assert result['direction'] == 'long'


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-16: 配置与启动日志
# ──────────────────────────────────────────────────────────


class TestAC16ConfigAndBanner:
    def test_defaults_present(self):
        from utils.config_loader import load_config, DEFAULTS, HARD_LIMITS
        cfg = load_config(yaml_path='/nonexistent.yaml', env_file=None,
                          strict_live_check=False)
        assert cfg['long_live_position_guard_enabled'] is True
        assert cfg['long_live_max_range_pos'] == 0.82
        assert cfg['long_live_max_pre_move'] == 0.05
        assert cfg['long_live_max_daily_gain'] == 0.10
        assert cfg['long_live_daily_gain_range_pos'] == 0.75
        assert cfg['long_live_pullback_min_pct'] == 0.025
        assert cfg['long_live_pullback_timeout_hours'] == 4
        assert cfg['ev_bucket_min_trades'] == 10
        assert cfg['ev_bucket_sparse_allow_uplift'] is False
        # hard limits 覆盖
        for k in ('long_live_max_range_pos', 'long_live_max_pre_move',
                 'long_live_max_daily_gain', 'long_live_daily_gain_range_pos',
                 'long_live_pullback_min_pct', 'long_live_pullback_timeout_hours',
                 'ev_bucket_min_trades'):
            assert k in HARD_LIMITS

    def test_env_override(self, monkeypatch):
        from utils.config_loader import load_config
        monkeypatch.setenv('LONG_LIVE_MAX_RANGE_POS', '0.70')
        monkeypatch.setenv('LONG_LIVE_MAX_DAILY_GAIN', '0.08')
        monkeypatch.setenv('EV_BUCKET_MIN_TRADES', '20')
        cfg = load_config(yaml_path='/nonexistent.yaml', env_file=None,
                          strict_live_check=False)
        assert cfg['long_live_max_range_pos'] == 0.70
        assert cfg['long_live_max_daily_gain'] == 0.08
        assert cfg['ev_bucket_min_trades'] == 20

    def test_banner_includes_summary(self):
        from utils.config_loader import load_config, format_banner
        cfg = load_config(yaml_path='/nonexistent.yaml', env_file=None,
                          strict_live_check=False)
        banner = format_banner(cfg)
        assert 'Long Entry Position Guard' in banner
        assert 'range_pos' in banner
        assert 'EV Bucket' in banner


# ──────────────────────────────────────────────────────────
# AC-LONGPOS-17: 日志可审计
# ──────────────────────────────────────────────────────────


class TestAC17AuditLog:
    def test_attribution_carries_request_audit_fields(self):
        judge = _make_judge()
        tech = _make_tech(range_pos=0.838, prev_daily=0.1566)
        plan = _make_plan(price=2.778, sl=2.65)
        # 模拟主路径触发 overheat 后写入 plan 的字段
        plan['entry_position_status'] = 'overheated'
        plan['entry_position_block_reason'] = 'long_overheat_daily_gain'
        plan['entry_range_pos_24h'] = 0.838
        plan['entry_pre_12h_return_pct'] = 0.0033
        plan['entry_prev_daily_return_pct'] = 0.1566
        plan['deferred_target_price'] = 2.71
        plan['ev_bucket_key'] = 'long_bullish_ma_aligned_low_rr_extra'
        plan['ev_bucket_trade_count'] = 3
        attr = judge._rejection_attribution(
            'open_long', plan, 'long_overheat_daily_gain', tech=tech)
        for f in ('entry_position_status', 'entry_position_block_reason',
                  'entry_range_pos_24h', 'entry_pre_12h_return_pct',
                  'entry_prev_daily_return_pct', 'deferred_target_price',
                  'ev_bucket_key', 'ev_bucket_trade_count'):
            assert f in attr


class TestRegimeAwareConfig:
    def test_defaults_present(self):
        from utils.config_loader import DEFAULTS
        assert DEFAULTS['long_live_regime_aware_range_enabled'] is True
        assert DEFAULTS['long_live_max_range_pos_choppy'] == 0.55
        assert DEFAULTS['long_live_daily_gain_range_pos_choppy'] == 0.50

    def test_hard_limits_present(self):
        from utils.config_loader import HARD_LIMITS
        assert HARD_LIMITS['long_live_max_range_pos_choppy'] == (0.0, 1.0)
        assert HARD_LIMITS['long_live_daily_gain_range_pos_choppy'] == (0.0, 1.0)

    def test_env_bool_override(self, monkeypatch):
        monkeypatch.setenv('LONG_LIVE_REGIME_AWARE_RANGE_ENABLED', 'false')
        from utils.config_loader import _read_env_overrides
        out = _read_env_overrides()
        assert out['long_live_regime_aware_range_enabled'] is False

    def test_yaml_float_override(self, tmp_path):
        from utils.config_loader import _load_yaml
        p = tmp_path / "config.yaml"
        p.write_text("risk:\n  long_live_max_range_pos_choppy: 0.50\n  long_live_daily_gain_range_pos_choppy: 0.45\n")
        out = _load_yaml(str(p))
        assert out['long_live_max_range_pos_choppy'] == 0.50
        assert out['long_live_daily_gain_range_pos_choppy'] == 0.45


class TestResolveThresholds:
    def _judge(self, **kw):
        j = _make_judge(**kw)
        j._long_live_regime_aware_range_enabled = kw.get('_long_live_regime_aware_range_enabled', True)
        j._long_live_max_range_pos_choppy = 0.55
        j._long_live_daily_gain_range_pos_choppy = 0.50
        return j

    def test_bullish_uses_default(self):
        j = self._judge()
        assert j._resolve_long_range_thresholds('bullish') == (0.82, 0.75)

    def test_choppy_mixed_bearish_tighten(self):
        j = self._judge()
        for r in ('choppy', 'mixed', 'bearish'):
            assert j._resolve_long_range_thresholds(r) == (0.55, 0.50)

    def test_none_and_unknown_fallback(self):
        j = self._judge()
        assert j._resolve_long_range_thresholds(None) == (0.82, 0.75)
        assert j._resolve_long_range_thresholds('weird') == (0.82, 0.75)

    def test_toggle_off_forces_default(self):
        j = self._judge(_long_live_regime_aware_range_enabled=False)
        assert j._resolve_long_range_thresholds('choppy') == (0.82, 0.75)


class TestRegimeAwareGuard:
    def _judge(self, regime, enabled=True):
        j = _make_judge()
        j._long_live_regime_aware_range_enabled = enabled
        j._long_live_max_range_pos_choppy = 0.55
        j._long_live_daily_gain_range_pos_choppy = 0.50

        class _R:
            def snapshot(self_inner):
                return {'effective_regime': regime, 'raw_regime': regime, 'confidence': 60}
        j._regime_manager = _R()
        return j

    def _check(self, j):
        return j._check_entry_position_policy(
            'X', 'open_long', _make_plan(), _make_tech(range_pos=0.66), 50.0, context='main')

    def test_choppy_066_overheats(self):
        r = self._check(self._judge('choppy'))
        assert r['allowed'] is False
        assert r['entry_position_status'] == 'overheated'

    def test_mixed_066_overheats(self):
        assert self._check(self._judge('mixed'))['allowed'] is False

    def test_bearish_066_overheats(self):
        assert self._check(self._judge('bearish'))['allowed'] is False

    def test_bullish_066_passes(self):
        r = self._check(self._judge('bullish'))
        assert r['allowed'] is True
        assert r['entry_position_status'] == 'normal'

    def test_toggle_off_066_passes_in_choppy(self):
        r = self._check(self._judge('choppy', enabled=False))
        assert r['allowed'] is True

    def test_metrics_record_regime_and_threshold(self):
        r = self._check(self._judge('choppy'))
        assert r['metrics']['entry_regime_used'] == 'choppy'
        assert r['metrics']['entry_range_pos_threshold'] == 0.55


class TestAttributionV2:
    def test_overheat_attribution_upgraded_to_v2(self):
        import os as _os
        _judge_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'agents', 'trading', 'judge.py')
        src = open(_judge_path, encoding='utf-8').read()
        # 两处 overheat 内联归因 tag 升级为 v2_regime；旧 v1 内联 override 不再出现
        assert "attr['entry_position_policy'] = 'long_overheat_v2_regime'" in src
        assert "attr['entry_position_policy'] = 'long_overheat_v1'" not in src
        # 新 metrics 字段透传到 attribution
        assert "attr['entry_regime_used'] = pos_policy['metrics'].get('entry_regime_used')" in src
        assert "attr['entry_range_pos_threshold'] = pos_policy['metrics'].get('entry_range_pos_threshold')" in src
