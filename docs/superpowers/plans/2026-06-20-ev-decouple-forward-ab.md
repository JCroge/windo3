---
change: ev-decouple-forward-ab
design-doc: docs/superpowers/specs/2026-06-20-ev-decouple-forward-ab-design.md
base-ref: 93951d020d0e61851d14c236927882c80f4254ec
archived-with: 2026-06-20-ev-decouple-forward-ab
---

# 胜率解耦放行单前向期望复核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 observability-only 驱动 `cf_ev_decouple_ab.py`，量化"胜率解耦放行单"（旧胜率门会拒、解耦后才过）的前向期望，对比双门皆过桶，诚实门领先裁定。

**Architecture:** 镜像 `cf_lever2_rejected_ab.py`——分类头用 `replay_decision` gate-toggle 两臂复盘 + baseline 自检挑出解耦放行单，结算半身复用 `load_bars`+`resolve_counterfactual` TP1 保守 R + 簇去重 + `cf_honesty_gate.summarize_bucket`。所有核心逻辑factor 成可注入依赖的纯/半纯函数便于 TDD，`main()` 仅编排。零库改动，不碰 live。

**Tech Stack:** Python 3.9, asyncio, sqlite3, pytest；复用 `utils/decision_replay.py` / `utils/counterfactual_pnl.py` / `utils/cf_honesty_gate.py`。

archived-with: 2026-06-20-ev-decouple-forward-ab
---

## Task 1: `_is_accept` + 分类头（gate-toggle 两臂复盘 + baseline 自检）

**Files:**
- Create: `cf_ev_decouple_ab.py`
- Test: `tests/test_cf_ev_decouple_ab.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_cf_ev_decouple_ab.py`：

```python
"""ev-decouple-forward-ab: 胜率解耦放行单前向期望复核驱动单测。"""
import asyncio


def test_is_accept():
    from cf_ev_decouple_ab import _is_accept
    assert _is_accept("open_long") is True
    assert _is_accept("open_short") is True
    assert _is_accept("hold") is False
    assert _is_accept(None) is False


def test_classify_decouple_admitted(monkeypatch):
    import cf_ev_decouple_ab as m

    async def fake_replay(rec, cfg):
        # gate OFF(baseline)=accept 复现 live; gate ON(旧门)=reject → 解耦放行
        if cfg.get("ev_winrate_gate_enabled") is False:
            return {"action": "open_long"}
        return {"action": "hold", "attribution": {"blocked_by": "ev_gate"}}

    rec = {"symbol": "XLM-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert res["mismatch"] == 0
    assert len(res["decouple_admitted"]) == 1
    assert len(res["both_pass"]) == 0
    assert res["admitted_reject_reasons"]["ev_gate"] == 1


def test_classify_both_pass(monkeypatch):
    import cf_ev_decouple_ab as m

    async def fake_replay(rec, cfg):
        return {"action": "open_long"}   # 两臂都 accept → 双门皆过

    rec = {"symbol": "ETH-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert len(res["both_pass"]) == 1
    assert len(res["decouple_admitted"]) == 0


def test_classify_baseline_mismatch_excluded():
    import cf_ev_decouple_ab as m

    async def fake_replay(rec, cfg):
        # baseline 臂 hold（复现不出 live accept）→ 失真排除
        return {"action": "hold"}

    rec = {"symbol": "X", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert res["mismatch"] == 1
    assert len(res["decouple_admitted"]) == 0
    assert len(res["both_pass"]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cf_ev_decouple_ab.py::test_is_accept tests/test_cf_ev_decouple_ab.py::test_classify_decouple_admitted -v`
Expected: FAIL with ModuleNotFoundError (`cf_ev_decouple_ab` 不存在)

- [ ] **Step 3: Write minimal implementation**

创建 `cf_ev_decouple_ab.py`：

```python
"""ev-decouple-forward-ab: 复核胜率解耦放行单的前向期望（observability-only write-only）。

对决策磁带 accept 流做 gate-toggle 两臂复盘——baseline=replay(ev_winrate_gate_enabled=False)
（= live 现配置，自检复现 live accept）vs 反事实=replay(ev_winrate_gate_enabled=True)
（= 06-18 前旧胜率门）。旧门翻 reject = "解耦放行"。两桶（解耦放行 vs 双门皆过）统一
resolve_counterfactual+klines TP1 保守口径结算前向净 R，cf_honesty_gate 薄样本拒答。

红线：observability-only write-only —— 输出严禁任何交易决策/风控路径消费；绝不自动改线上 config。
"""
import asyncio
import json
import os
import sqlite3
from collections import Counter, defaultdict

from utils.decision_replay import replay_decision
from utils.counterfactual_pnl import resolve_counterfactual
from utils.cf_honesty_gate import summarize_bucket

TAPE = "data/decision_replay_tape.jsonl"
KL1 = "data/klines_1s.db"
KL = "data/klines.db"
LIFECYCLE = "data/live_position_lifecycle.json"

GATE_OFF = {"ev_winrate_gate_enabled": False}   # = live 现配置(baseline 自检锚)
GATE_ON = {"ev_winrate_gate_enabled": True}     # = 06-18 前旧胜率门(反事实)


def _is_accept(action):
    return action in ("open_long", "open_short")


def _reject_reason(decision):
    if not isinstance(decision, dict):
        return "hold_other"
    b = ((decision.get("attribution") or {}).get("blocked_by")) or decision.get("reject_reason")
    return str(b).split(":")[0] if b else "hold_other"


async def classify_accepts(records, *, replay_fn=replay_decision):
    """对 accept 记录 gate-toggle 分类。返回 decouple_admitted / both_pass / mismatch。"""
    decouple_admitted = []
    both_pass = []
    mismatch = 0
    reasons = Counter()
    for rec in records:
        baseline = await replay_fn(rec, GATE_OFF)
        if not _is_accept((baseline or {}).get("action")):    # 复盘失真 → 排除
            mismatch += 1
            continue
        cf = await replay_fn(rec, GATE_ON)
        if not _is_accept((cf or {}).get("action")):
            decouple_admitted.append(rec)
            reasons[_reject_reason(cf)] += 1
        else:
            both_pass.append(rec)
    return {"decouple_admitted": decouple_admitted, "both_pass": both_pass,
            "mismatch": mismatch, "admitted_reject_reasons": dict(reasons)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cf_ev_decouple_ab.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add cf_ev_decouple_ab.py tests/test_cf_ev_decouple_ab.py
git commit -m "feat(ev-decouple-ab): 分类头 gate-toggle 两臂复盘 + baseline 自检"
```

archived-with: 2026-06-20-ev-decouple-forward-ab
---

## Task 2: 簇去重（纯函数）

**Files:**
- Modify: `cf_ev_decouple_ab.py`
- Test: `tests/test_cf_ev_decouple_ab.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_cf_ev_decouple_ab.py`：

```python
def test_dedup_clusters():
    from cf_ev_decouple_ab import dedup_clusters
    # 同 symbol/side: <1h 归一簇取最早; >1h 新簇
    recs = [
        {"symbol": "XLM-USDT", "_side": "long", "_created": 1000.0},
        {"symbol": "XLM-USDT", "_side": "long", "_created": 2000.0},   # +1000s <1h 同簇
        {"symbol": "XLM-USDT", "_side": "long", "_created": 6000.0},   # +4000s >1h 新簇
        {"symbol": "ETH-USDT", "_side": "long", "_created": 1500.0},   # 不同标的
    ]
    clusters = dedup_clusters(recs)
    # XLM 2 簇(1000代表, 6000代表) + ETH 1 簇 = 3
    assert len(clusters) == 3
    createds = sorted(c["_created"] for c in clusters)
    assert createds == [1000.0, 1500.0, 6000.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cf_ev_decouple_ab.py::test_dedup_clusters -v`
Expected: FAIL with ImportError (`dedup_clusters` 不存在)

- [ ] **Step 3: Write minimal implementation**

在 `cf_ev_decouple_ab.py` 加（`_created`/`_side` 由 Task 4 的提取逻辑注入，纯函数只读这两个键）：

```python
def dedup_clusters(items, gap_sec=3600):
    """同 (symbol,_side) 按 _created 排序, 间隔 > gap_sec 为新簇, 取每簇最早代表。"""
    by_key = defaultdict(list)
    for x in items:
        by_key[(x["symbol"], x["_side"])].append(x)
    clusters = []
    for key, lst in by_key.items():
        lst.sort(key=lambda z: z["_created"])
        last = None
        for it in lst:
            if last is None or it["_created"] - last > gap_sec:
                clusters.append(it)
            last = it["_created"]
    return clusters
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cf_ev_decouple_ab.py::test_dedup_clusters -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cf_ev_decouple_ab.py tests/test_cf_ev_decouple_ab.py
git commit -m "feat(ev-decouple-ab): 簇去重纯函数(symbol,side,>1h)"
```

archived-with: 2026-06-20-ev-decouple-forward-ab
---

## Task 3: 结算（load_bars + resolve_counterfactual，可注入）

**Files:**
- Modify: `cf_ev_decouple_ab.py`
- Test: `tests/test_cf_ev_decouple_ab.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_cf_ev_decouple_ab.py`：

```python
def test_settle_clusters():
    import cf_ev_decouple_ab as m

    class FakeRes:
        def __init__(self, outcome): self.outcome = outcome

    # 注入 load_bars/resolve：一簇 tp、一簇 sl、一簇无 klines
    def fake_load(db, sym, created, window=86400):
        return [] if sym == "NODATA-USDT" else [{"open_time": 1, "high": 2, "low": 1, "close": 1}]

    outcomes = {"TP-USDT": "tp", "SL-USDT": "sl"}
    def fake_resolve(plan, bars, source="tape"):
        return FakeRes(outcomes[plan["symbol"]])

    clusters = [
        {"symbol": "TP-USDT", "_tp1_dist": 0.04, "_sl_dist": 0.02, "_plan": {"symbol": "TP-USDT"}},
        {"symbol": "SL-USDT", "_tp1_dist": 0.03, "_sl_dist": 0.03, "_plan": {"symbol": "SL-USDT"}},
        {"symbol": "NODATA-USDT", "_tp1_dist": 0.03, "_sl_dist": 0.03, "_plan": {"symbol": "NODATA-USDT"}},
    ]
    s = m.settle_clusters(clusters, load_bars_fn=fake_load, resolve_fn=fake_resolve)
    assert s["tp"] == 1 and s["sl"] == 1 and s["nodata"] == 1
    assert abs(s["net_R"] - (0.04 / 0.02 - 1.0)) < 1e-9   # tp: +2R, sl: -1R → net +1R
    assert s["resolved"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cf_ev_decouple_ab.py::test_settle_clusters -v`
Expected: FAIL with AttributeError (`settle_clusters` 不存在)

- [ ] **Step 3: Write minimal implementation**

在 `cf_ev_decouple_ab.py` 加（复用 cf_lever2_rejected_ab 的 load_bars + TP1 保守口径）：

```python
def load_bars(db, sym, created, window=86400):
    if not db or not os.path.exists(db):
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


def settle_clusters(clusters, *, load_bars_fn=load_bars, resolve_fn=resolve_counterfactual):
    """每簇代表用 klines+resolve_counterfactual 结算, TP1 保守 R(含亏单)。"""
    tp = sl = exp = nodata = 0
    net_R = 0.0
    r_samples = []
    for cl in clusters:
        bars = load_bars_fn(KL1, cl["symbol"], cl["_created"]) or \
            load_bars_fn(KL, cl["symbol"], cl["_created"])
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
```

注：`_created`(开仓时点 created_at)、`_tp1_dist`/`_sl_dist`(从 plan entry/sl/tp1 推)、`_plan`(原始 plan dict) 三个内部键由 Task 5 的 `extract_settle_fields` 注入。

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cf_ev_decouple_ab.py::test_settle_clusters -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cf_ev_decouple_ab.py tests/test_cf_ev_decouple_ab.py
git commit -m "feat(ev-decouple-ab): 结算 load_bars+resolve TP1 保守 R(含亏单)"
```

archived-with: 2026-06-20-ev-decouple-forward-ab
---

## Task 4: 字段提取 + real PnL 模糊 join

**Files:**
- Modify: `cf_ev_decouple_ab.py`
- Test: `tests/test_cf_ev_decouple_ab.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_cf_ev_decouple_ab.py`：

```python
def test_extract_settle_fields():
    from cf_ev_decouple_ab import extract_settle_fields
    rec = {"symbol": "XLM-USDT", "timestamp": 1000.0,
           "trade_decision_output": {"plan": {
               "side": "long", "entry_ref": 0.20, "stop_loss": 0.19,
               "take_profit": [0.22, 0.24, 0.26]}}}
    out = extract_settle_fields(rec)
    assert out["symbol"] == "XLM-USDT" and out["_side"] == "long"
    assert out["_created"] == 1000.0
    assert abs(out["_sl_dist"] - 0.05) < 1e-6      # (0.20-0.19)/0.20
    assert abs(out["_tp1_dist"] - 0.10) < 1e-6     # (0.22-0.20)/0.20
    assert out["_plan"]["side"] == "long"


def test_extract_settle_fields_invalid():
    from cf_ev_decouple_ab import extract_settle_fields
    # 缺 stop_loss → 返回 None(不可结算)
    rec = {"symbol": "X", "timestamp": 1.0,
           "trade_decision_output": {"plan": {"side": "long", "entry_ref": 1.0,
                                              "take_profit": [1.1]}}}
    assert extract_settle_fields(rec) is None


def test_fuzzy_join_real_pnl():
    from cf_ev_decouple_ab import fuzzy_join_real_pnl
    lifecycle = {"p1": {"symbol": "XLM-USDT", "side": "long", "opened_at": 1200.0,
                        "total_realized_pnl": -10.0, "reconcile_status": "matched"}}
    # 决策 ts=1000, 开仓 1200 在 [1000,1600] 窗口内 → 命中
    admitted = [{"symbol": "XLM-USDT", "_side": "long", "_created": 1000.0}]
    joined = fuzzy_join_real_pnl(admitted, lifecycle, window=600)
    assert len(joined) == 1 and joined[0]["real_pnl"] == -10.0
    # 窗口外不命中
    assert fuzzy_join_real_pnl(
        [{"symbol": "XLM-USDT", "_side": "long", "_created": 100.0}],
        lifecycle, window=600) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cf_ev_decouple_ab.py::test_extract_settle_fields tests/test_cf_ev_decouple_ab.py::test_fuzzy_join_real_pnl -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write minimal implementation**

在 `cf_ev_decouple_ab.py` 加：

```python
def extract_settle_fields(rec):
    """从磁带 accept 记录提取结算所需字段；缺关键字段返回 None。"""
    plan = (rec.get("trade_decision_output") or {}).get("plan") or {}
    side = plan.get("side")
    entry = plan.get("entry_ref")
    sl = plan.get("stop_loss")
    tp = plan.get("take_profit") or []
    if not (side and entry and sl and tp):
        return None
    is_long = (side == "long")
    sl_dist = (entry - sl) / entry if is_long else (sl - entry) / entry
    tp1_dist = (tp[0] - entry) / entry if is_long else (entry - tp[0]) / entry
    if sl_dist <= 0 or tp1_dist <= 0:
        return None
    return {"symbol": rec.get("symbol"), "_side": side, "_created": rec.get("timestamp"),
            "_sl_dist": sl_dist, "_tp1_dist": tp1_dist, "_plan": plan}


def fuzzy_join_real_pnl(admitted_clusters, lifecycle, window=600):
    """解耦放行簇 symbol+side, opened_at ∈ [created, created+window] 取最近 lifecycle。

    无 request_id → 模糊匹配；pending/external_close 不计入。
    """
    by_sym = defaultdict(list)
    for v in lifecycle.values():
        if isinstance(v, dict) and v.get("reconcile_status") == "matched":
            by_sym[(v.get("symbol"), v.get("side"))].append(v)
    out = []
    for cl in admitted_clusters:
        cands = by_sym.get((cl["symbol"], cl["_side"]), [])
        hit = None
        for v in cands:
            op = v.get("opened_at")
            if op is not None and cl["_created"] <= op <= cl["_created"] + window:
                if hit is None or op < hit.get("opened_at"):
                    hit = v
        if hit is not None:
            out.append({"symbol": cl["symbol"], "real_pnl": hit.get("total_realized_pnl"),
                        "fuzzy": True})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cf_ev_decouple_ab.py::test_extract_settle_fields tests/test_cf_ev_decouple_ab.py::test_extract_settle_fields_invalid tests/test_cf_ev_decouple_ab.py::test_fuzzy_join_real_pnl -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cf_ev_decouple_ab.py tests/test_cf_ev_decouple_ab.py
git commit -m "feat(ev-decouple-ab): 结算字段提取 + real PnL 模糊 join"
```

archived-with: 2026-06-20-ev-decouple-forward-ab
---

## Task 5: `main()` 编排 + 诚实门领先裁定报表

**Files:**
- Modify: `cf_ev_decouple_ab.py`
- Test: `tests/test_cf_ev_decouple_ab.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_cf_ev_decouple_ab.py`（测诚实门薄样本裁定的报表函数，不依赖真实磁带）：

```python
def test_bucket_verdict_thin_sample():
    from cf_ev_decouple_ab import bucket_verdict
    # 2 簇(<30) → 诚实门 INSUFFICIENT_SAMPLE
    settle = {"tp": 1, "sl": 1, "expired": 0, "nodata": 0, "resolved": 2,
              "net_R": 1.0, "r_samples": [2.0, -1.0]}
    v = bucket_verdict(settle)
    assert v["verdict"] == "INSUFFICIENT_SAMPLE"
    assert v["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cf_ev_decouple_ab.py::test_bucket_verdict_thin_sample -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write minimal implementation**

在 `cf_ev_decouple_ab.py` 加 `bucket_verdict` + `load_tape_accepts` + `main()`：

```python
def bucket_verdict(settle):
    """诚实门裁定(min_sample=30 不下调)。net_usdt_samples 用 R 序列(口径一致)。"""
    return summarize_bucket(wins=settle["tp"], losses=settle["sl"],
                            net_usdt_samples=settle["r_samples"],
                            min_sample=30, lowconf_sample=100)


def load_tape_accepts(path=TAPE):
    accepts = []
    if not os.path.exists(path):
        return accepts
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if (r.get("decision") == "accept" and r.get("replayable")
                and r.get("state_snapshot_before_decision")):
            accepts.append(r)
    return accepts


def _settle_bucket_records(records):
    """记录 → 提取结算字段 → 簇去重 → 结算 + 诚实门。"""
    fields = [f for f in (extract_settle_fields(r) for r in records) if f]
    clusters = dedup_clusters(fields)
    settle = settle_clusters(clusters)
    return clusters, settle, bucket_verdict(settle)


def main():
    accepts = load_tape_accepts()
    cls = asyncio.run(classify_accepts(accepts))
    da, bp = cls["decouple_admitted"], cls["both_pass"]
    print("=== ev-decouple-forward-ab: 胜率解耦放行单前向期望复核 ===")
    print(f"replayable accept: {len(accepts)} | baseline 自检: 忠实 "
          f"{len(da) + len(bp)} / 失真排除 {cls['mismatch']}")
    print(f"解耦放行(旧胜率门会拒): {len(da)} | 双门皆过: {len(bp)} "
          f"| 拒因 {cls['admitted_reject_reasons']}")

    lifecycle = json.load(open(LIFECYCLE)) if os.path.exists(LIFECYCLE) else {}
    for name, recs in [("解耦放行", da), ("双门皆过", bp)]:
        clusters, settle, v = _settle_bucket_records(recs)
        print(f"\n--- {name}桶 ---")
        print(f"  簇去重: {len(clusters)} | 可结算 {settle['resolved']}(无 klines 跳过 {settle['nodata']})")
        print(f"  tp={settle['tp']} sl={settle['sl']} expired={settle['expired']}")
        print(f"  含亏单净 R(TP1 保守): {settle['net_R']:+.2f} over {settle['resolved']} 簇"
              + (f" → {settle['net_R']/settle['resolved']:+.3f} R/簇" if settle['resolved'] else ""))
        print(f"  诚实门裁定: {v['verdict']}  (n={v['n']})")
        if name == "解耦放行":
            joined = fuzzy_join_real_pnl(clusters, lifecycle)
            if joined:
                rp = sum(j["real_pnl"] or 0 for j in joined)
                print(f"  [sanity] 模糊 join 到 {len(joined)} 笔真实开仓, 真实净 PnL {rp:+.2f}U"
                      f"(无 request_id 模糊匹配/pending 不计)")
    print("\n注: 诚实门 min_sample=30 不下调；薄样本裁定 INSUFFICIENT_SAMPLE 时净 R 仅 suggestive 不作结论。")
    print("    判据(解耦放行净R << 双门皆过且<0 → 解耦放行亏损单)仅在两桶诚实门通过时成立。")
    print("    klines 覆盖受限(klines_1s 近 ~数日 ~24 标的)无覆盖簇已跳过并计数。observability-only。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cf_ev_decouple_ab.py::test_bucket_verdict_thin_sample -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cf_ev_decouple_ab.py tests/test_cf_ev_decouple_ab.py
git commit -m "feat(ev-decouple-ab): main 编排 + 诚实门领先裁定报表"
```

archived-with: 2026-06-20-ev-decouple-forward-ab
---

## Task 6: 红线守卫扩展（禁读断言）

**Files:**
- Modify: `tests/test_cf_red_line_guard.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_cf_red_line_guard.py` 末尾追加：

```python
def test_decision_paths_do_not_read_ev_decouple_ab():
    """ev-decouple-forward-ab: 决策/风控路径严禁读解耦复核驱动产物（observability-only）。"""
    for mp in ["agents.trading.judge", "agents.trading.executor", "executor",
               "agents.trading.portfolio_risk_guard", "agents.trading.reviewer",
               "agents.trading.position_analyst"]:
        src = _src(mp)
        assert "cf_ev_decouple_ab" not in src, mp
```

- [ ] **Step 2: Run test to verify it passes (守卫应天然通过——决策路径本就没引用新驱动)**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py::test_decision_paths_do_not_read_ev_decouple_ab -v`
Expected: PASS（新驱动是独立文件，决策路径未引用）

- [ ] **Step 3: Commit**

```bash
git add tests/test_cf_red_line_guard.py
git commit -m "test(ev-decouple-ab): 红线守卫扩展禁读断言"
```

archived-with: 2026-06-20-ev-decouple-forward-ab
---

## Task 7: 真跑 + 全量回归

**Files:**
- 无（验证 only）

- [ ] **Step 1: 真跑驱动**

Run: `python3 cf_ev_decouple_ab.py`
Expected: 打印两桶结算 + 诚实门裁定（解耦放行桶预计 INSUFFICIENT_SAMPLE，簇 <30）；不报错。记录输出留作 verify 报告证据。

- [ ] **Step 2: 模块编译 + 本 change 测试**

Run: `python3 -m compileall -q cf_ev_decouple_ab.py && python3 -m pytest tests/test_cf_ev_decouple_ab.py tests/test_cf_red_line_guard.py -q`
Expected: PASS（全部）

- [ ] **Step 3: 全量回归**

Run: `python3 -m pytest -q`
Expected: PASS 数 ≥ 1319 基线 + 新增用例；8 failed 仅既有 round2 asyncio flaky（`test_round2_probe_long_dispatcher` / `test_round2_request_id_position`），非本 change 引入；零新退化

- [ ] **Step 4: Commit (若有 main() 注册等收尾改动)**

```bash
git add -A
git commit -m "test(ev-decouple-ab): 真跑记录 + 全量回归零退化" || echo "nothing to commit"
```

archived-with: 2026-06-20-ev-decouple-forward-ab
---

## Self-Review 结论

- **Spec coverage**：delta spec 四个 requirement 全覆盖——分类(Task 1)、结算与桶对比(Task 3/5)、诚实门与 coverage 透明(Task 5)、observability 红线(Task 6)；real PnL 交叉(Task 4)。
- **Placeholder scan**：无 TBD/TODO，每个代码 step 含完整代码。
- **Type consistency**：内部键 `_side`/`_created`/`_sl_dist`/`_tp1_dist`/`_plan` 在 `extract_settle_fields`(产出) → `dedup_clusters`/`settle_clusters`/`fuzzy_join_real_pnl`(消费) 跨任务一致；函数签名 `classify_accepts(records,*,replay_fn)` / `dedup_clusters(items,gap_sec)` / `settle_clusters(clusters,*,load_bars_fn,resolve_fn)` / `extract_settle_fields(rec)` / `fuzzy_join_real_pnl(admitted_clusters,lifecycle,window)` / `bucket_verdict(settle)` 一致。
