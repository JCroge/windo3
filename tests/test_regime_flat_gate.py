"""tests/test_regime_flat_gate.py

体制空仓硬门(choppy flat gate) 单元测试
Tasks 1–5: config 开关、_compute_directional_evidence helper、
           _classify_regime_flat_gate 全分支、wiring 不变量、attribution 四字段。
"""

# ──────────────────────────────────────────────────────────────
# Task 1: config 开关
# ──────────────────────────────────────────────────────────────
from agents.trading.judge import MultiJudge


def test_flag_default_true():
    # I-1: 真正走 __init__ 默认路径 — config 缺该键 → 属性默认 True
    j = MultiJudge(config={})
    assert j._regime_flat_gate_enabled is True


def test_config_loader_has_flag():
    from utils.config_loader import DEFAULTS
    assert DEFAULTS.get('regime_flat_gate_enabled') is True


# ──────────────────────────────────────────────────────────────
# Task 2: _compute_directional_evidence helper
# ──────────────────────────────────────────────────────────────

def _mk_judge():
    j = MultiJudge.__new__(MultiJudge)
    j._path_evidence_min_strength = 60
    j._path_evidence_min_pre12h_return = 0.03
    j._path_evidence_max_range_pos = 0.92
    j._path_evidence_aligned_enabled = False  # lever1 OFF(现状)
    return j


def test_path_evidence_raw_ungated_true_when_thresholds_met():
    j = _mk_judge()
    tech = {
        "trend": {"direction": "bullish", "strength": 70},
        "entry_context": {"pre_12h_return_pct": 0.05, "position_in_24h_range": 0.6},
    }
    plan = {"side": "long"}
    aligned, pe_raw = j._compute_directional_evidence("open_long", plan, tech, score=60)
    assert pe_raw is True   # ungated: lever1 OFF 仍为 True


def test_path_evidence_raw_false_below_threshold():
    j = _mk_judge()
    tech = {
        "trend": {"direction": "bullish", "strength": 30},  # strength < 60
        "entry_context": {"pre_12h_return_pct": 0.05, "position_in_24h_range": 0.6},
    }
    aligned, pe_raw = j._compute_directional_evidence("open_long", {"side": "long"}, tech, 60)
    assert pe_raw is False


# ──────────────────────────────────────────────────────────────
# Task 3: _classify_regime_flat_gate + _has_directional_thesis 全分支
# ──────────────────────────────────────────────────────────────

class _RM:
    def __init__(self, eff):
        self._eff = eff
        self._effective_regime = eff
        self._raw_regime = eff
        self._confidence = 0.8

    def snapshot(self):
        return {"effective_regime": self._eff, "raw_regime": self._eff, "confidence": 0.8}


def _judge_with(eff, flag=True):
    j = _mk_judge()
    j._regime_flat_gate_enabled = flag
    j._regime_manager = _RM(eff)
    return j


def _long_no_thesis_tech():
    return {
        "trend": {"direction": "neutral", "strength": 20},
        "entry_context": {"pre_12h_return_pct": 0.0, "position_in_24h_range": 0.5},
    }


def test_choppy_neutral_long_rejected():
    j = _judge_with("choppy")
    allow, reason = j._classify_regime_flat_gate(
        "open_long", {"side": "long"}, _long_no_thesis_tech(), 60
    )
    assert allow is False and reason == "regime_flat_no_thesis"


def test_choppy_with_path_evidence_allowed():
    j = _judge_with("choppy")
    tech = {
        "trend": {"direction": "bullish", "strength": 70},
        "entry_context": {"pre_12h_return_pct": 0.05, "position_in_24h_range": 0.6},
    }
    allow, _ = j._classify_regime_flat_gate("open_long", {"side": "long"}, tech, 60)
    assert allow is True


def test_trend_regime_allowed():
    j = _judge_with("bullish")
    allow, _ = j._classify_regime_flat_gate(
        "open_long", {"side": "long"}, _long_no_thesis_tech(), 60
    )
    assert allow is True


def test_mixed_no_thesis_rejected():
    j = _judge_with("mixed")
    allow, _ = j._classify_regime_flat_gate(
        "open_long", {"side": "long"}, _long_no_thesis_tech(), 60
    )
    assert allow is False


def test_open_short_always_allowed_long_only():
    j = _judge_with("choppy")
    allow, _ = j._classify_regime_flat_gate(
        "open_short", {"side": "short"}, _long_no_thesis_tech(), 60
    )
    assert allow is True


def test_flag_off_allows():
    j = _judge_with("choppy", flag=False)
    allow, _ = j._classify_regime_flat_gate(
        "open_long", {"side": "long"}, _long_no_thesis_tech(), 60
    )
    assert allow is True


def test_non_open_allowed():
    j = _judge_with("choppy")
    allow, _ = j._classify_regime_flat_gate(
        "close", {"side": "long"}, _long_no_thesis_tech(), 60
    )
    assert allow is True


# ──────────────────────────────────────────────────────────────
# Task 4: 接入 wiring 不变量（source count 检查）
# ──────────────────────────────────────────────────────────────

import inspect
from agents.trading import judge as J


def test_flat_gate_called_in_all_open_paths():
    src = inspect.getsource(J)
    # 至少 4 处调用(主 + 3 deferred);宽松计数防漏接
    count = src.count("_classify_regime_flat_gate(")
    assert count >= 4, f"Expected >=4 calls to _classify_regime_flat_gate, found {count}"


# ──────────────────────────────────────────────────────────────
# Task 5: attribution 四字段 accept+reject 双写
# ──────────────────────────────────────────────────────────────

def test_rejection_attribution_has_flat_fields():
    j = _judge_with("choppy")
    # 构造拒单 attribution (按 _rejection_attribution 签名;最小可调)
    attr = j._rejection_attribution(
        "open_long", {"side": "long"}, "regime_flat_no_thesis:...", tech=_long_no_thesis_tech()
    )
    assert attr.get("regime_flat_decision") == "reject"
    assert attr.get("has_directional_thesis") is False
    assert "regime_flat" in (attr.get("regime_flat_reason") or "")


# M-1: accept 路径 attribution 也写四字段
def test_build_attribution_has_flat_fields_on_accept():
    j = _judge_with("choppy")
    j._ev_bucket_min_trades = 10
    # 有方向论据的 tech (aligned)，accept 路径
    tech = {
        "trend": {"direction": "bullish", "strength": 70,
                  "higher_tf_bias": "bullish", "daily_bias": "bullish"},
        "momentum": {"rsi": 55},
        "risk": {"liquidity_score": 60},
        "rule_signal": {},
        "entry_timing": {},
        "entry_context": {"pre_12h_return_pct": 0.05, "position_in_24h_range": 0.6},
    }
    plan = {"effective_risk_reward_ratio": 2.0}
    attr = j._build_attribution(tech, "open_long", 60, plan, None, "rule_signal")
    assert attr.get("regime_flat_gate") == "v1"
    assert attr.get("regime_flat_decision") == "allow"
    assert attr.get("has_directional_thesis") is True
    assert attr.get("regime_flat_reason") == ""


# ──────────────────────────────────────────────────────────────
# C-1: _select_rr_floor floor-grant 必须复原全部原始守卫(含 lever1 ON)
# ──────────────────────────────────────────────────────────────

def _mk_rr_judge(eff="choppy", lever1_on=True):
    """构造可调 _select_rr_floor 的 judge: lever1 默认 ON 以验证 floor-grant 守卫。"""
    j = _mk_judge()
    j._path_evidence_aligned_enabled = lever1_on
    j._regime_manager = _RM(eff)
    j._rr_floor_default = 1.50
    j._rr_floor_long_bullish = 1.30
    j._rr_floor_long_aligned_choppy = 1.30
    j._rr_floor_short_bullish = 1.80
    j._probe_rr_floor = 1.30
    j._low_rr_slot_enabled = True
    j._low_rr_long_aligned_enabled = True
    j._short_regime_guard_enabled = True
    j._min_deferred_signal_score = 45
    return j


def _path_evidence_tech(block_long=False):
    """bias 漏报(neutral htf/daily)但三阈值满足 → path_evidence_raw True。"""
    return {
        "trend": {"direction": "bullish", "strength": 70,
                  "higher_tf_bias": "neutral", "daily_bias": "neutral"},
        "entry_timing": {"tf_15m_block_long": block_long},
        "entry_context": {"pre_12h_return_pct": 0.05, "position_in_24h_range": 0.6},
    }


def test_rr_floor_path_evidence_granted_when_lever1_on_and_guards_pass():
    """lever1 ON + not block_long + score>=45 + 三阈值满足 → 授 path_evidence 地板。"""
    j = _mk_rr_judge(lever1_on=True)
    min_rr, policy, _ = j._select_rr_floor(
        "open_long", {}, _path_evidence_tech(block_long=False), score=60
    )
    assert policy == "long_aligned_path_evidence"
    assert min_rr == 1.30


def test_rr_floor_path_evidence_blocked_when_block_long_true():
    """C-1: lever1 ON + block_long True + 三阈值满足 → 不授地板(回退 default)。"""
    j = _mk_rr_judge(lever1_on=True)
    min_rr, policy, _ = j._select_rr_floor(
        "open_long", {}, _path_evidence_tech(block_long=True), score=60
    )
    assert policy == "default"
    assert min_rr == 1.50


def test_rr_floor_path_evidence_blocked_when_score_below_threshold():
    """C-1: lever1 ON + score<45 + 三阈值满足 → 不授地板(回退 default)。"""
    j = _mk_rr_judge(lever1_on=True)
    min_rr, policy, _ = j._select_rr_floor(
        "open_long", {}, _path_evidence_tech(block_long=False), score=30
    )
    assert policy == "default"
    assert min_rr == 1.50


def test_rr_floor_path_evidence_blocked_when_lever1_off():
    """lever1 OFF(现状) → 即使三阈值满足也不授 path_evidence 地板。"""
    j = _mk_rr_judge(lever1_on=False)
    min_rr, policy, _ = j._select_rr_floor(
        "open_long", {}, _path_evidence_tech(block_long=False), score=60
    )
    assert policy == "default"
    assert min_rr == 1.50


# ──────────────────────────────────────────────────────────────
# Task 6: event_backtest 同构硬门
# ──────────────────────────────────────────────────────────────

from event_backtest import EventBacktest


def _mk_row(regime='choppy', htf_bias='neutral', score_long=True):
    """构造最小 row dict，满足 _check_entry_with_regime 所需字段。
    score_long=True → entry_long=1 (score ≥ 35)；rsi=50 避免 RSI 压制。
    """
    return {
        'entry_long': 1 if score_long else 0,
        'entry_short': 0,
        'ma_aligned_long': 0,
        'ma_aligned_short': 0,
        'rsi': 50,
        'htf_bias': htf_bias,
        'daily_bias': 'neutral',
        'regime': regime,
        'position_in_24h_range': 0.5,
        'pre_12h_return_pct': 0.0,
        'prev_daily_return_pct': 0.0,
    }


def _mk_eb(regime_flat_gate_enabled=True):
    """构造关了 Long Position Guard 的 EventBacktest，只测 regime-flat gate。"""
    return EventBacktest(
        long_live_position_guard_enabled=False,
        regime_flat_gate_enabled=regime_flat_gate_enabled,
    )


def test_event_backtest_flat_gate_rejects_choppy_no_thesis():
    """choppy + 无方向论据(htf_bias neutral) → 拒入 long。"""
    eb = _mk_eb()
    row = _mk_row(regime='choppy', htf_bias='neutral')
    result = eb._check_entry_with_regime(row, regime='choppy', last_probe_sl_idx=-9999, current_idx=0)
    assert result is None, f"Expected None (gate reject), got {result}"


def test_event_backtest_flat_gate_rejects_mixed_no_thesis():
    """mixed + 无方向论据(htf_bias bearish) → 拒入 long。"""
    eb = _mk_eb()
    row = _mk_row(regime='mixed', htf_bias='bearish')
    result = eb._check_entry_with_regime(row, regime='mixed', last_probe_sl_idx=-9999, current_idx=0)
    assert result is None, f"Expected None (gate reject mixed), got {result}"


def test_event_backtest_flat_gate_allows_choppy_aligned():
    """choppy + htf_bias=bullish (aligned thesis) → 放行 long。"""
    eb = _mk_eb()
    row = _mk_row(regime='choppy', htf_bias='bullish')
    result = eb._check_entry_with_regime(row, regime='choppy', last_probe_sl_idx=-9999, current_idx=0)
    assert result is not None and result['direction'] == 'long', \
        f"Expected long allow, got {result}"


def test_event_backtest_flat_gate_allows_bullish_regime():
    """bullish 体制（非 choppy/mixed）→ gate 不干预，正常放行。"""
    eb = _mk_eb()
    row = _mk_row(regime='bullish', htf_bias='neutral')
    result = eb._check_entry_with_regime(row, regime='bullish', last_probe_sl_idx=-9999, current_idx=0)
    assert result is not None and result['direction'] == 'long', \
        f"Expected long allow in bullish, got {result}"


def test_event_backtest_flat_gate_flag_off_allows_choppy_no_thesis():
    """regime_flat_gate_enabled=False → choppy+无论据仍放行（A/B 对照臂）。"""
    eb = _mk_eb(regime_flat_gate_enabled=False)
    row = _mk_row(regime='choppy', htf_bias='neutral')
    result = eb._check_entry_with_regime(row, regime='choppy', last_probe_sl_idx=-9999, current_idx=0)
    assert result is not None and result['direction'] == 'long', \
        f"Expected long allow with gate off, got {result}"
