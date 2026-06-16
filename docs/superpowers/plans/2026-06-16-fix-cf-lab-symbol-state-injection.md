---
change: fix-cf-lab-symbol-state-injection
design-doc: docs/superpowers/specs/2026-06-16-fix-cf-lab-symbol-state-injection-design.md
base-ref: 3d683d87e3acebdea5b5f3194e28506652343265
archived-with: 2026-06-16-fix-cf-lab-symbol-state-injection
---

# CF Lab Symbol-State Injection Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** `_inject_cf_state` 保留录制的 `_symbol_state`(市场决策输入),修复 L3b sequential baseline_fidelity 0.798→~0.91。

**Architecture:** 一行修复(镜像现有 `_regime_manager` 透传) + 端到端 fidelity 坐实测试。observability-only。

**Tech Stack:** Python 3.9, pytest, asyncio。`utils/sequential_perturbation.py`。

archived-with: 2026-06-16-fix-cf-lab-symbol-state-injection
---

### Task 1: `_inject_cf_state` 保留录制 `_symbol_state`

**Files:**
- Modify: `utils/sequential_perturbation.py`（`_inject_cf_state`）
- Test: `tests/test_sequential_perturbation.py`

- [ ] **Step 1: 失败测试**

加到 `tests/test_sequential_perturbation.py`：

```python
def test_inject_cf_state_preserves_recorded_symbol_state():
    from utils.sequential_perturbation import _inject_cf_state
    from utils.cf_portfolio import CounterfactualPortfolio
    cf = CounterfactualPortfolio(initial_equity=1000.0)
    rec = {
        "symbol": "X-USDT",
        "state_snapshot_before_decision": {
            "_symbol_state": {"trend_streak": 5, "last_tech": {"k": 1}},
            "_regime_manager": {"effective_regime": "mixed"},
            "_recent_wins": 9, "_total_completed_trades": 52,
            "_archetype_cooldown": {"_history": {}, "_cooldown_until": {}},
        },
    }
    out = _inject_cf_state(rec, cf)
    # 注入后的快照保留录制 _symbol_state(非空 {})
    assert out["state_snapshot_before_decision"]["_symbol_state"] == {"trend_streak": 5, "last_tech": {"k": 1}}


def test_inject_cf_state_missing_symbol_state_safe():
    from utils.sequential_perturbation import _inject_cf_state
    from utils.cf_portfolio import CounterfactualPortfolio
    cf = CounterfactualPortfolio(initial_equity=1000.0)
    rec = {"symbol": "X-USDT", "state_snapshot_before_decision": {"_regime_manager": {}}}
    out = _inject_cf_state(rec, cf)
    assert out["state_snapshot_before_decision"]["_symbol_state"] == {}
```

- [ ] **Step 2: 确认失败**

Run: `python3 -m pytest tests/test_sequential_perturbation.py -k inject_cf_state_preserves -q`
Expected: FAIL（当前 `_symbol_state` 是 `cf.to_snapshot()` 的空 `{}`）

- [ ] **Step 3: 实现**

`utils/sequential_perturbation.py` 的 `_inject_cf_state`，在 `snap = cf.to_snapshot(regime_snapshot=recorded_snap.get("_regime_manager"))` 之后、`new_rec = dict(record)` 之前插入一行：

```python
    # 保留录制的 per-symbol 决策输入上下文(trend_streak/last_tech 等市场状态——CF 无法重建)，
    # 镜像上面的 _regime_manager 透传；空 {} 会让 Judge 信号强度路径误判"信号不足"→ hold。
    # 还原的是市场决策输入(非 reality 的 EV/胜率战绩累计)，不触 L3b 反模式。
    snap["_symbol_state"] = recorded_snap.get("_symbol_state") or {}
```

- [ ] **Step 4: 测试通过**

Run: `python3 -m pytest tests/test_sequential_perturbation.py -k inject_cf_state -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add utils/sequential_perturbation.py tests/test_sequential_perturbation.py
git commit -m "fix(cf): _inject_cf_state preserves recorded _symbol_state (signal-strength context, not blank)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-fix-cf-lab-symbol-state-injection
---

### Task 2: 坐实 sequential fidelity + 回归

**Files:**
- Test: `tests/test_sequential_perturbation.py`

- [ ] **Step 1: 坐实测试**

加到 `tests/test_sequential_perturbation.py`（端到端，真实磁带，skip-if-absent；复用文件已有的 `asyncio.run` 模式与 klines loader 约定；若已有 `_load_v2_rr`/loader 辅助则复用）：

```python
def test_sequential_baseline_fidelity_restored():
    import os, json, asyncio, sqlite3, pytest
    tape = os.path.join(os.path.dirname(__file__), "..", "data", "decision_replay_tape.jsonl")
    klines = os.path.join(os.path.dirname(__file__), "..", "data", "klines_1s.db")
    if not os.path.exists(tape) or not os.path.exists(klines):
        pytest.skip("no live tape/klines")
    from utils.sequential_perturbation import run_arm, _gate_of_recorded
    recs = []
    for line in open(tape):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("schema_version") not in ("decision_replay_record.v2", "decision_replay_record.v3"):
            continue
        if not (r.get("tech_analysis") or {}):
            continue
        recs.append(r)
    if len(recs) < 50:
        pytest.skip("insufficient tape")
    recs.sort(key=lambda r: r.get("timestamp", 0))

    def loader(sym, ca, win):
        lo, hi = int(ca * 1000), int((ca + win) * 1000)
        c = sqlite3.connect(klines)
        try:
            rows = c.execute("SELECT open_time,high,low,close FROM klines WHERE symbol=? "
                             "AND open_time>=? AND open_time<=? ORDER BY open_time", (sym, lo, hi)).fetchall()
        except Exception:
            return []
        finally:
            c.close()
        return [{"open_time": t, "high": h, "low": l, "close": cl} for t, h, l, cl in rows]

    arm = asyncio.run(run_arm(recs, {}, loader))
    agree = sum(1 for d, r in zip(arm["decisions"], recs) if d["gate"] == _gate_of_recorded(r))
    fid = agree / len(recs)
    assert fid >= 0.85, f"sequential baseline fidelity {fid:.3f} < 0.85 (应坐实 ~0.91, 修前 0.798)"
```

- [ ] **Step 2: 跑测试**

Run: `python3 -m pytest tests/test_sequential_perturbation.py -k sequential_baseline_fidelity -q`
Expected: PASS（fid ~0.91）。若 < 0.85 报告实际值，不降阈值。

- [ ] **Step 3: 红线 + 全量回归**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -q` → PASS
Run: `python3 -m pytest -q` → summary 行；passed ≥ 1252（基线不回退）。失败报告，不标 DONE。

- [ ] **Step 4: 提交**

```bash
git add tests/test_sequential_perturbation.py
git commit -m "test(cf): sequential baseline fidelity restored to >=0.85 + regression

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-fix-cf-lab-symbol-state-injection
---

## Self-Review
- **Spec coverage**：sequential-perturbation-driver 的"注入保留录制 _symbol_state"=Task1;"baseline 臂忠实复现"=Task2;"不改 EV/cooldown 累计"=Task1 改动只碰 _symbol_state 行(不动 EV/cooldown)+ Task2 全量回归。
- **Placeholder scan**：每步真实代码/命令/期望。
- **Type consistency**：`snap["_symbol_state"]`、`recorded_snap.get("_symbol_state")` 一致。
- **坦白**：Task2 断言 ≥0.85(实测 ~0.91),不强求 actionable direction。
