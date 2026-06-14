---
change: perturbation-replay-per-decision
design-doc: docs/superpowers/specs/2026-06-14-perturbation-replay-per-decision-design.md
base-ref: 21581226fc7ad387e7c211b97283e5136bccd0d7
---

# Per-Decision Perturbation Replay (L3a) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** 逐决策扰动引擎——同一 record 用 baseline vs perturbed 旋钮跑两次真实 `_make_decision`，量化哪些 gate 翻转。

**Architecture:** 新 `utils/perturbation_replay.py` 纯编排，复用 L2 `replay_decision`/`compare_decision` + L1 `cf_honesty_gate`。observability-only write-only。

**Tech Stack:** Python 3, asyncio, pytest；复用 L1/L2 既有模块。

**关键结构事实:** L1/L2 tape 的 `trade_decision_output` 是 accept=`{plan,attribution}` / reject=`{reject_reason,attribution}`，**不含 action/confidence**。所以 baseline 自检按**accept/reject 类**比（`record["decision"]` vs baseline replay 的 action 类）；flip 检测用两次 replay 的完整 payload（含 action）。

**红线:** observability-only write-only（Task 3 守卫）。零回归：基线 1201 不降。

---

## Task 1: 扰动引擎 utils/perturbation_replay.py

**Files:** Create `utils/perturbation_replay.py`, `tests/test_perturbation_replay.py`.

- [ ] **Step 1: 写失败测试**

```python
# tests/test_perturbation_replay.py
import asyncio
import pytest
from utils.perturbation_replay import replay_with_perturbation, _decision_class


@pytest.fixture(autouse=True)
def _restore_loop():
    # replay_decision 内部用 asyncio.run 语义；隔离事件循环（同 test_decision_replay）
    yield
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


def _accept_record():
    # 复用 L2 的真实 open_long fixture（强 bullish tech + 状态快照）
    from tests.test_decision_replay import _accept_fixture_record
    return _accept_fixture_record()


def test_decision_class():
    assert _decision_class({"action": "open_long"}) == "accept"
    assert _decision_class({"action": "open_short"}) == "accept"
    assert _decision_class({"action": "hold"}) == "reject"
    assert _decision_class({"action": None}) == "reject"
    assert _decision_class(None) == "reject"


def test_baseline_reproduces_accept_no_flip_when_same_config():
    rec = _accept_record()
    rec["decision"] = "accept"
    r = asyncio.run(replay_with_perturbation(rec, baseline_config={}, perturbed_config={}))
    assert r["status"] == "ok"
    assert r["flipped"] is False
    assert r["flip_kind"] == "none"


def test_perturb_tighten_rr_floor_flips_accept_to_reject():
    rec = _accept_record()
    rec["decision"] = "accept"
    # 把 R:R 地板抬到不可能（10.0）→ 开仓应被拒
    r = asyncio.run(replay_with_perturbation(rec, baseline_config={},
                                             perturbed_config={"rr_floor_default": 10.0,
                                                               "rr_floor_long_bullish": 10.0,
                                                               "rr_floor_long_aligned_choppy": 10.0}))
    assert r["status"] == "ok"
    assert r["flipped"] is True
    assert r["flip_kind"] == "accept_to_reject"


def test_baseline_mismatch_excluded():
    rec = _accept_record()
    rec["decision"] = "reject"  # 录下说 reject，但 baseline replay 会 accept → mismatch
    r = asyncio.run(replay_with_perturbation(rec, baseline_config={}, perturbed_config={}))
    assert r["status"] == "baseline_mismatch"
    assert r["flip_kind"] == "baseline_mismatch"


def test_not_replayable_returns_status():
    rec = {"replayable": False, "decision": "accept",
           "state_snapshot_before_decision": None}
    r = asyncio.run(replay_with_perturbation(rec, baseline_config={}, perturbed_config={}))
    assert r["status"] == "not_replayable"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_perturbation_replay.py -q` → FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 utils/perturbation_replay.py**

```python
"""逐决策扰动回放引擎（L3a）：同一 record 用 baseline vs perturbed 旋钮跑两次
真实 _make_decision，量化决策翻转。复用 L2 replay_decision/compare_decision。
observability-only —— 严禁交易决策路径 import/调用本模块。"""
from utils.decision_replay import replay_decision, compare_decision, _DISCRETE_ATTR


def _decision_class(payload):
    """accept = 开仓；其余（hold/close/None/无 payload）= reject。"""
    a = (payload or {}).get("action")
    return "accept" if a in ("open_long", "open_short") else "reject"


def _gate_label_changed(baseline, perturbed):
    """action 类相同但某 gate 标签变。"""
    ba = (baseline or {}).get("attribution") or {}
    pa = (perturbed or {}).get("attribution") or {}
    return any(ba.get(f) != pa.get(f) for f in _DISCRETE_ATTR)


async def replay_with_perturbation(record, baseline_config, perturbed_config):
    """返回 {status, flipped, flip_kind, baseline_action, perturbed_action, diffs}。
    status ∈ {ok, baseline_mismatch, not_replayable}。"""
    if not record.get("replayable") or not record.get("state_snapshot_before_decision"):
        return {"status": "not_replayable", "flipped": False, "flip_kind": "not_replayable",
                "baseline_action": None, "perturbed_action": None, "diffs": []}

    baseline = await replay_decision(record, baseline_config)
    # baseline 复现自检：baseline replay 的 accept/reject 类须与录下 decision 一致
    recorded_class = record.get("decision")  # "accept" | "reject"
    if _decision_class(baseline) != recorded_class:
        return {"status": "baseline_mismatch", "flipped": False,
                "flip_kind": "baseline_mismatch",
                "baseline_action": (baseline or {}).get("action"),
                "perturbed_action": None, "diffs": []}

    perturbed = await replay_decision(record, perturbed_config)
    b_cls, p_cls = _decision_class(baseline), _decision_class(perturbed)
    diffs = compare_decision(baseline, perturbed)["diffs"] if (baseline and perturbed) else []

    if b_cls != p_cls:
        flip_kind = "reject_to_accept" if b_cls == "reject" else "accept_to_reject"
        flipped = True
    elif _gate_label_changed(baseline, perturbed):
        flip_kind = "gate_label_change"
        flipped = True
    else:
        flip_kind = "none"
        flipped = False

    return {"status": "ok", "flipped": flipped, "flip_kind": flip_kind,
            "baseline_action": (baseline or {}).get("action"),
            "perturbed_action": (perturbed or {}).get("action"), "diffs": diffs}
```

- [ ] **Step 4: 运行通过**

Run: `python3 -m pytest tests/test_perturbation_replay.py -q` → PASS（6 passed）。
若 `test_perturb_tighten_rr_floor_flips_accept_to_reject` 不翻转，确认 perturbed_config 的键是 `_install_config_flags` 认得的旋钮（rr_floor_default/rr_floor_long_bullish/rr_floor_long_aligned_choppy）；如 fixture 走的是别的 rr policy 分支，补该分支对应的 floor 键到 perturbed_config 直到开仓被拒。不要改 harness/judge。

- [ ] **Step 5: 提交**

```bash
git add utils/perturbation_replay.py tests/test_perturbation_replay.py
git commit -m "feat(perturbation): per-decision knob perturbation engine (baseline self-check + flip_kind)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 翻转分桶报表

**Files:** Modify `utils/perturbation_replay.py`（加 `build_perturbation_report`）, `tests/test_perturbation_replay.py`（加测试）.

- [ ] **Step 1: 写失败测试**

```python
def test_build_report_buckets_and_metadata():
    from utils.perturbation_replay import build_perturbation_report
    rec = _accept_record()
    rec["decision"] = "accept"
    rec.setdefault("trade_decision_output", {})
    rec["trade_decision_output"]["reject_reason"] = None
    rec["effective_regime"] = rec["regime_state"]  # 分桶用
    rec["side"] = "long"
    recs = [rec for _ in range(3)]
    rep = asyncio.run(build_perturbation_report(
        recs, baseline_config={}, perturbed_config={"rr_floor_default": 10.0,
        "rr_floor_long_bullish": 10.0, "rr_floor_long_aligned_choppy": 10.0},
        min_sample=1, lowconf_sample=2))
    assert "buckets" in rep
    assert rep["metadata"]["perturbed_knobs"] == {"rr_floor_default": 10.0,
        "rr_floor_long_bullish": 10.0, "rr_floor_long_aligned_choppy": 10.0}
    assert "fidelity_note" in rep["metadata"]
    # 3 条同样的 accept→reject 翻转
    some_bucket = next(iter(rep["buckets"].values()))
    assert some_bucket["flip_count"] == 3


def test_build_report_skips_not_replayable():
    from utils.perturbation_replay import build_perturbation_report
    recs = [{"replayable": False, "decision": "accept",
             "state_snapshot_before_decision": None}]
    rep = asyncio.run(build_perturbation_report(recs, baseline_config={},
                      perturbed_config={}, min_sample=1, lowconf_sample=2))
    assert rep["metadata"]["skipped_not_replayable"] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_perturbation_replay.py -k build_report -q` → FAIL

- [ ] **Step 3: 实现 build_perturbation_report**

在 `utils/perturbation_replay.py` 追加：
```python
from collections import defaultdict
from utils.cf_honesty_gate import wilson_interval

_FIDELITY_NOTE = ("逐决策独立，不含级联（早期翻转改变后续状态留 L3b）；"
                  "只对非 LLM 旋钮确定（LLM 取录制内联输出）。")


async def build_perturbation_report(records, baseline_config, perturbed_config, *,
                                    min_sample=30, lowconf_sample=100):
    """逐 record 跑扰动引擎，按 reject_reason×regime×side 分桶统计翻转。observability-only。"""
    groups = defaultdict(list)
    skipped_nr = 0
    baseline_mismatch = 0
    for rec in records:
        r = await replay_with_perturbation(rec, baseline_config, perturbed_config)
        if r["status"] == "not_replayable":
            skipped_nr += 1
            continue
        if r["status"] == "baseline_mismatch":
            baseline_mismatch += 1
            continue
        key = (f"{(rec.get('trade_decision_output') or {}).get('reject_reason')}"
               f"|{rec.get('effective_regime') or rec.get('regime_state')}"
               f"|{rec.get('side')}")
        groups[key].append(r)
    buckets = {}
    for key, rs in groups.items():
        n = len(rs)
        flips = sum(1 for r in rs if r["flipped"])
        kinds = defaultdict(int)
        for r in rs:
            kinds[r["flip_kind"]] += 1
        bucket = {
            "n": n, "flip_count": flips,
            "flip_rate": flips / n if n else 0.0,
            "flip_rate_ci": wilson_interval(flips, n),
            "flip_kinds": dict(kinds),
        }
        bucket["verdict"] = "INSUFFICIENT_SAMPLE" if n < min_sample else (
            "low_confidence" if n < lowconf_sample else "actionable")
        buckets[key] = bucket
    return {"buckets": buckets, "metadata": {
        "perturbed_knobs": dict(perturbed_config or {}),
        "skipped_not_replayable": skipped_nr,
        "baseline_mismatch_count": baseline_mismatch,
        "fidelity_note": _FIDELITY_NOTE,
    }}
```

- [ ] **Step 4: 运行通过**

Run: `python3 -m pytest tests/test_perturbation_replay.py -q` → PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add utils/perturbation_replay.py tests/test_perturbation_replay.py
git commit -m "feat(perturbation): flip report (buckets + Wilson CI + honesty verdict + fidelity note)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 红线守卫 + 文档

**Files:** Modify `tests/test_cf_red_line_guard.py`, `CLAUDE.md`, `docs/to-do-list.md`, memory.

- [ ] **Step 1: 扩展红线守卫**

在 `tests/test_cf_red_line_guard.py::test_decision_paths_do_not_read_replay_products` 的循环体内追加：
```python
        assert "perturbation_replay" not in src, mp
```

- [ ] **Step 2: 运行通过**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -q` → PASS

- [ ] **Step 3: 文档 + 记忆**

CLAUDE.md 红线补 L3a 声明（`utils/perturbation_replay.py` observability-only write-only；逐决策独立不含级联=L3b；只对非 LLM 旋钮确定；baseline 复现自检排除 baseline_mismatch；守卫 `test_cf_red_line_guard.py`）。docs/to-do-list.md 路线图：#3 L3a 完成，L3b（序列组合态重演）/L4 待做。memory `counterfactual_replay_lab_roadmap.md` 标 L3a 完成。

- [ ] **Step 4: 提交**

```bash
git add tests/test_cf_red_line_guard.py CLAUDE.md docs/to-do-list.md
git commit -m "docs(perturbation): L3a red-line guard + roadmap update

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 全量验证

- [ ] **Step 1: 编译** — `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .` → exit 0
- [ ] **Step 2: 全量** — `python3 -m pytest -q` → ≥ 1201 + 新增（~10），无 failure
- [ ] **Step 3: tasks.md 全勾 + 最终提交**

```bash
git add -A && git commit -m "chore(perturbation): L3a full regression green"
```

---

## Self-Review

- **Spec 覆盖**：knob-perturbation-engine（Task 1，含 baseline 自检 + flip_kind）、perturbation-flip-report（Task 2，分桶 + Wilson + verdict + metadata）、红线守卫（Task 3）、零回归（Task 4）。
- **类型一致**：`replay_with_perturbation`/`_decision_class`/`build_perturbation_report` 跨 task 一致。复用 `replay_decision`/`compare_decision`/`_DISCRETE_ATTR`/`wilson_interval` 真实签名。
- **无 placeholder**：每步真实代码。YAGNI：引擎纯编排，零决策逻辑；baseline 自检按 accept/reject 类（tape 不含 action/confidence 的事实）。
