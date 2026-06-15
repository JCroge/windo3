---
change: decision-tape-capture-fix
design-doc: docs/superpowers/specs/2026-06-15-decision-tape-capture-fix-design.md
base-ref: 07e2730c64a75d440c7fc3dddd4722e61f8d129f
---

# Decision Tape Capture Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让决策磁带捕获真实 `tech_analysis` + `llm_output_inline`，使反事实回放 harness 能走到 gate；旧空记录标 `replayable=false`。

**Architecture:** `judge.py` 引入 `self._symbol_llm_cache` 镜像现有 `_symbol_tech_cache`（per-decision reset / `_ask_llm` 后 set / symbol 退出 pop），延迟 ranked 路径 flush 前从候选 re-prime cache；两个录制 chokepoint 防御性 `getattr` 读 cache；`decision_tape.build_bundle` 把 `replayable` 收紧为有快照 AND tech 非空，`SCHEMA_VERSION` v2。observability-only write-only，绝不改决策逻辑。

**Tech Stack:** Python 3.9 / asyncio / pytest。改动文件：`agents/trading/judge.py`、`utils/decision_tape.py`、`tests/`。

---

## File Structure

- `utils/decision_tape.py` — `build_bundle` replayable 守卫 + schema v2（纯函数，最先改，被测试依赖）。
- `agents/trading/judge.py` — `_symbol_llm_cache` 生命周期 + ranked re-prime + 两个 build_bundle 调用点。
- `tests/test_decision_tape.py` — build_bundle replayable 单测（已存在，扩展）。
- `tests/test_judge_decision_tape_wiring.py` — chokepoint 捕获 cache 单测（已存在，扩展）。
- `tests/test_decision_tape_capture.py` — **新增**，端到端 record→replay 复现拒因 + perturb 翻转。

---

### Task 1: decision_tape — replayable 真实性守卫 + schema v2

**Files:**
- Modify: `utils/decision_tape.py:9`（SCHEMA_VERSION）、`utils/decision_tape.py` build_bundle return
- Test: `tests/test_decision_tape.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_decision_tape.py` 追加：

```python
from utils.decision_tape import build_bundle, SCHEMA_VERSION


def _snap():
    return {"_available_balance": 1000.0}


def test_replayable_requires_nonempty_tech():
    # 有快照但 tech 空 -> 不可回放
    b = build_bundle(symbol="BTC-USDT", decision="reject", request_id="r",
                     tech_analysis={}, price_at_decision=1.0, regime_state="choppy",
                     llm_output=None, llm_audit_ref=None,
                     trade_decision_output={}, state_snapshot=_snap())
    assert b["replayable"] is False
    # 有快照且 tech 非空 -> 可回放
    b2 = build_bundle(symbol="BTC-USDT", decision="reject", request_id="r",
                      tech_analysis={"indicators": {"price": 1.0}}, price_at_decision=1.0,
                      regime_state="choppy", llm_output={"action": "hold"}, llm_audit_ref=None,
                      trade_decision_output={}, state_snapshot=_snap())
    assert b2["replayable"] is True


def test_missing_snapshot_not_replayable():
    b = build_bundle(symbol="BTC-USDT", decision="reject", request_id="r",
                     tech_analysis={"indicators": {}}, price_at_decision=1.0,
                     regime_state="choppy", llm_output=None, llm_audit_ref=None,
                     trade_decision_output={}, state_snapshot=None)
    assert b["replayable"] is False


def test_schema_version_is_v2():
    assert SCHEMA_VERSION == "decision_replay_record.v2"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_decision_tape.py::test_replayable_requires_nonempty_tech tests/test_decision_tape.py::test_schema_version_is_v2 -q`
Expected: FAIL（当前 replayable 只看快照；SCHEMA_VERSION 仍 v1）

- [ ] **Step 3: 改实现**

`utils/decision_tape.py:9` 改 schema：

```python
SCHEMA_VERSION = "decision_replay_record.v2"
```

build_bundle return 末行 `"replayable": ...` 改为：

```python
        "replayable": state_snapshot is not None and bool(tech_analysis),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_decision_tape.py -q`
Expected: PASS（含原有用例无回归）

- [ ] **Step 5: 提交**

```bash
git add utils/decision_tape.py tests/test_decision_tape.py
git commit -m "fix(tape): replayable requires non-empty tech + schema v2"
```

---

### Task 2: judge — `_symbol_llm_cache` 生命周期（init / reset / pop）

**Files:**
- Modify: `agents/trading/judge.py:156`（init）、`:644` _make_decision 顶部（reset）、`:1218` 之后（set）、`:378`（pop）
- Test: `tests/test_judge_decision_tape_wiring.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_judge_decision_tape_wiring.py` 追加（验证 init 建 cache、reject 捕获 cache 内容）：

```python
def test_reject_path_captures_tech_and_llm_from_cache(tmp_path):
    tape_path = str(tmp_path / "tape.jsonl")
    j = _partial_judge(tape_path)
    j._symbol_tech_cache = {"BTC-USDT": {"indicators": {"price": 100.0}}}
    j._symbol_llm_cache = {"BTC-USDT": {"action": "open_long", "confidence": 70,
                                        "reasoning": "r", "key_factors": [], "risk_warnings": []}}
    j._record_rejected_plan(
        "BTC-USDT", "open_long",
        {"entry_ref": 100.0, "stop_loss": 95.0, "take_profit": [110.0], "leverage": 5},
        score=50, confidence=60, reason="rr_below_floor:1.39<1.50",
        attribution={"request_id": "req-x"},
    )
    import json
    rows = [json.loads(l) for l in open(tape_path) if l.strip()]
    assert rows[0]["tech_analysis"] == {"indicators": {"price": 100.0}}
    assert rows[0]["llm_output_inline"]["action"] == "open_long"
    assert rows[0]["replayable"] is True


def test_reject_capture_defensive_when_caches_absent(tmp_path):
    # partial judge 无 cache 属性时，录制 chokepoint 不得抛 AttributeError（红线：磁带不破决策）
    tape_path = str(tmp_path / "tape.jsonl")
    j = _partial_judge(tape_path)  # 不设 _symbol_tech_cache / _symbol_llm_cache
    j._record_rejected_plan(
        "ETH-USDT", "open_short",
        {"entry_ref": 100.0, "stop_loss": 105.0, "take_profit": [90.0], "leverage": 5},
        score=-50, confidence=60, reason="rr_below_floor", attribution={"request_id": "r2"},
    )  # must not raise
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_judge_decision_tape_wiring.py::test_reject_path_captures_tech_and_llm_from_cache -q`
Expected: FAIL（reject 路径仍写死 `tech_analysis={}` / `llm_output=None`）

- [ ] **Step 3: 改实现 — init 建 cache**

`agents/trading/judge.py:156` 现有 `self._symbol_tech_cache = {}` 之后加一行：

```python
        self._symbol_tech_cache = {}
        self._symbol_llm_cache = {}
```

`_make_decision` 顶部（`agents/trading/judge.py:645`，`await self._update_balance()` 之后）插入 per-decision reset：

```python
        await self._update_balance()
        # 决策磁带：本次决策的 LLM 输出按 symbol 重置，rule-only 路径保持 None（诚实）
        if hasattr(self, "_symbol_llm_cache"):
            self._symbol_llm_cache[symbol] = None
```

`agents/trading/judge.py:1218` `llm_result = await self._ask_llm(symbol, tech, score)` 之后插入 set：

```python
            llm_result = await self._ask_llm(symbol, tech, score)
            if hasattr(self, "_symbol_llm_cache"):
                self._symbol_llm_cache[symbol] = llm_result
```

`agents/trading/judge.py:378` symbol 退出清理 `self._symbol_tech_cache.pop(s, None)` 之后加：

```python
                self._symbol_tech_cache.pop(s, None)
                if hasattr(self, "_symbol_llm_cache"):
                    self._symbol_llm_cache.pop(s, None)
```

- [ ] **Step 4: 改实现 — reject chokepoint 读 cache（防御性）**

`agents/trading/judge.py:3032/3035`（`_record_rejected_plan` 内 build_bundle）改：

```python
                tech_analysis=getattr(self, "_symbol_tech_cache", {}).get(symbol) or {},
                price_at_decision=(plan or {}).get("entry_ref") or (plan or {}).get("entry_price"),
                regime_state=regime,
                llm_output=getattr(self, "_symbol_llm_cache", {}).get(symbol), llm_audit_ref=None,
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest tests/test_judge_decision_tape_wiring.py -q`
Expected: PASS（含原有 wiring 用例无回归）

- [ ] **Step 6: 提交**

```bash
git add agents/trading/judge.py tests/test_judge_decision_tape_wiring.py
git commit -m "fix(judge): capture real tech+llm into decision tape via _symbol_llm_cache (reject path)"
```

---

### Task 3: judge — accept chokepoint 读 llm cache

**Files:**
- Modify: `agents/trading/judge.py:1986`（accept build_bundle）
- Test: `tests/test_judge_decision_tape_wiring.py`

- [ ] **Step 1: 写失败测试**

追加（直接验证 `_gate_and_publish_open` 录制点取 llm cache；用最小 judge 调用录制段较重，改为断言源码契约 + 一个轻量集成）：

```python
def test_accept_tape_reads_llm_cache_not_hardcoded_none():
    import inspect
    src = inspect.getsource(judge_mod)
    # accept 录制点不得再硬编码 llm_output=None；两个录制点都应从 _symbol_llm_cache 取
    assert src.count("_symbol_llm_cache") >= 3  # init + set + reset/pop + 两个读点
    # 不应再存在 "llm_output=None" 作为录制点写死（rule-only 经 cache=None 体现，而非字面量）
    assert "llm_output=None, llm_audit_ref=None" not in src
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_judge_decision_tape_wiring.py::test_accept_tape_reads_llm_cache_not_hardcoded_none -q`
Expected: FAIL（accept 点仍 `llm_output=None, llm_audit_ref=None`）

- [ ] **Step 3: 改实现**

`agents/trading/judge.py:1986` accept build_bundle 内 `llm_output=None, llm_audit_ref=None,` 改为：

```python
                llm_output=getattr(self, "_symbol_llm_cache", {}).get(symbol), llm_audit_ref=None,
```

（reject 点的 `llm_output=None` 已在 Task 2 改掉；此 Step 后源码中两处录制点均不含字面量 `llm_output=None, llm_audit_ref=None`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_judge_decision_tape_wiring.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/judge.py tests/test_judge_decision_tape_wiring.py
git commit -m "fix(judge): accept-path tape reads llm from cache (no hardcoded None)"
```

---

### Task 4: judge — 延迟 ranked 路径 re-prime（保真补丁）

**Files:**
- Modify: `agents/trading/judge.py:1816`（enqueue 挂 llm/tech）、`agents/trading/judge.py:2031`（flush 派发前 re-prime）
- Test: `tests/test_judge_decision_tape_wiring.py`

- [ ] **Step 1: 写失败测试**

```python
def test_ranked_candidate_carries_llm_and_tech_for_faithful_flush():
    import inspect
    src = inspect.getsource(judge_mod)
    # 入队时把 llm_result + tech 挂到候选；flush 派发前用候选值 re-prime cache
    assert "rank_candidate['llm_output']" in src or 'rank_candidate["llm_output"]' in src
    assert "rank_candidate['tech']" in src or 'rank_candidate["tech"]' in src
    # flush 循环 re-prime：从 candidate 写回 _symbol_llm_cache
    flush = src[src.index("async def _flush_ranked_candidates"):]
    assert "_symbol_llm_cache[symbol] = candidate" in flush
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_judge_decision_tape_wiring.py::test_ranked_candidate_carries_llm_and_tech_for_faithful_flush -q`
Expected: FAIL

- [ ] **Step 3: 改实现 — 入队挂载**

`agents/trading/judge.py:1816`（`rank_candidate['decision'] = decision` 之后）加：

```python
                        rank_candidate['decision'] = decision
                        rank_candidate['llm_output'] = llm_result
                        rank_candidate['tech'] = tech
```

- [ ] **Step 4: 改实现 — flush re-prime**

`agents/trading/judge.py:2031` flush 循环内 `state = self._get_state(symbol)` 之后加：

```python
            state = self._get_state(symbol)
            # 延迟派发：用候选入队时挂载的 llm/tech re-prime cache，避免读到被新决策 reset 的串味值
            if hasattr(self, "_symbol_llm_cache"):
                self._symbol_llm_cache[symbol] = candidate.get('llm_output')
            if hasattr(self, "_symbol_tech_cache") and candidate.get('tech') is not None:
                self._symbol_tech_cache[symbol] = candidate.get('tech')
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest tests/test_judge_decision_tape_wiring.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add agents/trading/judge.py tests/test_judge_decision_tape_wiring.py
git commit -m "fix(judge): ranked-flush re-primes llm/tech cache from candidate (faithful deferred capture)"
```

---

### Task 5: 端到端 record→replay — 复现拒因 + perturb 翻转

**Files:**
- Create: `tests/test_decision_tape_capture.py`
- Test: 同上

> 用 `test_decision_replay.py` 已验证的 fixture 构造法（`_accept_fixture_record` 的强多头 tech 给出有效 R:R≈1.39）。构造一条 regime 取默认 floor 1.50 的记录：baseline 回放在 1.39<1.50 拒单，perturb `rr_floor_default=1.30` 后翻转 accept。这直接复现 L4 空转的根因被解除。

- [ ] **Step 1: 写测试（先确认会 reject）**

```python
import asyncio
from utils.decision_replay import replay_decision


def _rr_reject_record():
    """强多头 tech，有效 R:R≈1.39；regime=mixed 取默认 floor 1.50 -> rr_below_floor 拒单。"""
    snap = {
        "_open_positions": [], "_pending_open_symbols": [],
        "_position_slots": {}, "_pending_open_slots": {},
        "_archetype_cooldown": {"_history": {}, "_cooldown_until": {}},
        "_recent_wins": 8, "_total_completed_trades": 14, "_recent_win_rate": 0.57,
        "_probe_short_active": None, "_probe_short_sl_count": 0,
        "_probe_short_cooldown_until": 0.0,
        "_symbol_state": {}, "_available_balance": 1000.0,
        "_regime_manager": {"effective_regime": "mixed", "confidence": 60, "basis": {}},
    }
    price = 50000.0
    tech = {
        "indicators": {"price": price, "rsi": 56},
        "trend": {"direction": "bullish", "strength": 82, "higher_tf_bias": "bullish",
                  "daily_bias": "bullish", "change_24h_pct": 2.0,
                  "daily_near_resistance": False, "daily_near_support": False, "tf_4h_rsi": 58},
        "momentum": {"rsi": 56, "rsi_divergence": None, "volume_ratio": 1.4, "atr_pct": 0.02},
        "rule_signal": {"entry_long": True, "entry_short": False,
                        "ma_aligned_long": True, "ma_aligned_short": False},
        "levels": {"support": [49000.0, 48000.0], "resistance": [52000.0, 54000.0]},
        "risk": {"liquidity_score": 80},
        "microstructure": {"whale_direction": "accumulating"},
        "money_flow": {"oi_price_divergence": "bullish", "taker_pressure": "buy"},
        "crowd": {"contrarian_signal": "neutral"},
        "entry_timing": {"tf_15m_available": True, "tf_15m_confirm_long": True,
                         "tf_15m_block_long": False, "tf_15m_bias": "bullish",
                         "tf_15m_rsi": 55, "tf_15m_recent_closes": "up"},
    }
    return {
        "schema_version": "decision_replay_record.v2",
        "request_id": "cap-1", "timestamp": 1700000000.0, "symbol": "BTC-USDT",
        "decision": "reject",
        "tech_analysis": tech, "price_at_decision": price, "regime_state": "mixed",
        "llm_output_inline": {"action": "open_long", "confidence": 70, "reasoning": "bull",
                              "key_factors": [], "risk_warnings": []},
        "llm_audit_ref": None,
        "trade_decision_output": {"reject_reason": "rr_below_floor", "attribution": {}},
        "state_snapshot_before_decision": snap, "replayable": True,
    }


def test_capture_record_replays_to_gate_reject():
    rec = _rr_reject_record()
    out = asyncio.run(replay_decision(rec, {"rr_floor_default": 1.50}))
    # 走到 R:R 闸并复现拒单（action 非开仓；reject_reason 非 None 短路）
    assert (out or {}).get("action") in (None, "hold")
    rr = (out or {}).get("reject_reason") or ((out or {}).get("attribution") or {}).get("blocked_by")
    assert rr and "rr_below_floor" in rr
```

- [ ] **Step 2: 跑测试**

Run: `python3 -m pytest tests/test_decision_tape_capture.py::test_capture_record_replays_to_gate_reject -q`
Expected: PASS（若有效 R:R / regime 与假设不符导致拒因不同，按实际回放输出微调 tech 数值使其落在 rr_below_floor；不得为过测改决策逻辑）

- [ ] **Step 3: 加 perturb 翻转用例**

```python
def test_capture_record_flips_to_accept_when_floor_lowered():
    rec = _rr_reject_record()
    out = asyncio.run(replay_decision(rec, {"rr_floor_default": 1.30}))
    assert (out or {}).get("action") in ("open_long", "open_short")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_decision_tape_capture.py -q`
Expected: PASS（证明捕获使旋钮在回放中生效——L4 空转根因解除）

- [ ] **Step 5: 提交**

```bash
git add tests/test_decision_tape_capture.py
git commit -m "test(tape): end-to-end record->replay reproduces reject + perturb flip"
```

---

### Task 6: 决策不变性 + 红线守卫 + 全量回归

**Files:**
- Test: `tests/test_cf_red_line_guard.py`（验证不回归）、全量

- [ ] **Step 1: 红线守卫不回归**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -q`
Expected: PASS（新增 `_symbol_llm_cache` 是写侧，不引入对 CF 产物的读）

- [ ] **Step 2: 决策不变性静态确认**

Run: `git diff 07e2730c64a75d440c7fc3dddd4722e61f8d129f -- agents/trading/judge.py`
Expected: 改动仅触碰 — `_symbol_llm_cache` init / reset / set / pop、ranked 候选挂载与 flush re-prime、两个 build_bundle 调用点。**不得**出现 gate 阈值 / `_select_rr_floor` / `_check_entry_position_policy` / `_classify_short_entry_risk` / plan 计算 / ranking 选择逻辑的改动。人工核对 diff 满足此约束。

- [ ] **Step 3: 编译检查**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q agents/trading/judge.py utils/decision_tape.py`
Expected: 无输出（编译通过）

- [ ] **Step 4: 全量测试**

Run: `python3 -m pytest -q`
Expected: PASS，总数为 1223 + 本 change 新增用例（test_decision_tape +3、test_judge_decision_tape_wiring +4、test_decision_tape_capture +2），无回归

- [ ] **Step 5: 提交（如有未提交的测试微调）**

```bash
git add -A
git commit -m "test(tape): red-line guard + decision invariance + full regression for capture fix"
```

---

## Self-Review

- **Spec 覆盖**：proposal「修复 reject+accept 捕获」→ Task 2/3；「_symbol_llm_cache」→ Task 2；「ranked re-prime」→ Task 4；「replayable 守卫 + schema v2」→ Task 1；delta spec「复现拒因」「扰动翻转」Scenario → Task 5；「决策不变性」「红线守卫」→ Task 6。无遗漏。
- **占位符**：无 TBD/TODO，每步含真实代码与命令。
- **类型一致**：`_symbol_llm_cache` 命名全程一致；`getattr(self, "_symbol_llm_cache", {})` 防御读法在两个 chokepoint 与 flush 一致；`build_bundle` replayable 表达式与测试断言一致。
- **已知微调点**：Task 5 的 tech 数值假设有效 R:R≈1.39，若实跑回放拒因/翻转阈值不符，按实际输出微调 tech（绝不改决策逻辑过测）。
