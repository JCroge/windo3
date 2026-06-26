---
change: cf-neutral-momentum-rescue-ab
design-doc: docs/superpowers/specs/2026-06-26-cf-neutral-momentum-rescue-ab-design.md
base-ref: f09b7d9f801c7f6e00f77246a2b9cd224678e5d3
archived-with: 2026-06-26-cf-neutral-momentum-rescue-ab
---

# cf-neutral-momentum-rescue-ab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 observability-only 测量驱动 `cf_neutral_momentum_rescue_ab.py`,以信号口径量化"被误标 neutral 但有客观上行动量"的 choppy/mixed 多单候选(A 桶)vs 对照(B 桶)的前向反事实净 R,诚实门裁定,决定是否值得放宽 path_evidence 阀门。

**Architecture:** 对决策磁带 population(`regime∈{choppy,mixed}` & `direction=='neutral'`)按方向无关谓词分 A/B 桶,合成标准化退出(entry=决策价,sl/tp=策略典型几何),经既有 `resolve_counterfactual`+`klines_1s` TP1 保守结算,簇去重,`cf_honesty_gate` 分桶裁定。镜像 `cf_choppy_neutral_tp1_floor_ab.py` 的结算栈,但**不实例化 Judge、不 replay**(信号口径)。

**Tech Stack:** Python 3.9,stdlib(json/sqlite3/statistics/collections),复用 `utils/counterfactual_pnl.resolve_counterfactual` + `utils/cf_honesty_gate.summarize_bucket`。

## Global Constraints

- observability-only,write-only:决策/风控路径(judge/executor/portfolio_risk_guard/reviewer/position_analyst)MUST NOT import 本驱动;`tests/test_cf_red_line_guard.py` 守卫。
- 谓词 MUST NOT 读取 `trend.strength`(代理根因)。
- CF 结算契约 MUST 传 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`(非 `entry_ref`)。
- 诚实门 `min_sample=30` 不下调;`n<30` → INSUFFICIENT_SAMPLE,仅 suggestive。
- TP1 保守(同根 SL/TP 冲突 SL-first)= `resolve_counterfactual` 既有行为,不改。
- 不下单、不改 config、不改任何 Judge/executor/live 逻辑。
- 基线测试 1460 passed,新增后须全绿。

archived-with: 2026-06-26-cf-neutral-momentum-rescue-ab
---

### Task 1: population 加载 + 方向无关谓词

**Files:**
- Create: `cf_neutral_momentum_rescue_ab.py`
- Test: `tests/test_cf_neutral_momentum_rescue_ab.py`

**Interfaces:**
- Produces:
  - `load_population(path="data/decision_replay_tape.jsonl") -> list[dict]` — 返回 replayable 且 `regime_state∈{choppy,mixed}` & `tech.trend.direction=='neutral'` 的记录(accept+reject 皆纳入)。
  - `rescue_predicate(rec: dict, pre12h_min: float, range_pos_max: float) -> bool` — A 桶谓词;读 `trend.daily_bias`/`trend.higher_tf_bias`/`entry_context.pre_12h_return_pct`/`entry_context.position_in_24h_range`;**不读 strength**。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cf_neutral_momentum_rescue_ab.py
import cf_neutral_momentum_rescue_ab as drv


def _rec(direction="neutral", regime="choppy", daily="bullish", htf="neutral",
         pre12h=0.05, range_pos=0.5, strength=25, decision="reject", replayable=True):
    return {
        "decision": decision, "replayable": replayable, "regime_state": regime,
        "symbol": "AAA-USDT", "timestamp": 1000.0, "price_at_decision": 10.0,
        "tech_analysis": {
            "trend": {"direction": direction, "daily_bias": daily,
                      "higher_tf_bias": htf, "strength": strength},
            "entry_context": {"pre_12h_return_pct": pre12h,
                              "position_in_24h_range": range_pos},
        },
    }


def test_predicate_hit_daily_bullish():
    assert drv.rescue_predicate(_rec(daily="bullish", htf="neutral",
                                     pre12h=0.05, range_pos=0.5), 0.03, 0.92) is True


def test_predicate_hit_htf_bullish():
    assert drv.rescue_predicate(_rec(daily="neutral", htf="bullish",
                                     pre12h=0.04, range_pos=0.8), 0.03, 0.92) is True


def test_predicate_excludes_no_bullish_bias():
    assert drv.rescue_predicate(_rec(daily="bearish", htf="bearish",
                                     pre12h=0.05, range_pos=0.5), 0.03, 0.92) is False


def test_predicate_excludes_low_pre12h():
    assert drv.rescue_predicate(_rec(daily="bullish", pre12h=0.01,
                                     range_pos=0.5), 0.03, 0.92) is False


def test_predicate_excludes_high_range_pos():
    assert drv.rescue_predicate(_rec(daily="bullish", pre12h=0.05,
                                     range_pos=0.95), 0.03, 0.92) is False


def test_predicate_ignores_strength():
    # 高 strength 但动量不足 → 仍 False(证明不依赖 strength 救入)
    assert drv.rescue_predicate(_rec(daily="bullish", pre12h=0.01,
                                     range_pos=0.5, strength=100), 0.03, 0.92) is False
    # 低 strength 但动量充分 → True(证明不被 strength 挡出)
    assert drv.rescue_predicate(_rec(daily="bullish", pre12h=0.05,
                                     range_pos=0.5, strength=10), 0.03, 0.92) is True
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_cf_neutral_momentum_rescue_ab.py -q`
Expected: FAIL（`ModuleNotFoundError: cf_neutral_momentum_rescue_ab` 或 `AttributeError`）

- [ ] **Step 3: 写最小实现**

```python
# cf_neutral_momentum_rescue_ab.py
"""cf-neutral-momentum-rescue-ab: path_evidence 阀门双重失效测量(observability-only write-only)。

体制空仓硬门 _classify_regime_flat_gate 的 path_evidence 救援阀门因 sym_dir=='bullish' +
strength>=60(neutral 结构封顶 ~50,是 bullish 的隐式代理)双重失效,从未触发。本驱动以
**信号口径**测量:对决策磁带 population(regime∈{choppy,mixed} & direction=='neutral')按
方向无关谓词(daily/htf bias 看多 + 12h 真实在涨 + 不在区间顶部,不读 strength)分 A(命中)/
B(对照)两桶,合成标准化退出(entry=决策价, sl/tp=策略典型几何),经 resolve_counterfactual+
klines_1s TP1 保守结算净 R,cf_honesty_gate(min_sample=30)分桶裁定。A 显著正 & B 不显著正 →
谓词有判别力、阀门值得放宽;A≈B 或皆负 → 救援无 edge。不实例化 Judge、不 replay、不下单/改 config。

红线:observability-only write-only —— 严禁任何交易决策/风控路径 import。
"""
import json
import os
import sqlite3
import statistics
from collections import defaultdict

from utils.counterfactual_pnl import resolve_counterfactual
from utils.cf_honesty_gate import summarize_bucket

TAPE = "data/decision_replay_tape.jsonl"
KL1 = "data/klines_1s.db"
KL = "data/klines.db"

PRE12H_GRID = [0.02, 0.03, 0.05]
RANGEPOS_GRID = [0.85, 0.92]


def load_population(path=TAPE):
    out = []
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not r.get("replayable"):
            continue
        if r.get("regime_state") not in ("choppy", "mixed"):
            continue
        trend = (r.get("tech_analysis") or {}).get("trend") or {}
        if trend.get("direction") != "neutral":
            continue
        out.append(r)
    return out


def rescue_predicate(rec, pre12h_min, range_pos_max):
    tech = rec.get("tech_analysis") or {}
    trend = tech.get("trend") or {}
    ectx = tech.get("entry_context") or {}
    bias_up = (trend.get("daily_bias") == "bullish"
               or trend.get("higher_tf_bias") == "bullish")
    pre12h = ectx.get("pre_12h_return_pct")
    range_pos = ectx.get("position_in_24h_range")
    if pre12h is None or range_pos is None:
        return False
    return bool(bias_up and pre12h >= pre12h_min and range_pos <= range_pos_max)
```

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_cf_neutral_momentum_rescue_ab.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**

```bash
git add cf_neutral_momentum_rescue_ab.py tests/test_cf_neutral_momentum_rescue_ab.py
git commit -m "feat(cf-neutral-momentum-rescue-ab): population 加载 + 方向无关谓词(不读 strength)"
```

archived-with: 2026-06-26-cf-neutral-momentum-rescue-ab
---

### Task 2: 策略典型几何派生 + 合成退出字段

**Files:**
- Modify: `cf_neutral_momentum_rescue_ab.py`
- Test: `tests/test_cf_neutral_momentum_rescue_ab.py`

**Interfaces:**
- Consumes: `load_population`(Task 1)。
- Produces:
  - `derive_strategy_geometry(path=TAPE) -> tuple[float, float]` — 从磁带 choppy-long **accept** 记录的 plan 取 median `(sl_dist, tp1_dist)`;无样本回退 `(0.015, 0.0225)`(R:R≈1.5)。
  - `synthesize_settle_fields(rec: dict, sl_dist: float, tp1_dist: float) -> dict | None` — 合成做多 settle 字段;entry=`price_at_decision`;`_plan` 含 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`(非 entry_ref);无效(无价/dist≤0)→ None。

- [ ] **Step 1: 写失败测试**

```python
def test_synthesize_long_geometry():
    rec = _rec()  # price_at_decision=10.0, timestamp=1000.0
    f = drv.synthesize_settle_fields(rec, sl_dist=0.02, tp1_dist=0.03)
    assert f["_side"] == "long"
    assert f["symbol"] == "AAA-USDT"
    assert f["_created"] == 1000.0
    assert abs(f["_sl_dist"] - 0.02) < 1e-9
    assert abs(f["_tp1_dist"] - 0.03) < 1e-9
    p = f["_plan"]
    assert p["side"] == "long"
    assert abs(p["entry_price"] - 10.0) < 1e-9
    assert p["created_at"] == 1000.0
    assert abs(p["stop_loss"] - 9.8) < 1e-9          # 10*(1-0.02)
    assert abs(p["take_profit"][0] - 10.3) < 1e-9    # 10*(1+0.03)
    assert "entry_ref" not in p                        # 契约:用 entry_price


def test_synthesize_invalid_dist_returns_none():
    assert drv.synthesize_settle_fields(_rec(), 0.0, 0.03) is None
    assert drv.synthesize_settle_fields(_rec(), 0.02, -0.01) is None


def test_synthesize_missing_price_returns_none():
    rec = _rec()
    rec["price_at_decision"] = None
    assert drv.synthesize_settle_fields(rec, 0.02, 0.03) is None


def test_derive_geometry_fallback(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    sl, tp = drv.derive_strategy_geometry(str(empty))
    assert abs(sl - 0.015) < 1e-9 and abs(tp - 0.0225) < 1e-9
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_cf_neutral_momentum_rescue_ab.py -q`
Expected: FAIL（`AttributeError: synthesize_settle_fields` / `derive_strategy_geometry`）

- [ ] **Step 3: 写最小实现**

在 `cf_neutral_momentum_rescue_ab.py` 追加:

```python
def derive_strategy_geometry(path=TAPE):
    """从磁带 choppy-long accept 的真实 plan 取 median sl_dist/tp1_dist。"""
    sl_ds, tp_ds = [], []
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("decision") != "accept" or r.get("regime_state") != "choppy":
                continue
            plan = (r.get("trade_decision_output") or {}).get("plan") or {}
            if plan.get("side") != "long":
                continue
            entry = plan.get("entry_ref")
            sl = plan.get("stop_loss")
            tp = plan.get("take_profit") or []
            if not (entry and sl and tp):
                continue
            sl_dist = (entry - sl) / entry
            tp1_dist = (tp[0] - entry) / entry
            if sl_dist > 0 and tp1_dist > 0:
                sl_ds.append(sl_dist)
                tp_ds.append(tp1_dist)
    if sl_ds and tp_ds:
        return statistics.median(sl_ds), statistics.median(tp_ds)
    return 0.015, 0.0225  # 回退 R:R≈1.5


def synthesize_settle_fields(rec, sl_dist, tp1_dist):
    if sl_dist <= 0 or tp1_dist <= 0:
        return None
    entry = rec.get("price_at_decision")
    if not entry:
        return None
    entry = float(entry)
    created = rec.get("timestamp")
    sl = entry * (1 - sl_dist)
    tp1 = entry * (1 + tp1_dist)
    return {"symbol": rec.get("symbol"), "_side": "long", "_created": created,
            "_sl_dist": sl_dist, "_tp1_dist": tp1_dist,
            "_plan": {"side": "long", "entry_price": entry, "created_at": created,
                      "stop_loss": sl, "take_profit": [tp1]}}
```

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_cf_neutral_momentum_rescue_ab.py -q`
Expected: PASS（11 passed）

- [ ] **Step 5: 提交**

```bash
git add cf_neutral_momentum_rescue_ab.py tests/test_cf_neutral_momentum_rescue_ab.py
git commit -m "feat(cf-neutral-momentum-rescue-ab): 策略典型几何派生 + 合成标准化退出(entry_price 契约)"
```

archived-with: 2026-06-26-cf-neutral-momentum-rescue-ab
---

### Task 3: 结算栈(bars/dedup/settle/verdict) + 集成结算测试

**Files:**
- Modify: `cf_neutral_momentum_rescue_ab.py`
- Test: `tests/test_cf_neutral_momentum_rescue_ab.py`

**Interfaces:**
- Consumes: `synthesize_settle_fields`(Task 2),`resolve_counterfactual`,`summarize_bucket`。
- Produces:
  - `load_bars(db, sym, created, window=86400) -> list[dict]`
  - `dedup_clusters(items, gap_sec=3600) -> list[dict]`
  - `settle_clusters(clusters, *, load_bars_fn=load_bars, resolve_fn=resolve_counterfactual) -> dict`
  - `bucket_verdict(settle) -> dict`
  - `settle_records(records, sl_dist, tp1_dist) -> tuple[list, dict, dict]` — 合成→去重→结算→裁定一条龙。

- [ ] **Step 1: 写失败测试**

```python
def test_settle_records_tp_and_sl_via_fake_bars(monkeypatch):
    # 两个不同 symbol 的候选:一个走到 TP,一个走到 SL
    recs = [_rec(), _rec()]
    recs[0]["symbol"] = "WIN-USDT"
    recs[1]["symbol"] = "LOSE-USDT"

    def fake_bars(db, sym, created, window=86400):
        if sym == "WIN-USDT":
            return [{"open_time": int((created + 60) * 1000),
                     "high": 11.0, "low": 9.95, "close": 11.0}]   # 命中 tp(10.3)
        return [{"open_time": int((created + 60) * 1000),
                 "high": 10.05, "low": 9.5, "close": 9.5}]         # 命中 sl(9.8)

    monkeypatch.setattr(drv, "load_bars", fake_bars)
    clusters, settle, v = drv.settle_records(recs, sl_dist=0.02, tp1_dist=0.03)
    assert settle["tp"] == 1
    assert settle["sl"] == 1
    assert settle["resolved"] == 2
    # net_R = (tp1_dist/sl_dist) + (-1) = 1.5 - 1 = 0.5
    assert abs(settle["net_R"] - 0.5) < 1e-6


def test_settle_records_nodata_skipped(monkeypatch):
    monkeypatch.setattr(drv, "load_bars", lambda *a, **k: [])
    clusters, settle, v = drv.settle_records([_rec()], 0.02, 0.03)
    assert settle["nodata"] == 1
    assert settle["resolved"] == 0


def test_bucket_verdict_thin_sample_insufficient(monkeypatch):
    # 单簇 → n=1 < 30 → INSUFFICIENT
    monkeypatch.setattr(drv, "load_bars", lambda *a, **k: [
        {"open_time": 1060000, "high": 11.0, "low": 9.95, "close": 11.0}])
    _, _, v = drv.settle_records([_rec()], 0.02, 0.03)
    assert "INSUFFICIENT" in v["verdict"]
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_cf_neutral_momentum_rescue_ab.py -q`
Expected: FAIL（`AttributeError: settle_records`）

- [ ] **Step 3: 写最小实现**

在 `cf_neutral_momentum_rescue_ab.py` 追加(结算栈镜像 `cf_choppy_neutral_tp1_floor_ab.py`):

```python
def load_bars(db, sym, created, window=86400):
    if not db or not os.path.exists(db) or created is None:
        return []
    conn = sqlite3.connect(db)
    try:
        lo, hi = int(created * 1000), int((created + window) * 1000)
        rows = conn.execute(
            "SELECT open_time,high,low,close FROM klines WHERE symbol=? "
            "AND open_time>=? AND open_time<=? ORDER BY open_time",
            (sym, lo, hi)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{"open_time": t, "high": h, "low": l, "close": c} for t, h, l, c in rows]


def dedup_clusters(items, gap_sec=3600):
    by_key = defaultdict(list)
    for x in items:
        by_key[(x["symbol"], x["_side"])].append(x)
    clusters = []
    for _key, lst in by_key.items():
        lst.sort(key=lambda z: z["_created"])
        last = None
        for it in lst:
            if last is None or it["_created"] - last > gap_sec:
                clusters.append(it)
            last = it["_created"]
    return clusters


def settle_clusters(clusters, *, load_bars_fn=None, resolve_fn=resolve_counterfactual):
    load_bars_fn = load_bars_fn or load_bars
    tp = sl = exp = nodata = 0
    net_R = 0.0
    r_samples = []
    for cl in clusters:
        bars = load_bars_fn(KL1, cl["symbol"], cl.get("_created")) or \
            load_bars_fn(KL, cl["symbol"], cl.get("_created"))
        if not bars:
            nodata += 1
            continue
        res = resolve_fn(cl["_plan"], bars, source="tape")
        if res.outcome == "tp":
            tp += 1
            r = cl["_tp1_dist"] / cl["_sl_dist"]
        elif res.outcome == "sl":
            sl += 1
            r = -1.0
        else:
            exp += 1
            r = 0.0
        net_R += r
        r_samples.append(r)
    return {"tp": tp, "sl": sl, "expired": exp, "nodata": nodata,
            "resolved": tp + sl + exp, "net_R": net_R, "r_samples": r_samples}


def bucket_verdict(settle):
    return summarize_bucket(wins=settle["tp"], losses=settle["sl"],
                            net_usdt_samples=settle["r_samples"],
                            min_sample=30, lowconf_sample=100)


def settle_records(records, sl_dist, tp1_dist):
    fields = [f for f in (synthesize_settle_fields(r, sl_dist, tp1_dist)
                          for r in records) if f]
    clusters = dedup_clusters(fields)
    settle = settle_clusters(clusters)
    return clusters, settle, bucket_verdict(settle)
```

> 注:`settle_clusters` 内部硬引用模块级 `load_bars`,测试用 `monkeypatch.setattr(drv,"load_bars",...)` 覆盖;默认参数设为 `None` 再回退,避免绑定到旧引用。

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_cf_neutral_momentum_rescue_ab.py -q`
Expected: PASS（14 passed）

- [ ] **Step 5: 提交**

```bash
git add cf_neutral_momentum_rescue_ab.py tests/test_cf_neutral_momentum_rescue_ab.py
git commit -m "feat(cf-neutral-momentum-rescue-ab): 结算栈(dedup/settle/honesty)+ 集成结算测试"
```

archived-with: 2026-06-26-cf-neutral-momentum-rescue-ab
---

### Task 4: main 编排(A/B 桶 × 阈值网格 × 退出假设)+ 冒烟

**Files:**
- Modify: `cf_neutral_momentum_rescue_ab.py`

**Interfaces:**
- Consumes: 全部上文函数。
- Produces: `main()` — 打印 population 规模、A/B 对比、阈值网格、退出假设敏感性、诚实门裁定、结论指引。

- [ ] **Step 1: 写 main 实现**

在 `cf_neutral_momentum_rescue_ab.py` 追加:

```python
def _print_bucket(name, clusters, settle, v):
    rr = (f" → {settle['net_R']/settle['resolved']:+.3f} R/簇"
          if settle["resolved"] else "")
    print(f"    [{name}] 簇 {len(clusters)} | 可结算 {settle['resolved']}"
          f"(无 klines 跳过 {settle['nodata']}) | tp={settle['tp']} sl={settle['sl']} "
          f"exp={settle['expired']} | 净R {settle['net_R']:+.2f}{rr} | 诚实门 {v['verdict']}(n={v['n']})")


def _run_grid(population, sl_dist, tp1_dist, exit_label):
    print(f"\n========== 退出假设: {exit_label} "
          f"(sl_dist={sl_dist:.4f} tp1_dist={tp1_dist:.4f}, R:R={tp1_dist/sl_dist:.2f}) ==========")
    for pre12h_min in PRE12H_GRID:
        for range_pos_max in RANGEPOS_GRID:
            a = [r for r in population if rescue_predicate(r, pre12h_min, range_pos_max)]
            b = [r for r in population if not rescue_predicate(r, pre12h_min, range_pos_max)]
            print(f"\n  --- 谓词 pre12h≥{pre12h_min} range_pos≤{range_pos_max} "
                  f"| A命中 {len(a)} / B对照 {len(b)} ---")
            ca, sa, va = settle_records(a, sl_dist, tp1_dist)
            cb, sb, vb = settle_records(b, sl_dist, tp1_dist)
            _print_bucket("A 救援候选", ca, sa, va)
            _print_bucket("B 对照", cb, sb, vb)


def main():
    population = load_population()
    n_choppy = sum(1 for r in population if r.get("regime_state") == "choppy")
    n_mixed = sum(1 for r in population if r.get("regime_state") == "mixed")
    med_sl, med_tp = derive_strategy_geometry()
    print("=== cf-neutral-momentum-rescue-ab: path_evidence 阀门双重失效测量(信号口径)===")
    print(f"population(choppy/mixed + neutral 方向): {len(population)} "
          f"(choppy {n_choppy} / mixed {n_mixed})")
    print(f"策略典型几何(choppy-long accept median): sl_dist={med_sl:.4f} tp1_dist={med_tp:.4f}")
    exit_assumptions = [
        (med_sl, med_tp, "策略中位"),
        (0.015, 0.0225, "固定 R:R=1.5"),
        (0.010, 0.0150, "更紧 SL"),
    ]
    for sl_dist, tp1_dist, label in exit_assumptions:
        _run_grid(population, sl_dist, tp1_dist, label)
    print("\n注: 诚实门 min_sample=30 不下调;n<30 INSUFFICIENT 时净 R 仅 suggestive。")
    print("    判据: A 净R/簇 显著>0 且 B 不显著>0 且 A 诚实门通过 → 谓词有判别力、阀门值得放宽(另起 change)。")
    print("    A≈B 或皆 ≤0 或 A INSUFFICIENT → 救援无 edge,结案。")
    print("    klines 覆盖受限(klines_1s 近 ~数日 ~数十标的)无覆盖簇已跳过并计数。observability-only。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 编译检查**

Run: `python3 -c "import ast; ast.parse(open('cf_neutral_momentum_rescue_ab.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: 冒烟运行(真实磁带)**

Run: `python3 cf_neutral_momentum_rescue_ab.py 2>/dev/null | head -40`
Expected: 打印 population 规模、策略几何、各退出假设下 A/B 桶对比与诚实门裁定(数字随数据;不报错即通过)。

- [ ] **Step 4: 提交**

```bash
git add cf_neutral_momentum_rescue_ab.py
git commit -m "feat(cf-neutral-momentum-rescue-ab): main 编排(A/B × 阈值网格 × 退出假设)+ 结论指引"
```

archived-with: 2026-06-26-cf-neutral-momentum-rescue-ab
---

### Task 5: 红线守卫 + 全量回归

**Files:**
- Modify: `tests/test_cf_red_line_guard.py`

**Interfaces:**
- Consumes: 既有 `_src(modpath)` helper。

- [ ] **Step 1: 写失败测试**

在 `tests/test_cf_red_line_guard.py` 末尾追加:

```python
def test_decision_paths_do_not_read_neutral_momentum_rescue_ab():
    """neutral-momentum-rescue 测量驱动严禁被决策/风控路径 import。"""
    for mp in ["agents.trading.judge", "agents.trading.executor", "executor",
               "agents.trading.portfolio_risk_guard", "agents.trading.reviewer",
               "agents.trading.position_analyst"]:
        src = _src(mp)
        assert "cf_neutral_momentum_rescue_ab" not in src, mp
```

- [ ] **Step 2: 运行守卫测试通过**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -q`
Expected: PASS（既有 + 新增 1 例全绿;因为无决策路径 import 本驱动,断言成立）

- [ ] **Step 3: 编译全仓**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q cf_neutral_momentum_rescue_ab.py tests/test_cf_neutral_momentum_rescue_ab.py tests/test_cf_red_line_guard.py`
Expected: 无输出(成功)

- [ ] **Step 4: 全量回归**

Run: `python3 -m pytest -q 2>&1 | tail -5`
Expected: `1460 + 新增(本驱动 14 + 红线 1 = 15)` 全 passed,0 failed(deselected 不计)。

- [ ] **Step 5: 提交**

```bash
git add tests/test_cf_red_line_guard.py
git commit -m "test(cf-neutral-momentum-rescue-ab): 红线守卫禁决策/风控路径 import + 全量回归绿"
```

archived-with: 2026-06-26-cf-neutral-momentum-rescue-ab
---

## Self-Review

**Spec coverage:**
- "population = choppy/mixed + neutral"(spec Req 1)→ Task 1 `load_population` + 测试。
- "谓词方向无关 + A/B 判别 + 不读 strength"(Req 2)→ Task 1 `rescue_predicate` + `test_predicate_ignores_strength`;A/B 分桶 → Task 4 `_run_grid`。
- "标准化合成退出 + 多退出假设 + 阈值网格 + CF 契约 entry_price + 无覆盖跳过 + sl_dist≤0 跳过"(Req 3)→ Task 2(合成/契约/无效跳过)+ Task 3(无覆盖跳过/结算)+ Task 4(退出假设/网格)。
- "诚实门 min_sample=30 不下调"(Req 4)→ Task 3 `bucket_verdict` + `test_bucket_verdict_thin_sample_insufficient`。
- "红线守卫 + 不改运行时"(Req 5)→ Task 5 守卫测试;全程不实例化 Judge/不改 config。

**Placeholder scan:** 无 TBD/TODO;每步含完整代码与命令。

**Type consistency:** `synthesize_settle_fields` 产出 `_plan{entry_price,created_at,side,stop_loss,take_profit}` 与 `resolve_counterfactual` 读取键(`side/entry_price/stop_loss/take_profit/created_at`)一致;`settle_records` 复用 `_side`/`_created`/`_sl_dist`/`_tp1_dist`/`_plan` 与 `dedup_clusters`/`settle_clusters` 一致;`bucket_verdict` 返回 `verdict`/`n` 与打印一致。
