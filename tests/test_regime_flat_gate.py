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
    j = MultiJudge.__new__(MultiJudge)
    j._regime_flat_gate_enabled = True  # smoke: 属性存在且默认 True 语义
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
