# Research Liquidity Hard Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove low-liquidity perpetual symbols from the initial research candidate list before any LLM selection.

**Architecture:** Keep the existing cheap ticker-volume prefilter, then apply a deterministic hard liquidity gate in `MarketScanner` after open interest enrichment. Store the filtered last-good candidates and publish a compact `liquidity_filter` summary on normal and degraded market-data payloads.

**Tech Stack:** Python 3, asyncio, pytest, existing `MessageBus`, existing `utils.config_loader` config defaults and environment overrides.

---

### Task 1: Add RED scanner liquidity tests

**Files:**
- Modify: `test_research_market_scanner_failover.py`

- [x] **Step 1: Add a reusable exchange fixture for multiple ticker candidates**

```python
class MultiTickerExchange:
    def fetch_tickers(self):
        return {
            "BTC/USDT:USDT": {
                "quoteVolume": 200_000_000,
                "high": 105_000,
                "low": 100_000,
                "last": 103_000,
                "percentage": 3.0,
            },
            "BABY/USDT:USDT": {
                "quoteVolume": 80_000_000,
                "high": 0.016,
                "low": 0.014,
                "last": 0.015,
                "percentage": 4.0,
            },
            "MISS/USDT:USDT": {
                "quoteVolume": 90_000_000,
                "high": 1.2,
                "low": 1.0,
                "last": 1.1,
                "percentage": 2.0,
            },
            "THIN/USDT:USDT": {
                "quoteVolume": 20_000_000,
                "high": 2.4,
                "low": 2.0,
                "last": 2.2,
                "percentage": 8.0,
            },
        }

    def market(self, symbol):
        return {"swap": True}
```

- [x] **Step 2: Write a failing test for normal scan filtering**

```python
@pytest.mark.asyncio
async def test_market_scanner_filters_low_liquidity_before_publish(monkeypatch):
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register("catcher", ["research_market_data"])

    scanner = MarketScanner({
        "market_scan_retries": 1,
        "market_scan_retry_delay": 0,
        "research_min_volume_24h_usdt": 50_000_000,
        "research_min_open_interest_usd": 10_000_000,
    })
    scanner.exchange = MultiTickerExchange()
    scanner._current_cycle_id = "cycle_liquidity"

    oi_by_inst = {
        "BTC-USDT-SWAP": 100_000_000,
        "BABY-USDT-SWAP": 2_000_000,
        "MISS-USDT-SWAP": None,
        "THIN-USDT-SWAP": 50_000_000,
    }
    monkeypatch.setattr(scanner, "_fetch_monthly_kline_count", lambda inst_id: asyncio.sleep(0, result=12))
    monkeypatch.setattr(scanner, "_fetch_funding", lambda symbol: asyncio.sleep(0, result=0.0001))
    monkeypatch.setattr(scanner, "_fetch_long_short_ratio", lambda inst_id: asyncio.sleep(0, result=1.05))
    monkeypatch.setattr(scanner, "_fetch_open_interest", lambda inst_id: asyncio.sleep(0, result=oi_by_inst[inst_id]))
    monkeypatch.setattr(scanner, "_fetch_sl_structure", lambda inst_id, price: asyncio.sleep(0, result={"sl_viable": True}))

    await scanner._scan_market()

    msg = await bus.receive("catcher", timeout=0.2)
    payload = msg["payload"]
    assert [c["symbol"] for c in payload["candidates"]] == ["BTC-USDT"]
    assert payload["liquidity_filter"]["kept"] == 1
    assert payload["liquidity_filter"]["removed"] == 3
    assert payload["liquidity_filter"]["min_volume_24h_usdt"] == 50_000_000
    assert payload["liquidity_filter"]["min_open_interest_usd"] == 10_000_000
    examples = {item["symbol"]: item["reason"] for item in payload["liquidity_filter"]["examples"]}
    assert examples["BABY-USDT"] == "open_interest_below_min"
    assert examples["MISS-USDT"] == "open_interest_missing"
    assert examples["THIN-USDT"] == "volume_below_min"
```

- [x] **Step 3: Write a failing test for degraded last-good summary reuse**

```python
@pytest.mark.asyncio
async def test_market_scanner_degraded_payload_carries_liquidity_filter(monkeypatch):
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register("catcher", ["research_market_data"])

    scanner = MarketScanner({
        "market_scan_retries": 1,
        "market_scan_retry_delay": 0,
        "research_min_volume_24h_usdt": 50_000_000,
        "research_min_open_interest_usd": 10_000_000,
    })
    scanner.exchange = SequencedExchange()
    scanner._current_cycle_id = "cycle_ok"
    monkeypatch.setattr(scanner, "_fetch_monthly_kline_count", lambda inst_id: asyncio.sleep(0, result=12))
    monkeypatch.setattr(scanner, "_fetch_funding", lambda symbol: asyncio.sleep(0, result=0.0001))
    monkeypatch.setattr(scanner, "_fetch_long_short_ratio", lambda inst_id: asyncio.sleep(0, result=1.05))
    monkeypatch.setattr(scanner, "_fetch_open_interest", lambda inst_id: asyncio.sleep(0, result=100_000_000))
    monkeypatch.setattr(scanner, "_fetch_sl_structure", lambda inst_id, price: asyncio.sleep(0, result={"sl_viable": True}))

    await scanner._scan_market()
    first = await bus.receive("catcher", timeout=0.2)
    assert first["payload"]["liquidity_filter"]["kept"] == 1

    scanner._current_cycle_id = "cycle_fail"
    await scanner._scan_market()

    second = await bus.receive("catcher", timeout=0.2)
    assert second["payload"]["degraded"] is True
    assert second["payload"]["fallback_source"] == "last_good"
    assert second["payload"]["liquidity_filter"]["kept"] == 1
    assert second["payload"]["candidates"][0]["symbol"] == "BTC-USDT"
```

- [x] **Step 4: Run tests to verify RED**

Run:

```bash
python3 -m pytest -q test_research_market_scanner_failover.py::test_market_scanner_filters_low_liquidity_before_publish test_research_market_scanner_failover.py::test_market_scanner_degraded_payload_carries_liquidity_filter
```

Expected: tests fail because `liquidity_filter` does not exist and low-OI candidates are still published.

### Task 2: Add config defaults and env mappings

**Files:**
- Modify: `utils/config_loader.py`

- [x] **Step 1: Add safe default values**

```python
"research_min_volume_24h_usdt": 50_000_000,
"research_min_open_interest_usd": 10_000_000,
```

- [x] **Step 2: Add environment overrides**

```python
"RESEARCH_MIN_VOLUME_24H_USDT": ("research_min_volume_24h_usdt", float),
"RESEARCH_MIN_OPEN_INTEREST_USD": ("research_min_open_interest_usd", float),
```

- [x] **Step 3: Add hard-limit validation bounds**

```python
"research_min_volume_24h_usdt": (0.0, 10_000_000_000.0),
"research_min_open_interest_usd": (0.0, 10_000_000_000.0),
```

### Task 3: Implement MarketScanner hard filter

**Files:**
- Modify: `agents/research/market_scanner.py`

- [x] **Step 1: Initialize thresholds and last-good summary**

```python
self.research_min_volume_24h_usdt = float(self.config.get('research_min_volume_24h_usdt', 50_000_000))
self.research_min_open_interest_usd = float(self.config.get('research_min_open_interest_usd', 10_000_000))
self._last_good_liquidity_filter = None
```

- [x] **Step 2: Add helper to classify removals**

```python
def _liquidity_rejection_reason(self, candidate: dict) -> str | None:
    if float(candidate.get('volume_24h') or 0) < self.research_min_volume_24h_usdt:
        return "volume_below_min"
    open_interest = candidate.get('open_interest_usd')
    if open_interest is None:
        return "open_interest_missing"
    if float(open_interest or 0) < self.research_min_open_interest_usd:
        return "open_interest_below_min"
    return None
```

- [x] **Step 3: Add helper to apply the hard filter and build summary**

```python
def _apply_liquidity_hard_filter(self, candidates: list) -> tuple[list, dict]:
    kept = []
    examples = []
    for candidate in candidates:
        reason = self._liquidity_rejection_reason(candidate)
        if reason is None:
            kept.append(candidate)
            continue
        if len(examples) < 5:
            examples.append({
                "symbol": candidate.get("symbol"),
                "volume_24h": candidate.get("volume_24h"),
                "open_interest_usd": candidate.get("open_interest_usd"),
                "reason": reason,
            })
    return kept, {
        "min_volume_24h_usdt": self.research_min_volume_24h_usdt,
        "min_open_interest_usd": self.research_min_open_interest_usd,
        "removed": len(candidates) - len(kept),
        "kept": len(kept),
        "examples": examples,
    }
```

- [x] **Step 4: Apply helper after enrichment and before publish**

```python
top_candidates, liquidity_filter = self._apply_liquidity_hard_filter(top_candidates)
self._remember_last_good(top_candidates, len(tickers), len(candidates), liquidity_filter)
```

- [x] **Step 5: Publish summary on normal and degraded payloads**

```python
"liquidity_filter": liquidity_filter,
```

and in degraded payload when available:

```python
if liquidity_filter is not None:
    payload["liquidity_filter"] = liquidity_filter
```

### Task 4: Verify and commit implementation

**Files:**
- Verify: `test_research_market_scanner_failover.py`
- Verify: `agents/research/market_scanner.py`
- Verify: `utils/config_loader.py`

- [x] **Step 1: Run focused tests**

```bash
python3 -m pytest -q test_research_market_scanner_failover.py
```

Expected: all tests in the file pass.

- [x] **Step 2: Run compile check**

```bash
python3 -m compileall -q agents/research/market_scanner.py utils/config_loader.py
```

Expected: exit code 0.

- [x] **Step 3: Run broad test suite if practical**

```bash
python3 -m pytest -q
```

Expected: pass, or report exact failures if unrelated environment dependencies block the full suite.

- [ ] **Step 4: Commit only the implementation files and plan**

```bash
git add docs/superpowers/plans/2026-06-07-research-liquidity-hard-filter.md test_research_market_scanner_failover.py agents/research/market_scanner.py utils/config_loader.py
git commit -m "feat: filter low-liquidity research candidates"
```

- [ ] **Step 5: Push committed work**

```bash
git push origin main
```

Expected: remote `origin/main` receives the design and implementation commits.

---

## Self-Review

- Spec coverage: Tasks 1 and 3 cover initial candidate filtering, missing OI fail-closed behavior, published summary, and degraded last-good reuse. Task 2 covers defaults and env mapping. Task 4 covers verification, commit, and push.
- Deferred-item scan: This plan avoids postponed work and includes exact paths, commands, and expected results.
- Type consistency: Helper names, config keys, payload keys, and test assertions use the same identifiers across tasks.
