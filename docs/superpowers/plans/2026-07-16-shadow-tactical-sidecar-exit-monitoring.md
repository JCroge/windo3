---
change: shadow-tactical-sidecar-exit-monitoring
design-doc: docs/superpowers/specs/2026-07-16-shadow-tactical-sidecar-exit-monitoring-design.md
base-ref: 3a3b003dea6a3ca0c57f162817db6a5ffc7e8ead
---

# Shadow Tactical Sidecar Exit Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Shadow Tactical sidecar manage its own open Tactical positions after entry, with canonical symbol ownership and shared Tactical exit semantics.

**Architecture:** Keep sidecar as a separate process and add one monitor pass to its existing loop. `utils.shadow_tactical_live` owns pure symbol/registry helpers, `executor.py` owns the shared Tactical exit evaluator and execution primitives, and `scripts/shadow_tactical_live_sidecar.py` binds them together for poll, reduce, close, and stop.

**Tech Stack:** Python 3, pytest, existing `ContractExecutor`, JSON owner registry/state files, OKX ccxt-compatible symbols, OpenSpec/Comet artifacts.

---

## File Structure

- Modify `utils/shadow_tactical_live.py`: add internal/exchange symbol canonicalization, owner-row migration, and ownership proof helpers.
- Modify `executor.py`: resolve sidecar opens to exchange swap symbols, persist internal symbol metadata, and extract a reusable local exit evaluator behind `check_stop_loss_take_profit()`.
- Modify `scripts/shadow_tactical_live_sidecar.py`: add sidecar monitor pass and reuse the same proven-owner drain logic for stop.
- Modify `tests/test_shadow_tactical_live_core.py`: symbol canonicalization, owner migration, and guard tests.
- Modify `tests/test_shadow_tactical_live_executor.py`: sidecar open uses swap execution symbol and persists internal symbol.
- Modify `tests/test_shadow_tactical_live_cli.py`: monitor-loop and stop/drain tests.
- Create `tests/test_shadow_tactical_exit_monitoring.py`: Tactical exit evaluator tests for TP1, TP2, invalidation, weakened-no-progress, and max hold.
- Update `openspec/changes/shadow-tactical-sidecar-exit-monitoring/tasks.md` as tasks complete.

## Task 1: Canonical Sidecar Symbols and Owner Rows

**Files:**
- Modify: `utils/shadow_tactical_live.py`
- Modify: `executor.py`
- Modify: `tests/test_shadow_tactical_live_core.py`
- Modify: `tests/test_shadow_tactical_live_executor.py`

- [ ] **Step 1: Write failing tests for symbol canonicalization and owner migration**

Add tests like:

```python
from utils.shadow_tactical_live import canonical_sidecar_symbols


def test_canonical_sidecar_symbols_split_internal_and_exchange():
    assert canonical_sidecar_symbols("ONDO-USDT") == {
        "internal_symbol": "ONDO-USDT",
        "exchange_symbol": "ONDO-USDT-SWAP",
    }
    assert canonical_sidecar_symbols("ONDO/USDT:USDT") == {
        "internal_symbol": "ONDO-USDT",
        "exchange_symbol": "ONDO-USDT-SWAP",
    }


def test_owner_registry_migrates_legacy_symbol_rows(tmp_path):
    path = tmp_path / "owners.json"
    path.write_text('{"owners":{"s1":{"shadow_id":"s1","symbol":"ONDO-USDT-SWAP","side":"long","status":"open"}}}')
    row = ShadowTacticalOwnerRegistry(str(path)).active_for("ONDO-USDT", "long")
    assert row["internal_symbol"] == "ONDO-USDT"
    assert row["exchange_symbol"] == "ONDO-USDT-SWAP"
```

Extend `test_open_sidecar_plan_places_order_without_drift_gate()`:

```python
pos = ex.open_sidecar_plan(_plan(symbol="ONDO-USDT"), size_usdt=30.0)
assert pos["symbol"] == "ONDO-USDT-SWAP"
assert pos["internal_symbol"] == "ONDO-USDT"
ex.exchange.fetch_ticker.assert_called_with("ONDO-USDT-SWAP")
```

- [ ] **Step 2: Run tests and verify they fail for missing canonical fields**

Run:

```bash
pytest tests/test_shadow_tactical_live_core.py::test_canonical_sidecar_symbols_split_internal_and_exchange tests/test_shadow_tactical_live_core.py::test_owner_registry_migrates_legacy_symbol_rows tests/test_shadow_tactical_live_executor.py::test_open_sidecar_plan_places_order_without_drift_gate -q
```

Expected: failures because `canonical_sidecar_symbols`, owner-row migration, and internal symbol persistence are not implemented.

- [ ] **Step 3: Implement canonical symbol helpers and owner-row migration**

Implement in `utils/shadow_tactical_live.py`:

```python
from utils.symbol import to_internal, to_okx_inst


def canonical_sidecar_symbols(symbol: str) -> dict:
    internal = to_internal(symbol)
    return {"internal_symbol": internal, "exchange_symbol": to_okx_inst(internal)}
```

Update registry load/record/match helpers so rows always carry `symbol`, `internal_symbol`, and `exchange_symbol`, with `symbol` retained as the exchange symbol for backward compatibility.

- [ ] **Step 4: Resolve sidecar opens to exchange symbols**

In `ContractExecutor.open_sidecar_plan()`:

```python
symbols = canonical_sidecar_symbols(plan["symbol"])
internal_symbol = plan.get("internal_symbol") or symbols["internal_symbol"]
symbol = plan.get("exchange_symbol") or symbols["exchange_symbol"]
```

Use `symbol` for all exchange calls and position keying. Persist `internal_symbol` and `sidecar_source` into the position.

- [ ] **Step 5: Re-run focused tests and commit**

Run:

```bash
pytest tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_executor.py -q
```

Commit:

```bash
git add utils/shadow_tactical_live.py executor.py tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_executor.py
git commit -m "feat: canonicalize shadow tactical sidecar symbols"
```

## Task 2: Shared Tactical Exit Evaluator

**Files:**
- Modify: `executor.py`
- Create: `tests/test_shadow_tactical_exit_monitoring.py`

- [ ] **Step 1: Write failing Tactical exit evaluator tests**

Create tests that instantiate `ContractExecutor.__new__(ContractExecutor)` and bind a local position:

```python
def test_tactical_tp1_returns_reduce_trigger():
    ex = _executor_with_position(price=1.32, tp_filled=0)
    assert ex.check_stop_loss_take_profit("ONDO-USDT-SWAP") == "tactical_tp1"


def test_tactical_tp2_returns_second_reduce_trigger():
    ex = _executor_with_position(price=1.38, tp_filled=1)
    assert ex.check_stop_loss_take_profit("ONDO-USDT-SWAP") == "partial_tp_2"


def test_tactical_max_hold_returns_close_trigger():
    ex = _executor_with_position(price=1.26, open_age_minutes=91)
    assert ex.check_stop_loss_take_profit("ONDO-USDT-SWAP") == "tactical_max_hold"
```

Include invalidated and weakened-no-progress cases by setting `tactical_thesis_state` and `tactical_last_progress_time`.

- [ ] **Step 2: Run tests and verify TP2/helper behavior fails**

Run:

```bash
pytest tests/test_shadow_tactical_exit_monitoring.py -q
```

Expected: at least TP2 fails because current Tactical branch only handles TP1 and close conditions inline.

- [ ] **Step 3: Extract shared evaluator behind the existing wrapper**

Move the post-price-fetch decision logic from `check_stop_loss_take_profit()` into a method such as:

```python
def _evaluate_local_exit_trigger(self, symbol: str, position: dict, current_price: float, *, now: float | None = None) -> Optional[str]:
    ...
```

Keep `check_stop_loss_take_profit()` responsible for robust price fetching, failure counting, and extrema updates before calling the helper.

- [ ] **Step 4: Add Tactical TP2 to the Tactical branch**

When `position["track"] == "tactical"` and `tp_filled == 1`, return `partial_tp_2` when TP2 is reached and set `position["tactical_close_reason"] = "tactical_tp2"`.

- [ ] **Step 5: Re-run focused tests and commit**

Run:

```bash
pytest tests/test_shadow_tactical_exit_monitoring.py test_partial_tp_lifecycle.py -q
```

Commit:

```bash
git add executor.py tests/test_shadow_tactical_exit_monitoring.py
git commit -m "feat: share tactical exit evaluation"
```

## Task 3: Sidecar Monitor Loop

**Files:**
- Modify: `scripts/shadow_tactical_live_sidecar.py`
- Modify: `tests/test_shadow_tactical_live_cli.py`

- [ ] **Step 1: Write failing monitor tests**

Add tests that import the script module and use a fake executor:

```python
def test_monitor_reduces_sidecar_tactical_tp1(tmp_path):
    paths = mod.SidecarPaths(owners=str(tmp_path / "owners.json"), audit=str(tmp_path / "audit.jsonl"))
    reg = mod.ShadowTacticalOwnerRegistry(paths.owners)
    reg.record_open("s1", "ONDO-USDT-SWAP", "long", 30.0, "ord", "cl", "algo", "slcl")
    fake = MagicMock()
    fake.positions = {"ONDO-USDT-SWAP": {"symbol": "ONDO-USDT-SWAP", "internal_symbol": "ONDO-USDT", "side": "long", "shadow_id": "s1", "sidecar_source": "shadow_tactical_live"}}
    fake.check_stop_loss_take_profit.return_value = "tactical_tp1"

    result = mod.monitor_sidecar_owned_exposure(paths, fake)

    fake.reduce_position.assert_called_once_with("ONDO-USDT-SWAP", 0.5, tp_advance=1, action_kind="sidecar_tactical_tp1")
    assert result["reduced"] == 1
```

Add a second test for no new events in `cmd_run(... --once ...)` with an existing owner row to prove idle monitoring runs.

- [ ] **Step 2: Run tests and verify monitor function is missing**

Run:

```bash
pytest tests/test_shadow_tactical_live_cli.py::test_monitor_reduces_sidecar_tactical_tp1 -q
```

Expected: failure because `monitor_sidecar_owned_exposure` does not exist.

- [ ] **Step 3: Implement monitor and trigger routing**

Add `monitor_sidecar_owned_exposure(paths, executor) -> dict` that:
- loads owner rows
- skips non-open rows
- proves row to local position
- calls `executor.check_stop_loss_take_profit(exchange_symbol)`
- routes `tactical_tp1` to `reduce_position(..., 0.5, tp_advance=1)`
- routes `partial_tp_2` to `reduce_position(..., 0.25, tp_advance=2)`
- routes close triggers to `close_position(..., action_kind=f"sidecar_{trigger}")`
- writes audit rows for reduce, close, skip, and failure

- [ ] **Step 4: Call monitor from `cmd_run()`**

After processing event rows in the run loop, call the monitor once per poll when not in dry-run mode. Keep `--once` semantics: process events, run one monitor pass, then exit.

- [ ] **Step 5: Re-run focused tests and commit**

Run:

```bash
pytest tests/test_shadow_tactical_live_cli.py -q
```

Commit:

```bash
git add scripts/shadow_tactical_live_sidecar.py tests/test_shadow_tactical_live_cli.py
git commit -m "feat: monitor shadow tactical sidecar exits"
```

## Task 4: Stop Reuse, OpenSpec Tasks, and Verification

**Files:**
- Modify: `scripts/shadow_tactical_live_sidecar.py`
- Modify: `openspec/changes/shadow-tactical-sidecar-exit-monitoring/tasks.md`

- [ ] **Step 1: Refactor stop to reuse proven-owner drain logic**

Replace the duplicate proof/close loop in `stop_sidecar_owned_exposure()` with the shared proven-owner helper used by the monitor. Stop should still cancel the recorded SL algo before closing and should keep skip behavior for unproven rows.

- [ ] **Step 2: Run sidecar regression suite**

Run:

```bash
pytest tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_executor.py tests/test_shadow_tactical_live_cli.py tests/test_shadow_tactical_owner_isolation.py tests/test_shadow_tactical_exit_monitoring.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Update OpenSpec tasks**

Mark completed checkboxes in:

```text
openspec/changes/shadow-tactical-sidecar-exit-monitoring/tasks.md
```

- [ ] **Step 4: Run broader executor safety tests**

Run:

```bash
pytest test_partial_tp_lifecycle.py test_executor_terminal_result.py test_owner_tag_clord_id_callsites.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit final task updates**

Commit:

```bash
git add scripts/shadow_tactical_live_sidecar.py openspec/changes/shadow-tactical-sidecar-exit-monitoring/tasks.md
git commit -m "test: verify shadow tactical sidecar exit monitoring"
```
