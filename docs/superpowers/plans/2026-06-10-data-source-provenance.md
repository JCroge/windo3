---
change: data-source-provenance
design-doc: docs/superpowers/specs/2026-06-10-data-source-provenance-design.md
base-ref: 5f2ae3f8585610bb00acb1d1a3937a129f411cd3
archived-with: 2026-06-10-data-source-provenance
---

# Data Source Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Attach a `source`/`freshness_sec`/`confidence` provenance triple to cross-source market dimensions, propagate it through tech_analysis and into Judge attribution, and let Reviewer bucket outcomes by data-source quality — observability only, no decision-behavior change.

**Architecture:** A single `derive_confidence` function in `utils/data_provenance.py` scores each dimension from freshness + cross-exchange origin + degraded state. `multi_data_collector` captures source + the API item timestamp (currently discarded) and emits a non-breaking parallel `provenance` block in `market_data`. `tech_analyst` forwards it into `tech_analysis` (pass-through, like `data_quality`). `judge` summarizes it into `trade_decision.attribution` (metadata-only). `reviewer` buckets trade records by the summary.

**Tech Stack:** Python 3.9, asyncio, pytest. Mirrors `utils/symbol_mentions.py` provenance precedent.

**Source of truth:** OpenSpec delta `openspec/changes/data-source-provenance/specs/data-source-provenance/spec.md` + the Design Doc.

**Global invariants:**
- Flat field values in `market_data` are byte-identical to pre-change (non-breaking).
- Judge decision action/ranking/veto unchanged — provenance is metadata only.
- All confidence scoring routes through the single `derive_confidence`.
- Legacy payloads/records without provenance → consumers treat as `unknown`, never crash.

archived-with: 2026-06-10-data-source-provenance
---

## Task 1: `utils/data_provenance.py` + tests

**Files:**
- Create: `utils/data_provenance.py`
- Test: `tests/test_data_provenance.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data_provenance.py
import pytest
from utils.data_provenance import derive_confidence, provenance_entry


def test_fresh_native_scores_high():
    c = derive_confidence("okx", 10, native_venue="okx", period_sec=60)
    assert c > 0.9


def test_stale_beyond_two_periods_is_zero():
    c = derive_confidence("okx", 7201, native_venue="okx", period_sec=3600)
    assert c == 0.0


def test_cross_exchange_penalty():
    native = derive_confidence("okx", 0, native_venue="okx", period_sec=3600)
    cross = derive_confidence("binance_fapi", 0, native_venue="okx", period_sec=3600)
    assert cross == pytest.approx(native * 0.7)


def test_degraded_floors_to_zero():
    assert derive_confidence("okx", 0, native_venue="okx", period_sec=60, degraded=True) == 0.0


def test_monotonic_decay():
    a = derive_confidence("okx", 100, native_venue="okx", period_sec=3600)
    b = derive_confidence("okx", 2000, native_venue="okx", period_sec=3600)
    assert a > b


def test_provenance_entry_shape():
    e = provenance_entry("binance_fapi", item_ts_ms=1000_000, now=1100.0,
                         period_sec=3600, native_venue="okx")
    assert set(e.keys()) == {"source", "freshness_sec", "confidence"}
    assert e["source"] == "binance_fapi"
    assert e["freshness_sec"] == pytest.approx(100.0)  # now(1100s) - item_ts(1000s)


def test_provenance_entry_missing_ts_falls_back_to_fetch_age():
    e = provenance_entry("okx", item_ts_ms=None, now=1100.0, fetch_ts=1090.0,
                         period_sec=60, native_venue="okx")
    assert e["freshness_sec"] == pytest.approx(10.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_data_provenance.py -q`
Expected: FAIL (module does not exist)

- [ ] **Step 3: Implement `utils/data_provenance.py`**

```python
"""Data-source provenance: source / freshness_sec / confidence for market dimensions.

Single source of truth for confidence scoring. Mirrors utils/symbol_mentions.py.
Observability metadata only — never gates trading decisions.
"""
from typing import Optional

CROSS_EXCHANGE_FACTOR = 0.7


def derive_confidence(source: str, freshness_sec: float, *,
                      native_venue: str = "okx",
                      period_sec: Optional[float] = None,
                      degraded: bool = False) -> float:
    """0.0-1.0. Linear freshness decay to 0 at 2x the sampling period, times a
    cross-exchange penalty; floored to 0 when degraded."""
    if degraded:
        return 0.0
    if period_sec and period_sec > 0:
        freshness_factor = 1.0 - (freshness_sec / (period_sec * 2.0))
    else:
        freshness_factor = 1.0
    freshness_factor = max(0.0, min(1.0, freshness_factor))
    source_factor = 1.0 if _venue_of(source) == native_venue else CROSS_EXCHANGE_FACTOR
    return round(freshness_factor * source_factor, 4)


def _venue_of(source: str) -> str:
    """Map a source feed id to its venue (e.g. binance_fapi -> binance)."""
    if not source:
        return "unknown"
    return source.split("_", 1)[0]


def provenance_entry(source: str, item_ts_ms: Optional[float], now: float, *,
                     period_sec: Optional[float] = None,
                     native_venue: str = "okx",
                     fetch_ts: Optional[float] = None,
                     degraded: bool = False) -> dict:
    """Build {source, freshness_sec, confidence}. freshness from the datum
    timestamp when available, else time-since-fetch, else 0."""
    if item_ts_ms is not None:
        freshness_sec = max(0.0, now - (float(item_ts_ms) / 1000.0))
    elif fetch_ts is not None:
        freshness_sec = max(0.0, now - float(fetch_ts))
    else:
        freshness_sec = 0.0
    return {
        "source": source,
        "freshness_sec": round(freshness_sec, 1),
        "confidence": derive_confidence(source, freshness_sec,
                                        native_venue=native_venue,
                                        period_sec=period_sec, degraded=degraded),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_data_provenance.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/data_provenance.py tests/test_data_provenance.py
git commit -m "feat(provenance): add data_provenance confidence helper"
```

archived-with: 2026-06-10-data-source-provenance
---

## Task 2: Collector captures source + item timestamp

Extend the `_fetch_*` helpers to return `(value_dict, meta)` where `meta={source, item_ts}`. Value dict content unchanged.

**Files:**
- Modify: `agents/trading/multi_data_collector.py` — `_fetch_oi_delta` (494), `_fetch_taker_ratio` (522), `_fetch_big_trades` (542), `_fetch_long_short_ratio` (586)
- Test: `tests/test_data_provenance_collector.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data_provenance_collector.py
import pytest
from agents.trading.multi_data_collector import MultiDataCollector


def test_fetch_helpers_return_value_and_meta(monkeypatch):
    # The four cross-source fetchers must return (value_dict, meta) with meta.source set.
    import inspect
    from agents.trading import multi_data_collector as m
    for name in ("_fetch_oi_delta", "_fetch_taker_ratio", "_fetch_big_trades", "_fetch_long_short_ratio"):
        fn = getattr(m.MultiDataCollector, name)
        assert fn is not None
    # Behavioral contract covered by collector integration test below.
```

(Note: the `_fetch_*` helpers make live HTTP calls; the implementer SHOULD add a focused test that monkeypatches `aiohttp`/the session to return a canned API item with a timestamp and asserts the returned `meta` carries `source` and `item_ts`. See Step 3 for the exact source/ts mapping to assert.)

- [ ] **Step 2: Run to verify current shape**

Run: `python3 -m pytest tests/test_data_provenance_collector.py -q`
Expected: PASS trivially (presence check) — real assertions added with implementation in Step 3.

- [ ] **Step 3: Implement the return-shape change**

Change each helper's return to `(value_dict, meta)` and each early/empty return to `({}, {"source": <src>, "item_ts": None})`:
- `_fetch_oi_delta`: `src="binance_fapi"`; `item_ts = int(data[-1]['timestamp'])` (Binance openInterestHist points carry `timestamp`).
- `_fetch_taker_ratio`: `src="binance_fapi"`; `item_ts = int(item['timestamp'])`.
- `_fetch_long_short_ratio`: `src="binance_fapi"`; `item_ts = int(item['timestamp'])`.
- `_fetch_big_trades`: `src="okx"`; `item_ts = int(trades[0]['ts'])` (newest trade; OKX trades carry `ts` in ms).

Add a focused test (monkeypatch the aiohttp session to return a canned list/dict with a known `timestamp`/`ts`) asserting `meta["source"]` and `meta["item_ts"]` for each helper.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_data_provenance_collector.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/multi_data_collector.py tests/test_data_provenance_collector.py
git commit -m "feat(provenance): fetchers return (value, meta) with source + item_ts"
```

archived-with: 2026-06-10-data-source-provenance
---

## Task 3: Collector assembles + emits the `provenance` block

**Files:**
- Modify: `agents/trading/multi_data_collector.py` — `_full_collect` (249-377)
- Test: `tests/test_data_provenance_collector.py`

- [ ] **Step 1: Write the failing test**

Add a test that drives `_full_collect` with monkeypatched fetchers returning canned `(value, meta)` and asserts:
- `payload["provenance"]["taker_ratio"]` has `source == "binance_fapi"`, `freshness_sec` ≈ the canned age, `confidence` in [0,1].
- A 50-min-old `period=1h` taker sample → `freshness_sec` ≈ 3000.
- Flat `payload["taker_ratio"]` is byte-identical to the canned value dict.
- A failed dimension → its provenance `confidence == 0.0`.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_data_provenance_collector.py -k provenance_block -q`
Expected: FAIL

- [ ] **Step 3: Implement assembly in `_full_collect`**

Unpack `(value, meta)` from the four cross-source fetchers (and funding from ccxt). After computing `degraded`, build:
```python
        now = time.time()
        PERIOD_SEC = {"oi_data": 300, "taker_ratio": 3600,
                      "long_short_account": 3600, "big_trades": 60, "funding_rate": 28800}
        from utils.data_provenance import provenance_entry
        provenance = {}
        for dim, (val, meta) in (
            ("oi_data", (oi_data, oi_meta)),
            ("taker_ratio", (taker_ratio, taker_meta)),
            ("long_short_account", (long_short, ls_meta)),
            ("big_trades", (big_trades, bt_meta)),
        ):
            src = (meta or {}).get("source", "unknown")
            provenance[dim] = provenance_entry(
                src, (meta or {}).get("item_ts"), now,
                period_sec=PERIOD_SEC.get(dim), native_venue="okx",
                degraded=degraded or not val,
            )
        # funding_rate provenance (OKX native)
        provenance["funding_rate"] = provenance_entry(
            "okx", funding_meta_ts, now, period_sec=PERIOD_SEC["funding_rate"],
            native_venue="okx", degraded=degraded or funding_rate is None)
        payload["provenance"] = provenance
```
Adapt the unpacking to however the helpers are called (update the call sites at lines 286/291/296/301 to receive tuples). Keep flat assignment of the value dicts in the payload exactly as today.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_data_provenance_collector.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/multi_data_collector.py tests/test_data_provenance_collector.py
git commit -m "feat(provenance): emit per-dimension provenance block in market_data"
```

archived-with: 2026-06-10-data-source-provenance
---

## Task 4: Propagate through tech_analyst into tech_analysis

**Files:**
- Modify: `agents/trading/tech_analyst.py` — the `result` dict (132-162)
- Test: `tests/test_data_provenance_propagation.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data_provenance_propagation.py
import pytest
from agents.trading.tech_analyst import TechAnalyst


@pytest.mark.asyncio
async def test_tech_analysis_forwards_provenance(monkeypatch):
    # Build a minimal market_data payload with a provenance block and assert the
    # published tech_analysis carries it. (Implementer: reuse existing tech_analyst
    # test fixtures/harness; assert result["provenance"] == payload["provenance"].)
    ...


def test_legacy_market_data_without_provenance_tolerated():
    # A payload with no "provenance" key must not crash and yields no provenance block.
    ...
```

(Implementer: flesh these out against the existing tech_analyst test harness; if none exists, construct the payload dict and call the analyze method directly, capturing the published result via a stub `publish`.)

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_data_provenance_propagation.py -q`
Expected: FAIL

- [ ] **Step 3: Implement the forward**

In `tech_analyst.py` `result` dict (after the `data_quality` line 161), add a pass-through mirroring `data_quality`:
```python
            "data_quality": payload.get('data_quality', {}),
            "provenance": payload.get('provenance', {}),
```
Pure forward — no re-derivation. Missing provenance → `{}` (consumers treat as unknown).

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_data_provenance_propagation.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/tech_analyst.py tests/test_data_provenance_propagation.py
git commit -m "feat(provenance): forward provenance through tech_analysis"
```

archived-with: 2026-06-10-data-source-provenance
---

## Task 5: Judge attaches provenance summary to attribution (metadata-only)

**Files:**
- Modify: `agents/trading/judge.py` — `_build_attribution` + `_rejection_attribution`
- Test: `tests/test_data_provenance_propagation.py`

- [ ] **Step 1: Write the failing test**

```python
def test_attribution_carries_provenance_summary():
    from agents.trading.judge import Judge
    tech = {"provenance": {
        "taker_ratio": {"source": "binance_fapi", "freshness_sec": 3000, "confidence": 0.35},
        "big_trades": {"source": "okx", "freshness_sec": 5, "confidence": 0.95},
    }}
    summary = Judge._summarize_provenance(tech)  # static/helper
    assert summary["weakest_confidence"] == pytest.approx(0.35)
    assert summary["has_cross_exchange"] is True
    assert summary["quality"] != "unknown"


def test_attribution_provenance_unknown_when_missing():
    from agents.trading.judge import Judge
    summary = Judge._summarize_provenance({})
    assert summary["quality"] == "unknown"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_data_provenance_propagation.py -k attribution -q`
Expected: FAIL

- [ ] **Step 3: Implement `_summarize_provenance` + wire into attribution**

Add a helper (static or instance) to `Judge`:
```python
    @staticmethod
    def _summarize_provenance(tech: dict) -> dict:
        prov = (tech or {}).get("provenance") or {}
        if not prov:
            return {"quality": "unknown", "weakest_confidence": None, "has_cross_exchange": False}
        confs = [e.get("confidence", 0.0) for e in prov.values()]
        weakest = min(confs) if confs else 0.0
        has_cross = any(
            (e.get("source", "").split("_", 1)[0] != "okx") for e in prov.values()
        )
        return {"quality": "known", "weakest_confidence": round(weakest, 4),
                "has_cross_exchange": bool(has_cross)}
```
In BOTH `_build_attribution` and `_rejection_attribution`, add `attribution['provenance'] = self._summarize_provenance(tech)` (the `tech`/`tech_analysis` dict is already in scope at those call sites — confirm the param name and pass it). This is metadata only; do not reference the summary in any gate/ranking/veto.

- [ ] **Step 4: Run tests + decision-unchanged guard**

Run: `python3 -m pytest tests/test_data_provenance_propagation.py -q`
Then run the existing Judge suites to confirm decisions unchanged:
Run: `python3 -m pytest tests/test_short_main_path_risk_guard.py tests/test_rr_floor_policy.py -q` (or the closest existing Judge decision tests)
Expected: PASS, no decision changes.

- [ ] **Step 5: Commit**

```bash
git add agents/trading/judge.py tests/test_data_provenance_propagation.py
git commit -m "feat(provenance): Judge attaches metadata-only provenance summary to attribution"
```

archived-with: 2026-06-10-data-source-provenance
---

## Task 6: Reviewer buckets by provenance summary

**Files:**
- Modify: `agents/trading/reviewer.py` — trade_record bucket assignment (near 187-188)
- Test: `tests/test_data_provenance_propagation.py`

- [ ] **Step 1: Write the failing test**

```python
def test_reviewer_sets_provenance_bucket():
    # A trade record whose attribution has a provenance summary gets a provenance_bucket;
    # legacy records without one get 'unknown'. (Implementer: mirror how liquidity_bucket /
    # rr_bucket are tested, reviewer.py:187-188.)
    ...
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_data_provenance_propagation.py -k reviewer -q`
Expected: FAIL

- [ ] **Step 3: Implement bucketing**

Where reviewer sets `liquidity_bucket`/`rr_bucket` from attribution (reviewer.py:187-188), add:
```python
                prov = attribution.get('provenance') or {}
                if prov.get('quality') == 'known':
                    wc = prov.get('weakest_confidence')
                    band = 'low' if (wc is not None and wc < 0.5) else 'high'
                    trade_record['provenance_bucket'] = (
                        'cross_exchange' if prov.get('has_cross_exchange') else 'native'
                    ) + f'/{band}'
                else:
                    trade_record['provenance_bucket'] = 'unknown'
```
Confirm `attribution` is in scope at that line (same dict the existing buckets read from).

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_data_provenance_propagation.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/reviewer.py tests/test_data_provenance_propagation.py
git commit -m "feat(provenance): Reviewer buckets trade records by provenance summary"
```

archived-with: 2026-06-10-data-source-provenance
---

## Task 7: Verification + baseline

- [ ] **Step 1: Targeted suite**

Run: `python3 -m pytest tests/test_data_provenance.py tests/test_data_provenance_collector.py tests/test_data_provenance_propagation.py -q`
Expected: PASS

- [ ] **Step 2: Full regression + baseline**

Run: `python3 -m pytest -q`
Expected: PASS; record new `N passed` count.

- [ ] **Step 3: Compileall**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q agents/ utils/`
Expected: exit 0

- [ ] **Step 4: Check off tasks.md**

Mark all items in `openspec/changes/data-source-provenance/tasks.md` as `[x]`.

- [ ] **Step 5: Commit**

```bash
git add openspec/changes/data-source-provenance/tasks.md
git commit -m "chore(provenance): mark tasks complete + record baseline"
```

archived-with: 2026-06-10-data-source-provenance
---

## Self-Review Notes

- **Spec coverage:** provenance triple → Tasks 1-3; source identity → Task 2; freshness from datum → Tasks 1-3; confidence single-fn → Task 1; propagate through tech_analysis → Task 4; attribution summary to trade records → Task 5; Reviewer bucketing → Task 6; observability-only guard → Task 5 Step 4 (Judge decision-unchanged).
- **Non-breaking:** flat values asserted byte-identical (Task 3); legacy tolerance tested at every layer (Tasks 3,4,5,6).
- **Type consistency:** `(value, meta)` tuple shape and `{source, item_ts}` meta keys are uniform across Task 2; `{source, freshness_sec, confidence}` entry shape and the `_summarize_provenance` output (`quality/weakest_confidence/has_cross_exchange`) are stable across Tasks 1,3,5,6.
- **Live HTTP caveat:** the `_fetch_*` tests monkeypatch the aiohttp session — no network in CI (consistent with the project's network-test exclusion).
