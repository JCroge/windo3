---
change: cf-choppy-neutral-tp1-floor-ab
design-doc: docs/superpowers/specs/2026-06-24-cf-choppy-neutral-tp1-floor-ab-design.md
base-ref: a68e4e3fc613b84be127314d50c303a8b303b5b0
archived-with: 2026-06-24-cf-choppy-neutral-tp1-floor-ab
---

# cf-choppy-neutral-tp1-floor-ab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 observability-only 反事实驱动 `cf_choppy_neutral_tp1_floor_ab.py`，量化「choppy+neutral 多单卡 TP1 口径地板」对决策磁带的反事实 PnL delta。

**Architecture:** 镜像 `cf_ev_decouple_ab.py`：对决策磁带 accept 流做 `ladder_rr_enabled` two-arm 复盘（True=baseline 自检 / False=CF TP1 地板），scope 预过滤 choppy+neutral 主桶 + mixed 旁路，翻转纯度只计 `rr_below_floor`，两结算桶用 `resolve_counterfactual`+klines TP1 保守结算 + `cf_honesty_gate(min_sample=30)`。零 live 改动。

**Tech Stack:** Python 3.9, asyncio, sqlite3；复用 `utils.decision_replay.replay_decision` / `utils.counterfactual_pnl.resolve_counterfactual` / `utils.cf_honesty_gate.summarize_bucket` / `utils.symbol.to_internal`。

archived-with: 2026-06-24-cf-choppy-neutral-tp1-floor-ab
---

## File Structure

- **Create** `cf_choppy_neutral_tp1_floor_ab.py`（repo 根）— 驱动，结构镜像 `cf_ev_decouple_ab.py`。
- **Create** `tests/test_cf_choppy_neutral_tp1_floor_ab.py` — 驱动单测，镜像 `tests/test_cf_ev_decouple_ab.py`。
- **Modify** `tests/test_cf_red_line_guard.py` — +1 禁读断言。

> 结算栈（`load_bars`/`dedup_clusters`/`settle_clusters`/`extract_settle_fields`/`fuzzy_join_real_pnl`/`bucket_verdict`）与 `cf_ev_decouple_ab.py` 逐字节同形态——直接照搬，仅改 toggle 常量、scope 过滤、翻转纯度三处。

archived-with: 2026-06-24-cf-choppy-neutral-tp1-floor-ab
---

## Task 1: 驱动骨架 + 加载 + scope 过滤

**Files:**
- Create: `cf_choppy_neutral_tp1_floor_ab.py`
- Test: `tests/test_cf_choppy_neutral_tp1_floor_ab.py`

- [ ] **Step 1: 写失败测试（scope_filter + _is_accept）**

```python
"""cf-choppy-neutral-tp1-floor-ab 驱动单测。"""
import asyncio


def test_is_accept():
    from cf_choppy_neutral_tp1_floor_ab import _is_accept
    assert _is_accept("open_long") is True
    assert _is_accept("open_short") is True
    assert _is_accept("hold") is False
    assert _is_accept(None) is False


def _rec(symbol, regime, direction, action="open_long"):
    return {"symbol": symbol, "decision": "accept", "replayable": True,
            "state_snapshot_before_decision": {"x": 1},
            "regime_state": regime,
            "tech_analysis": {"trend": {"direction": direction}},
            "trade_decision_output": {"plan": {"side": "long"}},
            "_action": action}


def test_scope_filter_choppy_neutral_long():
    from cf_choppy_neutral_tp1_floor_ab import scope_filter
    recs = [
        _rec("A-USDT", "choppy", "neutral"),          # 命中主桶
        _rec("B-USDT", "mixed", "neutral"),           # 不命中 choppy
        _rec("C-USDT", "choppy", "bullish"),          # 非 neutral
        _rec("D-USDT", "bullish", "neutral"),         # 非 choppy
    ]
    out = scope_filter(recs, regime="choppy")
    syms = {r["symbol"] for r in out}
    assert syms == {"A-USDT"}


def test_scope_filter_mixed_sidecar():
    from cf_choppy_neutral_tp1_floor_ab import scope_filter
    recs = [_rec("A-USDT", "choppy", "neutral"), _rec("B-USDT", "mixed", "neutral")]
    out = scope_filter(recs, regime="mixed")
    assert {r["symbol"] for r in out} == {"B-USDT"}


def test_scope_filter_excludes_short():
    from cf_choppy_neutral_tp1_floor_ab import scope_filter
    r = _rec("S-USDT", "choppy", "neutral")
    r["trade_decision_output"]["plan"]["side"] = "short"
    assert scope_filter([r], regime="choppy") == []
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_cf_choppy_neutral_tp1_floor_ab.py -q`
Expected: FAIL（`ModuleNotFoundError: cf_choppy_neutral_tp1_floor_ab`）

- [ ] **Step 3: 写驱动骨架（docstring + 常量 + 加载 + scope_filter）**

```python
"""cf-choppy-neutral-tp1-floor-ab: choppy+neutral 多单卡 TP1 口径地板的反事实 A/B（observability-only write-only）。

对决策磁带 accept 流做 ladder toggle 两臂复盘——baseline=replay(ladder_rr_enabled=True)
（= live 现状，lever2 默认开，自检复现 live accept）vs CF=replay(ladder_rr_enabled=False)
（floor gate 改比 TP1 口径 effective_rr_tp1）。CF 臂因 rr_below_floor 翻 reject = "TP1 地板会拒掉"。
主桶 choppy+neutral，旁路 mixed+neutral。两结算桶统一 resolve_counterfactual+klines TP1
保守口径结算净 R，cf_honesty_gate(min_sample=30) 薄样本拒答。

红线：observability-only write-only —— 输出严禁任何交易决策/风控路径消费；绝不下单/改 config。
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

LADDER_ON = {"ladder_rr_enabled": True}    # = live 现状(lever2 默认开)，baseline 自检锚
LADDER_OFF = {"ladder_rr_enabled": False}  # = CF：floor gate 比 TP1 口径


def _is_accept(action):
    return action in ("open_long", "open_short")


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


def scope_filter(records, regime):
    """主桶 regime=choppy / 旁路 regime=mixed；均要求 trend.direction=neutral + 多单。"""
    out = []
    for r in records:
        if r.get("regime_state") != regime:
            continue
        trend = (r.get("tech_analysis") or {}).get("trend") or {}
        if trend.get("direction") != "neutral":
            continue
        side = ((r.get("trade_decision_output") or {}).get("plan") or {}).get("side")
        if side != "long":
            continue
        out.append(r)
    return out
```

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_cf_choppy_neutral_tp1_floor_ab.py -q`
Expected: PASS（3 scope + 1 is_accept = 4 passed）

- [ ] **Step 5: 提交**

```bash
git add cf_choppy_neutral_tp1_floor_ab.py tests/test_cf_choppy_neutral_tp1_floor_ab.py
git commit -m "feat(cf-choppy-tp1-floor): 驱动骨架 + 加载 + scope 过滤"
```

archived-with: 2026-06-24-cf-choppy-neutral-tp1-floor-ab
---

## Task 2: 两臂分类 + 自检闸 + 翻转纯度

**Files:**
- Modify: `cf_choppy_neutral_tp1_floor_ab.py`
- Test: `tests/test_cf_choppy_neutral_tp1_floor_ab.py`

- [ ] **Step 1: 写失败测试（4 分类分支）**

```python
def test_classify_tp1_floor_rejected():
    import cf_choppy_neutral_tp1_floor_ab as m

    async def fake_replay(rec, cfg):
        # ladder ON(baseline)=accept 复现; ladder OFF(TP1)=rr_below_floor reject → 避开
        if cfg.get("ladder_rr_enabled") is True:
            return {"action": "open_long"}
        return {"action": "hold", "attribution": {"blocked_by": "rr_below_floor:1.30<1.50"}}

    rec = {"symbol": "HYPE-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert res["mismatch"] == 0
    assert len(res["tp1_floor_rejected"]) == 1
    assert len(res["survives_tp1_floor"]) == 0
    assert len(res["other_flip"]) == 0
    assert res["rejected_reasons"]["rr_below_floor"] == 1


def test_classify_survives():
    import cf_choppy_neutral_tp1_floor_ab as m

    async def fake_replay(rec, cfg):
        return {"action": "open_long"}   # 两臂都过 → 卡 TP1 仍过

    rec = {"symbol": "X-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert len(res["survives_tp1_floor"]) == 1
    assert len(res["tp1_floor_rejected"]) == 0


def test_classify_other_flip_excluded():
    import cf_choppy_neutral_tp1_floor_ab as m

    async def fake_replay(rec, cfg):
        # CF 臂 reject 但原因非 rr_below_floor → other_flip，不计 tp1_floor_rejected
        if cfg.get("ladder_rr_enabled") is True:
            return {"action": "open_long"}
        return {"action": "hold", "attribution": {"blocked_by": "quality_gate"}}

    rec = {"symbol": "Q-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert len(res["other_flip"]) == 1
    assert len(res["tp1_floor_rejected"]) == 0


def test_classify_baseline_mismatch_excluded():
    import cf_choppy_neutral_tp1_floor_ab as m

    async def fake_replay(rec, cfg):
        return {"action": "hold"}   # baseline 复现不出 live accept → 失真排除

    rec = {"symbol": "M-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert res["mismatch"] == 1
    assert len(res["tp1_floor_rejected"]) == 0
    assert len(res["survives_tp1_floor"]) == 0
    assert len(res["other_flip"]) == 0
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_cf_choppy_neutral_tp1_floor_ab.py -k classify -q`
Expected: FAIL（`classify_accepts` 未定义）

- [ ] **Step 3: 实现 classify_accepts + _reject_reason**

```python
def _reject_reason(decision):
    if not isinstance(decision, dict):
        return "hold_other"
    b = ((decision.get("attribution") or {}).get("blocked_by")) or decision.get("reject_reason")
    return str(b).split(":")[0] if b else "hold_other"


async def classify_accepts(records, *, replay_fn=replay_decision):
    """ladder-toggle 两臂复盘分类。

    baseline=replay(LADDER_ON) 非 accept → baseline_mismatch 排除；
    cf=replay(LADDER_OFF)：accept→survives_tp1_floor；
      reject & reason==rr_below_floor → tp1_floor_rejected；其它 reject → other_flip（不结算）。
    """
    tp1_floor_rejected, survives, other_flip = [], [], []
    mismatch = 0
    reasons = Counter()
    for rec in records:
        baseline = await replay_fn(rec, LADDER_ON)
        if not _is_accept((baseline or {}).get("action")):
            mismatch += 1
            continue
        cf = await replay_fn(rec, LADDER_OFF)
        if _is_accept((cf or {}).get("action")):
            survives.append(rec)
            continue
        reason = _reject_reason(cf)
        if reason == "rr_below_floor":
            tp1_floor_rejected.append(rec)
            reasons[reason] += 1
        else:
            other_flip.append(rec)
            reasons[reason] += 1
    return {"tp1_floor_rejected": tp1_floor_rejected, "survives_tp1_floor": survives,
            "other_flip": other_flip, "mismatch": mismatch,
            "rejected_reasons": dict(reasons)}
```

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_cf_choppy_neutral_tp1_floor_ab.py -k classify -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add cf_choppy_neutral_tp1_floor_ab.py tests/test_cf_choppy_neutral_tp1_floor_ab.py
git commit -m "feat(cf-choppy-tp1-floor): 两臂分类 + 自检闸 + rr_below_floor 翻转纯度"
```

archived-with: 2026-06-24-cf-choppy-neutral-tp1-floor-ab
---

## Task 3: 结算栈（照搬 ev-decouple 同形态）

**Files:**
- Modify: `cf_choppy_neutral_tp1_floor_ab.py`
- Test: `tests/test_cf_choppy_neutral_tp1_floor_ab.py`

- [ ] **Step 1: 写失败测试（结算契约，含不 mock resolve）**

```python
def test_extract_settle_fields_contract():
    from cf_choppy_neutral_tp1_floor_ab import extract_settle_fields
    rec = {"symbol": "XLM-USDT", "timestamp": 1000.0,
           "trade_decision_output": {"plan": {
               "side": "long", "entry_ref": 0.20, "stop_loss": 0.19,
               "take_profit": [0.22, 0.24, 0.26]}}}
    out = extract_settle_fields(rec)
    assert out["_side"] == "long" and out["_created"] == 1000.0
    assert abs(out["_sl_dist"] - 0.05) < 1e-6
    assert abs(out["_tp1_dist"] - 0.10) < 1e-6
    # 结算契约：传 resolve 所需字段，不传原始 plan 的 entry_ref
    assert out["_plan"]["entry_price"] == 0.20
    assert out["_plan"]["created_at"] == 1000.0
    assert "entry_ref" not in out["_plan"]


def test_extract_settle_fields_invalid():
    from cf_choppy_neutral_tp1_floor_ab import extract_settle_fields
    rec = {"symbol": "X", "timestamp": 1.0,
           "trade_decision_output": {"plan": {"side": "long", "entry_ref": 1.0,
                                              "take_profit": [1.1]}}}
    assert extract_settle_fields(rec) is None


def test_settle_clusters_real_resolve():
    """不 mock resolve_counterfactual：锁死 _plan 契约不被 mock 掩盖。"""
    import cf_choppy_neutral_tp1_floor_ab as m
    from utils.counterfactual_pnl import resolve_counterfactual
    rec = {"symbol": "TST-USDT", "timestamp": 1000.0,
           "trade_decision_output": {"plan": {
               "side": "long", "entry_ref": 100.0, "stop_loss": 95.0,
               "take_profit": [110.0, 120.0, 130.0]}}}
    field = m.extract_settle_fields(rec)
    tp_bars = [{"open_time": 1001_000, "high": 105.0, "low": 99.0, "close": 104.0},
               {"open_time": 1002_000, "high": 112.0, "low": 104.0, "close": 111.0}]
    s_tp = m.settle_clusters([field], load_bars_fn=lambda *a, **k: tp_bars,
                             resolve_fn=resolve_counterfactual)
    assert s_tp["tp"] == 1 and s_tp["net_R"] > 0


def test_dedup_clusters():
    from cf_choppy_neutral_tp1_floor_ab import dedup_clusters
    recs = [
        {"symbol": "XLM-USDT", "_side": "long", "_created": 1000.0},
        {"symbol": "XLM-USDT", "_side": "long", "_created": 2000.0},
        {"symbol": "XLM-USDT", "_side": "long", "_created": 6000.0},
        {"symbol": "ETH-USDT", "_side": "long", "_created": 1500.0},
    ]
    assert len(dedup_clusters(recs)) == 3


def test_bucket_verdict_thin_sample():
    from cf_choppy_neutral_tp1_floor_ab import bucket_verdict
    settle = {"tp": 1, "sl": 1, "expired": 0, "nodata": 0, "resolved": 2,
              "net_R": 1.0, "r_samples": [2.0, -1.0]}
    v = bucket_verdict(settle)
    assert v["verdict"] == "INSUFFICIENT_SAMPLE" and v["n"] == 2
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_cf_choppy_neutral_tp1_floor_ab.py -k "settle or dedup or extract or verdict" -q`
Expected: FAIL（函数未定义）

- [ ] **Step 3: 实现结算栈（逐字节照搬 cf_ev_decouple_ab.py 的 load_bars/extract_settle_fields/dedup_clusters/settle_clusters/bucket_verdict/fuzzy_join_real_pnl/_settle_bucket_records）**

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


def extract_settle_fields(rec):
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
            "_sl_dist": sl_dist, "_tp1_dist": tp1_dist,
            "_plan": {"side": side, "entry_price": entry, "created_at": rec.get("timestamp"),
                      "stop_loss": sl, "take_profit": tp}}


def dedup_clusters(items, gap_sec=3600):
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


def settle_clusters(clusters, *, load_bars_fn=load_bars, resolve_fn=resolve_counterfactual):
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


def fuzzy_join_real_pnl(clusters, lifecycle, window=600):
    by_sym = defaultdict(list)
    for v in lifecycle.values():
        if isinstance(v, dict) and v.get("reconcile_status") == "matched":
            by_sym[(v.get("symbol"), v.get("side"))].append(v)
    out = []
    for cl in clusters:
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


def _settle_bucket_records(records):
    fields = [f for f in (extract_settle_fields(r) for r in records) if f]
    clusters = dedup_clusters(fields)
    settle = settle_clusters(clusters)
    return clusters, settle, bucket_verdict(settle)
```

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_cf_choppy_neutral_tp1_floor_ab.py -q`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add cf_choppy_neutral_tp1_floor_ab.py tests/test_cf_choppy_neutral_tp1_floor_ab.py
git commit -m "feat(cf-choppy-tp1-floor): 结算栈（resolve_counterfactual+klines TP1 保守 + 诚实门）"
```

archived-with: 2026-06-24-cf-choppy-neutral-tp1-floor-ab
---

## Task 4: main() 编排 + 红线守卫

**Files:**
- Modify: `cf_choppy_neutral_tp1_floor_ab.py`
- Modify: `tests/test_cf_red_line_guard.py`

- [ ] **Step 1: 写失败测试（红线禁读断言）**

在 `tests/test_cf_red_line_guard.py` 末尾追加：

```python
def test_decision_paths_do_not_read_choppy_tp1_floor_ab():
    """choppy-neutral TP1 地板反事实驱动严禁被决策/风控路径 import。"""
    for mp in ["agents.trading.judge", "agents.trading.executor", "executor",
               "agents.trading.portfolio_risk_guard", "agents.trading.reviewer",
               "agents.trading.position_analyst"]:
        src = _src(mp)
        assert "cf_choppy_neutral_tp1_floor_ab" not in src, mp
```

- [ ] **Step 2: 运行验证通过（守卫此刻应已 PASS——无人 import）**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py::test_decision_paths_do_not_read_choppy_tp1_floor_ab -q`
Expected: PASS

- [ ] **Step 3: 实现 main()（主桶 + mixed 旁路编排打印）**

```python
def _print_bucket(name, clusters, settle, v):
    print(f"\n--- {name}桶 ---")
    print(f"  簇去重: {len(clusters)} | 可结算 {settle['resolved']}(无 klines 跳过 {settle['nodata']})")
    print(f"  tp={settle['tp']} sl={settle['sl']} expired={settle['expired']}")
    print(f"  含亏单净 R(TP1 保守): {settle['net_R']:+.2f} over {settle['resolved']} 簇"
          + (f" → {settle['net_R']/settle['resolved']:+.3f} R/簇" if settle['resolved'] else ""))
    print(f"  诚实门裁定: {v['verdict']}  (n={v['n']})")


def _run_scope(accepts, regime, label, lifecycle):
    recs = scope_filter(accepts, regime=regime)
    cls = asyncio.run(classify_accepts(recs))
    print(f"\n========== {label}（regime={regime}+neutral 多单）==========")
    print(f"scope accept: {len(recs)} | baseline 自检: 忠实 "
          f"{len(cls['tp1_floor_rejected']) + len(cls['survives_tp1_floor']) + len(cls['other_flip'])}"
          f" / 失真排除 {cls['mismatch']}")
    print(f"TP1 地板拒掉(rr_below_floor): {len(cls['tp1_floor_rejected'])} | "
          f"卡 TP1 仍过: {len(cls['survives_tp1_floor'])} | "
          f"非地板翻转(排除结算): {len(cls['other_flip'])} | 拒因 {cls['rejected_reasons']}")
    for name, recs2 in [("tp1_floor_rejected(避开)", cls["tp1_floor_rejected"]),
                        ("survives_tp1_floor(保留)", cls["survives_tp1_floor"])]:
        clusters, settle, v = _settle_bucket_records(recs2)
        _print_bucket(name, clusters, settle, v)
        if name.startswith("tp1_floor_rejected"):
            joined = fuzzy_join_real_pnl(clusters, lifecycle)
            if joined:
                rp = sum(j["real_pnl"] or 0 for j in joined)
                print(f"  [sanity] 模糊 join 到 {len(joined)} 笔真实开仓, 真实净 PnL {rp:+.2f}U")


def main():
    accepts = load_tape_accepts()
    lifecycle = json.load(open(LIFECYCLE)) if os.path.exists(LIFECYCLE) else {}
    print("=== cf-choppy-neutral-tp1-floor-ab: choppy+neutral 多单卡 TP1 地板反事实 ===")
    print(f"replayable accept 总数: {len(accepts)}")
    _run_scope(accepts, "choppy", "主桶 choppy+neutral", lifecycle)
    _run_scope(accepts, "mixed", "旁路 mixed+neutral", lifecycle)
    print("\n注: 诚实门 min_sample=30 不下调；薄样本 INSUFFICIENT_SAMPLE 时净 R 仅 suggestive。")
    print("    判据(tp1_floor_rejected 净 R/簇 << 0 且诚实门通过 → 收紧对此原型 +EV)仅两桶诚实门通过时成立。")
    print("    klines 覆盖受限(klines_1s 近 ~数日 ~数十标的)无覆盖簇已跳过并计数。observability-only。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行全量 + compileall**

Run: `python3 -m pytest tests/test_cf_choppy_neutral_tp1_floor_ab.py tests/test_cf_red_line_guard.py -q && env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q cf_choppy_neutral_tp1_floor_ab.py`
Expected: PASS（全部）+ compile 无输出

- [ ] **Step 5: 提交**

```bash
git add cf_choppy_neutral_tp1_floor_ab.py tests/test_cf_red_line_guard.py
git commit -m "feat(cf-choppy-tp1-floor): main 编排(主桶+mixed旁路) + 红线禁读守卫"
```

archived-with: 2026-06-24-cf-choppy-neutral-tp1-floor-ab
---

## Task 5: 真跑 + 全量基线

**Files:** 无（运行验证）

- [ ] **Step 1: 真跑驱动**

Run: `python3 cf_choppy_neutral_tp1_floor_ab.py`
Expected: 打印主桶 choppy + 旁路 mixed 两 scope，各含 tp1_floor_rejected / survives_tp1_floor 两桶净 R/簇 + 诚实门裁定。记录输出供 verify 报告（结论：仅诚实门通过才下「收紧 +EV」，薄样本标 suggestive）。

- [ ] **Step 2: 全量基线**

Run: `python3 -m pytest -q`
Expected: PASS（1416 + 新驱动测试 ~12 + 红线 +1，无新增 fail）

- [ ] **Step 3: tasks.md 全勾选 + 提交**

```bash
git add openspec/changes/cf-choppy-neutral-tp1-floor-ab/tasks.md
git commit -m "chore(cf-choppy-tp1-floor): tasks 收尾 + 真跑结论待入 verify 报告"
```

archived-with: 2026-06-24-cf-choppy-neutral-tp1-floor-ab
---

## Self-Review

- **Spec coverage**：两臂复盘(Task2)/baseline 自检闸(Task2)/scope 主桶+mixed 旁路(Task1)/翻转纯度 rr_below_floor(Task2)/统一结算+诚实门+契约(Task3)/observability 红线守卫(Task4)——delta spec 6 requirement 全覆盖。
- **Placeholder scan**：无 TBD/TODO，每步含完整代码。
- **Type consistency**：`classify_accepts` 返回键 `tp1_floor_rejected`/`survives_tp1_floor`/`other_flip`/`mismatch`/`rejected_reasons` 在 main 与测试一致；`extract_settle_fields` 的 `_plan` 用 `entry_price`/`created_at`（非 `entry_ref`）在 Task3 测试锁死。
