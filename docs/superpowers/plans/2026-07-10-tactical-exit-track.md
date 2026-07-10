---
change: add-tactical-exit-track
design-doc: docs/superpowers/specs/2026-07-10-tactical-exit-track-design.md
base-ref: f4511f5789040e3df0789c3bcc13122b5ebf1324
---

# Tactical Exit Track Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class Tactical trading/exit track with Main Trend quality gating, Tactical R:R/EV, capped-risk exits, independent risk controls, and separated metrics.

**Architecture:** Extend the current Judge -> Executor -> Reviewer flow rather than adding a second executor. Judge classifies `track=main|tactical|shadow_only|reject`, Executor branches by `position["track"]` for local exit lifecycle, and downstream ledgers/replay carry `track` and `exit_profile` metadata.

**Tech Stack:** Python, pytest, existing `MultiJudge`, `CandidateRanker`, `ContractExecutor`, `TradingExecutor`, `Reviewer`, `CounterfactualLedger`, OpenSpec/Comet artifacts.

---

## File Structure

- Modify `utils/config_loader.py`: Tactical flags, limits, and env mappings.
- Modify `agents/trading/judge.py`: Main quality gate, Tactical classifier, Tactical plan math, slot/risk gate integration, attribution.
- Modify `utils/candidate_ranker.py`: Tactical slot accounting and Tactical ranking score inputs.
- Modify `executor.py`: Persist Tactical fields and branch local trailing/TP checks by `track`.
- Modify `agents/trading/executor.py`: No-add enforcement, Tactical close/reduce reason propagation, PnL metadata propagation.
- Modify `agents/trading/portfolio_risk_guard.py`: Tactical daily loss/concurrency/circuit state.
- Modify `agents/trading/reviewer.py`: Persist `track`, `exit_profile`, Tactical close reason, and segmented metrics.
- Modify `utils/counterfactual_ledger.py`: Record Tactical metadata for rejected/shadow candidates.
- Modify `utils/counterfactual_pnl.py`: Add Tactical max-hold/TP/SL resolver mode.
- Modify `utils/decision_replay.py`: Install Tactical config flags and preserve track metadata in replay.
- Create `test_tactical_track_classifier.py`: Main quality and WLD-like classifier tests.
- Create `test_tactical_plan_math.py`: Tactical stop, sizing, R:R, EV, cost gate tests.
- Create `test_tactical_risk_governor.py`: Daily loss, concurrency, loss streak, quality breaker tests.
- Create `test_tactical_exit_lifecycle.py`: Tactical TP/protect/invalidation/max-hold tests.
- Create `test_tactical_metadata_flow.py`: Decision -> executor -> PnL -> reviewer metadata tests.
- Create `tests/fixtures/wld_tactical_20260710.json`: compact WLD replay fixture.
- Create `tests/test_tactical_wld_replay.py`: WLD-like replay assertions.

## Task 1: Tactical Config and Metadata Defaults

**Files:**
- Modify: `utils/config_loader.py`
- Modify: `agents/trading/judge.py`
- Test: `test_tactical_metadata_flow.py`

- [ ] **Step 1: Write failing config/default tests**

Add this to `test_tactical_metadata_flow.py`:

```python
import os
from unittest.mock import patch


def test_tactical_config_defaults_are_present():
    from utils.config_loader import DEFAULTS

    assert DEFAULTS["tactical_track_enabled"] is False
    assert DEFAULTS["tactical_shadow_only"] is True
    assert DEFAULTS["main_quality_gate_enabled"] is True
    assert DEFAULTS["main_quality_min_provenance"] == 0.20
    assert DEFAULTS["main_quality_block_llm_reversal"] is True
    assert DEFAULTS["tactical_max_leverage"] == 5
    assert DEFAULTS["tactical_default_position_pct"] == 0.70
    assert DEFAULTS["tactical_max_hold_minutes"] == 90
    assert DEFAULTS["tactical_daily_loss_limit_usdt"] == -10.0


def test_tactical_env_overrides_are_loaded(monkeypatch):
    monkeypatch.setenv("TACTICAL_TRACK_ENABLED", "true")
    monkeypatch.setenv("TACTICAL_SHADOW_ONLY", "false")
    monkeypatch.setenv("TACTICAL_MAX_LEVERAGE", "4")
    monkeypatch.setenv("TACTICAL_DEFAULT_POSITION_PCT", "0.5")

    from utils.config_loader import load_config
    cfg = load_config()

    assert cfg["tactical_track_enabled"] is True
    assert cfg["tactical_shadow_only"] is False
    assert cfg["tactical_max_leverage"] == 4
    assert cfg["tactical_default_position_pct"] == 0.5
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest test_tactical_metadata_flow.py::test_tactical_config_defaults_are_present test_tactical_metadata_flow.py::test_tactical_env_overrides_are_loaded -q
```

Expected: failure because config keys are absent.

- [ ] **Step 3: Add config defaults and env mappings**

In `utils/config_loader.py`, add defaults:

```python
"tactical_track_enabled": False,
"tactical_shadow_only": True,
"main_quality_gate_enabled": True,
"main_quality_min_provenance": 0.20,
"main_quality_block_llm_reversal": True,
"main_quality_allow_mixed_override": False,
"main_quality_require_volume_or_oi": True,
"tactical_max_leverage": 5,
"tactical_default_position_pct": 0.70,
"tactical_very_near_position_pct": 1.00,
"tactical_stop_cap_r_main": 0.60,
"tactical_very_near_stop_r_main": 0.40,
"tactical_tp1_r": 0.60,
"tactical_cost_coverage_min": 4.0,
"tactical_max_hold_minutes": 90,
"tactical_weakened_no_progress_min_minutes": 30,
"tactical_weakened_no_progress_max_minutes": 45,
"tactical_daily_loss_limit_usdt": -10.0,
"tactical_loss_streak_pause_count": 3,
"tactical_loss_streak_pause_minutes": 60,
"tactical_quality_window_trades": 20,
"tactical_success_window_trades": 30,
"tactical_success_min_win_rate": 0.55,
"tactical_success_min_profit_factor": 1.2,
```

Add env mappings:

```python
"TACTICAL_TRACK_ENABLED": ("tactical_track_enabled", _to_bool),
"TACTICAL_SHADOW_ONLY": ("tactical_shadow_only", _to_bool),
"MAIN_QUALITY_GATE_ENABLED": ("main_quality_gate_enabled", _to_bool),
"MAIN_QUALITY_MIN_PROVENANCE": ("main_quality_min_provenance", float),
"MAIN_QUALITY_BLOCK_LLM_REVERSAL": ("main_quality_block_llm_reversal", _to_bool),
"MAIN_QUALITY_ALLOW_MIXED_OVERRIDE": ("main_quality_allow_mixed_override", _to_bool),
"TACTICAL_MAX_LEVERAGE": ("tactical_max_leverage", int),
"TACTICAL_DEFAULT_POSITION_PCT": ("tactical_default_position_pct", float),
"TACTICAL_MAX_HOLD_MINUTES": ("tactical_max_hold_minutes", int),
"TACTICAL_DAILY_LOSS_LIMIT_USDT": ("tactical_daily_loss_limit_usdt", float),
```

If this file has validation ranges, add ranges that match the defaults:

```python
"tactical_max_leverage": (1, 10),
"tactical_default_position_pct": (0.1, 1.0),
"main_quality_min_provenance": (0.0, 1.0),
```

- [ ] **Step 4: Initialize Judge config fields**

In `agents/trading/judge.py::__init__`, assign matching private fields:

```python
self._tactical_track_enabled = config.get("tactical_track_enabled", False) if config else False
self._tactical_shadow_only = config.get("tactical_shadow_only", True) if config else True
self._main_quality_gate_enabled = config.get("main_quality_gate_enabled", True) if config else True
self._main_quality_min_provenance = config.get("main_quality_min_provenance", 0.20) if config else 0.20
self._main_quality_block_llm_reversal = config.get("main_quality_block_llm_reversal", True) if config else True
self._main_quality_allow_mixed_override = config.get("main_quality_allow_mixed_override", False) if config else False
self._main_quality_require_volume_or_oi = config.get("main_quality_require_volume_or_oi", True) if config else True
self._tactical_max_leverage = config.get("tactical_max_leverage", 5) if config else 5
self._tactical_default_position_pct = config.get("tactical_default_position_pct", 0.70) if config else 0.70
self._tactical_very_near_position_pct = config.get("tactical_very_near_position_pct", 1.00) if config else 1.00
self._tactical_stop_cap_r_main = config.get("tactical_stop_cap_r_main", 0.60) if config else 0.60
self._tactical_very_near_stop_r_main = config.get("tactical_very_near_stop_r_main", 0.40) if config else 0.40
self._tactical_tp1_r = config.get("tactical_tp1_r", 0.60) if config else 0.60
self._tactical_cost_coverage_min = config.get("tactical_cost_coverage_min", 4.0) if config else 4.0
self._tactical_max_hold_minutes = config.get("tactical_max_hold_minutes", 90) if config else 90
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
pytest test_tactical_metadata_flow.py::test_tactical_config_defaults_are_present test_tactical_metadata_flow.py::test_tactical_env_overrides_are_loaded -q
```

Expected: both tests pass.

Commit:

```bash
git add utils/config_loader.py agents/trading/judge.py test_tactical_metadata_flow.py
git commit -m "feat: add tactical track config defaults"
```

## Task 2: Main Quality Gate and Track Classifier

**Files:**
- Modify: `agents/trading/judge.py`
- Test: `test_tactical_track_classifier.py`

- [ ] **Step 1: Write failing classifier tests**

Create `test_tactical_track_classifier.py`:

```python
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def make_judge():
    with patch.dict(os.environ, {
        "OKX_API_KEY": "test",
        "OKX_SECRET": "test",
        "OKX_PASSPHRASE": "test",
    }):
        from agents.trading.judge import MultiJudge
        j = MultiJudge.__new__(MultiJudge)
        j.logger = MagicMock()
        j._tactical_track_enabled = True
        j._tactical_shadow_only = False
        j._main_quality_gate_enabled = True
        j._main_quality_min_provenance = 0.20
        j._main_quality_block_llm_reversal = True
        j._main_quality_allow_mixed_override = False
        j._main_quality_require_volume_or_oi = True
        j._tactical_max_leverage = 5
        j._tactical_default_position_pct = 0.70
        j._tactical_very_near_position_pct = 1.00
        j._tactical_stop_cap_r_main = 0.60
        j._tactical_very_near_stop_r_main = 0.40
        j._tactical_tp1_r = 0.60
        j._tactical_cost_coverage_min = 4.0

        class Regime:
            _effective_regime = "mixed"
            def snapshot(self):
                return {"effective_regime": "mixed", "raw_regime": "mixed", "confidence": 50}
        j._regime_manager = Regime()
        return j


def strong_short_tech():
    return {
        "trend": {
            "direction": "bearish",
            "higher_tf_bias": "bearish",
            "daily_bias": "bearish",
            "strength": 75,
        },
        "entry_timing": {
            "tf_15m_bias": "bearish",
            "tf_15m_entry_status": "confirmed",
            "tf_15m_block_short": False,
        },
        "momentum": {"volume_ratio": 1.4, "atr_pct": 0.01},
        "market": {"oi_1h_change_pct": -0.002},
        "risk": {"liquidity_score": 50},
    }


def base_plan():
    return {
        "side": "short",
        "entry_ref": 0.385,
        "entry_zone": [0.3848, 0.3852],
        "stop_loss": 0.394,
        "take_profit": [0.367, 0.358, 0.349],
        "leverage": 20,
        "size_usdt": 30.0,
        "effective_risk_reward_ratio": 1.55,
        "effective_rr_ladder": 1.55,
        "effective_rr_tp1": 1.31,
        "net_profit_usdt": 21.3,
        "net_loss_usdt": 16.2,
    }


def test_clean_aligned_candidate_stays_main():
    judge = make_judge()
    tech = strong_short_tech()
    plan = base_plan()
    llm = {"risk_warnings": [], "reasoning": ""}
    attribution = {"provenance": {"weakest_confidence": 0.45}}

    decision = judge._classify_track("WLD-USDT", "open_short", plan, tech, -70, llm, attribution)

    assert decision["track"] == "main"
    assert decision["exit_profile"] == "trend_runner"


def test_wld_like_aligned_but_weak_candidate_is_not_main():
    judge = make_judge()
    tech = strong_short_tech()
    tech["momentum"]["volume_ratio"] = 0.41
    llm = {
        "reasoning": "趋势强度=100可能处于趋势末期，追空存在反弹风险",
        "risk_warnings": ["趋势末期追空风险"],
    }
    attribution = {
        "llm_short_reversal_risk": True,
        "provenance": {"weakest_confidence": 0.03},
    }

    decision = judge._classify_track("WLD-USDT", "open_short", base_plan(), tech, -58, llm, attribution)

    assert decision["track"] in ("tactical", "shadow_only", "reject")
    assert decision["track"] != "main"
    assert "main_quality" in decision["reason"]


def test_15m_opposing_block_is_hard_veto_not_tactical():
    judge = make_judge()
    tech = strong_short_tech()
    tech["entry_timing"]["tf_15m_block_short"] = True

    decision = judge._classify_track("WLD-USDT", "open_short", base_plan(), tech, -58, {}, {})

    assert decision["track"] == "reject"
    assert decision["exit_profile"] == "none"
    assert decision["reason"] == "15m_opposing_block"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest test_tactical_track_classifier.py -q
```

Expected: failure because `_classify_track` does not exist.

- [ ] **Step 3: Implement quality gate helpers**

In `agents/trading/judge.py`, add helpers near existing regime/entry policy helpers:

```python
def _extract_provenance_confidence(self, attribution: dict) -> float:
    prov = (attribution or {}).get("provenance") or {}
    val = prov.get("weakest_confidence")
    return 1.0 if val is None else float(val)


def _has_trend_exhaustion_warning(self, llm_result: dict) -> bool:
    text = " ".join([
        str((llm_result or {}).get("reasoning", "")),
        " ".join(str(x) for x in (llm_result or {}).get("risk_warnings", []) or []),
    ])
    return any(k in text for k in ("趋势末期", "追空风险", "追多风险", "反弹风险", "回撤风险"))


def _volume_or_oi_confirmed(self, tech: dict, action: str) -> bool:
    momentum = (tech or {}).get("momentum", {}) or {}
    market = (tech or {}).get("market", {}) or {}
    volume_ratio = float(momentum.get("volume_ratio", 0) or 0)
    oi_change = market.get("oi_1h_change_pct")
    if volume_ratio >= 1.0:
        return True
    if oi_change is None:
        return False
    return abs(float(oi_change)) >= 0.001


def _directionally_aligned(self, action: str, tech: dict) -> bool:
    trend = (tech or {}).get("trend", {}) or {}
    timing = (tech or {}).get("entry_timing", {}) or {}
    expected = "bullish" if action == "open_long" else "bearish"
    return (
        trend.get("higher_tf_bias") == expected
        and trend.get("daily_bias") == expected
        and timing.get("tf_15m_bias", expected) != ("bearish" if expected == "bullish" else "bullish")
    )


def _passes_main_trend_quality(self, action: str, tech: dict, llm_result: dict, attribution: dict) -> dict:
    if not getattr(self, "_main_quality_gate_enabled", True):
        return {"passed": True, "reason": "quality_gate_disabled", "flags": {}}

    flags = {}
    regime = self._regime_manager.snapshot().get("effective_regime", "mixed")
    expected_regime = "bullish" if action == "open_long" else "bearish"
    flags["regime"] = regime
    if regime != expected_regime and not getattr(self, "_main_quality_allow_mixed_override", False):
        flags["regime_weak"] = True

    if getattr(self, "_main_quality_block_llm_reversal", True) and (attribution or {}).get("llm_short_reversal_risk"):
        flags["llm_reversal_risk"] = True

    if self._has_trend_exhaustion_warning(llm_result):
        flags["trend_exhaustion_warning"] = True

    if getattr(self, "_main_quality_require_volume_or_oi", True) and not self._volume_or_oi_confirmed(tech, action):
        flags["weak_volume_oi"] = True

    prov = self._extract_provenance_confidence(attribution)
    flags["provenance"] = prov
    if prov < getattr(self, "_main_quality_min_provenance", 0.20):
        flags["weak_provenance"] = True

    blockers = [k for k, v in flags.items() if k != "provenance" and v is True]
    return {
        "passed": not blockers,
        "reason": "pass" if not blockers else "main_quality_failed:" + ",".join(blockers),
        "flags": flags,
    }


def _tactical_hard_veto_reason(self, symbol: str, action: str, plan: dict, tech: dict,
                               attribution: dict = None) -> str:
    timing = (tech or {}).get("entry_timing", {}) or {}
    if action == "open_short" and timing.get("tf_15m_block_short"):
        return "15m_opposing_block"
    if action == "open_long" and timing.get("tf_15m_block_long"):
        return "15m_opposing_block"

    attr = attribution or {}
    blocked_by = attr.get("blocked_by") or attr.get("reject_reason") or ""
    if action == "open_short" and (
        attr.get("short_gate_rejected")
        or attr.get("short_structural_gate") == "reject"
        or blocked_by in {"short_side_guard", "short_structural_guard", "daily_bias_block"}
    ):
        return "short_structural_hard_veto"

    if (plan or {}).get("same_symbol_position") or attr.get("same_symbol_position"):
        return "same_symbol_stack_block"

    return ""
```

- [ ] **Step 4: Implement `_classify_track`**

Add:

```python
def _classify_track(self, symbol: str, action: str, plan: dict, tech: dict,
                    score: float, llm_result: dict, attribution: dict = None) -> dict:
    if action not in ("open_long", "open_short"):
        return {"track": "none", "exit_profile": "none", "reason": "not_open", "quality_flags": {}}

    if not getattr(self, "_tactical_track_enabled", False):
        return {"track": "main", "exit_profile": "trend_runner", "reason": "tactical_disabled", "quality_flags": {}}

    hard_veto = self._tactical_hard_veto_reason(symbol, action, plan, tech, attribution or {})
    if hard_veto:
        return {"track": "reject", "exit_profile": "none", "reason": hard_veto, "quality_flags": {}}

    if not self._directionally_aligned(action, tech):
        return {"track": "shadow_only", "exit_profile": "none", "reason": "direction_not_aligned", "quality_flags": {}}

    quality = self._passes_main_trend_quality(action, tech, llm_result, attribution or {})
    if quality["passed"]:
        return {"track": "main", "exit_profile": "trend_runner", "reason": "main_quality_pass", "quality_flags": quality["flags"]}

    prov = self._extract_provenance_confidence(attribution or {})
    if prov < getattr(self, "_main_quality_min_provenance", 0.20) / 2:
        return {"track": "shadow_only", "exit_profile": "none", "reason": quality["reason"] + ":shadow_only", "quality_flags": quality["flags"]}

    return {"track": "tactical", "exit_profile": "tactical_v1", "reason": quality["reason"], "quality_flags": quality["flags"]}
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
pytest test_tactical_track_classifier.py -q
```

Expected: tests pass.

Commit:

```bash
git add agents/trading/judge.py test_tactical_track_classifier.py
git commit -m "feat: add tactical track classifier"
```

## Task 3: Tactical Plan Math and R:R Isolation

**Files:**
- Modify: `agents/trading/judge.py`
- Test: `test_tactical_plan_math.py`

- [ ] **Step 1: Write failing plan math tests**

Create `test_tactical_plan_math.py`:

```python
from test_tactical_track_classifier import make_judge, base_plan, strong_short_tech


def test_tactical_profile_recalculates_stop_size_and_rr():
    judge = make_judge()
    plan = base_plan()
    out = judge._apply_tactical_profile(plan.copy(), strong_short_tech(), {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "reason": "main_quality_failed:weak_volume_oi",
    })

    assert out["track"] == "tactical"
    assert out["exit_profile"] == "tactical_v1"
    assert out["slot_type"] == "tactical"
    assert out["leverage"] == 5
    assert out["size_usdt"] == 21.0
    assert out["main_diagnostic_effective_rr"] == 1.55
    assert out["tactical_effective_rr"] > 0
    assert out["effective_risk_reward_ratio"] == out["tactical_effective_rr"]
    assert out["take_profit"][0] > 0.367


def test_tactical_profile_does_not_use_main_ladder_rr_to_pass_cost_gate():
    judge = make_judge()
    plan = base_plan()
    plan["take_profit"] = [0.3847, 0.36, 0.35]
    out = judge._apply_tactical_profile(plan.copy(), strong_short_tech(), {
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "reason": "main_quality_failed:weak_volume_oi",
    })

    assert out["track"] == "shadow_only"
    assert out["tactical_cost_gate"] == "fail"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest test_tactical_plan_math.py -q
```

Expected: failure because `_apply_tactical_profile` does not exist.

- [ ] **Step 3: Implement Tactical profile helper**

In `agents/trading/judge.py`, add:

```python
def _apply_tactical_profile(self, plan: dict, tech: dict, track_decision: dict) -> dict:
    plan = dict(plan or {})
    side = plan.get("side", "long")
    is_short = side == "short"
    entry = float(plan.get("entry_ref") or (plan.get("entry_zone") or [0])[0])
    main_sl = float(plan.get("stop_loss", entry))
    main_r_abs = abs(main_sl - entry)
    if entry <= 0 or main_r_abs <= 0:
        plan.update({"track": "shadow_only", "exit_profile": "none", "tactical_reject_reason": "invalid_main_r"})
        return plan

    stop_cap = main_r_abs * getattr(self, "_tactical_stop_cap_r_main", 0.60)
    tactical_sl = entry + stop_cap if is_short else entry - stop_cap
    tp_dist = stop_cap * getattr(self, "_tactical_tp1_r", 0.60)
    tactical_tp1 = entry - tp_dist if is_short else entry + tp_dist

    size_pct = getattr(self, "_tactical_default_position_pct", 0.70)
    very_near = stop_cap <= main_r_abs * getattr(self, "_tactical_very_near_stop_r_main", 0.40)
    if very_near:
        size_pct = getattr(self, "_tactical_very_near_position_pct", 1.00)

    main_size = float(plan.get("size_usdt", 0) or 0)
    tactical_size = round(main_size * size_pct, 2)
    max_lev = getattr(self, "_tactical_max_leverage", 5)

    main_rr = plan.get("effective_risk_reward_ratio", plan.get("risk_reward_ratio", 0))
    gross_profit = tactical_size * max_lev * (tp_dist / entry)
    gross_loss = tactical_size * max_lev * (stop_cap / entry)
    total_cost = max(0.0, float(plan.get("funding_cost", 0) or 0)) + max(0.06, tactical_size * max_lev * 0.002)
    net_profit = gross_profit - total_cost
    net_loss = gross_loss + total_cost
    tactical_rr = round(net_profit / net_loss, 2) if net_loss > 0 else 0.0
    coverage = (net_profit / total_cost) if total_cost > 0 else 0.0
    cost_pass = net_profit > 0 and coverage >= getattr(self, "_tactical_cost_coverage_min", 4.0)

    plan.update({
        "track": "tactical" if cost_pass else "shadow_only",
        "exit_profile": "tactical_v1" if cost_pass else "none",
        "slot_type": "tactical" if cost_pass else plan.get("slot_type", "main"),
        "tactical_source": track_decision.get("reason", "unknown"),
        "main_diagnostic_effective_rr": main_rr,
        "tactical_effective_rr": tactical_rr,
        "effective_risk_reward_ratio": tactical_rr,
        "tactical_expected_value": round(net_profit * 0.55 - net_loss * 0.45, 4),
        "tactical_cost_gate": "pass" if cost_pass else "fail",
        "tactical_cost_coverage": round(coverage, 2),
        "stop_loss": round(tactical_sl, 6),
        "take_profit": [round(tactical_tp1, 6)],
        "leverage": min(int(plan.get("leverage", max_lev)), max_lev),
        "size_usdt": tactical_size,
        "max_holding_minutes": getattr(self, "_tactical_max_hold_minutes", 90),
        "tactical_stop_quality": "very_near" if very_near else "normal",
    })
    return plan
```

- [ ] **Step 4: Integrate classifier and Tactical profile before final publish**

In the Judge open path, after attribution is built and before final rank/slot publication, call:

```python
track_decision = self._classify_track(symbol, final_action, plan, tech, score, llm_result, attribution)
if track_decision["track"] == "tactical":
    plan = self._apply_tactical_profile(plan, tech, track_decision)
elif track_decision["track"] in ("shadow_only", "reject"):
    self._record_rejected_plan(symbol, final_action, plan, score, final_conf, track_decision["reason"], attribution)
    decision = {
        "symbol": symbol,
        "timestamp": time.time(),
        "action": "hold",
        "confidence": 0,
        "plan": None,
        "size_pct": 0,
        "reasoning": track_decision["reason"],
        "key_factors": ["track_classifier:shadow_only"],
        "risk_warnings": [track_decision["reason"]],
        "attribution": {**attribution, "track": track_decision["track"], "exit_profile": track_decision["exit_profile"]},
    }
    await self.publish("trade_decision", decision, symbol=symbol)
    return
else:
    plan["track"] = "main"
    plan["exit_profile"] = "trend_runner"
```

Ensure final attribution copies `track` and `exit_profile`.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
pytest test_tactical_track_classifier.py test_tactical_plan_math.py -q
```

Expected: all tests pass.

Commit:

```bash
git add agents/trading/judge.py test_tactical_track_classifier.py test_tactical_plan_math.py
git commit -m "feat: add tactical plan math"
```

## Task 4: Tactical Slot and Risk Governor

**Files:**
- Modify: `utils/candidate_ranker.py`
- Modify: `agents/trading/judge.py`
- Modify: `agents/trading/portfolio_risk_guard.py`
- Test: `test_tactical_risk_governor.py`
- Test: `test_ranking_slots.py`

- [ ] **Step 1: Write failing governor tests**

Create `test_tactical_risk_governor.py`:

```python
from unittest.mock import MagicMock


def make_guard():
    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard
    g = PortfolioRiskGuard.__new__(PortfolioRiskGuard)
    g.logger = MagicMock()
    g._positions = {}
    g._tactical_daily_pnl = 0.0
    g._tactical_loss_streak = 0
    g._tactical_pause_until = 0
    g._tactical_daily_loss_limit_usdt = -10.0
    g._tactical_loss_streak_pause_count = 3
    g._tactical_max_concurrent_calm = 2
    g._tactical_max_concurrent_high_vol = 1
    return g


def test_tactical_daily_loss_blocks_new_open():
    g = make_guard()
    g._tactical_daily_pnl = -10.0
    allowed, reason = g.can_open_tactical("WLD-USDT", {"track": "tactical"}, {"volatility": "calm"})
    assert allowed is False
    assert reason == "tactical_daily_loss_limit"


def test_tactical_concurrency_high_vol_caps_at_one():
    g = make_guard()
    g._positions = {"WLD-USDT": {"track": "tactical"}}
    allowed, reason = g.can_open_tactical("ETH-USDT", {"track": "tactical"}, {"volatility": "high"})
    assert allowed is False
    assert reason == "tactical_concurrency_full"


def test_three_tactical_losses_pause_track():
    g = make_guard()
    g.record_tactical_close("A-USDT", -1.0, "tactical_sl", {})
    g.record_tactical_close("B-USDT", -1.0, "tactical_sl", {})
    g.record_tactical_close("C-USDT", -1.0, "tactical_sl", {})
    assert g._tactical_loss_streak == 3
    assert g._tactical_pause_until > 0
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest test_tactical_risk_governor.py -q
```

Expected: failure because governor methods do not exist.

- [ ] **Step 3: Add Tactical slot to ranker**

In `utils/candidate_ranker.py`, include `tactical_slot` constructor arg defaulting to `1`, count tactical occupancy, and treat `plan["slot_type"] == "tactical"` as its own capped slot. Add a test in `test_ranking_slots.py`:

```python
def test_ranker_limits_tactical_slot():
    from utils.candidate_ranker import CandidateRanker

    ranker = CandidateRanker(max_slots=3, enabled=True, tactical_slot=1)
    for sym in ("A-USDT", "B-USDT"):
        ranker.add_candidate({
            "symbol": sym,
            "action": "open_short",
            "score": -70,
            "plan": {"slot_type": "tactical", "effective_risk_reward_ratio": 1.2},
            "tech": {},
            "decision": {"symbol": sym, "action": "open_short"},
        })
    selected, rejected = ranker.rank_and_select(set(), {"main": 0, "tactical": 0})
    assert len(selected) == 1
    assert len(rejected) == 1
```

- [ ] **Step 4: Add governor methods**

In `agents/trading/portfolio_risk_guard.py`, add methods:

```python
def _tactical_open_count(self) -> int:
    return sum(1 for p in self._positions.values() if p.get("track") == "tactical")


def can_open_tactical(self, symbol: str, plan: dict, market_state: dict):
    import time
    now = time.time()
    if now < getattr(self, "_tactical_pause_until", 0):
        return False, "tactical_paused"
    if getattr(self, "_tactical_daily_pnl", 0.0) <= getattr(self, "_tactical_daily_loss_limit_usdt", -10.0):
        return False, "tactical_daily_loss_limit"
    volatility = (market_state or {}).get("volatility", "calm")
    cap = 1 if volatility == "high" else 2
    if self._tactical_open_count() >= cap:
        return False, "tactical_concurrency_full"
    return True, "ok"


def record_tactical_close(self, symbol: str, pnl: float, close_reason: str, event: dict):
    import time
    self._tactical_daily_pnl = getattr(self, "_tactical_daily_pnl", 0.0) + float(pnl or 0.0)
    if pnl < 0:
        self._tactical_loss_streak = getattr(self, "_tactical_loss_streak", 0) + 1
    else:
        self._tactical_loss_streak = 0
    if self._tactical_loss_streak >= getattr(self, "_tactical_loss_streak_pause_count", 3):
        self._tactical_pause_until = time.time() + 3600
```

- [ ] **Step 5: Connect Judge slot gate**

In Judge slot counting, include tactical:

```python
"tactical": sum(1 for s in occupied if all_slots.get(s) == "tactical"),
```

In `_gate_and_publish_open`, reject tactical when tactical slot is full using reason `tactical_slot_full`.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
pytest test_tactical_risk_governor.py test_ranking_slots.py::test_ranker_limits_tactical_slot -q
```

Expected: all tests pass.

Commit:

```bash
git add utils/candidate_ranker.py agents/trading/judge.py agents/trading/portfolio_risk_guard.py test_tactical_risk_governor.py test_ranking_slots.py
git commit -m "feat: add tactical slot and risk governor"
```

## Task 5: Tactical Executor Lifecycle

**Files:**
- Modify: `executor.py`
- Modify: `agents/trading/executor.py`
- Test: `test_tactical_exit_lifecycle.py`

- [ ] **Step 1: Write failing lifecycle tests**

Create `test_tactical_exit_lifecycle.py`:

```python
import time


def make_executor():
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    ex._config = {"tactical_max_hold_minutes": 90}
    ex._move_sl = lambda symbol, pos, price: pos.update({"stop_loss": price})
    ex.positions = {}
    ex._sl_check_failures = {}
    ex._sl_max_failures = 3
    ex.logger = type("L", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None, "error": lambda *a, **k: None})()
    return ex


def tactical_position():
    return {
        "symbol": "WLD-USDT-SWAP",
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "side": "short",
        "entry_price": 0.385,
        "stop_loss": 0.3904,
        "original_sl": 0.3904,
        "take_profit": 0.38176,
        "take_profit_levels": [0.38176],
        "tp_filled": 0,
        "highest_price": 0.385,
        "lowest_price": 0.385,
        "open_time": time.time(),
        "tactical_max_hold_minutes": 90,
    }


def test_tactical_tp1_triggers_local_partial():
    ex = make_executor()
    pos = tactical_position()
    assert ex._update_trailing("WLD-USDT-SWAP", pos, 0.3817) == "tactical_tp1"


def test_tactical_max_hold_triggers_close():
    ex = make_executor()
    pos = tactical_position()
    pos["open_time"] = time.time() - 91 * 60
    assert ex._update_trailing("WLD-USDT-SWAP", pos, 0.3840) == "tactical_max_hold"


def test_tactical_stop_loss_still_triggers():
    ex = make_executor()
    pos = tactical_position()
    ex.positions = {"WLD-USDT-SWAP": pos}
    ex._fetch_price_robust = lambda symbol: 0.3905
    assert ex.check_stop_loss_take_profit("WLD-USDT-SWAP") == "stop_loss"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest test_tactical_exit_lifecycle.py -q
```

Expected: failure because Tactical triggers are not implemented.

- [ ] **Step 3: Persist Tactical position fields**

In `executor.py` position creation, add:

```python
"track": plan.get("track", "main"),
"exit_profile": plan.get("exit_profile", "trend_runner"),
"tactical_source": plan.get("tactical_source", ""),
"tactical_max_hold_minutes": plan.get("max_holding_minutes", plan.get("tactical_max_hold_minutes", 0)),
"tactical_close_reason": "",
```

- [ ] **Step 4: Add Tactical branch to `_update_trailing`**

At the top of `_update_trailing` after `profit_r` is computed:

```python
if position.get("track") == "tactical":
    max_hold = position.get("tactical_max_hold_minutes") or getattr(self, "_config", {}).get("tactical_max_hold_minutes", 90)
    if max_hold and time.time() - position.get("open_time", time.time()) >= max_hold * 60:
        position["tactical_close_reason"] = "tactical_max_hold"
        return "tactical_max_hold"
    if tp_filled == 0 and tp_levels:
        tp1 = tp_levels[0]
        if (is_long and price >= tp1) or (not is_long and price <= tp1):
            position["tactical_close_reason"] = "tactical_tp1"
            return "tactical_tp1"
    return None
```

Import `time` if needed in the file scope.

- [ ] **Step 5: Route Tactical TP trigger in agent executor**

In `agents/trading/executor.py`, when handling triggers:

```python
elif trigger == "tactical_tp1":
    await self._handle_partial_tp_trigger(symbol, "tactical_tp1")
elif trigger in ("tactical_max_hold", "tactical_invalidated", "tactical_weakened_no_progress"):
    pos = self.executor.positions.get(symbol)
    entry_req_id = (pos or {}).get("request_id", "")
    result = self.executor.close_position(symbol)
    if result:
        result["entry_request_id"] = entry_req_id
        result["tactical_close_reason"] = trigger
        result.setdefault("attribution", {}).update({
            "track": (pos or {}).get("track", "tactical"),
            "exit_profile": (pos or {}).get("exit_profile", "tactical_v1"),
            "tactical_close_reason": trigger,
        })
        payload = self._build_execution_result(
            status="force_closed",
            action="close",
            symbol=symbol,
            source="local_stop",
            reason=trigger,
            result=result,
            request_id=entry_req_id,
        )
        payload["tactical_close_reason"] = trigger
        payload.setdefault("attribution", {}).update(result["attribution"])
        await self.publish("execution_result", payload, symbol=symbol)
```

Also update `_handle_partial_tp_trigger` so `tactical_tp1` uses the same reduce amount as TP1 while preserving the Tactical reason:

```python
pct = 0.5 if trigger in ("partial_tp_1", "tactical_tp1") else 0.25
tp_advance = 1 if trigger in ("partial_tp_1", "tactical_tp1") else 2
```

- [ ] **Step 6: Reject add-to-position for Tactical**

In `agents/trading/executor.py`, before `_execute_add_position`:

```python
if source == "position_analyst" and action in ("open_long", "open_short") and position and position.get("track") == "tactical":
    await self.publish("execution_result", self._build_execution_result(
        status="rejected",
        action=action,
        symbol=symbol,
        source="executor_reject",
        reason="tactical_no_add",
        request_id=request_id,
    ), symbol=symbol)
    return
```

- [ ] **Step 7: Run tests and commit**

Run:

```bash
pytest test_tactical_exit_lifecycle.py -q
```

Expected: all tests pass.

Commit:

```bash
git add executor.py agents/trading/executor.py test_tactical_exit_lifecycle.py
git commit -m "feat: add tactical executor lifecycle"
```

## Task 6: Metadata Propagation and Metrics

**Files:**
- Modify: `agents/trading/executor.py`
- Modify: `agents/trading/reviewer.py`
- Modify: `utils/counterfactual_ledger.py`
- Test: `test_tactical_metadata_flow.py`

- [ ] **Step 1: Write failing metadata propagation tests**

Append to `test_tactical_metadata_flow.py`:

```python
def test_reviewer_persists_tactical_attribution(tmp_path):
    from agents.trading.reviewer import ReviewerAgent
    r = ReviewerAgent.__new__(ReviewerAgent)
    r.trade_history = []
    r.history_file = str(tmp_path / "trade_history.json")
    r.logger = type("L", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None})()
    r._save_trade_history = lambda: None

    msg = {
        "type": "execution_result",
        "timestamp": 123.0,
        "payload": {
            "status": "executed",
            "action": "close",
            "symbol": "WLD-USDT",
            "result": {"pnl": 1.2, "side": "short", "entry_price": 0.385, "exit_price": 0.382},
            "attribution": {
                "track": "tactical",
                "exit_profile": "tactical_v1",
                "slot_type": "tactical",
                "tactical_close_reason": "tactical_tp1",
            },
        },
    }

    import asyncio
    asyncio.run(r.on_message(msg))

    assert r.trade_history[0]["track"] == "tactical"
    assert r.trade_history[0]["exit_profile"] == "tactical_v1"
    assert r.trade_history[0]["tactical_close_reason"] == "tactical_tp1"


def test_counterfactual_rejection_records_tactical_metadata(tmp_path):
    from utils.counterfactual_ledger import CounterfactualLedger
    ledger = CounterfactualLedger(enabled=True, logger=None)
    ledger._events_path = str(tmp_path / "events.jsonl")
    ledger._lifecycle_path = str(tmp_path / "lifecycle.json")
    ledger._active = {}

    ledger.record_rejection(
        "WLD-USDT", "short",
        {"entry_zone": [0.385], "stop_loss": 0.3904, "take_profit": [0.3817],
         "track": "tactical", "exit_profile": "tactical_v1", "tactical_effective_rr": 0.8},
        "mixed", -58, 70, "main_quality_failed",
        {"track": "tactical", "exit_profile": "tactical_v1", "tactical_source": "main_quality_failed"},
    )

    rec = next(iter(ledger._active.values()))
    assert rec["track"] == "tactical"
    assert rec["exit_profile"] == "tactical_v1"
    assert rec["tactical_source"] == "main_quality_failed"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest test_tactical_metadata_flow.py -q
```

Expected: failure because metadata is not persisted.

- [ ] **Step 3: Persist metadata in Reviewer**

In `agents/trading/reviewer.py`, when attribution exists, add:

```python
trade_record["track"] = attribution.get("track", "main")
trade_record["exit_profile"] = attribution.get("exit_profile", "trend_runner")
trade_record["tactical_source"] = attribution.get("tactical_source", "")
trade_record["tactical_close_reason"] = attribution.get("tactical_close_reason", "")
```

In segmented metrics, add `metrics_by_track` and include `track` in the full bucket key:

```python
track = t.get("track", "main")
key = f"{side}_{regime}_{entry_type}_{track}_{slot_type}"
```

- [ ] **Step 4: Propagate metadata in executor events**

In `agents/trading/executor.py`, when building execution payloads, include:

```python
if position and position.get("track"):
    payload["track"] = position.get("track", "main")
    payload["exit_profile"] = position.get("exit_profile", "trend_runner")
    payload["tactical_close_reason"] = position.get("tactical_close_reason", "")
    payload.setdefault("attribution", {}).update({
        "track": payload["track"],
        "exit_profile": payload["exit_profile"],
        "tactical_close_reason": payload["tactical_close_reason"],
    })
```

- [ ] **Step 5: Persist metadata in CounterfactualLedger**

In `utils/counterfactual_ledger.py::record_rejection`, add:

```python
record["track"] = plan.get("track", (attribution or {}).get("track", "main"))
record["exit_profile"] = plan.get("exit_profile", (attribution or {}).get("exit_profile", "trend_runner"))
record["tactical_source"] = plan.get("tactical_source", (attribution or {}).get("tactical_source", ""))
record["tactical_effective_rr"] = plan.get("tactical_effective_rr")
record["tactical_expected_value"] = plan.get("tactical_expected_value")
```

- [ ] **Step 6: Run tests and commit**

Run:

```bash
pytest test_tactical_metadata_flow.py -q
```

Expected: all tests pass.

Commit:

```bash
git add agents/trading/executor.py agents/trading/reviewer.py utils/counterfactual_ledger.py test_tactical_metadata_flow.py
git commit -m "feat: propagate tactical metadata"
```

## Task 7: Tactical Counterfactual and WLD Replay Fixture

**Files:**
- Modify: `utils/counterfactual_pnl.py`
- Modify: `utils/decision_replay.py`
- Create: `tests/fixtures/wld_tactical_20260710.json`
- Test: `tests/test_tactical_wld_replay.py`

- [ ] **Step 1: Create WLD fixture**

Create `tests/fixtures/wld_tactical_20260710.json`:

```json
{
  "first_short": {
    "entry_time": "2026-07-10T10:11:44+08:00",
    "entry": 0.385,
    "main_sl": 0.394,
    "main_tp1": 0.367,
    "tactical_tp1": 0.38176,
    "tactical_sl": 0.3904,
    "bars": [
      {"t": "2026-07-10T10:13:13+08:00", "h": 0.3855, "l": 0.3855, "c": 0.3855},
      {"t": "2026-07-10T11:10:54+08:00", "h": 0.3824, "l": 0.3824, "c": 0.3824},
      {"t": "2026-07-10T11:32:59+08:00", "h": 0.3815, "l": 0.3815, "c": 0.3815}
    ]
  },
  "second_short": {
    "entry_time": "2026-07-10T16:17:49+08:00",
    "entry": 0.3849,
    "main_sl": 0.3932,
    "main_tp1": 0.3683,
    "tactical_tp1": 0.381912,
    "tactical_sl": 0.38988,
    "bars": [
      {"t": "2026-07-10T16:31:15+08:00", "h": 0.3841, "l": 0.3841, "c": 0.3841},
      {"t": "2026-07-10T17:57:06+08:00", "h": 0.3911, "l": 0.3911, "c": 0.3911},
      {"t": "2026-07-10T18:18:52+08:00", "h": 0.3942, "l": 0.3942, "c": 0.3942}
    ]
  }
}
```

- [ ] **Step 2: Write failing replay tests**

Create `tests/test_tactical_wld_replay.py`:

```python
import json
from datetime import datetime
from pathlib import Path


def ts(value):
    return datetime.fromisoformat(value).timestamp()


def bars_from_fixture(rows):
    return [
        {
            "open_time": int(ts(b["t"]) * 1000),
            "high": b["h"],
            "low": b["l"],
            "close": b["c"],
        }
        for b in rows
    ]


def test_wld_first_short_tactical_tp_before_main_tp():
    from utils.counterfactual_pnl import resolve_counterfactual
    fixture = json.loads(Path("tests/fixtures/wld_tactical_20260710.json").read_text())
    trade = fixture["first_short"]
    record = {
        "symbol": "WLD-USDT",
        "side": "short",
        "entry_price": trade["entry"],
        "stop_loss": trade["tactical_sl"],
        "take_profit": [trade["tactical_tp1"]],
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "created_at": ts(trade["entry_time"]),
    }
    bars = bars_from_fixture(trade["bars"])
    result = resolve_counterfactual(record, bars, max_hold_sec=90 * 60, exit_profile="tactical_v1")
    assert result.outcome == "tp"
    assert result.exit_profile == "tactical_v1"


def test_wld_second_short_tactical_sl_before_main_sl():
    from utils.counterfactual_pnl import resolve_counterfactual
    fixture = json.loads(Path("tests/fixtures/wld_tactical_20260710.json").read_text())
    trade = fixture["second_short"]
    record = {
        "symbol": "WLD-USDT",
        "side": "short",
        "entry_price": trade["entry"],
        "stop_loss": trade["tactical_sl"],
        "take_profit": [trade["tactical_tp1"]],
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "created_at": ts(trade["entry_time"]),
    }
    bars = bars_from_fixture(trade["bars"])
    result = resolve_counterfactual(record, bars, max_hold_sec=90 * 60, exit_profile="tactical_v1")
    assert result.outcome == "sl"
    assert result.exit_profile == "tactical_v1"


def test_tactical_max_hold_records_resolution_reason():
    from utils.counterfactual_pnl import resolve_counterfactual
    entry_ts = ts("2026-07-10T10:00:00+08:00")
    record = {
        "symbol": "WLD-USDT",
        "side": "short",
        "entry_price": 0.385,
        "stop_loss": 0.3904,
        "take_profit": [0.3817],
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "created_at": entry_ts,
    }
    bars = [
        {"open_time": int((entry_ts + 30 * 60) * 1000), "high": 0.3852, "low": 0.3848, "close": 0.3850},
        {"open_time": int((entry_ts + 91 * 60) * 1000), "high": 0.3851, "low": 0.3849, "close": 0.3850},
    ]
    result = resolve_counterfactual(record, bars, max_hold_sec=90 * 60, exit_profile="tactical_v1")
    assert result.outcome == "expired"
    assert result.resolution_reason == "tactical_max_hold"
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
pytest tests/test_tactical_wld_replay.py -q
```

Expected: failure because `resolve_counterfactual` does not accept `exit_profile`.

- [ ] **Step 4: Extend counterfactual resolver**

In `utils/counterfactual_pnl.py`, change signature:

```python
@dataclass
class CfResult:
    outcome: str
    exit_price: float
    gross_return_pct: float
    net_usdt: Optional[float]
    net_return_pct: float
    price_ambiguous: bool
    funding_approx: bool
    hold_hours: float
    source: str
    track: str = "main"
    exit_profile: str = "trend_runner"
    resolution_reason: str = ""


def resolve_counterfactual(record: dict, bars: List[dict], *, max_hold_sec: int = 86400,
                           source: str = "attribution_reconstructed",
                           exit_profile: str = "trend_runner",
                           cost_model=None) -> CfResult:
```

Preserve the existing `CfResult` return type and existing outcome values (`tp`, `sl`, `expired`). Add the metadata to the returned dataclass:

```python
track = record.get("track", "main")
profile = exit_profile or record.get("exit_profile", "trend_runner")
resolution_reason = ""
for bar in bars:
    if (bar["open_time"] / 1000.0) - created > max_hold_sec:
        resolution_reason = "tactical_max_hold" if profile == "tactical_v1" else "max_hold"
        break
    hi, lo = float(bar["high"]), float(bar["low"])
    hit_sl = sl and (lo <= sl if side == "long" else hi >= sl)
    hit_tp = tp and (hi >= tp if side == "long" else lo <= tp)
    if hit_sl and hit_tp:
        outcome, exit_price, ambiguous, resolution_reason = "sl", sl, True, "sl_hit_ambiguous"
        resolved_t = bar["open_time"] / 1000.0
        break
    if hit_sl:
        outcome, exit_price, resolution_reason = "sl", sl, "sl_hit"
        resolved_t = bar["open_time"] / 1000.0
        break
    if hit_tp:
        outcome, exit_price, resolution_reason = "tp", tp, "tp_hit"
        resolved_t = bar["open_time"] / 1000.0
        break
    exit_price = float(bar["close"])
    resolved_t = bar["open_time"] / 1000.0

return CfResult(
    outcome=outcome,
    exit_price=exit_price,
    gross_return_pct=gross_pct * 100,
    net_usdt=net_usdt,
    net_return_pct=net_return_pct * 100,
    price_ambiguous=ambiguous,
    funding_approx=(funding_rate != 0.0),
    hold_hours=hold_hours,
    source=source,
    track=track,
    exit_profile=profile,
    resolution_reason=resolution_reason or outcome,
)
```

Use `max_hold_sec=90*60` for Tactical callers. Preserve existing behavior when `exit_profile` is omitted.

- [ ] **Step 5: Install replay config flags**

In `utils/decision_replay.py::_install_config_flags`, add this block after the existing low-R:R config assignments:

```python
    # ── Tactical exit track ──
    judge._tactical_track_enabled = g("tactical_track_enabled", False)
    judge._tactical_shadow_only = g("tactical_shadow_only", True)
    judge._main_quality_gate_enabled = g("main_quality_gate_enabled", True)
    judge._main_quality_min_provenance = g("main_quality_min_provenance", 0.20)
    judge._main_quality_block_llm_reversal = g("main_quality_block_llm_reversal", True)
    judge._main_quality_allow_mixed_override = g("main_quality_allow_mixed_override", False)
    judge._main_quality_require_volume_or_oi = g("main_quality_require_volume_or_oi", True)
    judge._tactical_max_leverage = g("tactical_max_leverage", 5)
    judge._tactical_default_position_pct = g("tactical_default_position_pct", 0.70)
    judge._tactical_very_near_position_pct = g("tactical_very_near_position_pct", 1.00)
    judge._tactical_stop_cap_r_main = g("tactical_stop_cap_r_main", 0.60)
    judge._tactical_very_near_stop_r_main = g("tactical_very_near_stop_r_main", 0.40)
    judge._tactical_tp1_r = g("tactical_tp1_r", 0.60)
    judge._tactical_cost_coverage_min = g("tactical_cost_coverage_min", 4.0)
    judge._tactical_max_hold_minutes = g("tactical_max_hold_minutes", 90)
```

- [ ] **Step 6: Run tests and commit**

Run:

```bash
pytest tests/test_tactical_wld_replay.py tests/test_counterfactual_pnl.py -q
```

Expected: all tests pass.

Commit:

```bash
git add utils/counterfactual_pnl.py utils/decision_replay.py tests/fixtures/wld_tactical_20260710.json tests/test_tactical_wld_replay.py
git commit -m "feat: add tactical counterfactual replay"
```

## Task 8: Final Integration, Flags, and OpenSpec Task Sync

**Files:**
- Modify: `openspec/changes/add-tactical-exit-track/tasks.md`
- Modify: `docs/superpowers/plans/2026-07-10-tactical-exit-track.md`
- Optional Modify: `docs/runbook.md` if runtime flags are documented there

- [ ] **Step 1: Run focused Tactical suite**

Run:

```bash
pytest \
  test_tactical_track_classifier.py \
  test_tactical_plan_math.py \
  test_tactical_risk_governor.py \
  test_tactical_exit_lifecycle.py \
  test_tactical_metadata_flow.py \
  tests/test_tactical_wld_replay.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 2: Run adjacent regression suite**

Run:

```bash
pytest \
  test_ladder_weighted_rr.py \
  test_low_rr_slots.py \
  test_short_side_guard.py \
  test_ranking_slots.py \
  test_partial_tp_lifecycle.py \
  test_pnl_resolved_event_contract.py \
  test_counterfactual_ledger.py \
  tests/test_counterfactual_pnl.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 3: Verify OpenSpec**

Run:

```bash
openspec validate add-tactical-exit-track --strict
```

Expected: `Change 'add-tactical-exit-track' is valid`.

- [ ] **Step 4: Mark OpenSpec implementation tasks complete**

In `openspec/changes/add-tactical-exit-track/tasks.md`, change every task from `- [ ]` to `- [x]` only after the corresponding implementation and tests have passed.

- [ ] **Step 5: Commit final sync**

Commit:

```bash
git add openspec/changes/add-tactical-exit-track/tasks.md docs/superpowers/plans/2026-07-10-tactical-exit-track.md
git commit -m "docs: sync tactical exit track implementation tasks"
```

## Verification Commands

Run before build guard:

```bash
pytest \
  test_tactical_track_classifier.py \
  test_tactical_plan_math.py \
  test_tactical_risk_governor.py \
  test_tactical_exit_lifecycle.py \
  test_tactical_metadata_flow.py \
  tests/test_tactical_wld_replay.py \
  test_ladder_weighted_rr.py \
  test_low_rr_slots.py \
  test_short_side_guard.py \
  test_ranking_slots.py \
  test_partial_tp_lifecycle.py \
  test_pnl_resolved_event_contract.py \
  test_counterfactual_ledger.py \
  tests/test_counterfactual_pnl.py \
  -q
openspec validate add-tactical-exit-track --strict
```

Expected: pytest passes and OpenSpec validates.

## Spec Coverage Review

- `tactical-exit-track`: covered by Tasks 1-8.
- `ladder-weighted-rr` Tactical isolation: covered by Task 3.
- `low-rr-early-trailing` coexistence: covered by Task 5 adjacent regression.
- `short-main-path-risk-guard` hard vetoes: covered by Task 2 and adjacent short guard regression.
- `counterfactual-pnl`: covered by Task 7.
- `pnl-resolution-bus-events`: covered by Task 6.
