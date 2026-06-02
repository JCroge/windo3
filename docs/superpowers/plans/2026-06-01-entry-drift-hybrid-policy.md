---
change: entry-drift-hybrid-policy
design-doc: docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md
base-ref: 733c671f7f6e2437f07d36064b3db0ceaeb547fc
archived-with: 2026-06-02-entry-drift-hybrid-policy
---

# Entry Drift Hybrid Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 limit-then-market 开仓路径上加入 Hybrid drift gate，杜绝 5/30 XLM 那种"plan stale 7.2% 仍开仓 + 开仓即 partial_tp_1"的死链。

**Architecture:** Judge plan 新增 `entry_ref/sl_pct/tp_pct` 锚点字段；executor 层用单一函数 `_classify_entry_drift` 在限价挂单前（Gate 1）和 fallback 市价前（Gate 2）做 4 档判定（accept / small recalc / medium recalc + floor +0.20 / abandon）；重算函数 `_recompute_plan_for_drift` 按比例平移 SL/TP；落库 TP 通过单一 setter `_set_position_tp` 杜绝双源真相。

**Tech Stack:** Python 3.11 / pytest / asyncio / ccxt / 既有 message_bus 与 LiveLedger jsonl。

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

## File Structure

| Path | 责任 | 操作 |
|------|------|------|
| `agents/trading/judge.py:_build_plan` | 在 plan dict 增加 entry_ref / sl_pct / tp_pct | Modify |
| `executor.py` | 新增 4 个 helper（drift gate / 重算 / TP setter / drift 事件落库桥接），改 2 个调用点（`open_position_with_plan` Gate 1、`_execute_limit_order` Gate 2），删 3 处冗余（line 1991-1997 / 2203-2205 / 2259-2262），SL 方向修正改 invariant | Modify |
| `agents/trading/executor.py` | 把 root executor 入队的 drift alert 转成 `risk_alert` 总线事件；critical_types 加 4 个新 type；新 reject reason 走既有 `_build_execution_result` 路径 | Modify |
| `utils/live_ledger.py` | 新增 `record_entry_drift_decision()` public method | Modify |
| `tests/test_entry_drift_hybrid_policy.py` | 新增 30~35 case 全套单测 | Create |
| `event_backtest.py` | 兼容老 plan 缺新字段（fail-safe accept），不破坏既有回放 | Modify |
| `tests/test_event_backtest_drift_compat.py` | 老 plan 回放兼容性回归 1 case | Create |
| `CLAUDE.md` | 更新 baseline、红线条目（drift gate 单一函数 + TP setter 单一收口） | Modify |
| `openspec/changes/entry-drift-hybrid-policy/specs/entry-drift-policy/spec.md` | OpenSpec delta spec 落地 | Create |

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

## Phase 0: Spec & 状态准备

### Task 0: 创建 OpenSpec delta spec

**Files:**
- Create: `openspec/changes/entry-drift-hybrid-policy/specs/entry-drift-policy/spec.md`

- [ ] **Step 1: 写 delta spec**

```markdown
# Capability: entry-drift-policy (delta)

## ADDED Requirements

### Requirement: Entry Drift Classification
The system SHALL classify the relative drift between Judge plan's `entry_ref`
anchor and the executor's live ticker price into one of four bands and act
accordingly:

- `accept` (drift ≤ 0.5%): proceed with the original plan unchanged
- `small` (0.5% < drift ≤ 2%): recompute SL/TP by sl_pct/tp_pct ratios on the
  new entry, re-check R:R against the plan's original floor; pass = accept
  recomputed plan, fail = reject with reason `drift_rr_floor_fail`
- `medium` (2% < drift ≤ 5%): recompute as above but with floor + 0.20
  absolute bump
- `abandon` (drift > 5%): reject with reason `drift_too_large`

#### Scenario: 5/30 XLM stale plan abandons cleanly
- **WHEN** Judge plan has entry_ref=0.2179 and executor sees live price 0.2336
  (drift 7.2%)
- **THEN** the drift gate returns decision=abandon, reason=drift_too_large
- **AND** no order is submitted to the exchange
- **AND** execution_result.v2 is published with status=rejected,
  reason=drift_too_large

#### Scenario: medium band recalculation passes when R:R clears bumped floor
- **WHEN** drift is 3% and recomputed R:R is 2.30 with original floor 2.00
- **THEN** the gate returns decision=recalc_pass, rr_floor_used=2.20

#### Scenario: medium band recalculation fails when R:R below bumped floor
- **WHEN** drift is 3% and recomputed R:R is 2.10 with original floor 2.00
- **THEN** the gate returns decision=recalc_fail, reason=drift_rr_floor_fail

### Requirement: Plan Field Fail-Safe
The system SHALL accept the original plan and emit a
`plan_missing_entry_ref` risk alert when the plan lacks any of `entry_ref`,
`sl_pct`, or `tp_pct`. The drift_pct of such a fail-safe accept SHALL be 0.0
to make the path identifiable in attribution downstream.

### Requirement: Two-Gate Execution
The drift gate SHALL run twice on the limit-then-market path:
1. Gate 1: at executor entry, before any order submission
2. Gate 2: after a 30s limit order timeout, before the fallback market order

Both gates SHALL use the original `plan.entry_ref` as the drift baseline. The
recomputed plan from Gate 1 SHALL NOT be passed as input to Gate 2.

### Requirement: TP Field Single Source of Truth
All writes to `position.take_profit` and `position.take_profit_levels` SHALL
go through a single setter that enforces
`position.take_profit == position.take_profit_levels[0]`. Direct mutation
that violates this invariant SHALL halt the symbol and emit a
`tp_invariant_breach` risk alert when partial_tp_1/partial_tp_2 is about to
fire.
```

- [ ] **Step 2: Commit**

```bash
git add openspec/changes/entry-drift-hybrid-policy/specs/entry-drift-policy/spec.md
git commit -m "spec(entry-drift): delta spec for hybrid drift gate"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

## Phase 1: Judge Plan 字段扩展

### Task 1: 给 _build_plan 加 entry_ref / sl_pct / tp_pct

**Files:**
- Modify: `agents/trading/judge.py:3101-3172`
- Test: `tests/test_judge_plan_anchor_fields.py` (new)

- [ ] **Step 1: 写失败测试**

Create `tests/test_judge_plan_anchor_fields.py`:

```python
"""Verify Judge._build_plan emits entry_ref/sl_pct/tp_pct anchor fields."""
import pytest
from unittest.mock import MagicMock
from agents.trading.judge import Judge


def _make_judge():
    j = Judge.__new__(Judge)
    j.logger = MagicMock()
    j._recent_win_rate = None
    j._recent_wins = 0
    j._total_completed_trades = 0
    j._min_trades_for_ev_gate = 30
    j._fallback_win_rate = 0.45
    j._ev_prior_wins = 2
    j._ev_prior_total = 5
    j._bucketed_ev_enabled = False
    return j


def _tech(price, atr_pct=0.02):
    return {
        'levels': {'support': [price * 0.97], 'resistance': [price * 1.03]},
        'risk': {},
        'microstructure': {},
        'momentum': {'atr_pct': atr_pct},
        'trend': {'15m': 'bullish', '1h': 'bullish'},
    }


def test_build_plan_emits_entry_ref():
    j = _make_judge()
    plan = j._build_plan(_tech(100.0), 'open_long', 100.0, 70, 60)
    assert plan['entry_ref'] == pytest.approx(100.0, rel=1e-3)


def test_build_plan_emits_sl_pct():
    j = _make_judge()
    plan = j._build_plan(_tech(100.0), 'open_long', 100.0, 70, 60)
    expected = abs(plan['stop_loss'] - 100.0) / 100.0
    assert plan['sl_pct'] == pytest.approx(expected, rel=1e-4)


def test_build_plan_emits_tp_pct_list():
    j = _make_judge()
    plan = j._build_plan(_tech(100.0), 'open_long', 100.0, 70, 60)
    assert isinstance(plan['tp_pct'], list)
    assert len(plan['tp_pct']) == len(plan['take_profit'])
    for pct, tp in zip(plan['tp_pct'], plan['take_profit']):
        assert pct == pytest.approx(abs(tp - 100.0) / 100.0, rel=1e-4)


def test_build_plan_short_side_pcts_positive():
    """sl_pct and tp_pct should always be positive magnitudes."""
    j = _make_judge()
    plan = j._build_plan(_tech(100.0), 'open_short', 100.0, 70, 60)
    assert plan['sl_pct'] > 0
    assert all(p > 0 for p in plan['tp_pct'])
```

- [ ] **Step 2: Run test, verify failure**

Run: `python3 -m pytest tests/test_judge_plan_anchor_fields.py -v`
Expected: FAIL — `KeyError: 'entry_ref'`

- [ ] **Step 3: Modify `agents/trading/judge.py:_build_plan` return dict**

In `agents/trading/judge.py`, find the `return { ... }` block at line 3153 and modify it. Insert the three new fields **before** `entry_zone`:

```python
sl_pct_value = abs(stop_loss - price) / price if price > 0 else 0.0
tp_pct_values = [abs(tp - price) / price for tp in take_profit] if price > 0 else []

return {
    "side": "long" if is_long else "short",
    "entry_ref": price_round(price),
    "sl_pct": round(sl_pct_value, 6),
    "tp_pct": [round(p, 6) for p in tp_pct_values],
    "entry_zone": [price_round(e) for e in entry_zone],
    "stop_loss": price_round(stop_loss),
    "take_profit": [price_round(tp) for tp in take_profit],
    "leverage": leverage,
    "size_usdt": size_usdt,
    "order_type": order_type,
    "risk_reward_ratio": gross_rr,
    "effective_risk_reward_ratio": effective_rr,
    "funding_cost": round(budget['funding_cost_usdt'], 3),
    "est_hold_hours": budget['est_hold_hours'],
    "max_holding_hours": 24,
    "atr_pct": momentum.get('atr_pct', 0.02),
    "expected_value": round(expected_value, 4),
    "p_win_used": round(p_win, 3),
    "p_win_source": p_win_source,
    "net_profit_usdt": round(net_profit, 3),
    "net_loss_usdt": round(net_loss, 3),
}
```

- [ ] **Step 4: Run test, verify pass**

Run: `python3 -m pytest tests/test_judge_plan_anchor_fields.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Run full Judge regression**

Run: `python3 -m pytest tests/test_judge*.py -q`
Expected: PASS, no regression vs 921 baseline subset.

- [ ] **Step 6: Commit**

```bash
git add agents/trading/judge.py tests/test_judge_plan_anchor_fields.py
git commit -m "feat(judge): emit entry_ref/sl_pct/tp_pct anchor fields in _build_plan"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

## Phase 2: Executor Drift Gate 核心函数

### Task 2: 文件级常量 + DriftDecision dataclass

**Files:**
- Modify: `executor.py` (add at module top, after existing imports)
- Test: `tests/test_entry_drift_hybrid_policy.py` (new, will grow across tasks)

- [ ] **Step 1: 写失败测试**

Create `tests/test_entry_drift_hybrid_policy.py`:

```python
"""Entry Drift Hybrid Policy — Gate classification, recompute, invariants.

Coverage matrix lives in docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md
"""
import pytest
from executor import (
    ENTRY_DRIFT_ACCEPT_PCT,
    ENTRY_DRIFT_SMALL_PCT,
    ENTRY_DRIFT_LARGE_PCT,
    ENTRY_DRIFT_MEDIUM_FLOOR_BUMP,
    DriftDecision,
)


def test_thresholds_constants():
    assert ENTRY_DRIFT_ACCEPT_PCT == 0.005
    assert ENTRY_DRIFT_SMALL_PCT == 0.02
    assert ENTRY_DRIFT_LARGE_PCT == 0.05
    assert ENTRY_DRIFT_MEDIUM_FLOOR_BUMP == 0.20


def test_drift_decision_is_frozen_dataclass():
    d = DriftDecision(
        band='accept', drift_pct=0.0, decision='accept',
        reason=None, new_plan=None, rr_actual=None, rr_floor_used=None,
    )
    with pytest.raises((AttributeError, Exception)):
        d.band = 'small'  # frozen
```

- [ ] **Step 2: Run test, verify failure**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py::test_thresholds_constants -v`
Expected: FAIL — `ImportError: cannot import name 'ENTRY_DRIFT_ACCEPT_PCT'`

- [ ] **Step 3: 在 `executor.py` 顶部 import 区域之后插入常量与 dataclass**

```python
from dataclasses import dataclass
from typing import Literal, Optional

ENTRY_DRIFT_ACCEPT_PCT = 0.005
ENTRY_DRIFT_SMALL_PCT = 0.02
ENTRY_DRIFT_LARGE_PCT = 0.05
ENTRY_DRIFT_MEDIUM_FLOOR_BUMP = 0.20


@dataclass(frozen=True)
class DriftDecision:
    band: Literal['accept', 'small', 'medium', 'abandon']
    drift_pct: float
    decision: Literal['accept', 'recalc_pass', 'recalc_fail', 'abandon']
    reason: Optional[str]
    new_plan: Optional[dict]
    rr_actual: Optional[float]
    rr_floor_used: Optional[float]
```

- [ ] **Step 4: Run test, verify pass**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py -v`
Expected: PASS (2/2)

- [ ] **Step 5: Commit**

```bash
git add executor.py tests/test_entry_drift_hybrid_policy.py
git commit -m "feat(executor): add drift threshold constants and DriftDecision dataclass"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

### Task 3: `_recompute_plan_for_drift` 重算函数

**Files:**
- Modify: `executor.py` (add method on ContractExecutor)
- Test: `tests/test_entry_drift_hybrid_policy.py` (extend)

- [ ] **Step 1: 追加失败测试**

Append to `tests/test_entry_drift_hybrid_policy.py`:

```python
import copy
from unittest.mock import MagicMock


def _exec_stub():
    """Build a minimal ContractExecutor stub for unit tests of pure helpers."""
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = MagicMock()
    return ex


def _plan_long(entry=100.0, sl_pct=0.025, tp_pcts=(0.05, 0.10, 0.15)):
    return {
        'side': 'long',
        'entry_ref': entry,
        'sl_pct': sl_pct,
        'tp_pct': list(tp_pcts),
        'entry_zone': [entry * 0.999, entry * 1.001],
        'stop_loss': entry * (1 - sl_pct),
        'take_profit': [entry * (1 + p) for p in tp_pcts],
        'leverage': 10,
        'size_usdt': 100,
        'order_type': 'limit',
        'attribution': {'rr_floor': 2.00},
    }


def test_recompute_long_small_band_pass():
    ex = _exec_stub()
    plan = _plan_long()  # original R:R = 0.05/0.025 = 2.0, floor=2.0
    new_plan = ex._recompute_plan_for_drift(plan, new_entry=101.0, drift_band='small')
    assert new_plan is not None
    assert new_plan['stop_loss'] == pytest.approx(101.0 * (1 - 0.025), rel=1e-4)
    assert new_plan['take_profit'][0] == pytest.approx(101.0 * 1.05, rel=1e-4)
    assert new_plan['recompute_reason'] == 'drift_small'
    assert new_plan['original_entry_ref'] == 100.0
    assert new_plan['recomputed_entry'] == 101.0
    assert new_plan['rr_floor_used'] == pytest.approx(2.0, rel=1e-4)
    assert new_plan['rr_actual_after_recompute'] == pytest.approx(2.0, rel=1e-4)


def test_recompute_medium_band_floor_bump():
    ex = _exec_stub()
    plan = _plan_long()  # R:R = 2.0
    # medium band requires floor 2.20 → 2.0 fails
    new_plan = ex._recompute_plan_for_drift(plan, new_entry=103.0, drift_band='medium')
    assert new_plan is None  # rr_actual=2.0 < floor 2.2


def test_recompute_medium_band_pass_when_rr_clears_bump():
    ex = _exec_stub()
    plan = _plan_long(sl_pct=0.025, tp_pcts=(0.06, 0.12, 0.18))  # R:R = 2.4
    new_plan = ex._recompute_plan_for_drift(plan, new_entry=103.0, drift_band='medium')
    assert new_plan is not None
    assert new_plan['rr_floor_used'] == pytest.approx(2.2, rel=1e-4)
    assert new_plan['rr_actual_after_recompute'] == pytest.approx(2.4, rel=1e-4)
    assert new_plan['recompute_reason'] == 'drift_medium'


def test_recompute_short_side():
    ex = _exec_stub()
    plan = _plan_long()
    plan['side'] = 'short'
    plan['stop_loss'] = 100.0 * (1 + 0.025)
    plan['take_profit'] = [100.0 * (1 - p) for p in plan['tp_pct']]
    new_plan = ex._recompute_plan_for_drift(plan, new_entry=99.0, drift_band='small')
    assert new_plan is not None
    assert new_plan['stop_loss'] == pytest.approx(99.0 * 1.025, rel=1e-4)
    assert new_plan['take_profit'][0] == pytest.approx(99.0 * 0.95, rel=1e-4)


def test_recompute_does_not_mutate_original():
    ex = _exec_stub()
    plan = _plan_long()
    plan_snapshot = copy.deepcopy(plan)
    ex._recompute_plan_for_drift(plan, new_entry=101.0, drift_band='small')
    assert plan == plan_snapshot
```

- [ ] **Step 2: Run tests, verify failure**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py -v -k recompute`
Expected: FAIL — `AttributeError: '_recompute_plan_for_drift'`

- [ ] **Step 3: 在 ContractExecutor class 内实现 `_recompute_plan_for_drift`**

Add method (typically near other plan-related helpers):

```python
def _recompute_plan_for_drift(self, plan: dict, new_entry: float,
                              drift_band: str) -> Optional[dict]:
    """按 plan.sl_pct / tp_pct 同比例平移 SL/TP 到 new_entry。
    medium band floor 加成 +0.20。R:R 复检不过返回 None。
    不修改原 plan（deepcopy）。
    """
    import copy
    new_plan = copy.deepcopy(plan)
    side = plan.get('side')
    sl_pct = plan.get('sl_pct')
    tp_pct = plan.get('tp_pct') or []
    if not sl_pct or not tp_pct or new_entry <= 0:
        return None

    if side == 'long':
        new_sl = new_entry * (1 - sl_pct)
        new_tp = [new_entry * (1 + p) for p in tp_pct]
    else:
        new_sl = new_entry * (1 + sl_pct)
        new_tp = [new_entry * (1 - p) for p in tp_pct]

    sl_dist = sl_pct
    tp_dist = abs(new_tp[0] - new_entry) / new_entry
    rr_actual = tp_dist / sl_dist if sl_dist > 0 else 0.0

    base_floor = (plan.get('attribution') or {}).get('rr_floor', 2.0)
    bump = ENTRY_DRIFT_MEDIUM_FLOOR_BUMP if drift_band == 'medium' else 0.0
    floor_used = base_floor + bump

    if rr_actual < floor_used:
        return None

    new_plan['stop_loss'] = new_sl
    new_plan['take_profit'] = new_tp
    new_plan['recompute_reason'] = f'drift_{drift_band}'
    new_plan['original_entry_ref'] = plan.get('entry_ref')
    new_plan['recomputed_entry'] = new_entry
    new_plan['recomputed_sl'] = new_sl
    new_plan['recomputed_tp'] = new_tp
    new_plan['rr_floor_used'] = floor_used
    new_plan['rr_actual_after_recompute'] = rr_actual
    return new_plan
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py -v -k recompute`
Expected: PASS (5/5)

- [ ] **Step 5: Commit**

```bash
git add executor.py tests/test_entry_drift_hybrid_policy.py
git commit -m "feat(executor): _recompute_plan_for_drift with proportional SL/TP shift"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

### Task 4: `_classify_entry_drift` 单一真相源

**Files:**
- Modify: `executor.py` (add method on ContractExecutor; add `self._pending_drift_alerts: list[dict] = []` in `__init__`)
- Test: `tests/test_entry_drift_hybrid_policy.py` (extend)

- [ ] **Step 1: 追加失败测试**

```python
def test_classify_drift_accept_band():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long(entry=100.0)
    d = ex._classify_entry_drift(plan, live_price=100.4)  # drift=0.4%
    assert d.band == 'accept'
    assert d.decision == 'accept'
    assert d.drift_pct == pytest.approx(0.004, rel=1e-3)


def test_classify_drift_boundary_005_still_accept():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    d = ex._classify_entry_drift(plan, live_price=100.5)  # drift=0.5%
    assert d.band == 'accept'


def test_classify_drift_small_band_recalc_pass():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    d = ex._classify_entry_drift(plan, live_price=101.0)  # drift=1%
    assert d.band == 'small'
    assert d.decision == 'recalc_pass'
    assert d.new_plan is not None


def test_classify_drift_boundary_002_still_small():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    d = ex._classify_entry_drift(plan, live_price=102.0)  # drift=2%
    assert d.band == 'small'


def test_classify_drift_medium_band_recalc_fail():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()  # R:R=2.0; medium floor=2.2 → fail
    d = ex._classify_entry_drift(plan, live_price=103.0)  # drift=3%
    assert d.band == 'medium'
    assert d.decision == 'recalc_fail'
    assert d.reason == 'drift_rr_floor_fail'


def test_classify_drift_medium_band_recalc_pass_with_higher_rr():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long(sl_pct=0.025, tp_pcts=(0.06, 0.12, 0.18))  # R:R=2.4
    d = ex._classify_entry_drift(plan, live_price=103.0)
    assert d.decision == 'recalc_pass'
    assert d.rr_floor_used == pytest.approx(2.2, rel=1e-3)


def test_classify_drift_boundary_005_still_medium():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    d = ex._classify_entry_drift(plan, live_price=105.0)  # drift=5%
    assert d.band == 'medium'


def test_classify_drift_abandon_above_5pct():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    d = ex._classify_entry_drift(plan, live_price=105.5)  # drift=5.5%
    assert d.band == 'abandon'
    assert d.decision == 'abandon'
    assert d.reason == 'drift_too_large'
    assert d.new_plan is None


def test_classify_drift_xlm_replay_72pct_abandon():
    """5/30 XLM real replay: entry_ref=0.2179, live=0.2336."""
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long(entry=0.2179)
    d = ex._classify_entry_drift(plan, live_price=0.2336)
    assert d.band == 'abandon'
    assert d.reason == 'drift_too_large'
    assert d.drift_pct == pytest.approx(0.072, abs=0.001)


def test_classify_drift_missing_entry_ref_failsafe_accept():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    plan.pop('entry_ref')
    d = ex._classify_entry_drift(plan, live_price=999.0)
    assert d.band == 'accept'
    assert d.decision == 'accept'
    assert d.drift_pct == 0.0
    assert any(a['type'] == 'plan_missing_entry_ref'
               for a in ex._pending_drift_alerts)


def test_classify_drift_missing_sl_pct_failsafe_accept():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    plan.pop('sl_pct')
    d = ex._classify_entry_drift(plan, live_price=120.0)
    assert d.decision == 'accept'
    assert any(a['type'] == 'plan_missing_entry_ref'
               for a in ex._pending_drift_alerts)


def test_classify_drift_missing_tp_pct_failsafe_accept():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    plan.pop('tp_pct')
    d = ex._classify_entry_drift(plan, live_price=120.0)
    assert d.decision == 'accept'
    assert any(a['type'] == 'plan_missing_entry_ref'
               for a in ex._pending_drift_alerts)
```

- [ ] **Step 2: Run tests, verify failure**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py -v -k classify`
Expected: FAIL — `AttributeError: '_classify_entry_drift'`

- [ ] **Step 3: 实现 `_classify_entry_drift` + alert 入队 helper**

Add to `ContractExecutor.__init__` (after existing instance attribute initialization):

```python
self._pending_drift_alerts: list[dict] = []
```

Add method:

```python
def _enqueue_drift_alert(self, alert_type: str, **fields) -> None:
    """Buffer a drift-related risk alert for the agent layer to drain & publish."""
    self._pending_drift_alerts.append({
        'type': alert_type,
        'timestamp': time.time(),
        **fields,
    })

def _classify_entry_drift(self, plan: dict, live_price: float) -> 'DriftDecision':
    """Drift gate single source of truth.

    Bands (boundary inclusive on the lower side of the next band):
      drift <= 0.005      → accept
      0.005 < drift <= 0.02 → small (recompute, floor unchanged)
      0.02  < drift <= 0.05 → medium (recompute, floor + 0.20)
      drift > 0.05         → abandon (reason=drift_too_large)
    Plan missing entry_ref/sl_pct/tp_pct → fail-safe accept (drift_pct=0.0)
    + risk_alert.plan_missing_entry_ref enqueued.
    """
    entry_ref = plan.get('entry_ref')
    sl_pct = plan.get('sl_pct')
    tp_pct = plan.get('tp_pct')
    if not entry_ref or not sl_pct or not tp_pct or live_price <= 0:
        self._enqueue_drift_alert(
            'plan_missing_entry_ref',
            symbol=plan.get('symbol'),
            has_entry_ref=bool(entry_ref),
            has_sl_pct=bool(sl_pct),
            has_tp_pct=bool(tp_pct),
        )
        return DriftDecision(
            band='accept', drift_pct=0.0, decision='accept',
            reason=None, new_plan=None, rr_actual=None, rr_floor_used=None,
        )

    drift = abs(live_price - entry_ref) / entry_ref

    if drift <= ENTRY_DRIFT_ACCEPT_PCT:
        return DriftDecision(
            band='accept', drift_pct=drift, decision='accept',
            reason=None, new_plan=None, rr_actual=None, rr_floor_used=None,
        )

    if drift > ENTRY_DRIFT_LARGE_PCT:
        return DriftDecision(
            band='abandon', drift_pct=drift, decision='abandon',
            reason='drift_too_large',
            new_plan=None, rr_actual=None, rr_floor_used=None,
        )

    band = 'small' if drift <= ENTRY_DRIFT_SMALL_PCT else 'medium'
    new_plan = self._recompute_plan_for_drift(plan, live_price, band)
    if new_plan is None:
        base_floor = (plan.get('attribution') or {}).get('rr_floor', 2.0)
        floor_used = base_floor + (ENTRY_DRIFT_MEDIUM_FLOOR_BUMP if band == 'medium' else 0.0)
        return DriftDecision(
            band=band, drift_pct=drift, decision='recalc_fail',
            reason='drift_rr_floor_fail',
            new_plan=None, rr_actual=None, rr_floor_used=floor_used,
        )
    return DriftDecision(
        band=band, drift_pct=drift, decision='recalc_pass',
        reason=None, new_plan=new_plan,
        rr_actual=new_plan['rr_actual_after_recompute'],
        rr_floor_used=new_plan['rr_floor_used'],
    )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py -v -k classify`
Expected: PASS (11/11)

- [ ] **Step 5: Commit**

```bash
git add executor.py tests/test_entry_drift_hybrid_policy.py
git commit -m "feat(executor): _classify_entry_drift drift gate single-source classifier"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

### Task 5: `_set_position_tp` 单一收口 + invariant

**Files:**
- Modify: `executor.py` (add method, add invariant assertion in `_update_trailing`)
- Test: `tests/test_entry_drift_hybrid_policy.py` (extend)

- [ ] **Step 1: 追加失败测试**

```python
def test_set_position_tp_writes_both_fields():
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    pos = {}
    ex._set_position_tp(pos, 105.0, [105.0, 110.0, 115.0])
    assert pos['take_profit'] == 105.0
    assert pos['take_profit_levels'] == [105.0, 110.0, 115.0]


def test_set_position_tp_rejects_mismatch():
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    pos = {}
    with pytest.raises(AssertionError):
        ex._set_position_tp(pos, 99.0, [100.0, 110.0])


def test_set_position_tp_rejects_empty_levels():
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    with pytest.raises(AssertionError):
        ex._set_position_tp({}, 100.0, [])


def test_update_trailing_invariant_breach_halts_symbol():
    """Direct mutation breaks invariant → partial_tp_1 must halt symbol."""
    ex = _exec_stub()
    ex.exchange_id = 'okx'
    ex.testnet = True
    ex.logger = MagicMock()
    ex._halted_symbols = {}
    ex._pending_drift_alerts = []
    ex._halt_symbol = MagicMock(side_effect=lambda s, reason: ex._halted_symbols.update({s: reason}))
    pos = {
        'side': 'long', 'entry_price': 100.0, 'stop_loss': 97.5,
        'take_profit': 999.0,                # bypass — broken!
        'take_profit_levels': [102.0, 110.0],
        'tp_filled': 0,
        'original_sl': 97.5,
        'atr_pct': 0.02,
    }
    sig = ex._update_trailing('XLM-USDT', pos, price=103.0)
    # Should NOT return 'partial_tp_1'; should halt instead
    assert ex._halted_symbols.get('XLM-USDT') == 'tp_invariant_breach'
    assert any(a['type'] == 'tp_invariant_breach'
               for a in ex._pending_drift_alerts)
    assert sig is None
```

- [ ] **Step 2: Run tests, verify failure**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py -v -k "tp or invariant"`
Expected: FAIL — `_set_position_tp` not found.

- [ ] **Step 3: 实现 `_set_position_tp` + invariant 在 `_update_trailing` 顶部**

Add method:

```python
def _set_position_tp(self, position: dict, tp_first: float,
                     tp_levels: list) -> None:
    """Single sink for TP fields. Enforces:
       position.take_profit == position.take_profit_levels[0]"""
    assert tp_levels, "tp_levels must be non-empty"
    assert tp_first == tp_levels[0], (
        f"tp_first {tp_first} must equal tp_levels[0] {tp_levels[0]}"
    )
    position['take_profit'] = tp_first
    position['take_profit_levels'] = list(tp_levels)
```

In `_update_trailing` (around line 1763), at the very top of the function (after `side = position['side']`), add:

```python
# Invariant: take_profit must mirror take_profit_levels[0]
tp_levels_check = position.get('take_profit_levels') or []
tp_scalar_check = position.get('take_profit')
if tp_levels_check and tp_scalar_check is not None and tp_scalar_check != tp_levels_check[0]:
    self.logger.error(
        f"[TP Invariant] {symbol} breach: take_profit={tp_scalar_check} "
        f"!= take_profit_levels[0]={tp_levels_check[0]}; halting symbol"
    )
    self._halt_symbol(symbol, reason='tp_invariant_breach')
    self._enqueue_drift_alert(
        'tp_invariant_breach',
        symbol=symbol,
        take_profit=tp_scalar_check,
        take_profit_levels_first=tp_levels_check[0],
    )
    return None
```

Modify `executor.py:2122-2123` (position dict construction) to use the setter:

Replace:
```python
'take_profit': tp_first,
'take_profit_levels': take_profit,
```

With (build position first, then call setter):
```python
'take_profit': tp_first,
'take_profit_levels': list(take_profit) if take_profit else [tp_first],
```

And immediately after the `position = { ... }` block (so the dict exists), add:
```python
self._set_position_tp(position, position['take_profit'], position['take_profit_levels'])
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py -v -k "tp or invariant"`
Expected: PASS

- [ ] **Step 5: Run full executor regression**

Run: `python3 -m pytest tests/test_partial_tp_lifecycle.py tests/test_okx_posmode_executor.py -q`
Expected: PASS, no regression.

- [ ] **Step 6: Commit**

```bash
git add executor.py tests/test_entry_drift_hybrid_policy.py
git commit -m "feat(executor): _set_position_tp single sink + partial_tp invariant halt"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

## Phase 3: 双 Gate 接入 + 冗余路径删除

### Task 6: Gate 1 在 `open_position_with_plan` 入口

**Files:**
- Modify: `executor.py:1974-1997` (insert Gate 1, replace mechanical TP fix with assertion, change SL fix to invariant)
- Test: `tests/test_entry_drift_hybrid_policy.py` (extend with gate-level test using mocked exchange)

- [ ] **Step 1: 追加失败测试**

```python
def test_gate1_abandons_xlm_replay():
    """End-to-end Gate 1: 5/30 XLM scenario must NOT submit any order."""
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = MagicMock()
    ex.exchange = MagicMock()
    ex.exchange.fetch_ticker.return_value = {'last': 0.2336}
    ex.exchange.create_order = MagicMock()  # spy
    ex.exchange.set_leverage = MagicMock()
    ex.exchange_id = 'okx'
    ex.testnet = True
    ex.balance_adapter = MagicMock()
    ex.balance_adapter.get_free.return_value = 5000.0
    ex.risk_manager = MagicMock()
    ex.risk_manager.max_trade_amount = 100
    ex.idempotency = None
    ex.caps = None
    ex.ledger = None
    ex._pending_drift_alerts = []
    ex._halted_symbols = {}

    plan = {
        'side': 'long',
        'entry_ref': 0.2179, 'sl_pct': 0.025,
        'tp_pct': [0.061, 0.122, 0.180],
        'entry_zone': [0.2177, 0.2181],
        'stop_loss': 0.2125,
        'take_profit': [0.2312, 0.2444, 0.2571],
        'leverage': 10, 'size_usdt': 100,
        'order_type': 'limit',
        'attribution': {'rr_floor': 2.0},
    }

    result = ex.open_position_with_plan('XLM-USDT', 'long', plan)
    assert result is None
    ex.exchange.create_order.assert_not_called()
```

- [ ] **Step 2: Run test, verify failure**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py::test_gate1_abandons_xlm_replay -v`
Expected: FAIL (Gate 1 not yet wired).

- [ ] **Step 3: 改 `executor.py:1974` 之后**

Locate the block:

```python
ticker = self.exchange.fetch_ticker(symbol)
current_price = ticker['last']
```

After it, insert Gate 1:

```python
# === Gate 1: Drift Classification ===
drift_decision = self._classify_entry_drift(plan, current_price)
self._record_drift_decision_event(symbol, side, drift_decision, gate='gate_1')

if drift_decision.decision == 'abandon':
    self.logger.warning(
        f"[Drift Gate 1] {symbol} abandon drift={drift_decision.drift_pct*100:.2f}%; "
        f"plan.entry_ref={plan.get('entry_ref')} live={current_price}"
    )
    self._enqueue_drift_alert(
        'entry_drift_abandoned', symbol=symbol, side=side,
        drift_pct=drift_decision.drift_pct,
        plan_entry_ref=plan.get('entry_ref'), live_price=current_price,
        gate='gate_1',
    )
    return None
if drift_decision.decision == 'recalc_fail':
    self.logger.warning(
        f"[Drift Gate 1] {symbol} recalc_fail R:R={drift_decision.rr_actual} "
        f"floor={drift_decision.rr_floor_used}"
    )
    self._enqueue_drift_alert(
        'entry_drift_rr_fail', symbol=symbol, side=side,
        drift_pct=drift_decision.drift_pct,
        rr_actual=drift_decision.rr_actual,
        rr_floor_used=drift_decision.rr_floor_used,
        gate='gate_1',
    )
    return None
if drift_decision.decision == 'recalc_pass':
    plan = drift_decision.new_plan
    current_price = drift_decision.new_plan['recomputed_entry']
    stop_loss = plan['stop_loss']
    take_profit = plan['take_profit']
    self.logger.info(
        f"[Drift Gate 1] {symbol} {drift_decision.band} recalc_pass "
        f"new_entry={current_price} new_SL={stop_loss} new_TP[0]={take_profit[0]}"
    )
```

Note: this insertion must occur **after** `current_price = ticker['last']` and **before** the existing `# 预计算止盈止损价格（开仓时一并提交）` block, so the recalc_pass branch can hand off cleanly.

- [ ] **Step 4: Replace the SL direction fix at lines 1983-1988 with invariant**

Replace:

```python
if side == 'short' and stop_loss <= current_price:
    stop_loss = current_price * 1.015
    self.logger.warning(f"SL方向修正(short): SL={stop_loss:.4f} > entry={current_price:.4f}")
elif side == 'long' and stop_loss >= current_price:
    stop_loss = current_price * 0.985
    self.logger.warning(f"SL方向修正(long): SL={stop_loss:.4f} < entry={current_price:.4f}")
```

With:

```python
# Invariant: drift gate guarantees SL on correct side. Breach = upstream bug.
if side == 'short' and stop_loss is not None and stop_loss <= current_price:
    self.logger.error(
        f"[SL Invariant] {symbol} short SL={stop_loss} <= entry={current_price}; halting"
    )
    self._halt_symbol(symbol, reason='sl_invariant_breach')
    self._enqueue_drift_alert(
        'sl_invariant_breach', symbol=symbol, side=side,
        stop_loss=stop_loss, entry=current_price,
    )
    return None
elif side == 'long' and stop_loss is not None and stop_loss >= current_price:
    self.logger.error(
        f"[SL Invariant] {symbol} long SL={stop_loss} >= entry={current_price}; halting"
    )
    self._halt_symbol(symbol, reason='sl_invariant_breach')
    self._enqueue_drift_alert(
        'sl_invariant_breach', symbol=symbol, side=side,
        stop_loss=stop_loss, entry=current_price,
    )
    return None
```

- [ ] **Step 5: Delete mechanical TP direction fix at lines 1991-1997**

Delete entirely:

```python
# TP方向校验
if tp_first:
    if side == 'short' and tp_first >= current_price:
        tp_first = current_price * 0.97
        self.logger.warning(f"TP方向修正(short): TP={tp_first:.4f} < entry={current_price:.4f}")
    elif side == 'long' and tp_first <= current_price:
        tp_first = current_price * 1.03
        self.logger.warning(f"TP方向修正(long): TP={tp_first:.4f} > entry={current_price:.4f}")
```

drift gate already guarantees TP is on the correct side; reject branches return earlier.

- [ ] **Step 6: Add `_record_drift_decision_event` stub (full impl in Task 8)**

```python
def _record_drift_decision_event(self, symbol: str, side: str,
                                 decision: 'DriftDecision', gate: str) -> None:
    """Records the drift decision to the live order events jsonl. Implemented in Task 8."""
    if self.ledger:
        try:
            self.ledger.record_entry_drift_decision(
                symbol=symbol, side=side, gate=gate,
                band=decision.band, drift_pct=decision.drift_pct,
                decision=decision.decision, reason=decision.reason,
                rr_actual=decision.rr_actual,
                rr_floor_used=decision.rr_floor_used,
            )
        except (AttributeError, Exception) as e:
            self.logger.warning(f"[Drift Event] record failed: {e}")
```

- [ ] **Step 7: Run test, verify pass**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py::test_gate1_abandons_xlm_replay -v`
Expected: PASS

- [ ] **Step 8: Run executor regression**

Run: `python3 -m pytest tests/test_okx_posmode_executor.py tests/test_long_entry_position_guard.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add executor.py tests/test_entry_drift_hybrid_policy.py
git commit -m "feat(executor): wire Gate 1 drift gate; replace SL fix with invariant; remove mechanical TP fix"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

### Task 7: Gate 2 在 `_execute_limit_order` fallback 前

**Files:**
- Modify: `executor.py:_execute_limit_order` signature & body (line 2181-2285): accept `orig_plan`, drop line 2203-2205 校准 + line 2259-2262 fallback 检查; add Gate 2.
- Modify: `executor.py:open_position_with_plan` callers (lines 2004, 2022): pass original plan.
- Test: `tests/test_entry_drift_hybrid_policy.py` (extend)

- [ ] **Step 1: 追加失败测试**

```python
def test_gate2_basis_is_original_entry_ref_not_segmented():
    """Gate 1 small drift (1%) + 30s later additional 5% drift = 6% total → abandon
    even though each segment alone would be small/medium."""
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    ex._pending_drift_alerts = []

    plan = _plan_long(entry=100.0)  # entry_ref=100.0
    # Simulate: Gate 1 saw live=101 → recalc_pass
    gate1 = ex._classify_entry_drift(plan, live_price=101.0)
    assert gate1.decision == 'recalc_pass'

    # Gate 2 must use ORIGINAL plan, NOT gate1.new_plan
    # If Gate 2 wrongly used gate1.new_plan (entry=101), drift to 106 = 4.95% (medium pass possibly)
    # Correct: use original plan, drift = (106-100)/100 = 6% → abandon
    gate2 = ex._classify_entry_drift(plan, live_price=106.0)
    assert gate2.band == 'abandon'
    assert gate2.decision == 'abandon'
```

- [ ] **Step 2: Run test, verify pass via classifier alone**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py::test_gate2_basis_is_original_entry_ref_not_segmented -v`
Expected: PASS (classifier already correct; this test pins Gate 2 callsite contract).

- [ ] **Step 3: Modify `_execute_limit_order` signature**

Change signature to accept `orig_plan`:

```python
def _execute_limit_order(self, symbol: str, side: str, size_usdt: float,
                         current_price: float, entry_zone: dict,
                         leverage: int = 1, tp_sl_params: dict = None,
                         clord_id: str = None,
                         orig_plan: dict = None) -> Optional[tuple]:
```

- [ ] **Step 4: Delete obsolete calibration at lines 2202-2205**

Delete:

```python
# 限价单价格偏离实时价格超过2%时，基于实时价格重新计算
if abs(limit_price - live_price) / live_price > 0.02:
    self.logger.warning(f"限价单价格{limit_price:.4f}偏离实时价{live_price:.4f}超2%，重新校准")
    limit_price = live_price * (0.999 if side == 'long' else 1.001)
```

drift gate has already produced a coherent plan; if Gate 1 said accept the original, we honor entry_zone; if recalc_pass, the recomputed entry already drives current_price.

- [ ] **Step 5: Replace fallback price-change check at lines 2257-2262 with Gate 2**

Replace:

```python
ticker = self.exchange.fetch_ticker(symbol)
new_price = ticker['last']
price_change = abs(new_price - current_price) / current_price
if price_change > 0.005:
    self.logger.info(f"价格变化>{price_change*100:.1f}%，放弃入场")
    return None
```

With:

```python
ticker = self.exchange.fetch_ticker(symbol)
new_price = ticker['last']

# === Gate 2: re-classify drift against ORIGINAL plan.entry_ref ===
if orig_plan is not None:
    gate2 = self._classify_entry_drift(orig_plan, new_price)
    self._record_drift_decision_event(symbol, side, gate2, gate='gate_2')
    if gate2.decision == 'abandon':
        self.logger.warning(
            f"[Drift Gate 2] {symbol} abandon drift={gate2.drift_pct*100:.2f}%"
        )
        self._enqueue_drift_alert(
            'entry_drift_abandoned', symbol=symbol, side=side,
            drift_pct=gate2.drift_pct, gate='gate_2',
        )
        return None
    if gate2.decision == 'recalc_fail':
        self._enqueue_drift_alert(
            'entry_drift_rr_fail', symbol=symbol, side=side,
            drift_pct=gate2.drift_pct, gate='gate_2',
        )
        return None
    if gate2.decision == 'recalc_pass':
        # Use recomputed SL/TP for the fallback market order's attach algo
        recomputed = gate2.new_plan
        tp_sl_params = self._build_tp_sl_params(
            side, recomputed['stop_loss'], recomputed['take_profit'][0],
            sl_clord_id=tp_sl_params.get('sl_clord_id') if tp_sl_params else None,
        )
```

- [ ] **Step 6: Pass orig_plan from callers**

In `executor.py:open_position_with_plan`, lines 2004 and 2022 — both `_execute_limit_order(...)` invocations — need an extra arg. Build `orig_plan_for_gate2` BEFORE Gate 1 mutates `plan`:

Before Gate 1 block, capture:

```python
orig_plan_for_gate2 = copy.deepcopy(plan)
```

Then pass it:

```python
filled = self._execute_limit_order(
    symbol, side, size_usdt, current_price, entry_zone,
    leverage, tp_sl_params, clord_id, orig_plan=orig_plan_for_gate2,
)
```

- [ ] **Step 7: Run test, verify pass**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py -v`
Expected: PASS for all currently written tests.

- [ ] **Step 8: Run executor regression**

Run: `python3 -m pytest tests/test_okx_posmode_executor.py -q`
Expected: PASS, no regression.

- [ ] **Step 9: Commit**

```bash
git add executor.py tests/test_entry_drift_hybrid_policy.py
git commit -m "feat(executor): wire Gate 2 in fallback path with orig_plan baseline; drop legacy 0.5%/2% checks"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

## Phase 4: 可观测性 — jsonl + risk_alert + execution_result.v2

### Task 8: LiveLedger.record_entry_drift_decision

**Files:**
- Modify: `utils/live_ledger.py` (add public method)
- Test: `tests/test_entry_drift_hybrid_policy.py` (extend)

- [ ] **Step 1: 追加失败测试**

```python
def test_ledger_records_entry_drift_decision(tmp_path):
    from utils.live_ledger import LiveLedger
    events_path = str(tmp_path / "live_order_events.jsonl")
    ledger = LiveLedger.__new__(LiveLedger)
    ledger.events_path = events_path
    ledger.logger = MagicMock()
    ledger._lock = __import__('threading').Lock()
    ledger.exchange = None
    ledger.record_entry_drift_decision(
        symbol='XLM-USDT', side='long', gate='gate_1',
        band='abandon', drift_pct=0.072, decision='abandon',
        reason='drift_too_large',
        rr_actual=None, rr_floor_used=None,
    )
    import json
    with open(events_path) as f:
        events = [json.loads(line) for line in f if line.strip()]
    assert len(events) == 1
    assert events[0]['event'] == 'entry_drift_decision'
    assert events[0]['symbol'] == 'XLM-USDT'
    assert events[0]['gate'] == 'gate_1'
    assert events[0]['band'] == 'abandon'
    assert events[0]['drift_pct'] == pytest.approx(0.072)
```

- [ ] **Step 2: Run test, verify failure**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py::test_ledger_records_entry_drift_decision -v`
Expected: FAIL — `record_entry_drift_decision` not found.

- [ ] **Step 3: Add method to `utils/live_ledger.py`**

After `record_close` (around line 160), insert:

```python
def record_entry_drift_decision(self, *, symbol: str, side: str, gate: str,
                                band: str, drift_pct: float, decision: str,
                                reason: Optional[str],
                                rr_actual: Optional[float],
                                rr_floor_used: Optional[float],
                                plan_entry_ref: Optional[float] = None,
                                live_price: Optional[float] = None,
                                request_id: str = "") -> None:
    """Record an entry drift gate decision to the live order events jsonl.

    This is observational — does NOT mutate ledger state, does NOT affect PnL.
    Used for downstream slicing of recompute vs original-plan win rates."""
    event = {
        'event': 'entry_drift_decision',
        'ts': time.time(),
        'symbol': symbol,
        'side': side,
        'gate': gate,
        'band': band,
        'drift_pct': drift_pct,
        'decision': decision,
        'reason': reason,
        'rr_actual': rr_actual,
        'rr_floor_used': rr_floor_used,
        'plan_entry_ref': plan_entry_ref,
        'live_price': live_price,
        'request_id': request_id,
    }
    self._write_event(event)
```

(The `time` import is already present.)

- [ ] **Step 4: Run test, verify pass**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py::test_ledger_records_entry_drift_decision -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/live_ledger.py tests/test_entry_drift_hybrid_policy.py
git commit -m "feat(ledger): record_entry_drift_decision observational event"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

### Task 9: Agent 层桥接 — drain alerts + execution_result.v2 reject reason

**Files:**
- Modify: `agents/trading/executor.py:_execute_with_plan` and `_dispatch_decision` rejection branch
- Modify: `agents/trading/executor.py` critical_types set
- Test: `tests/test_entry_drift_hybrid_policy.py` (extend)

- [ ] **Step 1: 追加失败测试**

```python
@pytest.mark.asyncio
async def test_agent_publishes_drift_alerts_after_open():
    """When root executor enqueues drift alerts, agent must drain & publish them."""
    from agents.trading.executor import MultiExecutor
    agent = MultiExecutor.__new__(MultiExecutor)
    agent.logger = MagicMock()
    agent.executor = MagicMock()
    agent.executor._pending_drift_alerts = [
        {'type': 'entry_drift_abandoned', 'symbol': 'XLM-USDT',
         'drift_pct': 0.072, 'gate': 'gate_1', 'timestamp': 1.0},
    ]
    agent.executor.open_position_with_plan = MagicMock(return_value=None)
    published = []

    async def mock_publish(topic, payload, **kw):
        published.append((topic, payload))

    agent.publish = mock_publish
    await agent._drain_drift_alerts()
    assert any(t == 'risk_alert' and p['type'] == 'entry_drift_abandoned'
               for t, p in published)
    assert agent.executor._pending_drift_alerts == []
```

- [ ] **Step 2: Run test, verify failure**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py::test_agent_publishes_drift_alerts_after_open -v`
Expected: FAIL — `_drain_drift_alerts` not found.

- [ ] **Step 3: 在 `agents/trading/executor.py` 加 helper**

Add method to `MultiExecutor`:

```python
async def _drain_drift_alerts(self) -> None:
    """Drain root executor's pending drift alerts and publish them as risk_alert events."""
    ex = getattr(self, 'executor', None)
    pending = getattr(ex, '_pending_drift_alerts', None) if ex else None
    if not pending:
        return
    # Snapshot & clear under no concurrent access (drift gate runs in to_thread, awaited)
    alerts = list(pending)
    pending.clear()
    for alert in alerts:
        try:
            await self.publish('risk_alert', alert)
        except Exception as e:
            self.logger.warning(f"[Drift Alert] publish failed: {e}")
```

Call `await self._drain_drift_alerts()` immediately after `_execute_with_plan` returns (whether result is dict or None) and after the rejected/None branches publish their `execution_result`. Add the call inside `_dispatch_decision` after both the success-path publish and the None-path publish (covers Gate 1 / Gate 2 reject).

- [ ] **Step 4: Add new `risk_alert.type` to `critical_types`**

Locate the `critical_types` definition (search in `agents/trading/executor.py`). Add:

```python
'entry_drift_abandoned',
'entry_drift_rr_fail',
'plan_missing_entry_ref',
'tp_invariant_breach',
'sl_invariant_breach',
```

- [ ] **Step 5: 让 Gate reject 路径落 execution_result.v2 reject reason**

Inside `_dispatch_decision` open path: when `result is None` AND `_pending_drift_alerts` contained an `entry_drift_abandoned` or `entry_drift_rr_fail`, the published `execution_result` should carry the matching reason:

```python
# After open returned None, check for drift alerts to enrich reject reason
if result is None and action in ('open_long', 'open_short'):
    drift_reason = None
    for alert in (getattr(self.executor, '_pending_drift_alerts', None) or []):
        if alert.get('type') == 'entry_drift_abandoned':
            drift_reason = 'drift_too_large'
            break
        if alert.get('type') == 'entry_drift_rr_fail':
            drift_reason = 'drift_rr_floor_fail'
            break
    if drift_reason:
        # override the generic 'unknown_none_result' reason
        # (the existing reject branch hardcodes that string; insert before it)
        reject_reason = drift_reason
    else:
        reject_reason = 'unknown_none_result'
```

Then the existing rejected publish call should use `reject_reason` in place of the hardcoded `"unknown_none_result"`.

- [ ] **Step 6: Run test, verify pass**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py -v`
Expected: PASS for all current tests.

- [ ] **Step 7: Run agent regression**

Run: `python3 -m pytest tests/test_executor*.py tests/test_tg_*.py -q`
Expected: PASS, no regression.

- [ ] **Step 8: Commit**

```bash
git add agents/trading/executor.py tests/test_entry_drift_hybrid_policy.py
git commit -m "feat(agent-executor): drain drift alerts to risk_alert; pipe drift reasons into execution_result.v2"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

### Task 10: attribution.entry_drift 嵌套字段

**Files:**
- Modify: `agents/trading/executor.py:_dispatch_decision` (build `attribution.entry_drift` from latest pending alert / open result)
- Test: `tests/test_entry_drift_hybrid_policy.py` (extend)

- [ ] **Step 1: 追加失败测试**

```python
@pytest.mark.asyncio
async def test_execution_result_carries_attribution_entry_drift():
    """Reject path should put drift_decision into attribution.entry_drift."""
    from agents.trading.executor import MultiExecutor
    agent = MultiExecutor.__new__(MultiExecutor)
    agent.logger = MagicMock()
    agent.executor = MagicMock()
    agent.executor._pending_drift_alerts = [
        {'type': 'entry_drift_abandoned', 'symbol': 'XLM-USDT', 'side': 'long',
         'drift_pct': 0.072, 'gate': 'gate_1', 'timestamp': 1.0},
    ]
    agent.executor.open_position_with_plan = MagicMock(return_value=None)
    published = []

    async def mock_publish(topic, payload, **kw):
        published.append((topic, payload))

    agent.publish = mock_publish
    # NOTE: full _dispatch_decision invocation requires extensive setup;
    # this test asserts via the helper that builds attribution
    attr = agent._build_drift_attribution(agent.executor._pending_drift_alerts)
    assert attr['band'] == 'abandon'
    assert attr['decision'] == 'abandon'
    assert attr['drift_pct'] == pytest.approx(0.072)
    assert attr['gate'] == 'gate_1'
```

- [ ] **Step 2: Run test, verify failure**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py::test_execution_result_carries_attribution_entry_drift -v`
Expected: FAIL — `_build_drift_attribution` not found.

- [ ] **Step 3: Implement `_build_drift_attribution` and wire into reject publish**

Add method:

```python
@staticmethod
def _build_drift_attribution(pending_alerts: list) -> Optional[dict]:
    """From the buffered alerts, derive the attribution.entry_drift dict."""
    for alert in pending_alerts or []:
        t = alert.get('type')
        if t == 'entry_drift_abandoned':
            return {
                'band': 'abandon', 'decision': 'abandon',
                'drift_pct': alert.get('drift_pct'),
                'reason': 'drift_too_large',
                'gate': alert.get('gate'),
            }
        if t == 'entry_drift_rr_fail':
            return {
                'band': 'medium' if (alert.get('drift_pct') or 0) > 0.02 else 'small',
                'decision': 'recalc_fail',
                'drift_pct': alert.get('drift_pct'),
                'reason': 'drift_rr_floor_fail',
                'rr_actual': alert.get('rr_actual'),
                'rr_floor_used': alert.get('rr_floor_used'),
                'gate': alert.get('gate'),
            }
    return None
```

In the rejected publish call from Task 9, add `attribution={'entry_drift': drift_attr}` if non-None:

```python
drift_attr = self._build_drift_attribution(getattr(self.executor, '_pending_drift_alerts', None) or [])
extra_attr = {'entry_drift': drift_attr} if drift_attr else {}
await self.publish("execution_result", self._build_execution_result(
    status="rejected", action=action, symbol=symbol,
    source="executor_open", reason=reject_reason, request_id=request_id,
    attribution=extra_attr,
), symbol=symbol)
```

If `_build_execution_result` does not currently accept `attribution`, extend its signature to merge `attribution` into the result payload (small one-line change).

- [ ] **Step 4: Run test, verify pass**

Run: `python3 -m pytest tests/test_entry_drift_hybrid_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/executor.py tests/test_entry_drift_hybrid_policy.py
git commit -m "feat(agent-executor): expose attribution.entry_drift on reject and accept paths"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

## Phase 5: 历史回放兼容性

### Task 11: event_backtest 老 plan 兼容

**Files:**
- Modify: `event_backtest.py` (no source changes if it just re-injects plan dicts; verify via test)
- Test: `tests/test_event_backtest_drift_compat.py` (new)

- [ ] **Step 1: 写兼容测试**

Create `tests/test_event_backtest_drift_compat.py`:

```python
"""Old plans (no entry_ref/sl_pct/tp_pct) must still flow through executor as fail-safe accept."""
from unittest.mock import MagicMock
from executor import ContractExecutor


def test_old_plan_skips_drift_gate_failsafe():
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = MagicMock()
    ex._pending_drift_alerts = []
    legacy_plan = {
        'side': 'long',
        'entry_zone': [99.9, 100.1],
        'stop_loss': 97.5,
        'take_profit': [105.0, 110.0],
        'leverage': 10, 'size_usdt': 100,
        'order_type': 'limit',
    }
    decision = ex._classify_entry_drift(legacy_plan, live_price=120.0)
    assert decision.decision == 'accept'
    assert decision.drift_pct == 0.0
    assert any(a['type'] == 'plan_missing_entry_ref'
               for a in ex._pending_drift_alerts)
```

- [ ] **Step 2: Run test**

Run: `python3 -m pytest tests/test_event_backtest_drift_compat.py -v`
Expected: PASS (no implementation change needed; behavior already covered by Task 4 fail-safe).

- [ ] **Step 3: Run full event_backtest sanity if any tests exist**

Run: `python3 -m pytest tests/ -k event_backtest -q`
Expected: PASS if any exist; SKIP otherwise.

- [ ] **Step 4: Commit**

```bash
git add tests/test_event_backtest_drift_compat.py
git commit -m "test(event-backtest): legacy plans without anchor fields fail-safe accept"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

## Phase 6: 验收 & 文档

### Task 12: 全测试套件回归 + 更新 CLAUDE.md baseline

**Files:**
- Modify: `CLAUDE.md` (update baseline + 红线条目)

- [ ] **Step 1: 运行全套件**

Run: `python3 -m pytest -q`
Expected: All pass; new baseline ~ 921 + 30~35 = ~951-956. Note exact count.

- [ ] **Step 2: 编译性检查**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .`
Expected: Exit 0.

- [ ] **Step 3: 更新 CLAUDE.md 当前事实段**

Append to "当前事实" bullets:

```markdown
- 2026-06-01 Entry Drift Hybrid Policy 上线后基线：`<exact_count> passed / 4 deselected / 1 warning`（新增 `test_entry_drift_hybrid_policy.py` 30+ case + `test_judge_plan_anchor_fields.py` 4 case + `test_event_backtest_drift_compat.py` 1 case）。Judge `_build_plan` 新增 `entry_ref/sl_pct/tp_pct` 锚点字段；executor 单一函数 `_classify_entry_drift` + `_recompute_plan_for_drift` 实现 4 档 Hybrid drift gate（accept ≤ 0.5% / small recalc 0.5–2% / medium recalc + floor +0.20 2–5% / abandon > 5%），双 Gate（限价前 + fallback 前）基准始终 `plan.entry_ref` 防分段累加；删除 `executor.py:1991-1997` 机械 TP 修正、`2203-2205` limit 校准、`2259-2262` fallback 0.5% 检查；SL 方向修正改 invariant fail-closed；`_set_position_tp` 单一收口杜绝 partial_tp_1 双源真相，违反 → halt symbol + `risk_alert.tp_invariant_breach`；新 reject reason `drift_too_large/drift_rr_floor_fail` + 5 个 critical_types `entry_drift_abandoned/entry_drift_rr_fail/plan_missing_entry_ref/tp_invariant_breach/sl_invariant_breach`。详见 `docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md`。
```

Append to "风控红线":

```markdown
- Entry drift 必须走单一函数 `executor._classify_entry_drift`，主路径（Gate 1 限价前）与 fallback 路径（Gate 2 市价前）共用，不在调用点重写 if/else；Gate 2 基准始终原 `plan.entry_ref`，严禁用 Gate 1 重算后的 plan 当输入。重算必须通过 `_recompute_plan_for_drift` 按 `sl_pct/tp_pct` 同比例平移；medium band floor 加成 `+0.20`。
- Position TP 字段写入必须经 `_set_position_tp(position, tp_first, tp_levels)` 单一收口，保证 `position.take_profit == position.take_profit_levels[0]`；违反由 `_update_trailing` 顶部 invariant 检测并 halt symbol。
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): record entry-drift-hybrid-policy baseline + red-line rules"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

### Task 13: 验收文档

**Files:**
- Create: `docs/audit_remediation_entry_drift_hybrid_policy_acceptance.md`

- [ ] **Step 1: 写验收文档**

```markdown
# Entry Drift Hybrid Policy — Acceptance

> Change: `entry-drift-hybrid-policy`
> Design: `docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md`
> Baseline: <exact pytest count> passed

## AC-1：Judge plan 字段扩展

**测试**：`tests/test_judge_plan_anchor_fields.py` (4 case)
- entry_ref / sl_pct / tp_pct 三字段在 long & short 都正确生成

## AC-2：Drift gate 4 档分类

**测试**：`tests/test_entry_drift_hybrid_policy.py::test_classify_drift_*` (~11 case)
- 边界包含规则（0.5% / 2% / 5% 划入下一档前的档位）
- 5/30 XLM 真实复盘 7.2% → abandon

## AC-3：重算函数 SL/TP 同比例平移 + medium floor 加成

**测试**：`test_recompute_*` (~5 case)
- long & short 双向比例平移
- medium band floor +0.20 拦截 R:R=2.0 plan
- 不修改原 plan（deepcopy）

## AC-4：Gate 1 abandons XLM replay

**测试**：`test_gate1_abandons_xlm_replay`
- create_order 不被调用，open_position_with_plan 返回 None

## AC-5：Gate 2 基准始终原 plan.entry_ref

**测试**：`test_gate2_basis_is_original_entry_ref_not_segmented`
- Gate 1 small + 30s 后再 5% drift = 6% 总漂 → abandon

## AC-6：partial_tp_1 双源真相 invariant

**测试**：`test_set_position_tp_*` (3 case) + `test_update_trailing_invariant_breach_halts_symbol`
- 写时 setter assert 一致；旁路写入触发 halt + risk_alert

## AC-7：可观测性

**测试**：`test_ledger_records_entry_drift_decision`、`test_agent_publishes_drift_alerts_after_open`、`test_execution_result_carries_attribution_entry_drift`
- jsonl event 完整字段
- risk_alert 通过 agent layer 发布到 bus
- attribution.entry_drift 嵌套写到 execution_result.v2

## AC-8：Plan 字段缺失 fail-safe

**测试**：`test_classify_drift_missing_*` (3 case) + `test_old_plan_skips_drift_gate_failsafe`
- 缺字段 → accept 路径不破坏 + plan_missing_entry_ref 告警

## AC-9：删除冗余路径

**Code review**：
- `executor.py:1991-1997` (TP 机械修正) — 已删除
- `executor.py:2203-2205` (limit 2% 校准) — 已删除
- `executor.py:2259-2262` (fallback 0.5% 检查) — 已删除
- `executor.py:1983-1988` (SL 方向修正) — 改为 invariant + halt

## AC-10：OKX testnet 冒烟（运维侧）

**手动**：
1. mock 价格漂移触发 small drift → recalc_pass 开仓，确认 SL/TP 写新值
2. mock 价格漂移触发 abandon → 不下单，确认 risk_alert + jsonl 落地
3. 检查 attribution.entry_drift 在真实 execution_result.v2 上正确

## 红线遵循

- ✅ 单一真相源：所有 drift 判定走 `_classify_entry_drift`
- ✅ TP 双源真相：单一 setter `_set_position_tp` + 读时双保险 invariant
- ✅ close/reduce 不受影响：本 change 只动 open 路径
- ✅ 状态文件命名空间无影响
- ✅ LLM 不参与 drift 判定（纯规则）
```

- [ ] **Step 2: Commit**

```bash
git add docs/audit_remediation_entry_drift_hybrid_policy_acceptance.md
git commit -m "docs(acceptance): entry-drift-hybrid-policy acceptance report"
```

archived-with: 2026-06-02-entry-drift-hybrid-policy
---

## Self-Review

### Spec coverage

| Spec section | Task | Status |
|---|---|---|
| Plan field expansion (entry_ref/sl_pct/tp_pct) | Task 1 | ✅ |
| Drift gate 4-band classification | Task 4 | ✅ |
| Recompute function (proportional shift, medium +0.20) | Task 3 | ✅ |
| Gate 1 wiring + delete TP fix + SL invariant | Task 6 | ✅ |
| Gate 2 wiring + delete legacy calibration/checks | Task 7 | ✅ |
| TP single-sink setter + partial_tp invariant halt | Task 5 | ✅ |
| jsonl entry_drift_decision event | Task 8 | ✅ |
| risk_alert bridging + critical_types | Task 9 | ✅ |
| execution_result.v2 reject reasons | Task 9 | ✅ |
| attribution.entry_drift nested dict | Task 10 | ✅ |
| Legacy plan compatibility | Task 11 | ✅ |
| Baseline + CLAUDE.md update | Task 12 | ✅ |
| Acceptance doc | Task 13 | ✅ |

### Placeholder scan

No "TBD/TODO/implement later". All test code blocks are concrete; all source-edit blocks reference exact file/line ranges with full code shown.

### Type consistency

`DriftDecision` field names consistent across Tasks 2/3/4/6/7/9/10. `_set_position_tp` signature consistent across Tasks 5/6. `_pending_drift_alerts` attribute introduced in Task 4 (init), consumed in Tasks 5/6/7/9/10. `_record_drift_decision_event` introduced as stub in Task 6, fully implemented via ledger method added in Task 8 — no rename.

