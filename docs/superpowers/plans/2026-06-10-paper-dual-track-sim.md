---
change: paper-dual-track-sim
design-doc: docs/superpowers/specs/2026-06-10-paper-dual-track-sim-design.md
base-ref: ae64e12914d48de8f833a9a5e0325da1856e950d
archived-with: 2026-06-10-paper-dual-track-sim
---

# Paper Dual-Track Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run an idealized (market-immediate) shadow book alongside the existing realistic (limit-fill) paper book, and ship a paper-only reader that quantifies `limit_discipline_value = realistic_total − idealized_total`.

**Architecture:** Single `PaperExecutor` gains a `book ∈ {realistic, idealized}` dimension. `self._books` holds per-book `positions` + `equity`; `self._positions`/`self._equity` become read/write **properties proxying the realistic book**, so all untouched realistic code (and `telegram_notifier`'s flat `data/paper_positions.json` reader) keeps working unchanged. Core helpers are parameterized by `book`. Realistic keeps writing the existing state files; idealized writes new `*_idealized.json` files (separate-files, Decision D5). A pure-function reader computes the gap from `paper_trades.jsonl` grouped by `book`.

**Tech Stack:** Python 3.9, asyncio, pytest, existing `utils/atomic_io`, `utils/cost_model`, message bus (`agents/base.py`).

**Source of truth:** OpenSpec delta specs `openspec/changes/paper-dual-track-sim/specs/{paper-dual-track,paper-executor}/spec.md` and the Design Doc. Do not redefine requirements here.

**Global invariants every task must preserve:**
- Realistic book behavior is byte-for-byte equivalent to today when `paper_dual_track_enabled=false` (only an added `book='realistic'` tag on records).
- Idealized opens only when enabled AND a fresh tick price exists.
- `_pending_limits` is realistic-only and never serialized.
- No idealized record ever reaches a live Reviewer metric (paper/live isolation).

archived-with: 2026-06-10-paper-dual-track-sim
---

## Task 1: Book container + realistic proxies (no behavior change)

Introduce the two-book structure while keeping realistic behavior identical via proxy properties.

**Files:**
- Modify: `agents/trading/paper_executor.py:37-57` (`__init__`)
- Test: `tests/test_paper_dual_track.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paper_dual_track.py
import pytest
from agents.trading.paper_executor import PaperExecutor


def _mk(config=None):
    return PaperExecutor(config or {})


def test_books_exist_with_realistic_and_idealized():
    pe = _mk()
    assert set(pe._books.keys()) == {"realistic", "idealized"}
    assert pe._books["realistic"]["positions"] == {}
    assert pe._books["idealized"]["positions"] == {}


def test_positions_property_proxies_realistic_book():
    pe = _mk()
    pe._positions["BTC-USDT"] = {"side": "long"}
    assert pe._books["realistic"]["positions"]["BTC-USDT"] == {"side": "long"}


def test_equity_property_proxies_realistic_book():
    pe = _mk()
    start = pe._equity
    pe._equity -= 5.0
    assert pe._books["realistic"]["equity"] == pytest.approx(start - 5.0)


def test_idealized_book_starts_at_same_initial_equity():
    pe = _mk({"effective_balance_cap": 500})
    assert pe._books["idealized"]["equity"] == pytest.approx(500.0)
    assert pe._books["realistic"]["equity"] == pytest.approx(500.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_paper_dual_track.py -q`
Expected: FAIL (`AttributeError: 'PaperExecutor' object has no attribute '_books'`)

- [ ] **Step 3: Implement book container + proxy properties**

In `__init__`, replace the `self._positions: dict = {}` and `self._equity: float = self._initial_equity` lines (currently `paper_executor.py:45-46`) with the book container:

```python
        self._books = {
            "realistic": {"positions": {}, "equity": self._initial_equity},
            "idealized": {"positions": {}, "equity": self._initial_equity},
        }
```

Add these properties to the class body (place right after `__init__`):

```python
    @property
    def _positions(self) -> dict:
        """Realistic-book positions (proxy: preserves all legacy references)."""
        return self._books["realistic"]["positions"]

    @property
    def _equity(self) -> float:
        return self._books["realistic"]["equity"]

    @_equity.setter
    def _equity(self, value: float) -> None:
        self._books["realistic"]["equity"] = value
```

Note: `self._equity = self._initial_equity` was previously set in `__init__`; the book container now seeds it. Remove the old direct assignment (now handled by the dict). The setter makes `self._equity -= fee` keep working.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_paper_dual_track.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Run existing paper tests for regression**

Run: `python3 -m pytest tests/test_paper_limit_fill.py -q`
Expected: PASS (no regression — proxies keep realistic code working)

- [ ] **Step 6: Commit**

```bash
git add agents/trading/paper_executor.py tests/test_paper_dual_track.py
git commit -m "feat(paper): add two-book container with realistic proxy properties"
```

archived-with: 2026-06-10-paper-dual-track-sim
---

## Task 2: Parameterize core helpers by book (realistic default)

Switch the shared open/close/SL-TP/add/reduce/unrealized helpers from hard-coded `self._positions`/`self._equity` to a `book` parameter defaulting to realistic. Tag records with `book`.

**Files:**
- Modify: `agents/trading/paper_executor.py` — `_open_paper_at_price` (349-411), `_close_paper` (413-473), `_check_sl_tp` (570-586), `_add_paper` (475-525), `_reduce_paper` (527-568), `_unrealized_pnl` (647-659)
- Test: `tests/test_paper_dual_track.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_open_and_close_on_idealized_book_isolated_from_realistic():
    pe = _mk()
    pe._latest_price["BTC-USDT"] = 100.0
    plan = {"size_usdt": 30, "leverage": 5, "stop_loss": 90, "tp_levels": [120]}
    await pe._open_paper_at_price(
        symbol="BTC-USDT", side="long", action="open_long",
        plan=plan, decision={"request_id": "r1"},
        fill_price=100.0, entry_method="market", book="idealized",
    )
    assert "BTC-USDT" in pe._books["idealized"]["positions"]
    assert pe._books["idealized"]["positions"]["BTC-USDT"]["book"] == "idealized"
    # realistic untouched
    assert "BTC-USDT" not in pe._books["realistic"]["positions"]


@pytest.mark.asyncio
async def test_realistic_record_tagged_realistic_by_default():
    pe = _mk()
    pe._latest_price["ETH-USDT"] = 50.0
    plan = {"size_usdt": 20, "leverage": 3, "stop_loss": 45, "tp_levels": [60]}
    await pe._open_paper_at_price(
        symbol="ETH-USDT", side="long", action="open_long",
        plan=plan, decision={"request_id": "r2"},
        fill_price=50.0, entry_method="market",
    )
    assert pe._books["realistic"]["positions"]["ETH-USDT"]["book"] == "realistic"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_paper_dual_track.py -k book -q`
Expected: FAIL (`_open_paper_at_price() got an unexpected keyword argument 'book'`)

- [ ] **Step 3: Implement book parameter across helpers**

For each helper, add `book: str = "realistic"` as the last parameter and replace internal `self._positions` → `self._books[book]["positions"]`, `self._equity` (read/write) → `self._books[book]["equity"]`, and `self._locked_margin()` → `self._locked_margin(book)`.

`_open_paper_at_price` signature (line 349-351) becomes:
```python
    async def _open_paper_at_price(self, symbol: str, side: str, action: str,
                                   plan: Optional[dict], decision: dict,
                                   fill_price: float, entry_method: str,
                                   book: str = "realistic") -> None:
```
Inside, the position dict (line 384-396) gains `'book': book,` and the free-equity guard + `self._positions[symbol] = pos` + equity decrement + the `paper_execution_result` publish all use `self._books[book]`. Add `"book": book,` to the published payload dict (line 405-411).

`_close_paper` (413): add `book: str = "realistic"`; use `self._books[book]["positions"]` for the `del`, `self._books[book]["equity"]` for the PnL credit; set `trade_record['book'] = book`; add `"book": book` to the publish payload (463-473).

`_check_sl_tp` (570): add `book: str = "realistic"`; read `pos = self._books[book]["positions"].get(symbol)`; pass `book=book` into the `_close_paper(...)` calls.

`_add_paper` (475) and `_reduce_paper` (527): add `book: str = "realistic"`; switch position/equity access to `self._books[book]`.

`_unrealized_pnl` (647): add `book: str = "realistic"`; iterate `self._books[book]["positions"]`.

Update `_locked_margin` (610-611) to take a book:
```python
    def _locked_margin(self, book: str = "realistic") -> float:
        return sum(p['margin'] for p in self._books[book]["positions"].values())
```

- [ ] **Step 4: Run new + regression tests**

Run: `python3 -m pytest tests/test_paper_dual_track.py tests/test_paper_limit_fill.py -q`
Expected: PASS (book param works; realistic path unchanged because callers still omit `book`)

- [ ] **Step 5: Commit**

```bash
git add agents/trading/paper_executor.py tests/test_paper_dual_track.py
git commit -m "feat(paper): parameterize open/close/sl-tp/add/reduce by book"
```

archived-with: 2026-06-10-paper-dual-track-sim
---

## Task 3: Separate-file persistence + legacy load

Persist each book to its own files; load a legacy flat `paper_positions.json` as the realistic book.

**Files:**
- Modify: `agents/trading/paper_executor.py` — module constants (22-24), `_load_state` (598-608), `_persist_state` (613-627)
- Test: `tests/test_paper_dual_track.py`

- [ ] **Step 1: Write the failing test**

```python
import json, os, tempfile
from agents.trading import paper_executor as pe_mod


def test_legacy_flat_positions_loads_as_realistic(tmp_path, monkeypatch):
    legacy = {"BTC-USDT": {"side": "long", "margin": 10, "entry_price": 100}}
    pos_file = tmp_path / "paper_positions.json"
    pos_file.write_text(json.dumps(legacy))
    monkeypatch.setattr(pe_mod, "PAPER_POSITIONS_FILE", str(pos_file))
    monkeypatch.setattr(pe_mod, "PAPER_EQUITY_FILE", str(tmp_path / "paper_equity.json"))
    monkeypatch.setattr(pe_mod, "PAPER_POSITIONS_IDEAL_FILE", str(tmp_path / "paper_positions_idealized.json"))
    monkeypatch.setattr(pe_mod, "PAPER_EQUITY_IDEAL_FILE", str(tmp_path / "paper_equity_idealized.json"))
    pe = pe_mod.PaperExecutor({})
    pe._load_state()
    assert pe._books["realistic"]["positions"]["BTC-USDT"]["side"] == "long"
    assert pe._books["idealized"]["positions"] == {}


def test_round_trip_preserves_book_separation(tmp_path, monkeypatch):
    monkeypatch.setattr(pe_mod, "PAPER_POSITIONS_FILE", str(tmp_path / "paper_positions.json"))
    monkeypatch.setattr(pe_mod, "PAPER_EQUITY_FILE", str(tmp_path / "paper_equity.json"))
    monkeypatch.setattr(pe_mod, "PAPER_POSITIONS_IDEAL_FILE", str(tmp_path / "paper_positions_idealized.json"))
    monkeypatch.setattr(pe_mod, "PAPER_EQUITY_IDEAL_FILE", str(tmp_path / "paper_equity_idealized.json"))
    pe = pe_mod.PaperExecutor({})
    pe._books["realistic"]["positions"]["BTC-USDT"] = {"side": "long", "margin": 5}
    pe._books["idealized"]["positions"]["BTC-USDT"] = {"side": "long", "margin": 5, "book": "idealized"}
    pe._persist_state()
    pe2 = pe_mod.PaperExecutor({})
    pe2._load_state()
    assert "BTC-USDT" in pe2._books["realistic"]["positions"]
    assert "BTC-USDT" in pe2._books["idealized"]["positions"]
    # realistic file stays a flat map (telegram reader compatibility)
    flat = json.loads((tmp_path / "paper_positions.json").read_text())
    assert "BTC-USDT" in flat and "positions" not in flat
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_paper_dual_track.py -k "legacy or round_trip" -q`
Expected: FAIL (`module has no attribute 'PAPER_POSITIONS_IDEAL_FILE'`)

- [ ] **Step 3: Implement separate-file persistence**

Add constants after line 24:
```python
PAPER_POSITIONS_IDEAL_FILE = "data/paper_positions_idealized.json"
PAPER_EQUITY_IDEAL_FILE = "data/paper_equity_idealized.json"
```

Rewrite `_load_state` to load realistic from the existing flat files (unchanged semantics) and idealized from the new files if present:
```python
    def _load_state(self):
        try:
            if os.path.exists(PAPER_POSITIONS_FILE):
                with open(PAPER_POSITIONS_FILE) as f:
                    self._books["realistic"]["positions"] = json.load(f)
            if os.path.exists(PAPER_EQUITY_FILE):
                with open(PAPER_EQUITY_FILE) as f:
                    self._books["realistic"]["equity"] = float(
                        json.load(f).get('equity', self._initial_equity))
            if os.path.exists(PAPER_POSITIONS_IDEAL_FILE):
                with open(PAPER_POSITIONS_IDEAL_FILE) as f:
                    self._books["idealized"]["positions"] = json.load(f)
            if os.path.exists(PAPER_EQUITY_IDEAL_FILE):
                with open(PAPER_EQUITY_IDEAL_FILE) as f:
                    self._books["idealized"]["equity"] = float(
                        json.load(f).get('equity', self._initial_equity))
        except Exception as e:
            self.logger.warning(f"[PaperExecutor] 状态加载失败: {e}")
```

Rewrite `_persist_state` to write realistic to the existing flat files (byte-compatible) and idealized to its own files:
```python
    def _persist_state(self):
        try:
            from utils.atomic_io import atomic_write_json
            for book, pos_file, eq_file in (
                ("realistic", PAPER_POSITIONS_FILE, PAPER_EQUITY_FILE),
                ("idealized", PAPER_POSITIONS_IDEAL_FILE, PAPER_EQUITY_IDEAL_FILE),
            ):
                positions = self._books[book]["positions"]
                atomic_write_json(pos_file, positions)
                locked = self._locked_margin(book)
                equity = self._books[book]["equity"]
                atomic_write_json(eq_file, {
                    'equity': round(equity, 4),
                    'locked_margin': round(locked, 4),
                    'free_equity': round(equity - locked, 4),
                    'initial_equity': self._initial_equity,
                    'open_positions': len(positions),
                    'updated_at': time.time(),
                    'book': book,
                })
        except Exception as e:
            self.logger.error(f"[PaperExecutor] 状态持久化失败: {e}")
```

Note: realistic `paper_positions.json` remains a flat symbol→position map — `telegram_notifier.py:901` and the reconciler keep working unchanged.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_paper_dual_track.py tests/test_paper_limit_fill.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/paper_executor.py tests/test_paper_dual_track.py
git commit -m "feat(paper): separate-file persistence per book with legacy realistic load"
```

archived-with: 2026-06-10-paper-dual-track-sim
---

## Task 4: Config toggle `paper_dual_track_enabled`

**Files:**
- Modify: `utils/config_loader.py` — DEFAULTS (~162), env map (~289)
- Modify: `agents/trading/paper_executor.py:37-57` (`__init__`)
- Test: `tests/test_paper_dual_track.py`

- [ ] **Step 1: Write the failing test**

```python
def test_dual_track_flag_defaults_and_override():
    assert _mk({}).dual_track_enabled is True  # paper default on
    assert _mk({"paper_dual_track_enabled": False}).dual_track_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_paper_dual_track.py -k dual_track_flag -q`
Expected: FAIL (`AttributeError: ... 'dual_track_enabled'`)

- [ ] **Step 3: Implement the flag**

In `utils/config_loader.py` DEFAULTS dict add:
```python
    # Paper dual-track simulation (idealized vs realistic)
    "paper_dual_track_enabled": True,
```
In `_read_env_overrides` env map add:
```python
        "PAPER_DUAL_TRACK_ENABLED": ("paper_dual_track_enabled", _to_bool),
```
In `PaperExecutor.__init__` (after line 56) add:
```python
        self.dual_track_enabled = bool(
            (config or {}).get('paper_dual_track_enabled', True))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_paper_dual_track.py -k dual_track_flag -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/config_loader.py agents/trading/paper_executor.py tests/test_paper_dual_track.py
git commit -m "feat(paper): add paper_dual_track_enabled config flag (paper default on)"
```

archived-with: 2026-06-10-paper-dual-track-sim
---

## Task 5: Idealized open path (market-immediate, tick-fresh gate)

When enabled, every `open_*` decision also opens an idealized market position at the latest fresh tick.

**Files:**
- Modify: `agents/trading/paper_executor.py` — `_execute_decision` (107-159), add `_open_idealized` helper
- Test: `tests/test_paper_dual_track.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_limit_decision_still_fills_idealized_at_market():
    pe = _mk({})
    pe._latest_price["BTC-USDT"] = 102.0
    pe._latest_tick_ts["BTC-USDT"] = time.time()
    plan = {"order_type": "limit", "entry_zone": [100, 101],
            "size_usdt": 30, "leverage": 5, "stop_loss": 95, "tp_levels": [120]}
    await pe._execute_decision({"action": "open_long", "symbol": "BTC-USDT",
                                "confidence": 99, "plan": plan, "request_id": "r1"})
    # realistic queued a pending limit, no realistic position yet
    assert "BTC-USDT" in pe._pending_limits
    assert "BTC-USDT" not in pe._books["realistic"]["positions"]
    # idealized opened at market 102
    ideal = pe._books["idealized"]["positions"]["BTC-USDT"]
    assert ideal["entry_price"] == pytest.approx(102.0)
    assert ideal["entry_method"] == "market" and ideal["book"] == "idealized"


@pytest.mark.asyncio
async def test_idealized_skipped_when_tick_missing():
    pe = _mk({})
    plan = {"order_type": "limit", "entry_zone": [100, 101],
            "size_usdt": 30, "leverage": 5, "stop_loss": 95, "tp_levels": [120]}
    await pe._execute_decision({"action": "open_long", "symbol": "X-USDT",
                                "confidence": 99, "plan": plan, "request_id": "r2"})
    assert "X-USDT" not in pe._books["idealized"]["positions"]


@pytest.mark.asyncio
async def test_idealized_not_opened_when_disabled():
    pe = _mk({"paper_dual_track_enabled": False})
    pe._latest_price["BTC-USDT"] = 102.0
    pe._latest_tick_ts["BTC-USDT"] = time.time()
    plan = {"size_usdt": 30, "leverage": 5, "stop_loss": 95, "tp_levels": [120]}
    await pe._execute_decision({"action": "open_long", "symbol": "BTC-USDT",
                                "confidence": 99, "plan": plan, "request_id": "r3"})
    assert pe._books["idealized"]["positions"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_paper_dual_track.py -k idealized -q`
Expected: FAIL (idealized position not created)

- [ ] **Step 3: Implement `_open_idealized` and wire into `_execute_decision`**

Add the helper:
```python
    def _tick_fresh(self, symbol: str) -> bool:
        ts = self._latest_tick_ts.get(symbol)
        return ts is not None and (time.time() - ts) <= self._tick_staleness_sec

    async def _open_idealized(self, symbol: str, action: str,
                             plan: Optional[dict], decision: dict):
        """Idealized baseline: immediate market fill at fresh latest tick."""
        if not self.dual_track_enabled:
            return
        if symbol in self._books["idealized"]["positions"]:
            return
        price = self._latest_price.get(symbol)
        if not price or not self._tick_fresh(symbol):
            return  # fail-safe: never fabricate an entry price
        side = 'long' if action == 'open_long' else 'short'
        await self._open_paper_at_price(
            symbol=symbol, side=side, action=action,
            plan=plan, decision=decision,
            fill_price=float(price), entry_method='market', book='idealized',
        )
```

In `_execute_decision`, in the open branch (after the realistic `await self._open_paper(...)` at line 145), add the idealized open. Replace lines 136-145 block's tail so both books are driven:
```python
        if action in ('open_long', 'open_short') and position is None:
            if symbol in self._pending_limits:
                self.logger.info(f"[PAPER] {norm_symbol} {action} 跳过：已有 pending limit")
                # idealized may still open even while realistic limit is pending
                await self._open_idealized(norm_symbol, action, plan, decision)
                return
            if source == 'position_analyst':
                return
            await self._open_paper(norm_symbol, action, plan, decision)
            await self._open_idealized(norm_symbol, action, plan, decision)
```

(The confidence/halt/mirror_risk gates above already returned early for rejected opens, so idealized only opens for accepted decisions — consistent with realistic.)

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_paper_dual_track.py -k idealized -q`
Expected: PASS

- [ ] **Step 5: Run full paper regression**

Run: `python3 -m pytest tests/test_paper_dual_track.py tests/test_paper_limit_fill.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agents/trading/paper_executor.py tests/test_paper_dual_track.py
git commit -m "feat(paper): open idealized market book on accepted open decisions"
```

archived-with: 2026-06-10-paper-dual-track-sim
---

## Task 6: Per-book SL/TP + mirror strategy close/reduce/add

Idealized exits mirror the realistic strategy decisions (D3) and also run its own SL/TP. The unfilled-realistic case leaves idealized to exit on its own SL/TP.

**Files:**
- Modify: `agents/trading/paper_executor.py` — `on_message` price_tick branch (86-97), `_execute_decision` close/add branches (146-159)
- Test: `tests/test_paper_dual_track.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_price_tick_checks_both_books_independently():
    pe = _mk({})
    # realistic long entry 100 SL 95 ; idealized long entry 102 SL 95
    for book, entry in (("realistic", 100.0), ("idealized", 102.0)):
        pe._books[book]["positions"]["BTC-USDT"] = {
            "symbol": "BTC-USDT", "side": "long", "entry_price": entry,
            "sl": 95.0, "tp": 130.0, "margin": 10, "leverage": 5,
            "notional": 50, "opened_at": time.time(), "entry_fee": 0.05,
            "book": book,
        }
    await pe.on_message({"type": "price_tick", "symbol": "BTC-USDT",
                         "payload": {"symbol": "BTC-USDT", "price": 94.0}})
    # both hit SL at 95 -> both closed
    assert "BTC-USDT" not in pe._books["realistic"]["positions"]
    assert "BTC-USDT" not in pe._books["idealized"]["positions"]


@pytest.mark.asyncio
async def test_strategy_close_applies_to_both_books():
    pe = _mk({})
    for book in ("realistic", "idealized"):
        pe._books[book]["positions"]["ETH-USDT"] = {
            "symbol": "ETH-USDT", "side": "long", "entry_price": 50.0,
            "sl": 45.0, "tp": 60.0, "margin": 10, "leverage": 3,
            "notional": 30, "opened_at": time.time(), "entry_fee": 0.03,
            "book": book,
        }
    pe._latest_price["ETH-USDT"] = 55.0
    await pe._execute_decision({"action": "close", "symbol": "ETH-USDT"})
    assert "ETH-USDT" not in pe._books["realistic"]["positions"]
    assert "ETH-USDT" not in pe._books["idealized"]["positions"]


@pytest.mark.asyncio
async def test_close_noop_for_idealized_when_not_held():
    pe = _mk({})
    pe._books["realistic"]["positions"]["ETH-USDT"] = {
        "symbol": "ETH-USDT", "side": "long", "entry_price": 50.0,
        "sl": 45.0, "tp": 60.0, "margin": 10, "leverage": 3,
        "notional": 30, "opened_at": time.time(), "entry_fee": 0.03,
        "book": "realistic",
    }
    pe._latest_price["ETH-USDT"] = 55.0
    await pe._execute_decision({"action": "close", "symbol": "ETH-USDT"})  # idealized has none
    assert "ETH-USDT" not in pe._books["realistic"]["positions"]  # realistic closed normally


@pytest.mark.asyncio
async def test_unfilled_realistic_leaves_idealized_to_self_sl(monkeypatch):
    pe = _mk({})
    pe._latest_price["NEAR-USDT"] = 2.40
    pe._latest_tick_ts["NEAR-USDT"] = time.time()
    plan = {"order_type": "limit", "entry_zone": [2.35, 2.36], "limit_no_fallback": True,
            "limit_timeout_sec": 1800, "size_usdt": 30, "leverage": 5,
            "stop_loss": 2.52, "tp_levels": [2.10]}
    await pe._execute_decision({"action": "open_short", "symbol": "NEAR-USDT",
                                "confidence": 99, "plan": plan, "request_id": "r9"})
    # idealized short opened at market; realistic pending
    assert pe._books["idealized"]["positions"]["NEAR-USDT"]["side"] == "short"
    # idealized self SL hit (short SL above) closes only idealized
    await pe.on_message({"type": "price_tick", "symbol": "NEAR-USDT",
                         "payload": {"symbol": "NEAR-USDT", "price": 2.55}})
    assert "NEAR-USDT" not in pe._books["idealized"]["positions"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_paper_dual_track.py -k "both_books or strategy_close or noop or unfilled_realistic" -q`
Expected: FAIL (idealized not closed on tick / close not mirrored)

- [ ] **Step 3: Implement per-book tick + mirrored exits**

In `on_message` price_tick branch (line 96), check both books:
```python
                await self._check_sl_tp(symbol, price_f, book="realistic")
                if self.dual_track_enabled:
                    await self._check_sl_tp(symbol, price_f, book="idealized")
```

In `_execute_decision`, mirror close/reduce/add into idealized. After the realistic close handling (line 159) and the realistic add handling (line 148), add idealized mirroring. Concretely, restructure the `close` branch:
```python
        elif action == 'close':
            if norm_symbol in self._pending_limits:
                self._pending_limits.pop(norm_symbol, None)
                self.logger.info(f"[PAPER] {norm_symbol} close 取消 pending limit")
                # fall through to idealized mirror below (idealized may hold a position)
            elif position is not None:
                if size_pct < 1.0 and source == 'position_analyst':
                    await self._reduce_paper(norm_symbol, size_pct, position)
                else:
                    await self._close_paper(norm_symbol, position, reason='signal_close')
            # mirror into idealized book
            ideal_pos = self._books["idealized"]["positions"].get(norm_symbol)
            if self.dual_track_enabled and ideal_pos is not None:
                if size_pct < 1.0 and source == 'position_analyst':
                    await self._reduce_paper(norm_symbol, size_pct, ideal_pos, book="idealized")
                else:
                    await self._close_paper(norm_symbol, ideal_pos, reason='signal_close', book="idealized")
            return
```
And mirror the `add` branch (line 146-148):
```python
        elif action in ('open_long', 'open_short') and position is not None:
            if source == 'position_analyst':
                await self._add_paper(norm_symbol, action, size_pct, position)
                ideal_pos = self._books["idealized"]["positions"].get(norm_symbol)
                if self.dual_track_enabled and ideal_pos is not None:
                    await self._add_paper(norm_symbol, action, size_pct, ideal_pos, book="idealized")
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_paper_dual_track.py -q`
Expected: PASS

- [ ] **Step 5: Full paper regression**

Run: `python3 -m pytest tests/test_paper_limit_fill.py tests/test_paper_dual_track.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agents/trading/paper_executor.py tests/test_paper_dual_track.py
git commit -m "feat(paper): per-book SL/TP and mirror strategy close/reduce/add to idealized"
```

archived-with: 2026-06-10-paper-dual-track-sim
---

## Task 7: Comparison reader `compute_gap`

Pure function computing per-book metrics + `limit_discipline_value` from trade records.

**Files:**
- Create: `agents/trading/paper_dual_track_report.py`
- Test: `tests/test_paper_dual_track_report.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paper_dual_track_report.py
from agents.trading.paper_dual_track_report import compute_gap


def _t(book, net, closed_at=0.0):
    return {"book": book, "net_pnl": net, "closed_at": closed_at}


def test_gap_basic_metrics_and_sign():
    trades = [
        _t("realistic", 5.0), _t("realistic", -2.0),
        _t("idealized", 1.0), _t("idealized", -8.0),
    ]
    g = compute_gap(trades, window_days=None, min_trades=1)
    assert g["realistic"]["n"] == 2
    assert g["realistic"]["total_net_pnl"] == 3.0
    assert g["idealized"]["total_net_pnl"] == -7.0
    assert g["limit_discipline_value"] == 10.0   # 3 - (-7); limit discipline helps
    assert g["low_sample"] is False


def test_missing_book_field_counts_as_realistic():
    g = compute_gap([{"net_pnl": 4.0}], window_days=None, min_trades=1)
    assert g["realistic"]["n"] == 1 and g["realistic"]["total_net_pnl"] == 4.0


def test_low_sample_flagged():
    g = compute_gap([_t("realistic", 1.0)], window_days=None, min_trades=5)
    assert g["low_sample"] is True


def test_win_pct_and_drawdown():
    trades = [_t("realistic", 10.0, 1), _t("realistic", -4.0, 2), _t("realistic", -3.0, 3)]
    g = compute_gap(trades, window_days=None, min_trades=1)
    assert g["realistic"]["win_pct"] == pytest.approx(33.33, abs=0.1)
    # equity path 0->10->6->3 ; peak 10 ; max drawdown 7
    assert g["realistic"]["max_drawdown"] == pytest.approx(7.0)


import pytest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_paper_dual_track_report.py -q`
Expected: FAIL (module does not exist)

- [ ] **Step 3: Implement `compute_gap`**

```python
# agents/trading/paper_dual_track_report.py
"""Paper dual-track comparison: realistic vs idealized gap.

Pure functions over paper_trades.jsonl records. No agent, no bus, paper-only.
Never feeds live Reviewer metrics.
"""
import json
import time
from typing import Optional

PAPER_TRADES_FILE = "data/paper_trades.jsonl"


def _book_of(rec: dict) -> str:
    return rec.get("book", "realistic")  # legacy default


def _metrics(trades: list) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "win_pct": 0.0, "avg_net_pnl": 0.0,
                "total_net_pnl": 0.0, "max_drawdown": 0.0}
    pnls = [float(t.get("net_pnl", 0.0)) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    # max drawdown over the cumulative equity path
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in sorted(trades, key=lambda t: t.get("closed_at", 0.0)):
        cum += float(p.get("net_pnl", 0.0))
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return {
        "n": n,
        "win_pct": round(100.0 * wins / n, 2),
        "avg_net_pnl": round(total / n, 4),
        "total_net_pnl": round(total, 4),
        "max_drawdown": round(max_dd, 4),
    }


def compute_gap(trades: list, window_days: Optional[float] = None,
                min_trades: int = 10) -> dict:
    if window_days is not None:
        cutoff = time.time() - window_days * 86400
        trades = [t for t in trades if float(t.get("closed_at", 0.0)) >= cutoff]
    realistic = [t for t in trades if _book_of(t) == "realistic"]
    idealized = [t for t in trades if _book_of(t) == "idealized"]
    rm = _metrics(realistic)
    im = _metrics(idealized)
    return {
        "realistic": rm,
        "idealized": im,
        "limit_discipline_value": round(rm["total_net_pnl"] - im["total_net_pnl"], 4),
        "low_sample": rm["n"] < min_trades or im["n"] < min_trades,
        "window_days": window_days,
    }


def load_trades(path: str = PAPER_TRADES_FILE) -> list:
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        return []
    return rows


def format_gap(gap: dict) -> str:
    """Human-readable summary for logs / Telegram."""
    r, i = gap["realistic"], gap["idealized"]
    ldv = gap["limit_discipline_value"]
    verdict = "限价纪律净赚" if ldv > 0 else ("限价纪律净亏" if ldv < 0 else "持平")
    lines = [
        "📊 Paper 双轨对比 (realistic vs idealized)",
        f"realistic: n={r['n']} 胜率{r['win_pct']}% 总PnL{r['total_net_pnl']:+} 回撤{r['max_drawdown']}",
        f"idealized: n={i['n']} 胜率{i['win_pct']}% 总PnL{i['total_net_pnl']:+} 回撤{i['max_drawdown']}",
        f"limit_discipline_value = {ldv:+} ({verdict})",
    ]
    if gap["low_sample"]:
        lines.append("⚠️ 样本不足，误差大，仅供参考")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_paper_dual_track_report.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/paper_dual_track_report.py tests/test_paper_dual_track_report.py
git commit -m "feat(paper): add compute_gap reader for realistic vs idealized comparison"
```

archived-with: 2026-06-10-paper-dual-track-sim
---

## Task 8: Telegram `/paper_gap` command + periodic log

Surface the gap. `/paper_gap [days]` reads the ledger and replies; `tick()` logs the gap periodically.

**Files:**
- Modify: `agents/trading/telegram_notifier.py` — command dispatch (find `handlers` / `handlers_with_args` per CLAUDE.md TG command pattern)
- Modify: `agents/trading/paper_executor.py` — `tick()` (636-645)
- Test: `tests/test_paper_dual_track_report.py` (command handler unit), reuse existing TG test patterns

- [ ] **Step 1: Write the failing test**

```python
def test_format_gap_contains_verdict_and_low_sample():
    from agents.trading.paper_dual_track_report import compute_gap, format_gap
    g = compute_gap([{"book": "realistic", "net_pnl": 3.0}], window_days=None, min_trades=5)
    out = format_gap(g)
    assert "limit_discipline_value" in out
    assert "样本不足" in out  # low_sample path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_paper_dual_track_report.py -k format_gap -q`
Expected: FAIL if `format_gap` not yet covered (it exists from Task 7 — this asserts the low-sample line). If green already, proceed.

- [ ] **Step 3: Wire `/paper_gap` into Telegram and periodic log**

Locate the command registration in `agents/trading/telegram_notifier.py` (grep `handlers_with_args` and the `_handle_command` dispatch added for `/resume_symbol` / `/pnl` per CLAUDE.md). Add a `/paper_gap` handler that takes an optional days arg:
```python
    async def _handle_paper_gap(self, args: list) -> str:
        from agents.trading.paper_dual_track_report import load_trades, compute_gap, format_gap
        days = None
        if args:
            try:
                days = float(args[0])
            except ValueError:
                return "用法: /paper_gap [天数]"
        gap = compute_gap(load_trades(), window_days=days, min_trades=10)
        return format_gap(gap)
```
Register `paper_gap` in the `handlers_with_args` set and the dispatch map exactly as the existing `resume_symbol`/`pnl` commands do (follow that pattern; do not invent a new dispatch mechanism).

In `PaperExecutor.tick()` (after line 644), add a periodic gap log (every ~5 min, reuse the existing `int(time.time()) % 300 < 30` gate already present):
```python
        if self.dual_track_enabled and int(time.time()) % 300 < 30:
            try:
                from agents.trading.paper_dual_track_report import load_trades, compute_gap, format_gap
                self.logger.info(format_gap(compute_gap(load_trades(), window_days=7, min_trades=10)))
            except Exception as e:
                self.logger.debug(f"[PaperExecutor] gap log skipped: {e}")
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_paper_dual_track_report.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/telegram_notifier.py agents/trading/paper_executor.py tests/test_paper_dual_track_report.py
git commit -m "feat(tg): add /paper_gap command and periodic paper dual-track gap log"
```

archived-with: 2026-06-10-paper-dual-track-sim
---

## Task 9: Paper/live isolation guard test

Assert idealized data never reaches a live Reviewer metric.

**Files:**
- Test: `tests/test_paper_dual_track.py`

- [ ] **Step 1: Write the failing/﻿guard test**

```python
def test_reviewer_does_not_consume_idealized_or_paper():
    # Reviewer must not subscribe to paper streams nor read idealized files.
    import inspect
    from agents.trading import reviewer as rv
    src = inspect.getsource(rv)
    assert "paper_execution_result" not in src
    assert "paper_positions_idealized" not in src
    assert "book='idealized'" not in src and 'book="idealized"' not in src
```

- [ ] **Step 2: Run test**

Run: `python3 -m pytest tests/test_paper_dual_track.py -k isolation -q`
Expected: PASS (reviewer is paper-agnostic today; this locks it in)

- [ ] **Step 3: Commit**

```bash
git add tests/test_paper_dual_track.py
git commit -m "test(paper): lock paper/live isolation — reviewer never consumes idealized"
```

archived-with: 2026-06-10-paper-dual-track-sim
---

## Task 10: Full verification + baseline

**Files:** none (verification only)

- [ ] **Step 1: Run targeted dual-track suite**

Run: `python3 -m pytest tests/test_paper_dual_track.py tests/test_paper_dual_track_report.py tests/test_paper_limit_fill.py -q`
Expected: PASS

- [ ] **Step 2: Run full regression and record the new baseline**

Run: `python3 -m pytest -q`
Expected: PASS; record the new `N passed / 4 deselected / 1 warning` count for CLAUDE.md / to-do.

- [ ] **Step 3: Compileall**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q agents/ utils/`
Expected: exit 0

- [ ] **Step 4: Check off tasks.md**

Mark all items in `openspec/changes/paper-dual-track-sim/tasks.md` as `[x]`.

- [ ] **Step 5: Commit**

```bash
git add openspec/changes/paper-dual-track-sim/tasks.md
git commit -m "chore(paper): mark dual-track tasks complete + record baseline"
```

archived-with: 2026-06-10-paper-dual-track-sim
---

## Self-Review Notes

- **Spec coverage:** paper-dual-track requirements → Tasks 5 (idealized open), 6 (mirror exits + per-book SL/TP + unfilled case), 3 (book records / legacy), 7 (comparison + low_sample), 4/8 (toggle + surface); paper-executor delta → Tasks 1-3 (book-parameterized paths, persistence, legacy load), 6 (per-book SL/TP), 8 (paper_execution_result book tag is set in Task 2 publish edits). Isolation requirement → Task 9.
- **Realistic regression:** every code task re-runs `tests/test_paper_limit_fill.py`; Task 10 runs full suite. Proxy properties (Task 1) keep untouched realistic references and telegram reader working.
- **Type consistency:** `book` param name and the `{"positions","equity"}` book dict shape are used identically across Tasks 1-8; `_book_of`/`compute_gap`/`format_gap`/`load_trades` signatures are stable across Tasks 7-8.
- **No placeholders:** all code steps contain runnable code; the only deliberate lookup is the exact TG `handlers_with_args` registration site in Task 8 (follow the existing `/resume_symbol`/`/pnl` pattern documented in CLAUDE.md).
