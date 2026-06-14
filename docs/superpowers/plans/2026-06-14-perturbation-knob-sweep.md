---
change: perturbation-knob-sweep
design-doc: docs/superpowers/specs/2026-06-14-perturbation-knob-sweep-design.md
base-ref: b850e70e19a0b8de46946569cc4735fd61bfdf59
---

# Knob Sweep + Direction Recommend (L4) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** 单旋钮 grid 扫描 + 诚实门控 + 方向推荐（含多重比较守卫），收尾反事实策略实验室。

**Architecture:** `utils/knob_sweep.py` 纯编排，复用 L3b `build_delta_report` + L1 `cf_honesty_gate`。observability-only write-only，绝不自动改线上 config。

**确认的 API:** `build_delta_report(records, baseline_config, perturbed_config, price_loader, *, initial_equity=1000, max_slots=3, fidelity_threshold=0.8, daily_pnl_hard_stop=-50, consecutive_loss_limit=3)` → `{baseline, perturbed, delta:{net_pnl,win_rate,max_drawdown}, metadata:{baseline_fidelity, untrustworthy, divergence_ratio, sequence_len, fidelity_note}}`（async）。

**红线:** observability-only write-only（Task 3 守卫）；绝不自动应用。零回归：基线 1217 不降。

---

## Task 1: 扫描引擎 utils/knob_sweep.py :: sweep_knob

**Files:** Create `utils/knob_sweep.py`, `tests/test_knob_sweep.py`.

- [ ] **Step 1: 写失败测试**

```python
# tests/test_knob_sweep.py
import asyncio
import pytest
from utils.knob_sweep import sweep_knob


@pytest.fixture(autouse=True)
def _restore_loop():
    yield
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


def _price_loader_tp(symbol, created_at, window_sec=86400):
    return [{"open_time": int((created_at + 60) * 1000), "high": 53400, "low": 49900, "close": 53400}]


def _accept_rec(ts):
    from tests.test_decision_replay import _accept_fixture_record
    rec = _accept_fixture_record()
    rec["timestamp"] = ts
    rec["decision"] = "accept"
    return rec


def test_sweep_collects_per_value():
    recs = [_accept_rec(1000.0 + i * 100000.0) for i in range(2)]
    result = asyncio.run(sweep_knob(recs, knob="rr_floor_long_bullish",
                                    values=[1.3, 10.0], price_loader=_price_loader_tp,
                                    fidelity_threshold=0.5))
    assert len(result) == 2
    assert {r["value"] for r in result} == {1.3, 10.0}
    for r in result:
        assert "delta" in r and "baseline_fidelity" in r and "untrustworthy" in r
        assert "sequence_len" in r


def test_sweep_explicit_value_list_order_preserved():
    recs = [_accept_rec(1000.0)]
    result = asyncio.run(sweep_knob(recs, knob="rr_floor_long_bullish",
                                    values=[1.4, 1.3, 1.6], price_loader=_price_loader_tp,
                                    fidelity_threshold=0.5))
    assert [r["value"] for r in result] == [1.4, 1.3, 1.6]
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 实现 sweep_knob**

```python
"""旋钮扫描 + 方向推荐（L4，反事实策略实验室收官）：单旋钮 grid 扫描 L3b
build_delta_report + 诚实门控 + 多重比较守卫 → 方向推荐或拒答。
observability-only —— 严禁交易决策路径 import；推荐绝不自动改线上 config。"""
from utils.sequential_perturbation import build_delta_report


async def sweep_knob(records, knob, values, price_loader, *, baseline_config=None,
                     fidelity_threshold=0.8, initial_equity=1000.0, max_slots=3,
                     daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3):
    """对 knob 的 values 逐值跑 L3b build_delta_report，收集每值 delta + 信任/样本元数据。"""
    base_cfg = dict(baseline_config or {})
    out = []
    for v in values:
        rep = await build_delta_report(
            records, base_cfg, {knob: v}, price_loader,
            initial_equity=initial_equity, max_slots=max_slots,
            fidelity_threshold=fidelity_threshold,
            daily_pnl_hard_stop=daily_pnl_hard_stop,
            consecutive_loss_limit=consecutive_loss_limit)
        meta = rep.get("metadata", {})
        out.append({
            "value": v, "delta": rep.get("delta"),
            "baseline_fidelity": meta.get("baseline_fidelity"),
            "untrustworthy": meta.get("untrustworthy", False),
            "divergence_ratio": meta.get("divergence_ratio"),
            "sequence_len": meta.get("sequence_len", 0),
            "fidelity_note": meta.get("fidelity_note"),
        })
    return out
```

- [ ] **Step 4: 运行通过** — `python3 -m pytest tests/test_knob_sweep.py -q` → 2 passed
- [ ] **Step 5: 提交**

```bash
git add utils/knob_sweep.py tests/test_knob_sweep.py
git commit -m "feat(L4): knob sweep engine (1D grid over L3b build_delta_report)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 方向推荐器 recommend_direction（含多重比较守卫）

**Files:** Modify `utils/knob_sweep.py`, `tests/test_knob_sweep.py`.

- [ ] **Step 1: 写失败测试**

```python
def _row(value, net_pnl, untrustworthy=False, fidelity=1.0, n=100, div=0.5):
    return {"value": value, "delta": {"net_pnl": net_pnl, "win_rate": 0.0, "max_drawdown": 0.0},
            "baseline_fidelity": fidelity, "untrustworthy": untrustworthy,
            "divergence_ratio": div, "sequence_len": n, "fidelity_note": "note"}


def test_recommend_coherent_trend():
    from utils.knob_sweep import recommend_direction
    # 单调趋势：值越大 delta 越正 → 推荐最优值（连贯）
    sweep = [_row(1.3, 1.0), _row(1.4, 3.0), _row(1.5, 6.0)]
    rec = recommend_direction(sweep, min_sample=30, actionable_min_pnl=1.0)
    assert rec["verdict"] == "recommend"
    assert rec["recommended_value"] == 1.5
    assert "all_values" in rec and len(rec["all_values"]) == 3
    assert "confidence" in rec and "baseline_fidelity" in rec


def test_recommend_isolated_spike_refused():
    from utils.knob_sweep import recommend_direction
    # 孤立尖刺：1.4 远高于两侧 → 疑似噪声 → 拒推荐
    sweep = [_row(1.3, -1.0), _row(1.4, 20.0), _row(1.5, -1.0)]
    rec = recommend_direction(sweep, min_sample=30, actionable_min_pnl=1.0)
    assert rec["verdict"] == "no_actionable_direction"
    assert rec.get("isolated_spike") is True


def test_recommend_no_trustworthy_refused():
    from utils.knob_sweep import recommend_direction
    sweep = [_row(1.3, 5.0, untrustworthy=True), _row(1.4, 6.0, n=5)]  # 一个 untrustworthy 一个薄样本
    rec = recommend_direction(sweep, min_sample=30, actionable_min_pnl=1.0)
    assert rec["verdict"] == "no_actionable_direction"


def test_recommend_below_threshold_refused():
    from utils.knob_sweep import recommend_direction
    sweep = [_row(1.3, 0.1), _row(1.4, 0.2), _row(1.5, 0.3)]  # 连贯但改善不显著
    rec = recommend_direction(sweep, min_sample=30, actionable_min_pnl=5.0)
    assert rec["verdict"] == "no_actionable_direction"
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 实现 recommend_direction**

```python
def _confidence(best):
    """三因子派生（observability 标签，非精确概率）：fidelity × (1-div惩罚) × 样本档。"""
    fid = best.get("baseline_fidelity") or 0.0
    div = best.get("divergence_ratio") or 0.0
    n = best.get("sequence_len", 0)
    div_factor = max(0.0, 1.0 - max(0.0, div - 0.5))   # divergence>0.5 开始惩罚
    sample_factor = 1.0 if n >= 100 else (0.6 if n >= 30 else 0.0)
    return round(fid * div_factor * sample_factor, 3)


def _is_isolated_spike(best, trustworthy, coherence_frac=0.5):
    """最优值是否孤立尖刺：值序相邻无任一同向支撑（相邻 net_pnl < best * coherence_frac）。"""
    by_val = sorted(trustworthy, key=lambda r: r["value"])
    vals = [r["value"] for r in by_val]
    i = vals.index(best["value"])
    bp = best["delta"]["net_pnl"]
    if bp <= 0:
        return False
    neighbors = []
    if i > 0:
        neighbors.append(by_val[i - 1])
    if i < len(by_val) - 1:
        neighbors.append(by_val[i + 1])
    if not neighbors:
        return True  # 仅一个值，无趋势支撑
    # 连贯 = 至少一个相邻值同向且达到 best 的 coherence_frac
    coherent = any(nb["delta"]["net_pnl"] >= bp * coherence_frac for nb in neighbors)
    return not coherent


def recommend_direction(sweep_result, *, min_sample=30, actionable_min_pnl=0.0,
                        value_penalty_k=0.1, coherence_frac=0.5):
    """门控 + 排名 + 多重比较守卫（连贯趋势）→ recommend / no_actionable_direction。
    证据不足绝不杜撰方向。observability-only，绝不自动应用。"""
    note = next((r.get("fidelity_note") for r in sweep_result if r.get("fidelity_note")), None)
    base = {"all_values": sweep_result, "fidelity_note": note,
            "tested_count": len(sweep_result)}
    trustworthy = [r for r in sweep_result
                   if not r.get("untrustworthy") and (r.get("sequence_len", 0) >= min_sample)
                   and r.get("delta") is not None]
    if not trustworthy:
        return {**base, "verdict": "no_actionable_direction", "reason": "no_trustworthy_values"}
    ranked = sorted(trustworthy, key=lambda r: r["delta"]["net_pnl"], reverse=True)
    best = ranked[0]
    effective_min = actionable_min_pnl * (1 + value_penalty_k * len(sweep_result))
    if best["delta"]["net_pnl"] <= effective_min:
        return {**base, "verdict": "no_actionable_direction",
                "reason": "below_threshold", "effective_min_pnl": effective_min}
    if _is_isolated_spike(best, trustworthy, coherence_frac):
        return {**base, "verdict": "no_actionable_direction",
                "reason": "isolated_spike", "isolated_spike": True}
    return {**base, "verdict": "recommend", "recommended_value": best["value"],
            "delta_net_pnl": best["delta"]["net_pnl"], "confidence": _confidence(best),
            "baseline_fidelity": best.get("baseline_fidelity"),
            "divergence_ratio": best.get("divergence_ratio"),
            "sample": best.get("sequence_len")}
```

- [ ] **Step 4: 运行通过** — `python3 -m pytest tests/test_knob_sweep.py -q` → 6 passed
- [ ] **Step 5: 提交**

```bash
git add utils/knob_sweep.py tests/test_knob_sweep.py
git commit -m "feat(L4): direction recommender (gate + rank + multiple-comparison guard + confidence)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 红线守卫 + 文档

**Files:** Modify `tests/test_cf_red_line_guard.py`, `CLAUDE.md`, `docs/to-do-list.md`, memory.

- [ ] **Step 1: 扩展红线守卫** — 在 `test_decision_paths_do_not_read_replay_products` 循环体加：
```python
        assert "knob_sweep" not in src, mp
```
- [ ] **Step 2: 运行通过** — `python3 -m pytest tests/test_cf_red_line_guard.py -q` → PASS
- [ ] **Step 3: 文档 + 记忆** — CLAUDE.md 红线补 L4 声明（`utils/knob_sweep.py` observability-only；单旋钮 grid 扫描；多重比较守卫连贯趋势/孤峰拒答；confidence 三因子透明；**绝不自动改线上 config，人审**；证据不足拒答不杜撰）。docs/to-do-list.md 路线图（#4 完成 = 反事实实验室 L1-L4 全收官）。memory roadmap 标 L4 完成 + 实验室完整。
- [ ] **Step 4: 提交**

```bash
git add tests/test_cf_red_line_guard.py CLAUDE.md docs/to-do-list.md
git commit -m "docs(L4): red-line guard + roadmap (lab L1-L4 complete)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 全量验证

- [ ] **Step 1: 编译** — `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .` → exit 0
- [ ] **Step 2: 全量** — `python3 -m pytest -q` → ≥ 1217 + 新增（~8），无 failure
- [ ] **Step 3: tasks.md 全勾 + 最终提交** — `git add -A && git commit -m "chore(L4): full regression green — counterfactual lab complete"`

---

## Self-Review

- **Spec 覆盖**：knob-sweep-engine（Task 1）、direction-recommender（Task 2：门控+排名+多重比较守卫+confidence 三因子）、红线守卫（Task 3）、零回归（Task 4）。
- **类型一致**：`sweep_knob`/`recommend_direction`/`_confidence`/`_is_isolated_spike` 跨 task 一致。复用 `build_delta_report` 真实签名。
- **无 placeholder**：每步真实代码。YAGNI：单旋钮 1D；纯编排零决策逻辑；多重比较守卫是 L4 诚实核心。
- **红线**：observability-only，绝不自动改线上 config（文档+守卫）。
