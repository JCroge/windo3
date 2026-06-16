---
change: fix-cf-lab-ev-coldstart-deadlock
design-doc: docs/superpowers/specs/2026-06-16-fix-cf-lab-ev-coldstart-deadlock-design.md
base-ref: 561cf11da2fea6b9597e609cdd72106be42209c0
---

# CF Lab EV 冷启动死锁修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 L3b 序列组合模拟的 EV-gate 冷启动死锁,使被扰动旋钮能真正影响 CF 开仓结果,实验室能给出可信方向(或可信的"不值得"结论)。

**Architecture:** CF 组合维护与 live Reviewer 同语义的 rolling 胜率窗口(长 20),序列起点用录制滚动率暖启动播种,之后从 CF 自身结算 FIFO 演化;`to_snapshot` emit 窗口率;baseline_fidelity 改 gate-level 比对;驱动按 v2 过滤。全程 observability-only。

**Tech Stack:** Python 3.9, pytest, asyncio。涉及 `utils/cf_portfolio.py`、`utils/sequential_perturbation.py`、`cf_direction_recommendation.py`。

---

## File Structure

- `utils/cf_portfolio.py` — 加 rolling 胜率窗口(deque),resolve_due 结算时 append,to_snapshot emit 窗口率。
- `utils/sequential_perturbation.py` — `_seed_cf_prior` 暖启动播种窗口;`run_arm` decisions 捕获 gate;`build_delta_report` fidelity/divergence 改 gate-level。
- `cf_direction_recommendation.py` — `load_records` 按 v2 + tech 非空过滤。
- `tests/test_cf_portfolio.py` / `tests/test_sequential_perturbation.py` — 新增测试。

---

### Task 1: CF rolling 胜率窗口（cf_portfolio.py）

**Files:**
- Modify: `utils/cf_portfolio.py:4`（import）、`:14`（__init__）、`:63-83`（resolve_due）、`:84-96`（to_snapshot）
- Test: `tests/test_cf_portfolio.py`

- [ ] **Step 1: 写失败测试**

加到 `tests/test_cf_portfolio.py`：

```python
from collections import deque
from utils.cf_portfolio import CounterfactualPortfolio


def _force_close(cf, symbol, net):
    """直接把一个已开 CF 仓的结算结果设为 net 并到期解析。"""
    cf._open[symbol] = {"resolved_ts": 1.0, "net_usdt": net,
                        "archetype": "test", "created_at": 0.0}
    cf.resolve_due(2.0)


def test_rolling_window_tracks_recent_results():
    cf = CounterfactualPortfolio(initial_equity=1000.0)
    # 5 笔: 3 胜 2 负
    for net in (1.0, 1.0, -1.0, 1.0, -1.0):
        _force_close(cf, "X-USDT", net)
    snap = cf.to_snapshot()
    assert snap["_recent_win_rate"] == 3 / 5  # 窗口率, 不是 _recent_wins/_total


def test_rolling_window_is_capped_at_window_size():
    cf = CounterfactualPortfolio(initial_equity=1000.0, rolling_window_size=20)
    # 25 笔全胜 → 窗口只保留最近 20, 率=1.0; deque maxlen=20
    for _ in range(25):
        _force_close(cf, "X-USDT", 1.0)
    assert len(cf._cf_win_window) == 20
    assert cf.to_snapshot()["_recent_win_rate"] == 1.0


def test_window_empty_emits_none():
    cf = CounterfactualPortfolio(initial_equity=1000.0)
    assert cf.to_snapshot()["_recent_win_rate"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_cf_portfolio.py -k "rolling_window or window_empty" -q`
Expected: FAIL（`_cf_win_window` 不存在 / `_recent_win_rate` 仍是 wins/total）

- [ ] **Step 3: 实现**

`utils/cf_portfolio.py` 第 4 行 import 改为：

```python
from collections import defaultdict, deque
```

`__init__` 签名加 `rolling_window_size=20` 参数（放在 `window_sec=86400` 后）：

```python
    def __init__(self, initial_equity=1000.0, max_slots=3, price_loader=None,
                 daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3, window_sec=86400,
                 rolling_window_size=20):
```

`__init__` 体内 `self.realized = []` 后加：

```python
        self.rolling_window_size = rolling_window_size
        # CF 自身已结算结果的滚动窗口(win=True/loss=False), 与 live Reviewer 同语义。
        # 只吃 CF 自己的结算; 序列起点由 _seed_cf_prior 暖启动播种。
        self._cf_win_window = deque(maxlen=rolling_window_size)
```

`resolve_due` 中 `self._total_completed_trades += 1` 之后、`if net > 0:` 块内外补一行——在现有 if/else 后追加窗口 append（不改原 `_recent_wins`/`_consec_losses` 逻辑）。把：

```python
            self._total_completed_trades += 1
            if net > 0:
                self._recent_wins += 1
                self._consec_losses = 0
            else:
                self._consec_losses += 1
```

改为：

```python
            self._total_completed_trades += 1
            self._cf_win_window.append(net > 0)
            if net > 0:
                self._recent_wins += 1
                self._consec_losses = 0
            else:
                self._consec_losses += 1
```

`to_snapshot` 的 `_recent_win_rate` 一行改为窗口率：

```python
            "_recent_win_rate": (sum(self._cf_win_window) / len(self._cf_win_window)
                                 if self._cf_win_window else None),
```

（`_recent_wins` / `_total_completed_trades` 两行保持不变，供 bayesian fallback。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_cf_portfolio.py -q`
Expected: PASS（含原有用例不回退）

- [ ] **Step 5: 提交**

```bash
git add utils/cf_portfolio.py tests/test_cf_portfolio.py
git commit -m "feat(cf): CF rolling win-rate window (live Reviewer semantics) for EV gate fidelity"
```

---

### Task 2: `_seed_cf_prior` 暖启动播种窗口（sequential_perturbation.py）

**Files:**
- Modify: `utils/sequential_perturbation.py:23-37`（`_seed_cf_prior`）
- Test: `tests/test_sequential_perturbation.py`

- [ ] **Step 1: 写失败测试**

加到 `tests/test_sequential_perturbation.py`：

```python
from utils.cf_portfolio import CounterfactualPortfolio
from utils.sequential_perturbation import _seed_cf_prior


def test_seed_warms_rolling_window_from_recorded_rate():
    cf = CounterfactualPortfolio(initial_equity=1000.0, rolling_window_size=20)
    rec = {"state_snapshot_before_decision": {
        "_recent_win_rate": 0.45, "_recent_wins": 9, "_total_completed_trades": 52,
        "_archetype_cooldown": {"_history": {}, "_cooldown_until": {}}}}
    _seed_cf_prior(cf, rec)
    # 窗口被 0.45 等价填满 → 起步 _recent_win_rate == 0.45 (9 胜 / 20)
    assert len(cf._cf_win_window) == 20
    assert cf.to_snapshot()["_recent_win_rate"] == 0.45


def test_seed_window_evicted_by_cf_results_after_full_turnover():
    cf = CounterfactualPortfolio(initial_equity=1000.0, rolling_window_size=20)
    rec = {"state_snapshot_before_decision": {
        "_recent_win_rate": 0.45, "_recent_wins": 9, "_total_completed_trades": 52,
        "_archetype_cooldown": {"_history": {}, "_cooldown_until": {}}}}
    _seed_cf_prior(cf, rec)
    # 20 笔 CF 全胜结算后, 合成种子全部 FIFO 挤出 → 率=1.0 (纯 CF)
    for _ in range(20):
        cf._open["X-USDT"] = {"resolved_ts": 1.0, "net_usdt": 1.0,
                              "archetype": "t", "created_at": 0.0}
        cf.resolve_due(2.0)
    assert cf.to_snapshot()["_recent_win_rate"] == 1.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_sequential_perturbation.py -k seed_warms -q`
Expected: FAIL（窗口未被播种，率为 None）

- [ ] **Step 3: 实现**

`utils/sequential_perturbation.py` 的 `_seed_cf_prior`，在 `cf._total_completed_trades = ...` 行之后、`ac = snap.get(...)` 之前插入窗口播种：

```python
    cf._recent_wins = snap.get("_recent_wins", 0) or 0
    cf._total_completed_trades = snap.get("_total_completed_trades", 0) or 0
    # 用录制滚动胜率暖启动 CF 窗口(= 磁带窗口前真实滚动率), 破 EV gate 冷启动死锁;
    # CF 自身结算结果之后 FIFO 逐步挤出合成种子。
    rate = snap.get("_recent_win_rate")
    cf._cf_win_window.clear()
    if rate is not None:
        n = cf.rolling_window_size
        wins = round(float(rate) * n)
        cf._cf_win_window.extend([True] * wins + [False] * (n - wins))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_sequential_perturbation.py -k seed -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add utils/sequential_perturbation.py tests/test_sequential_perturbation.py
git commit -m "feat(cf): warm-seed CF rolling window from recorded rate (break EV cold-start deadlock)"
```

---

### Task 3: gate-level baseline_fidelity / divergence（sequential_perturbation.py）

**Files:**
- Modify: `utils/sequential_perturbation.py:38-66`（`run_arm` decisions）、`:80-82`（`_decision_class` 旁加 `_gate_of`）、`:96-106`（fidelity / divergence 比对）
- Test: `tests/test_sequential_perturbation.py`

- [ ] **Step 1: 写失败测试**

加到 `tests/test_sequential_perturbation.py`：

```python
from utils.sequential_perturbation import _gate_of_recorded, _gate_of_replayed


def test_gate_extraction_prefix():
    # recorded: 取 reject_reason 冒号前前缀
    rec = {"decision": "reject",
           "trade_decision_output": {"reject_reason": "rr_below_floor:1.37<1.50"}}
    assert _gate_of_recorded(rec) == "rr_below_floor"
    rec_acc = {"decision": "accept", "trade_decision_output": {}}
    assert _gate_of_recorded(rec_acc) == "accept"
    # replayed: open → accept; reject → attribution.blocked_by 前缀
    assert _gate_of_replayed({"action": "open_long"}) == "accept"
    d = {"action": "hold", "attribution": {"blocked_by": "ev_gate:EV=-0.41"}}
    assert _gate_of_replayed(d) == "ev_gate"


def test_changed_gate_counts_as_non_reproduction():
    # 录制是 rr_below_floor 拦, baseline-sim 却被 ev_gate 拦 → 不复现
    recorded = {"action": "hold", "attribution": {"blocked_by": "ev_gate:x"}}
    rec = {"decision": "reject",
           "trade_decision_output": {"reject_reason": "rr_below_floor:1.37<1.50"}}
    assert _gate_of_replayed(recorded) != _gate_of_recorded(rec)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_sequential_perturbation.py -k gate -q`
Expected: FAIL（`_gate_of_recorded` / `_gate_of_replayed` 不存在）

- [ ] **Step 3: 实现**

在 `utils/sequential_perturbation.py` 的 `_decision_class` 函数旁（约第 80 行）新增两个 helper：

```python
def _gate_of_replayed(decision):
    """回放决策触达的 gate: 开仓=accept; 否则取 attribution.blocked_by 冒号前前缀。"""
    action = (decision or {}).get("action")
    if action in ("open_long", "open_short"):
        return "accept"
    blocked = ((decision or {}).get("attribution") or {}).get("blocked_by")
    if blocked:
        return str(blocked).split(":")[0]
    return "hold_other"


def _gate_of_recorded(record):
    """录制决策触达的 gate: accept; 否则取 reject_reason 冒号前前缀。"""
    if (record or {}).get("decision") == "accept":
        return "accept"
    rr = ((record or {}).get("trade_decision_output") or {}).get("reject_reason")
    if rr:
        return str(rr).split(":")[0]
    return "hold_other"
```

`run_arm` 内 decisions.append 改为同时记录 gate（把现有那行替换）：

```python
        decisions.append({"timestamp": ts, "symbol": rec.get("symbol"),
                          "action": action, "gate": _gate_of_replayed(decision)})
```

`build_delta_report` 的 fidelity 比对（`agree = ...` 那两行）改为 gate-level：

```python
    agree = sum(1 for d, r in zip(base["decisions"], recs)
                if d["gate"] == _gate_of_recorded(r))
```

divergence 比对（`div = ...` 行）改为 gate-level：

```python
    div = sum(1 for b, p in zip(base["decisions"], pert["decisions"]) if b["gate"] != p["gate"])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_sequential_perturbation.py -q`
Expected: PASS（含原有用例；注意原用例若断言 fidelity 数值，可能因 gate-level 更严而变化——若原用例失败，核对其 fixture 的 recorded reject_reason 与 replayed blocked_by 是否本就同 gate，按真实语义更新断言，不得放松 gate-level 定义）

- [ ] **Step 5: 提交**

```bash
git add utils/sequential_perturbation.py tests/test_sequential_perturbation.py
git commit -m "feat(cf): gate-level baseline_fidelity/divergence (changed-gate = non-reproduction)"
```

---

### Task 4: 驱动 v2 过滤（cf_direction_recommendation.py）

**Files:**
- Modify: `cf_direction_recommendation.py:21-32`（`load_records`）
- Test: `tests/test_sequential_perturbation.py`（或新建 `tests/test_cf_direction_driver.py`）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_cf_direction_driver.py`：

```python
import importlib.util, json, os


def _load_module():
    path = os.path.join(os.path.dirname(__file__), "..", "cf_direction_recommendation.py")
    spec = importlib.util.spec_from_file_location("cf_direction_recommendation", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_load_records_filters_v1_and_empty_tech(tmp_path, monkeypatch):
    mod = _load_module()
    tape = tmp_path / "tape.jsonl"
    rows = [
        {"schema_version": "decision_replay_record.v1", "tech_analysis": {}, "replayable": True},
        {"schema_version": "decision_replay_record.v2", "tech_analysis": {}, "replayable": True},
        {"schema_version": "decision_replay_record.v2",
         "tech_analysis": {"rule_signal": {}}, "replayable": True},
    ]
    tape.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(mod, "TAPE", str(tape))
    recs = mod.load_records()
    # 只有第 3 条 (v2 + tech 非空) 通过
    assert len(recs) == 1
    assert recs[0]["tech_analysis"] == {"rule_signal": {}}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_cf_direction_driver.py -q`
Expected: FAIL（当前 load_records 不过滤，返回 3 条）

- [ ] **Step 3: 实现**

`cf_direction_recommendation.py` 的 `load_records` 在 `recs.append(...)` 处加过滤条件。把：

```python
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
```

改为：

```python
            try:
                r = json.loads(line)
            except Exception:
                continue
            # 按内容判定可回放, 不盲信 stale replayable: 旧 v1 空记录写入时即标 true。
            tech = r.get("tech_analysis")
            if r.get("schema_version") == "decision_replay_record.v2" and tech:
                recs.append(r)
    return recs
```

（注意：原函数末尾已有 `return recs`，确保不要重复；若原 append 在 for 循环内，过滤块替换 append 后保留循环外那个 `return recs`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_cf_direction_driver.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add cf_direction_recommendation.py tests/test_cf_direction_driver.py
git commit -m "fix(cf): driver filters tape to v2 + non-empty tech (no stale replayable trust)"
```

---

### Task 5: 端到端坐实 + 红线 + 全量回归

**Files:**
- Test: `tests/test_sequential_perturbation.py`

- [ ] **Step 1: 写端到端集成测试**

加到 `tests/test_sequential_perturbation.py`。用一条带 state_snapshot 的合成 rr_below_floor record（从真实磁带取一条 fixture 更稳；若 fixture 不便，用最小构造），断言放宽地板后 perturbed 臂开仓数 > 0：

```python
import json, os
import pytest
from utils.sequential_perturbation import build_delta_report

TAPE = os.path.join(os.path.dirname(__file__), "..", "data", "decision_replay_tape.jsonl")


def _load_v2_rr(limit=200):
    if not os.path.exists(TAPE):
        pytest.skip("no live tape available")
    out = []
    for line in open(TAPE):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("schema_version") != "decision_replay_record.v2":
            continue
        if not (r.get("tech_analysis") or {}):
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _loader(symbol, created_at, window_sec):
    import sqlite3
    db = os.path.join(os.path.dirname(__file__), "..", "data", "klines_1s.db")
    if not os.path.exists(db):
        return []
    lo, hi = int(created_at * 1000), int((created_at + window_sec) * 1000)
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT open_time,high,low,close FROM klines WHERE symbol=? "
            "AND open_time>=? AND open_time<=? ORDER BY open_time",
            (symbol, lo, hi)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{"open_time": t, "high": h, "low": l, "close": c} for t, h, l, c in rows]


@pytest.mark.asyncio
async def test_relaxing_floor_breaks_deadlock_perturbed_opens():
    recs = _load_v2_rr()
    rep = await build_delta_report(recs, {}, {"rr_floor_default": 0.3}, _loader,
                                   fidelity_threshold=0.0)
    # 死锁已解: 极端放宽地板后 perturbed 臂至少开出 1 仓
    assert rep["metadata"]["perturbed_cf_open_count"] > 0
```

- [ ] **Step 2: 跑端到端测试**

Run: `python3 -m pytest tests/test_sequential_perturbation.py -k relaxing_floor -q`
Expected: PASS（`perturbed_cf_open_count > 0`，对照修复前为 0）

- [ ] **Step 3: 红线守卫维持**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -q`
Expected: PASS（observability-only 未被破坏）

- [ ] **Step 4: 全量回归**

Run: `python3 -m pytest -q`
Expected: PASS，总数 ≥ 1238（基线不回退；新增测试使总数上升）

- [ ] **Step 5: 提交**

```bash
git add tests/test_sequential_perturbation.py
git commit -m "test(cf): end-to-end deadlock-broken + red-line + full regression"
```

---

## Self-Review

- **Spec coverage**：① 窗口语义(Task1)②暖启动播种(Task2)③gate-level 保真(Task3)④v2 过滤(Task4)⑤端到端坐实+红线(Task5)——覆盖 4 个 delta spec 全部 requirement。
- **Placeholder scan**：每步含真实代码/命令/期望输出，无 TBD。
- **Type consistency**：`_cf_win_window`(deque)、`rolling_window_size`、`_gate_of_replayed`/`_gate_of_recorded`、decisions 的 `gate` 键在各 Task 间一致。
- **保真坦白**：Task5 只断言"死锁已解(perturbed_cf_open>0)",不强求出现 actionable direction（设计已说明放宽地板大概率仍 no_actionable_direction，是可信结论）。
