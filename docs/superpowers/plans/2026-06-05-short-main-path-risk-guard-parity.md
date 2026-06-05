---
change: short-main-path-risk-guard-parity
design-doc: docs/superpowers/specs/2026-06-05-short-main-path-risk-guard-parity-design.md
base-ref: 2023e464bbe2da71223b5753336157a4f2fe120b
---

# Short Main Path Risk Guard Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Judge main-path and deferred-path short entries share one structural short risk gate while preserving the existing `RSI <= 30` hard no-short threshold.

**Architecture:** Add one structured short gate helper in `agents/trading/judge.py` and route both main and deferred short candidates through it. Keep the existing `RSI <= 30` hard gate in place, use LLM reversal-risk text only as attribution/tightening context, and emit versioned short gate metadata for accepted and rejected candidates.

**Tech Stack:** Python 3, pytest, existing `MultiJudge` methods, existing OpenSpec change `short-main-path-risk-guard-parity`.

---

## File Structure

- Modify: `agents/trading/judge.py`
  - Add a small structured short-gate result helper.
  - Normalize main/deferred callers to use the helper before executable `open_short` publication.
  - Add short gate attribution fields to pass/reject paths.
  - Preserve existing `RSI <= 30` hard gate blocks unchanged.
- Create: `tests/test_short_main_path_risk_guard.py`
  - Unit coverage for daily-bullish main rejection, deferred parity, RSI hard-threshold separation, LLM risk attribution, and parse-failure structural rejection.
- Modify: `openspec/changes/short-main-path-risk-guard-parity/tasks.md`
  - Check off completed OpenSpec implementation tasks as each task lands.
- No change: executor, OKX order code, protective SL lifecycle, long entry guard behavior.

## Implementation Notes

Use these names consistently:

```python
SHORT_GATE_VERSION = "short_main_path_parity_v1"

# result dict shape returned by helper
{
    "allowed": bool,
    "decision": "pass" | "reject" | "probe",
    "reason": "" | "daily_bearish_required" | "range_position_too_low" | "pre_move_too_deep" | "rsi_too_low_for_short" | "short_score_too_low" | "htf_votes_insufficient",
    "llm_short_reversal_risk": bool,
    "metrics": {
        "range_position_24h": float,
        "pre_12h_return_pct": float,
        "rsi": float,
        "htf_bearish_votes": int,
    },
}
```

The helper should be private to `MultiJudge`, e.g. `_classify_short_entry_risk(...)`, and should avoid publishing or recording by itself. Callers decide whether to publish hold, route probe, or continue.

## Task 1: Add focused failing tests for the short gate helper

**Files:**
- Create: `tests/test_short_main_path_risk_guard.py`
- Modify later: `agents/trading/judge.py`

- [ ] **Step 1: Create the test file with fixtures and failing helper tests**

Write this file:

```python
import pytest
from unittest.mock import MagicMock

from agents.trading.judge import MultiJudge as Judge


def _make_judge():
    j = Judge.__new__(Judge)
    j.logger = MagicMock()
    j._short_regime_guard_enabled = True
    j._short_live_require_daily_bearish = True
    j._short_live_min_range_pos = 0.45
    j._short_live_max_pre_move = -0.01
    j._short_live_min_rsi = 40
    j._short_live_min_score = 55
    j._short_live_min_htf_votes = 2
    j._probe_short_enabled = True
    j._probe_short_active = None
    j._probe_short_sl_count = 0
    j._probe_short_cooldown_until = 0
    j._probe_short_max_concurrent = 1
    j._probe_short_cooldown_hours = 24
    j._probe_rr_floor = 1.30
    j._probe_short_max_position_pct = 0.3
    j._probe_short_max_leverage = 3
    rm = MagicMock()
    rm.snapshot.return_value = {
        'effective_regime': 'bearish',
        'raw_regime': 'bearish',
        'confidence': 74,
    }
    rm._effective_regime = 'bearish'
    j._regime_manager = rm
    return j


def _plan():
    return {
        'side': 'short',
        'entry_ref': 2.239,
        'entry_zone': [2.249, 2.251],
        'stop_loss': 2.414,
        'take_profit': [1.922, 1.757, 1.593],
        'leverage': 20,
        'size_usdt': 30.0,
        'risk_reward_ratio': 2.0,
        'effective_risk_reward_ratio': 1.95,
    }


def _tech(**overrides):
    tech = {
        'trend': {
            'direction': 'bearish',
            'higher_tf_bias': 'bearish',
            'daily_bias': 'bullish',
        },
        'short_context': {
            'position_in_24h_range': 0.1014,
            'pre_12h_return_pct': -0.0771,
            'prev_daily_return_pct': -0.2195,
        },
        'indicators': {'rsi': 31.5},
        'momentum': {'rsi': 31.5},
        'entry_timing': {'tf_15m_confirm_short': True},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(tech.get(key), dict):
            tech[key].update(value)
        else:
            tech[key] = value
    return tech


def test_main_short_gate_rejects_daily_bullish_near_shape():
    j = _make_judge()

    result = j._classify_short_entry_risk(
        'NEAR-USDT', 'open_short', _plan(), _tech(), score=-45.0,
        llm_result={'action': 'hold', 'reasoning': 'RSI=31.5处于超卖区域且出现看涨背离，禁止做空'}
    )

    assert result['allowed'] is False
    assert result['decision'] == 'reject'
    assert result['reason'] == 'daily_bearish_required'
    assert result['llm_short_reversal_risk'] is True
    assert result['short_gate_version'] == 'short_main_path_parity_v1'


def test_parse_failure_default_hold_still_rejects_structural_risk():
    j = _make_judge()

    result = j._classify_short_entry_risk(
        'NEAR-USDT', 'open_short', _plan(), _tech(), score=-45.3,
        llm_result={'action': 'hold', 'reasoning': '', 'key_factors': [], 'risk_warnings': []}
    )

    assert result['allowed'] is False
    assert result['reason'] == 'daily_bearish_required'
    assert result['llm_short_reversal_risk'] is False


def test_rsi_above_hard_threshold_can_fail_structural_gate_without_changing_hard_gate():
    j = _make_judge()
    tech = _tech(indicators={'rsi': 34.0}, momentum={'rsi': 34.0})
    tech['trend']['daily_bias'] = 'bearish'
    tech['short_context']['position_in_24h_range'] = 0.60
    tech['short_context']['pre_12h_return_pct'] = -0.02

    result = j._classify_short_entry_risk(
        'NEAR-USDT', 'open_short', _plan(), tech, score=-60.0,
        llm_result={'action': 'hold', 'reasoning': ''}
    )

    assert result['allowed'] is False
    assert result['reason'] == 'pre_move_too_deep'
    assert result['metrics']['rsi'] == pytest.approx(34.0)


def test_structurally_clean_short_passes_with_versioned_attribution():
    j = _make_judge()
    tech = _tech(
        trend={'direction': 'bearish', 'higher_tf_bias': 'bearish', 'daily_bias': 'bearish'},
        short_context={'position_in_24h_range': 0.60, 'pre_12h_return_pct': -0.005},
        indicators={'rsi': 44.0},
        momentum={'rsi': 44.0},
    )

    result = j._classify_short_entry_risk(
        'NEAR-USDT', 'open_short', _plan(), tech, score=-60.0,
        llm_result={'action': 'hold', 'reasoning': '观望'}
    )

    assert result['allowed'] is True
    assert result['decision'] == 'pass'
    assert result['reason'] == ''
    assert result['short_gate_version'] == 'short_main_path_parity_v1'
    assert result['metrics']['htf_bearish_votes'] == 3
```

- [ ] **Step 2: Run the new tests to verify they fail because the helper is missing**

Run:

```bash
python3 -m pytest -q tests/test_short_main_path_risk_guard.py
```

Expected: FAIL with `AttributeError: 'MultiJudge' object has no attribute '_classify_short_entry_risk'`.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_short_main_path_risk_guard.py
git commit -m "test: cover short main path risk gate parity"
```

## Task 2: Implement the single short gate helper

**Files:**
- Modify: `agents/trading/judge.py`
- Test: `tests/test_short_main_path_risk_guard.py`

- [ ] **Step 1: Add a helper constant near the Judge class setup**

In `agents/trading/judge.py`, add a module-level constant near imports or class-level constants:

```python
SHORT_GATE_VERSION = "short_main_path_parity_v1"
```

- [ ] **Step 2: Add `_detect_llm_short_reversal_risk` helper**

Add this method inside `MultiJudge`, near `_check_entry_position_policy` so short-risk helpers live together:

```python
    def _detect_llm_short_reversal_risk(self, llm_result: dict) -> bool:
        llm_result = llm_result or {}
        if llm_result.get('action') != 'hold':
            return False
        parts = [str(llm_result.get('reasoning') or '')]
        for key in ('key_factors', 'risk_warnings'):
            value = llm_result.get(key) or []
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
            else:
                parts.append(str(value))
        text = ' '.join(parts)
        terms = ('禁止做空', '超卖', '看涨背离', 'bullish_div', '支撑', '追空风险')
        return any(term in text for term in terms)
```

- [ ] **Step 3: Add `_classify_short_entry_risk` helper**

Add this method below `_detect_llm_short_reversal_risk`:

```python
    def _classify_short_entry_risk(self, symbol: str, action: str, plan: dict,
                                   tech: dict, score: float,
                                   llm_result: dict = None) -> dict:
        tech = tech or {}
        plan = plan or {}
        trend = tech.get('trend', {}) or {}
        short_ctx = tech.get('short_context') or tech.get('entry_context') or {}
        indicators = tech.get('indicators', {}) or {}
        momentum = tech.get('momentum', {}) or {}

        result = {
            'allowed': True,
            'decision': 'pass',
            'reason': '',
            'short_gate_version': SHORT_GATE_VERSION,
            'llm_short_reversal_risk': self._detect_llm_short_reversal_risk(llm_result),
            'metrics': {},
        }

        if 'short' not in (action or '') or not getattr(self, '_short_regime_guard_enabled', True):
            return result
        if plan.get('is_probe'):
            result['decision'] = 'probe'
            return result

        range_pos = float(short_ctx.get('position_in_24h_range', 0.5) or 0.5)
        pre_move = float(short_ctx.get('pre_12h_return_pct', 0.0) or 0.0)
        rsi_val = float(indicators.get('rsi', momentum.get('rsi', 50)) or 50)
        daily_bias = trend.get('daily_bias', 'neutral')
        htf_bearish = sum(
            1 for d in (trend.get('direction'), trend.get('higher_tf_bias'), daily_bias)
            if d == 'bearish'
        )
        result['metrics'] = {
            'range_position_24h': round(range_pos, 4),
            'pre_12h_return_pct': round(pre_move, 4),
            'rsi': round(rsi_val, 4),
            'htf_bearish_votes': htf_bearish,
        }

        reason = ''
        if getattr(self, '_short_live_require_daily_bearish', True) and daily_bias != 'bearish':
            reason = 'daily_bearish_required'
        elif range_pos < getattr(self, '_short_live_min_range_pos', 0.45):
            reason = 'range_position_too_low'
        elif pre_move <= getattr(self, '_short_live_max_pre_move', -0.01):
            reason = 'pre_move_too_deep'
        elif rsi_val < getattr(self, '_short_live_min_rsi', 40):
            reason = 'rsi_too_low_for_short'
        elif abs(score or 0) < getattr(self, '_short_live_min_score', 55):
            reason = 'short_score_too_low'
        elif htf_bearish < getattr(self, '_short_live_min_htf_votes', 2):
            reason = 'htf_votes_insufficient'

        if reason:
            result['allowed'] = False
            result['decision'] = 'reject'
            result['reason'] = reason
        return result
```

- [ ] **Step 4: Run helper tests**

Run:

```bash
python3 -m pytest -q tests/test_short_main_path_risk_guard.py
```

Expected: PASS.

- [ ] **Step 5: Commit the helper implementation**

```bash
git add agents/trading/judge.py tests/test_short_main_path_risk_guard.py
git commit -m "feat: add structural short entry risk classifier"
```

## Task 3: Apply the helper to main-path short publication

**Files:**
- Modify: `agents/trading/judge.py`
- Modify: `tests/test_short_main_path_risk_guard.py`

- [ ] **Step 1: Add a test proving main path can reject before ranking**

Append this async test to `tests/test_short_main_path_risk_guard.py`:

```python
@pytest.mark.asyncio
async def test_main_path_short_gate_publishes_hold_before_ranking(monkeypatch):
    j = _make_judge()
    j._counterfactual_ledger = MagicMock()
    j._counterfactual_ledger._enabled = False
    j._available_balance = 1000.0
    j._confidence_split_enabled = True
    j._symbol_state = {}
    j._active_positions = {}
    j._pending_decisions = {}
    j._candidate_ranker = MagicMock()
    published = []

    async def fake_publish(topic, payload, symbol=None):
        published.append((topic, payload, symbol))

    j.publish = fake_publish
    monkeypatch.setattr(j, '_compute_score', lambda tech: -45.0)
    monkeypatch.setattr(j, '_ask_llm', lambda symbol, tech, score: {
        'action': 'hold',
        'confidence': 45,
        'reasoning': 'RSI=31.5处于超卖区域且出现看涨背离，禁止做空',
        'key_factors': [],
        'risk_warnings': [],
    })
    monkeypatch.setattr(j, '_build_plan', lambda tech, action, price, confidence, score: _plan())
    monkeypatch.setattr(j, '_open_quality_rejection', lambda *args, **kwargs: None)
    monkeypatch.setattr(j, '_check_expected_value', lambda *args, **kwargs: True)

    tech = _tech()
    tech['symbol'] = 'NEAR-USDT'
    tech['price'] = 2.239
    await j._handle_tech_analysis({'symbol': 'NEAR-USDT', 'analysis': tech})

    assert published
    topic, payload, symbol = published[-1]
    assert topic == 'trade_decision'
    assert payload['action'] == 'hold'
    assert payload['reasoning'] == 'daily_bearish_required'
    assert payload['attribution']['short_gate_decision'] == 'reject'
    assert payload['attribution']['short_gate_reason'] == 'daily_bearish_required'
    assert payload['attribution']['llm_short_reversal_risk'] is True
    assert j._candidate_ranker._compute_rank_score.call_count == 0
```

- [ ] **Step 2: Run the new async test to verify it fails**

Run:

```bash
python3 -m pytest -q tests/test_short_main_path_risk_guard.py::test_main_path_short_gate_publishes_hold_before_ranking
```

Expected: FAIL because main path still publishes/ranks an open decision.

- [ ] **Step 3: Add a small attribution helper**

In `agents/trading/judge.py`, add this method near `_rejection_attribution` or near the short helper:

```python
    def _apply_short_gate_attribution(self, attribution: dict, gate: dict) -> dict:
        attribution = attribution or {}
        gate = gate or {}
        attribution['short_gate_version'] = gate.get('short_gate_version', SHORT_GATE_VERSION)
        attribution['short_gate_decision'] = gate.get('decision', 'pass')
        attribution['short_gate_reason'] = gate.get('reason', '')
        attribution['llm_short_reversal_risk'] = gate.get('llm_short_reversal_risk', False)
        metrics = gate.get('metrics') or {}
        if metrics:
            attribution['short_gate_metrics'] = metrics
        return attribution
```

- [ ] **Step 4: Call the gate in the main path before ranking**

In `agents/trading/judge.py`, in the main open path after EV passes and before `_check_entry_position_policy(...)`, insert logic equivalent to:

```python
                    short_gate = self._classify_short_entry_risk(
                        symbol, final_action, plan, tech, score, llm_result
                    )
                    if final_action == 'open_short' and not short_gate['allowed']:
                        block_reason = short_gate['reason']
                        self._record_rejected_plan(
                            symbol, final_action, plan, score, final_conf, block_reason,
                            self._apply_short_gate_attribution(
                                self._rejection_attribution(final_action, plan, block_reason, tech=tech),
                                short_gate,
                            )
                        )
                        attr = self._apply_short_gate_attribution(
                            self._rejection_attribution(final_action, plan, block_reason, tech=tech),
                            short_gate,
                        )
                        decision = {
                            "symbol": symbol, "timestamp": time.time(),
                            "action": "hold", "confidence": 0,
                            "plan": None, "size_pct": 0,
                            "reasoning": block_reason,
                            "key_factors": [f"blocked_by={block_reason}"],
                            "risk_warnings": [block_reason],
                            "attribution": attr,
                        }
                        await self.publish("trade_decision", decision, symbol=symbol)
                        return
```

Do not move or change the existing `if final_action == 'open_short' and rsi <= 30:` block at `agents/trading/judge.py:1356`; it must remain the hard threshold behavior.

- [ ] **Step 5: Add pass attribution before executable main publication**

When building `attribution = self._build_attribution(...)` for an accepted open decision, call:

```python
                    if final_action == 'open_short':
                        short_gate = self._classify_short_entry_risk(
                            symbol, final_action, plan, tech, score, llm_result
                        )
                        attribution = self._apply_short_gate_attribution(attribution, short_gate)
```

If the code already has `short_gate` in scope from Step 4, reuse it instead of calling again.

- [ ] **Step 6: Run main-path tests**

Run:

```bash
python3 -m pytest -q tests/test_short_main_path_risk_guard.py::test_main_path_short_gate_publishes_hold_before_ranking tests/test_short_main_path_risk_guard.py
```

Expected: PASS.

- [ ] **Step 7: Commit main-path enforcement**

```bash
git add agents/trading/judge.py tests/test_short_main_path_risk_guard.py
git commit -m "fix: enforce short gate before main path publication"
```

## Task 4: Normalize deferred route parity and attribution

**Files:**
- Modify: `agents/trading/judge.py`
- Modify: `tests/test_short_main_path_risk_guard.py`

- [ ] **Step 1: Add route-parity helper test**

Append this test:

```python
def test_deferred_and_main_short_gate_return_same_rejection():
    j = _make_judge()
    plan = _plan()
    tech = _tech()
    llm_result = {'action': 'hold', 'reasoning': '禁止做空，支撑附近'}

    main_gate = j._classify_short_entry_risk(
        'NEAR-USDT', 'open_short', plan, tech, score=-45.0, llm_result=llm_result
    )
    deferred_gate = j._classify_short_entry_risk(
        'NEAR-USDT', 'open_short', plan, tech, score=-45.0, llm_result=llm_result
    )

    assert main_gate['allowed'] is False
    assert deferred_gate['allowed'] is False
    assert main_gate['reason'] == deferred_gate['reason'] == 'daily_bearish_required'
    assert main_gate['short_gate_version'] == deferred_gate['short_gate_version']
```

- [ ] **Step 2: Replace duplicated deferred short guard usage with the helper**

In the three deferred sections around these anchors:

- `agents/trading/judge.py:758` (`deferred_15m_confirmation`)
- `agents/trading/judge.py:872` (`deferred_pullback`)
- `agents/trading/judge.py:977` (`deferred_chase`)

ensure short candidates use `_classify_short_entry_risk(...)` for the side-aware short structural checks. The existing `_apply_regime_policy(...)` can still own R:R floor/low-RR mutation, but it must not be the only place where daily/range/pre-move/score short rejection happens.

Use this pattern before `_apply_regime_policy(...)` in each deferred branch:

```python
                short_gate = self._classify_short_entry_risk(
                    symbol, def_action, plan, tech, deferred.get('signal_score', 50),
                    llm_result=None,
                )
                if def_action == 'open_short' and not short_gate['allowed']:
                    block_reason = short_gate['reason']
                    self.logger.info(f"[Judge] {symbol} deferred short gate blocked: {block_reason}")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, block_reason, [block_reason])
                    return
```

Do not remove the existing `_apply_regime_policy(...)` call until R:R floor and probe behavior are covered elsewhere. If `_apply_regime_policy(...)` would duplicate recording for the same short gate, remove only the duplicate side-aware short gate portion after tests prove parity; leave dynamic R:R floor behavior intact.

- [ ] **Step 3: Add pass attribution to deferred accepted decisions**

In deferred branches where accepted `attribution` is built with `_build_attribution(...)`, apply:

```python
                if def_action == 'open_short':
                    attribution = self._apply_short_gate_attribution(attribution, short_gate)
```

- [ ] **Step 4: Run route parity tests**

Run:

```bash
python3 -m pytest -q tests/test_short_main_path_risk_guard.py
```

Expected: PASS.

- [ ] **Step 5: Commit deferred parity**

```bash
git add agents/trading/judge.py tests/test_short_main_path_risk_guard.py
git commit -m "fix: align deferred short gate with main path"
```

## Task 5: Preserve RSI hard-threshold behavior with regression coverage

**Files:**
- Modify: `tests/test_short_main_path_risk_guard.py`
- Modify if needed: `agents/trading/judge.py`

- [ ] **Step 1: Add explicit test for RSI<=30 hard gate separation**

Append this test:

```python
def test_rsi_hard_threshold_is_not_renamed_or_moved():
    j = _make_judge()
    tech = _tech(indicators={'rsi': 30.0}, momentum={'rsi': 30.0})
    tech['trend']['daily_bias'] = 'bearish'
    tech['short_context']['position_in_24h_range'] = 0.80
    tech['short_context']['pre_12h_return_pct'] = 0.0

    gate = j._classify_short_entry_risk(
        'NEAR-USDT', 'open_short', _plan(), tech, score=-70.0,
        llm_result={'action': 'hold', 'reasoning': ''}
    )

    assert gate['reason'] == 'rsi_too_low_for_short'
    assert gate['metrics']['rsi'] == pytest.approx(30.0)
```

This helper-level test confirms the structural gate still sees low RSI separately. The existing main/deferred code-level `RSI <= 30` blocks must remain in place and should continue to publish the existing RSI pullback/hold reason.

- [ ] **Step 2: Run related tests**

Run:

```bash
python3 -m pytest -q tests/test_short_main_path_risk_guard.py tests/test_judge_plan_anchor_fields.py tests/test_pullback_atr_policy.py
```

Expected: PASS.

- [ ] **Step 3: Commit RSI preservation coverage**

```bash
git add tests/test_short_main_path_risk_guard.py agents/trading/judge.py
git commit -m "test: preserve short RSI hard threshold semantics"
```

If `agents/trading/judge.py` has no changes in this task, omit it from `git add`.

## Task 6: Update OpenSpec tasks and run verification commands

**Files:**
- Modify: `openspec/changes/short-main-path-risk-guard-parity/tasks.md`
- Modify if necessary: test files or `agents/trading/judge.py`

- [ ] **Step 1: Check off completed OpenSpec tasks**

In `openspec/changes/short-main-path-risk-guard-parity/tasks.md`, change completed checkboxes from `- [ ]` to `- [x]` for tasks that have been implemented and verified.

- [ ] **Step 2: Run targeted test suite**

Run:

```bash
python3 -m pytest -q tests/test_short_main_path_risk_guard.py tests/test_judge_plan_anchor_fields.py tests/test_pullback_atr_policy.py
```

Expected: all selected tests pass.

- [ ] **Step 3: Run full project pytest baseline**

Run:

```bash
python3 -m pytest -q
```

Expected: full suite passes. Current documented baseline before this change is `993 passed / 4 deselected / 1 warning`; the exact pass count should increase by the new tests.

- [ ] **Step 4: Run compile check**

Run:

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
```

Expected: exit code 0.

- [ ] **Step 5: Commit task checklist and any final fixes**

```bash
git add openspec/changes/short-main-path-risk-guard-parity/tasks.md agents/trading/judge.py tests/test_short_main_path_risk_guard.py
git commit -m "chore: verify short main path risk guard parity"
```

If there are no code/test changes after verification, commit only `tasks.md`.

## Self-Review Checklist

- Spec coverage:
  - Route-consistent short risk gate: Tasks 1-4.
  - Hard RSI threshold preservation: Tasks 1, 5.
  - LLM reversal-risk tightening: Tasks 1-3.
  - Short gate attribution versioning: Tasks 3-4.
- Placeholder scan: no TBD/TODO placeholders are intentionally left in this plan.
- Type consistency: helper/result names are consistent across tasks: `_classify_short_entry_risk`, `_detect_llm_short_reversal_risk`, `_apply_short_gate_attribution`, `SHORT_GATE_VERSION`.
