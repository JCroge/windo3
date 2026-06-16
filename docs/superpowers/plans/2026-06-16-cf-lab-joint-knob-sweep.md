---
change: cf-lab-joint-knob-sweep
design-doc: docs/superpowers/specs/2026-06-16-cf-lab-joint-knob-sweep-design.md
base-ref: c2d2e767729bbdf790284a68d6790e8b0553a5d1
archived-with: 2026-06-16-cf-lab-joint-knob-sweep
---

# 多旋钮联合扫描 + 交互效应检验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为反事实实验室 L4 增加多旋钮笛卡尔积联合扫描，量化旋钮间 2-way 交互效应（协同/可加/拮抗），区分「单旋钮真没用」与「被另一个门掩盖」。

**Architecture:** 新建纯离线模块 `utils/joint_knob_sweep.py`（`sweep_grid` / `compute_interactions` / `recommend_direction_nd` 三函数）；对可信模块 `utils/sequential_perturbation.py` 仅做一处纯提取（局部闭包 `_summ` → 模块级 `_summarize_arm`），`build_delta_report` 与新模块共用。baseline 臂单次复用，交互显著性阈值复用推荐器口径。observability-only。

**Tech Stack:** Python 3.9 / asyncio / pytest / itertools.product。复用 `run_arm`、`_gate_of_recorded`、`_max_drawdown`、`_FIDELITY_NOTE`。

archived-with: 2026-06-16-cf-lab-joint-knob-sweep
---

### Task 1: 纯提取 `_summarize_arm`（可信模块重构，行为不变）

**Files:**
- Modify: `utils/sequential_perturbation.py`（`build_delta_report` 内 `_summ` 闭包 → 模块级 helper）
- Test: `tests/test_sequential_perturbation.py`（追加 helper 单测）

- [ ] **Step 1: 写失败测试**

在 `tests/test_sequential_perturbation.py` 末尾追加：

```python
def test_summarize_arm_extracted_helper():
    from utils.sequential_perturbation import _summarize_arm
    arm = {"final_equity": 1012.5, "realized": [5.0, -2.0, 3.0],
           "equity_curve": [1000.0, 1005.0, 1003.0, 1012.5]}
    s = _summarize_arm(arm, 1000.0)
    assert s["net_pnl"] == 12.5
    assert s["trades"] == 3
    assert abs(s["win_rate"] - 2 / 3) < 1e-9          # 2 wins / 3
    assert s["max_drawdown"] == 2.0                    # peak 1005 → 1003


def test_summarize_arm_empty_realized():
    from utils.sequential_perturbation import _summarize_arm
    arm = {"final_equity": 1000.0, "realized": [], "equity_curve": [1000.0]}
    s = _summarize_arm(arm, 1000.0)
    assert s["net_pnl"] == 0.0
    assert s["trades"] == 0
    assert s["win_rate"] == 0.0
    assert s["max_drawdown"] == 0.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_sequential_perturbation.py::test_summarize_arm_extracted_helper -v`
Expected: FAIL with `ImportError: cannot import name '_summarize_arm'`

- [ ] **Step 3: 提取 helper + 改 build_delta_report 复用**

在 `utils/sequential_perturbation.py` 的 `_max_drawdown` 定义之后，新增模块级函数：

```python
def _summarize_arm(arm, initial_equity):
    """单臂 PnL summary（从 build_delta_report 的 _summ 提取，供联合扫描复用）。"""
    rl = arm["realized"]
    wins = sum(1 for x in rl if x > 0)
    return {"net_pnl": arm["final_equity"] - initial_equity, "trades": len(rl),
            "win_rate": wins / len(rl) if rl else 0.0,
            "max_drawdown": _max_drawdown(arm["equity_curve"])}
```

然后在 `build_delta_report` 内，把局部闭包 `_summ` 删除，改用 helper。原代码：

```python
    def _summ(arm):
        rl = arm["realized"]
        wins = sum(1 for x in rl if x > 0)
        return {"net_pnl": arm["final_equity"] - initial_equity, "trades": len(rl),
                "win_rate": wins / len(rl) if rl else 0.0,
                "max_drawdown": _max_drawdown(arm["equity_curve"])}
    b_s, p_s = _summ(base), _summ(pert)
```

替换为：

```python
    b_s, p_s = _summarize_arm(base, initial_equity), _summarize_arm(pert, initial_equity)
```

- [ ] **Step 4: 跑测试确认通过 + 回归 build_delta_report**

Run: `python3 -m pytest tests/test_sequential_perturbation.py -v`
Expected: PASS（新 2 测试 + 原有全部，build_delta_report 输出不变）

- [ ] **Step 5: 提交**

```bash
git add utils/sequential_perturbation.py tests/test_sequential_perturbation.py
git commit -m "refactor(cf): extract _summarize_arm from build_delta_report (joint-sweep 复用准备, 行为不变)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-cf-lab-joint-knob-sweep
---

### Task 2: `compute_interactions` 纯函数（交互项 + 三判定 + 自检锚点）

先做纯函数（不依赖引擎），数学可独立确定性验证。

**Files:**
- Create: `utils/joint_knob_sweep.py`
- Test: `tests/test_joint_knob_sweep.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_joint_knob_sweep.py`：

```python
from utils.joint_knob_sweep import compute_interactions


def _gr(combos):
    """构造最小 grid_result：combos = list of (combo_dict, net_pnl)。"""
    return {"combos": [{"combo": c, "delta": {"net_pnl": p, "win_rate": 0.0, "max_drawdown": 0.0},
                        "divergence_ratio": 0.0} for c, p in combos],
            "baseline_fidelity": 0.95, "sequence_len": 200, "untrustworthy": False,
            "fidelity_note": "note"}


# base: rr=1.5, conf=60
BV = {"rr_floor_default": 1.5, "min_confidence": 60}


def test_additive_when_joint_equals_sum_of_edges():
    # edge_A = +4, edge_B = +6, joint ≈ 10 → 交互≈0 → additive
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),    # anchor
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 4.0),    # edge_A
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 6.0),    # edge_B
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 10.0)])  # joint
    out = compute_interactions(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert out["anchor_ok"] is True
    inter = next(i for i in out["interactions"] if i["combo"] == {"rr_floor_default": 1.3, "min_confidence": 40})
    assert abs(inter["interaction"]) < out["effective_threshold"]
    assert inter["classification"] == "additive"


def test_synergy_when_joint_exceeds_sum():
    # edge_A=+1, edge_B=+1, joint=+20 → 交互=+18 → synergy
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 1.0),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 1.0),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 20.0)])
    out = compute_interactions(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    inter = next(i for i in out["interactions"] if i["combo"] == {"rr_floor_default": 1.3, "min_confidence": 40})
    assert inter["interaction"] == 18.0
    assert inter["classification"] == "synergy"


def test_antagonism_when_joint_below_sum():
    # edge_A=+10, edge_B=+10, joint=0 → 交互=-20 → antagonism
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 10.0),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 10.0),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 0.0)])
    out = compute_interactions(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    inter = next(i for i in out["interactions"] if i["combo"] == {"rr_floor_default": 1.3, "min_confidence": 40})
    assert inter["interaction"] == -20.0
    assert inter["classification"] == "antagonism"


def test_anchor_fail_when_base_base_nonzero():
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 5.0),   # anchor 非零!
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 4.0),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 6.0),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 10.0)])
    out = compute_interactions(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert out["anchor_ok"] is False


def test_higher_order_skipped():
    # 3 个非 base 轴 → skipped:higher_order（首发只做 2 轴 pairwise）
    bv3 = {"a": 0, "b": 0, "c": 0}
    gr = _gr([({"a": 0, "b": 0, "c": 0}, 0.0),
              ({"a": 1, "b": 1, "c": 1}, 5.0)])
    out = compute_interactions(gr, bv3, actionable_min_pnl=1.0, value_penalty_k=0.0)
    inter = next(i for i in out["interactions"] if i["combo"] == {"a": 1, "b": 1, "c": 1})
    assert inter["classification"] == "skipped:higher_order"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_joint_knob_sweep.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.joint_knob_sweep'`

- [ ] **Step 3: 实现 compute_interactions**

新建 `utils/joint_knob_sweep.py`：

```python
"""多旋钮联合扫描 + 交互效应检验（L4 扩展）：笛卡尔积扫 L3b + 2-way 交互项
量化协同/可加/拮抗 + 多维孤峰守卫方向推荐。
observability-only —— 严禁交易决策路径 import；推荐绝不自动改线上 config。"""
import itertools

from utils.sequential_perturbation import (run_arm, _gate_of_recorded,
                                           _summarize_arm, _FIDELITY_NOTE)


def _non_base_axes(combo, base_values):
    """combo 中取值偏离 base 的旋钮 key 列表。"""
    return [k for k, v in combo.items() if base_values.get(k) != v]


def _delta_of(grid_result, combo):
    for c in grid_result["combos"]:
        if c["combo"] == combo:
            return c["delta"]
    return None


def compute_interactions(grid_result, base_values, *, actionable_min_pnl=0.0,
                         value_penalty_k=0.1):
    """对每个 2-轴联合点算 interaction = Δ(a,b) − Δ(a,base) − Δ(base,b)，
    判定 synergy/additive/antagonism；(base,base) delta≈0 自检。"""
    combos = grid_result["combos"]
    m = len(combos)
    threshold = actionable_min_pnl * (1 + value_penalty_k * m)

    # 自检锚点：(base,base) 组合
    base_combo = dict(base_values)
    anchor_delta = _delta_of(grid_result, base_combo)
    anchor_ok = anchor_delta is not None and abs(anchor_delta["net_pnl"]) <= threshold

    interactions = []
    for c in combos:
        combo = c["combo"]
        non_base = _non_base_axes(combo, base_values)
        if len(non_base) != 2:
            if len(non_base) >= 1:  # 非 anchor、非 edge 的高阶点
                interactions.append({"combo": combo, "interaction": None,
                                     "classification": "skipped:higher_order"})
            continue
        ka, kb = non_base
        d_ab = c["delta"]["net_pnl"]
        edge_a = _delta_of(grid_result, {**base_values, ka: combo[ka]})
        edge_b = _delta_of(grid_result, {**base_values, kb: combo[kb]})
        if edge_a is None or edge_b is None:
            interactions.append({"combo": combo, "interaction": None,
                                 "classification": "skipped:missing_edge"})
            continue
        inter = d_ab - edge_a["net_pnl"] - edge_b["net_pnl"]
        if abs(inter) <= threshold:
            cls = "additive"
        elif inter > 0:
            cls = "synergy"
        else:
            cls = "antagonism"
        interactions.append({"combo": combo, "interaction": inter, "classification": cls,
                             "delta_ab": d_ab, "delta_a": edge_a["net_pnl"],
                             "delta_b": edge_b["net_pnl"]})
    return {"interactions": interactions, "anchor_ok": anchor_ok,
            "effective_threshold": threshold,
            "fidelity_note": grid_result.get("fidelity_note", _FIDELITY_NOTE)}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_joint_knob_sweep.py -v`
Expected: PASS（5 测试）

- [ ] **Step 5: 提交**

```bash
git add utils/joint_knob_sweep.py tests/test_joint_knob_sweep.py
git commit -m "feat(cf): compute_interactions — 2-way 交互项 + 协同/可加/拮抗判定 + 锚点自检

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-cf-lab-joint-knob-sweep
---

### Task 3: `sweep_grid` 笛卡尔积扫描（baseline 单次复用）

**Files:**
- Modify: `utils/joint_knob_sweep.py`（追加 `sweep_grid`）
- Test: `tests/test_joint_knob_sweep.py`（追加，monkeypatch run_arm）

- [ ] **Step 1: 写失败测试**

在 `tests/test_joint_knob_sweep.py` 追加：

```python
import asyncio
import utils.joint_knob_sweep as jks


class _FakeArm:
    """run_arm 返回结构的最小桩：按 config 决定 final_equity。"""
    @staticmethod
    def make(config):
        # baseline (空 config / 全 base) → equity 1000；每放宽一个旋钮 +5
        bump = 5.0 * len(config) if config else 0.0
        n = 4
        return {"final_equity": 1000.0 + bump, "realized": [1.0] * n,
                "equity_curve": [1000.0, 1000.0 + bump],
                "decisions": [{"gate": "accept" if config else "rr_below_floor"} for _ in range(n)],
                "cf_open_count": len(config)}


def test_sweep_grid_cartesian_and_baseline_reuse(monkeypatch):
    calls = []

    async def fake_run_arm(recs, config, price_loader, **kw):
        calls.append(dict(config))
        return _FakeArm.make(config)

    monkeypatch.setattr(jks, "run_arm", fake_run_arm)
    # _gate_of_recorded 桩：录制全 reject
    monkeypatch.setattr(jks, "_gate_of_recorded", lambda r: "rr_below_floor")

    recs = [{"timestamp": i, "symbol": "X"} for i in range(4)]
    grids = {"rr_floor_default": [1.5, 1.3], "min_confidence": [60, 40]}
    res = asyncio.run(jks.sweep_grid(recs, grids, price_loader=None,
                                     baseline_config={}, fidelity_threshold=0.0))
    # 笛卡尔积 = 2×2 = 4 组合
    assert len(res["combos"]) == 4
    # baseline 臂只跑 1 次：calls 中空 config（baseline）恰好 1 个
    baseline_calls = [c for c in calls if not c]
    assert len(baseline_calls) == 1
    # 总调用 = 1 baseline + 4 perturbed
    assert len(calls) == 5
    # 多 key perturbed_config 正确透传
    assert {"rr_floor_default": 1.3, "min_confidence": 40} in calls
    assert res["untrustworthy"] is False


def test_sweep_grid_untrustworthy_short_circuit(monkeypatch):
    async def fake_run_arm(recs, config, price_loader, **kw):
        return _FakeArm.make(config)
    monkeypatch.setattr(jks, "run_arm", fake_run_arm)
    # 录制 gate 与 baseline 回放永不一致 → fidelity = 0
    monkeypatch.setattr(jks, "_gate_of_recorded", lambda r: "NEVER_MATCH")

    recs = [{"timestamp": i, "symbol": "X"} for i in range(4)]
    grids = {"rr_floor_default": [1.5, 1.3], "min_confidence": [60, 40]}
    res = asyncio.run(jks.sweep_grid(recs, grids, price_loader=None,
                                     baseline_config={}, fidelity_threshold=0.8))
    assert res["untrustworthy"] is True
    assert res["combos"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_joint_knob_sweep.py::test_sweep_grid_cartesian_and_baseline_reuse -v`
Expected: FAIL with `AttributeError: ... has no attribute 'sweep_grid'`

- [ ] **Step 3: 实现 sweep_grid**

在 `utils/joint_knob_sweep.py` 的 import 之后、`_non_base_axes` 之前插入：

```python
async def sweep_grid(records, knob_grids, price_loader, *, baseline_config=None,
                     fidelity_threshold=0.8, initial_equity=1000.0, max_slots=3,
                     daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3):
    """多旋钮笛卡尔积扫描。baseline 臂只跑一次复用（fidelity 是 baseline 属性）。
    每个组合作为多 key perturbed_config 跑一个 perturbed 臂。"""
    recs = sorted(records, key=lambda r: r.get("timestamp", 0))
    kw = dict(price_loader=price_loader, initial_equity=initial_equity, max_slots=max_slots,
              daily_pnl_hard_stop=daily_pnl_hard_stop, consecutive_loss_limit=consecutive_loss_limit)
    base_cfg = dict(baseline_config or {})

    base = await run_arm(recs, base_cfg, **kw)
    agree = sum(1 for d, r in zip(base["decisions"], recs)
                if d["gate"] == _gate_of_recorded(r))
    fidelity = agree / len(recs) if recs else 0.0
    out_meta = {"baseline_fidelity": fidelity, "sequence_len": len(recs),
                "fidelity_note": _FIDELITY_NOTE,
                "baseline_cf_open_count": base["cf_open_count"]}
    if fidelity < fidelity_threshold:
        return {"combos": [], "untrustworthy": True, **out_meta}

    base_summary = _summarize_arm(base, initial_equity)
    knob_keys = list(knob_grids.keys())
    combos = []
    for values in itertools.product(*[knob_grids[k] for k in knob_keys]):
        perturbed_config = dict(zip(knob_keys, values))
        pert = await run_arm(recs, perturbed_config, **kw)
        p_summary = _summarize_arm(pert, initial_equity)
        delta = {"net_pnl": p_summary["net_pnl"] - base_summary["net_pnl"],
                 "win_rate": p_summary["win_rate"] - base_summary["win_rate"],
                 "max_drawdown": p_summary["max_drawdown"] - base_summary["max_drawdown"]}
        div = sum(1 for b, p in zip(base["decisions"], pert["decisions"])
                  if b["gate"] != p["gate"])
        combos.append({"combo": perturbed_config, "delta": delta,
                       "divergence_ratio": div / len(recs) if recs else 0.0,
                       "perturbed_cf_open_count": pert["cf_open_count"]})
    return {"combos": combos, "untrustworthy": False,
            "baseline_summary": base_summary, **out_meta}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_joint_knob_sweep.py -v`
Expected: PASS（Task 2 的 5 + 本任务 2 = 7 测试）

- [ ] **Step 5: 提交**

```bash
git add utils/joint_knob_sweep.py tests/test_joint_knob_sweep.py
git commit -m "feat(cf): sweep_grid — 多旋钮笛卡尔积扫描, baseline 臂单次复用

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-cf-lab-joint-knob-sweep
---

### Task 4: `recommend_direction_nd` 多维孤峰守卫

**Files:**
- Modify: `utils/joint_knob_sweep.py`（追加 `recommend_direction_nd` + `_confidence_nd`）
- Test: `tests/test_joint_knob_sweep.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_joint_knob_sweep.py` 追加：

```python
from utils.joint_knob_sweep import recommend_direction_nd


def test_recommend_coherent_neighbor():
    # best=(1.3,40) net=10；轴邻居 (1.5,40) net=6 同向 → 连贯 → recommend
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 5.0),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 6.0),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 10.0)])
    out = recommend_direction_nd(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert out["verdict"] == "recommend"
    assert out["recommended_combo"] == {"rr_floor_default": 1.3, "min_confidence": 40}


def test_recommend_isolated_spike():
    # best=(1.3,40) net=100；轴邻居都 ≈0 → 孤立尖刺 → 拒答
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 0.5),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 0.5),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 100.0)])
    out = recommend_direction_nd(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert out["verdict"] == "no_actionable_direction"
    assert out.get("isolated_spike") is True


def test_recommend_below_threshold():
    # 全部 delta 都很小 → below_threshold
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 0.1),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 0.1),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 0.2)])
    out = recommend_direction_nd(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert out["verdict"] == "no_actionable_direction"
    assert out["reason"] == "below_threshold"


def test_recommend_reports_all_combos():
    gr = _gr([({"rr_floor_default": 1.5, "min_confidence": 60}, 0.0),
              ({"rr_floor_default": 1.3, "min_confidence": 60}, 5.0),
              ({"rr_floor_default": 1.5, "min_confidence": 40}, 6.0),
              ({"rr_floor_default": 1.3, "min_confidence": 40}, 10.0)])
    out = recommend_direction_nd(gr, BV, actionable_min_pnl=1.0, value_penalty_k=0.0)
    assert "all_combos" in out and len(out["all_combos"]) == 4
    assert "fidelity_note" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_joint_knob_sweep.py::test_recommend_coherent_neighbor -v`
Expected: FAIL with `ImportError: cannot import name 'recommend_direction_nd'`

- [ ] **Step 3: 实现 recommend_direction_nd**

在 `utils/joint_knob_sweep.py` 末尾追加：

```python
def _confidence_nd(best, baseline_fidelity, sequence_len):
    fid = baseline_fidelity or 0.0
    div = best.get("divergence_ratio") or 0.0
    n = sequence_len or 0
    div_factor = max(0.0, 1.0 - max(0.0, div - 0.5))
    sample_factor = 1.0 if n >= 100 else (0.6 if n >= 30 else 0.0)
    return round(fid * div_factor * sample_factor, 3)


def _axis_neighbors(combo, knob_grids):
    """网格上沿每个轴 ±1 step 的相邻组合（曼哈顿距离=1）。"""
    out = []
    for k, vals in knob_grids.items():
        if combo[k] not in vals:
            continue
        i = vals.index(combo[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                nb = dict(combo)
                nb[k] = vals[j]
                out.append(nb)
    return out


def recommend_direction_nd(grid_result, base_values, *, knob_grids=None,
                           min_sample=30, actionable_min_pnl=0.0, value_penalty_k=0.1,
                           coherence_frac=0.5):
    """多维轴邻居孤峰守卫 + 门槛随网格点数收紧。证据不足拒答不杜撰。"""
    combos = grid_result["combos"]
    note = grid_result.get("fidelity_note")
    base = {"all_combos": combos, "fidelity_note": note, "tested_count": len(combos)}
    if grid_result.get("untrustworthy"):
        return {**base, "verdict": "no_actionable_direction", "reason": "untrustworthy"}
    seq = grid_result.get("sequence_len", 0)
    trustworthy = [c for c in combos
                   if seq >= min_sample and c.get("delta") is not None]
    if not trustworthy:
        return {**base, "verdict": "no_actionable_direction", "reason": "no_trustworthy_combos"}

    m = len(combos)
    effective_min = actionable_min_pnl * (1 + value_penalty_k * m)
    ranked = sorted(trustworthy, key=lambda c: c["delta"]["net_pnl"], reverse=True)
    best = ranked[0]
    if best["delta"]["net_pnl"] <= effective_min:
        return {**base, "verdict": "no_actionable_direction", "reason": "below_threshold",
                "effective_min_pnl": effective_min}

    # 推导 knob_grids（每轴取值集合，保序）若未显式传
    if knob_grids is None:
        knob_grids = {}
        for c in combos:
            for k, v in c["combo"].items():
                knob_grids.setdefault(k, [])
                if v not in knob_grids[k]:
                    knob_grids[k].append(v)
        for k in knob_grids:
            knob_grids[k].sort()

    bp = best["delta"]["net_pnl"]
    neighbor_combos = _axis_neighbors(best["combo"], knob_grids)
    nb_deltas = [c["delta"]["net_pnl"] for c in trustworthy
                 if c["combo"] in neighbor_combos]
    coherent = any(d >= bp * coherence_frac for d in nb_deltas) if nb_deltas else False
    if not coherent:
        return {**base, "verdict": "no_actionable_direction", "reason": "isolated_spike",
                "isolated_spike": True}
    return {**base, "verdict": "recommend", "recommended_combo": best["combo"],
            "delta_net_pnl": bp,
            "confidence": _confidence_nd(best, grid_result.get("baseline_fidelity"), seq),
            "baseline_fidelity": grid_result.get("baseline_fidelity"),
            "divergence_ratio": best.get("divergence_ratio"), "sample": seq}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_joint_knob_sweep.py -v`
Expected: PASS（共 11 测试）

- [ ] **Step 5: 提交**

```bash
git add utils/joint_knob_sweep.py tests/test_joint_knob_sweep.py
git commit -m "feat(cf): recommend_direction_nd — 多维轴邻居孤峰守卫 + 门槛随网格点数收紧

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-cf-lab-joint-knob-sweep
---

### Task 5: 红线守卫扩展（禁生产链路 import 新模块）

**Files:**
- Test: `tests/test_cf_red_line_guard.py:62-72`（`test_decision_paths_do_not_read_replay_products` 内追加显式断言）

- [ ] **Step 1: 写失败断言**

在 `tests/test_cf_red_line_guard.py` 的 `test_decision_paths_do_not_read_replay_products` 函数体内，`assert "knob_sweep" not in src, mp` 那一行之后追加：

```python
        assert "joint_knob_sweep" not in src, mp
```

> 注：现有 `assert "knob_sweep" not in src` 因子串已隐式覆盖 `joint_knob_sweep`，此处加显式断言是为可读性与防回归（若未来有人放宽 knob_sweep 匹配）。

- [ ] **Step 2: 跑测试确认通过（应已通过，因无生产模块 import）**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -v`
Expected: PASS（4 测试，无生产模块 import joint_knob_sweep）

- [ ] **Step 3: 提交**

```bash
git add tests/test_cf_red_line_guard.py
git commit -m "test(cf): 红线守卫显式禁生产链路 import joint_knob_sweep

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-cf-lab-joint-knob-sweep
---

### Task 6: Driver — 真实磁带两轴联合扫描段

**Files:**
- Modify: `cf_direction_recommendation.py`（`main()` 末尾追加联合扫描段）

- [ ] **Step 1: 追加联合扫描段**

在 `cf_direction_recommendation.py` 顶部 import 区追加：

```python
from utils.joint_knob_sweep import sweep_grid, compute_interactions, recommend_direction_nd
```

在 `main()` 函数内、`if meta.get("fidelity_note"):` 那段**之前**插入：

```python
    # ── L4 联合扫描: rr_floor_default × min_confidence 交互效应 ──
    print("\n=== L4 联合扫描: rr_floor_default × min_confidence (交互效应) ===")
    base_values = {"rr_floor_default": 1.50, "min_confidence": 60}
    knob_grids = {"rr_floor_default": [1.50, 1.40, 1.30, 1.20],
                  "min_confidence": [60, 50, 40]}
    grid = await sweep_grid(recs, knob_grids, price_loader, baseline_config={})
    if grid.get("untrustworthy"):
        print(f"  untrustworthy (baseline_fidelity={grid['baseline_fidelity']:.3f}) → 拒答")
    else:
        print(f"  baseline_fidelity={grid['baseline_fidelity']:.3f}  组合数={len(grid['combos'])}")
        for c in grid["combos"]:
            d = c["delta"]
            print(f"    {c['combo']}  net={d['net_pnl']:+.2f}  div={c['divergence_ratio']:.3f}")
        inter = compute_interactions(grid, base_values)
        print(f"\n  交互矩阵 (anchor_ok={inter['anchor_ok']}, "
              f"threshold={inter['effective_threshold']:.2f}):")
        for i in inter["interactions"]:
            if i["interaction"] is None:
                print(f"    {i['combo']}  {i['classification']}")
            else:
                print(f"    {i['combo']}  interaction={i['interaction']:+.2f}  → {i['classification']}")
        rec = recommend_direction_nd(grid, base_values, knob_grids=knob_grids)
        print(f"\n  >>> recommend_direction_nd: {json.dumps(rec, ensure_ascii=False, default=str)[:600]}")
```

- [ ] **Step 2: 语法自检（不跑全量，磁带可能缺数据）**

Run: `python3 -c "import ast; ast.parse(open('cf_direction_recommendation.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add cf_direction_recommendation.py
git commit -m "feat(cf): driver 增 rr_floor × min_confidence 两轴联合扫描 + 交互矩阵段

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-cf-lab-joint-knob-sweep
---

### Task 7: 全量回归 + 勾选 tasks.md

**Files:**
- Modify: `openspec/changes/cf-lab-joint-knob-sweep/tasks.md`

- [ ] **Step 1: 跑全量 pytest 回归**

Run: `python3 -m pytest -q 2>&1 | tail -5`
Expected: PASS，基线 1255 + 新增（Task1 +2、Task2 +5、Task3 +2、Task4 +4 = +13）≈ 1268 passed，无回退

- [ ] **Step 2: 勾选 openspec tasks.md**

把 `openspec/changes/cf-lab-joint-knob-sweep/tasks.md` 中实现/测试/验收相关 `- [ ]` 改为 `- [x]`（验收里「跑真实磁带」一项若磁带数据不足则标注实测结果）。

- [ ] **Step 3: 提交**

```bash
git add openspec/changes/cf-lab-joint-knob-sweep/tasks.md
git commit -m "chore(comet): cf-lab-joint-knob-sweep tasks complete (全量回归 +13)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-cf-lab-joint-knob-sweep
---

## Self-Review

**Spec coverage**（delta spec `joint-knob-sweep` 5 requirement 对照）:
- 多旋钮笛卡尔积联合扫描 → Task 3 `sweep_grid`（product + 多 key 透传 + 复用 run_arm）✓
- baseline 臂单次复用 → Task 3（base 跑 1 次 + untrustworthy 短路）✓
- 交互效应量化 + 自检锚点 + 显著性阈值口径 → Task 2 `compute_interactions`（含 anchor + actionable_min_pnl×(1+k·M) 阈值）✓
- 多维孤峰守卫方向推荐 → Task 4 `recommend_direction_nd`（轴邻居 + 门槛收紧 + 报全貌）✓
- observability-only 绝不自动应用 → Task 5 红线守卫扩展 ✓

**Placeholder scan:** 无 TBD/TODO；每步含完整代码与命令。

**Type consistency:** `sweep_grid` 返回 `{combos:[{combo,delta,divergence_ratio,...}], baseline_fidelity, sequence_len, untrustworthy, fidelity_note}` 在 Task 2/4 测试桩 `_gr` 与 driver 中一致；`compute_interactions` 返回 `{interactions, anchor_ok, effective_threshold, fidelity_note}` 一致；`recommend_direction_nd` 返回 `{verdict, recommended_combo, all_combos, ...}` 一致。`base_values` 在三处均为 `{knob: base_value}` dict。
