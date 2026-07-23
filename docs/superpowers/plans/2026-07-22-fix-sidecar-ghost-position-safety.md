---
change: fix-sidecar-ghost-position-safety
design-doc: docs/superpowers/specs/2026-07-22-fix-sidecar-ghost-position-safety-design.md
base-ref: fee8268f9c68e37a9733f7102556692ee53de2af
archived-with: 2026-07-23-fix-sidecar-ghost-position-safety
---

# Sidecar Ghost Position Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the ADA-class sidecar ghost-position failure by preserving protection, blocking same-symbol sidecar stacking, failing closed on unproven present exposure, and adding stale-entry drift protection.

**Architecture:** Keep the fix conservative: do not add aggregate sidecar positions. Main migration preserves ambiguous protection for sidecar-owned present/unknown exposure; sidecar admission blocks new same-symbol stacks; monitor turns unproven present exposure into a fail-closed ghost state; sidecar open reuses the existing drift classifier through a narrow pre-order guard.

**Tech Stack:** Python 3, pytest, existing `ContractExecutor`, `ShadowTacticalOwnerRegistry`, `SidecarPaths`, JSON owner/state files, OKX ccxt-compatible position/algo structures.

## File Structure

- Modify `executor.py`
  - Add a sidecar-owned symbol state helper for migration.
  - Preserve ambiguous/manual protection during `_migrate_okx_algos_for_symbol()`.
  - Add sidecar drift precheck helpers used by `open_sidecar_plan()`.
- Modify `utils/shadow_tactical_live.py`
  - Add explicit active same-symbol owner detection for sidecar admission.
  - Change same-symbol guard so sidecar-owned exposure no longer permits stacking in `net_mode`.
- Modify `scripts/shadow_tactical_live_sidecar.py`
  - Add ghost exposure audit and fail-closed monitor path.
  - Add ambiguous same-symbol stack detection before reduce/close actions.
  - Record admission rejection reasons for new sidecar guard outcomes.
- Modify `tests/test_shadow_tactical_owner_isolation.py`
  - Add Main migration preservation regressions.
- Modify `tests/test_shadow_tactical_live_core.py`
  - Update same-symbol guard tests from old allow-stacking behavior to new block-stacking behavior.
- Modify `tests/test_shadow_tactical_exit_monitoring.py`
  - Add ghost exposure and ambiguous stacked-owner monitor regressions.
- Modify `tests/test_shadow_tactical_live_executor.py`
  - Replace the old `without_drift_gate` expectation with sidecar drift guard tests.
- Modify `openspec/changes/fix-sidecar-ghost-position-safety/tasks.md`
  - Check off completed tasks as implementation lands.
- Create or update `docs/superpowers/reports/2026-07-22-fix-sidecar-ghost-position-safety-verify.md`
  - Record verification commands and results.

## Task 1: Main Migration Preserves Sidecar-Owned Protection

**Files:**
- Modify: `tests/test_shadow_tactical_owner_isolation.py`
- Modify: `executor.py`
- Modify: `openspec/changes/fix-sidecar-ghost-position-safety/tasks.md`

- [ ] **Step 1: Add failing manual OCO preservation regression**

Append this test to `tests/test_shadow_tactical_owner_isolation.py`:

```python
def test_migration_preserves_manual_oco_for_sidecar_owned_present_exposure():
    ex = _executor()
    ex.positions = {}
    ex._list_pending_algos = MagicMock(
        return_value=[
            {
                "algoId": "manual-oco-ada",
                "algoClOrdId": "manual-okx-ui",
                "sl_trigger": "0.168",
                "tp_trigger": "0.180",
                "ordType": "oco",
            }
        ]
    )
    ex._is_sidecar_owned_algo_clord_id = MagicMock(return_value=False)
    ex._is_foreign_owner_clord_id = MagicMock(return_value=False)
    ex._sidecar_symbol_exchange_state = MagicMock(return_value="present")
    ex._cancel_algo_by_id = MagicMock()

    summary = ex._migrate_okx_algos_for_symbol("ADA-USDT-SWAP")

    assert summary["orphan_sl"] == 0
    assert summary["sidecar_protected_algos"] == 1
    ex._cancel_algo_by_id.assert_not_called()
```

- [ ] **Step 2: Add failing manual conditional SL preservation regression**

Append this test next to the OCO test:

```python
def test_migration_preserves_manual_conditional_sl_for_sidecar_owned_unknown_exposure():
    ex = _executor()
    ex.positions = {}
    ex._list_pending_algos = MagicMock(
        return_value=[
            {
                "algoId": "manual-sl-ada",
                "algoClOrdId": "manual-sl",
                "sl_trigger": "0.168",
                "tp_trigger": "",
                "ordType": "conditional",
            }
        ]
    )
    ex._is_sidecar_owned_algo_clord_id = MagicMock(return_value=False)
    ex._is_foreign_owner_clord_id = MagicMock(return_value=False)
    ex._sidecar_symbol_exchange_state = MagicMock(return_value="unknown")
    ex._cancel_algo_by_id = MagicMock()

    summary = ex._migrate_okx_algos_for_symbol("ADA-USDT-SWAP")

    assert summary["orphan_sl"] == 0
    assert summary["sidecar_protected_algos"] == 1
    ex._cancel_algo_by_id.assert_not_called()
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```bash
pytest tests/test_shadow_tactical_owner_isolation.py::test_migration_preserves_manual_oco_for_sidecar_owned_present_exposure tests/test_shadow_tactical_owner_isolation.py::test_migration_preserves_manual_conditional_sl_for_sidecar_owned_unknown_exposure -q
```

Expected: FAIL because `_migrate_okx_algos_for_symbol()` has no `sidecar_protected_algos` summary field and still cancels orphan SL/OCO when `position is None`.

- [ ] **Step 4: Add sidecar exchange state helper and preservation branch**

In `executor.py`, extend the migration summary in `_migrate_okx_algos_for_symbol()`:

```python
        summary = {
            'symbol': symbol,
            'cancelled_tp': 0,
            'matched_sl': None,
            'orphan_sl': 0,
            'missing_sl': False,
            'halted': False,
            'oco_replaced': 0,
            'foreign_algos': 0,
            'sidecar_protected_algos': 0,
        }
```

Add this helper near `_is_sidecar_owned_algo_clord_id()`:

```python
    def _sidecar_symbol_exchange_state(self, symbol: str) -> str:
        owners = self._load_sidecar_owner_registry()
        if owners is None:
            return "none"

        sides = ("long", "short")
        try:
            has_owner = any(owners.matches_position(symbol, side) for side in sides)
        except Exception as e:
            self.logger.warning(f"[SidecarOwner] symbol state lookup failed: {e}")
            return "unknown"
        if not has_owner:
            return "none"

        try:
            ex_pos = self._fetch_okx_position_state(symbol)
        except Exception as e:
            self.logger.warning(f"[SidecarOwner] exchange state lookup failed: {e}")
            return "unknown"
        return "present" if ex_pos is not None else "flat"
```

Then change the `if position is None:` branch:

```python
        if position is None:
            sidecar_state = self._sidecar_symbol_exchange_state(symbol)
            if sidecar_state in ("present", "unknown"):
                for algo in sl_algos + oco_algos:
                    summary["sidecar_protected_algos"] += 1
                    self.logger.warning(
                        f"[Migrate] {symbol} preserve ambiguous protection "
                        f"{algo['algoId']} for sidecar-owned exposure "
                        f"(exchange_state={sidecar_state}, ordType={algo.get('ordType')})"
                    )
                return summary

            for algo in sl_algos + oco_algos:
                if self._cancel_algo_by_id(symbol, algo['algoId']):
                    summary['orphan_sl'] += 1
                    self.logger.info(
                        f"[Migrate] {symbol} 无本地仓位,撤残留 algo "
                        f"{algo['algoId']} (ordType={algo.get('ordType')})"
                    )
            return summary
```

- [ ] **Step 5: Update non-OKX expected summary**

In `test_non_okx_returns_empty_summary()`, add:

```python
            'sidecar_protected_algos': 0,
```

- [ ] **Step 6: Run migration tests**

Run:

```bash
pytest tests/test_shadow_tactical_owner_isolation.py test_partial_tp_lifecycle.py::TestAlgoMigration -q
```

Expected: PASS.

- [ ] **Step 7: Check off matching OpenSpec tasks and commit**

Update `openspec/changes/fix-sidecar-ghost-position-safety/tasks.md`:

```markdown
- [x] 1.1 Add a Main migration regression proving manual OCO/conditional protection is preserved when a symbol is sidecar-owned and exchange exposure is present or unknown.
- [x] 2.1 Extend Main sidecar-owner lookup or migration context so `_migrate_okx_algos_for_symbol()` can determine sidecar-owned present/unknown exchange exposure.
- [x] 2.2 Preserve ambiguous/manual OCO and conditional TP/SL algos for sidecar-owned present/unknown exposure instead of canceling them as orphan residuals.
- [x] 2.3 Keep existing exchange-flat or non-sidecar orphan cleanup behavior intact.
```

Commit:

```bash
git add executor.py tests/test_shadow_tactical_owner_isolation.py test_partial_tp_lifecycle.py openspec/changes/fix-sidecar-ghost-position-safety/tasks.md
git commit -m "fix: preserve protection for sidecar-owned exposure"
```

## Task 2: Sidecar Admission Blocks Same-Symbol Stacking

**Files:**
- Modify: `tests/test_shadow_tactical_live_core.py`
- Modify: `utils/shadow_tactical_live.py`
- Modify: `scripts/shadow_tactical_live_sidecar.py`
- Modify: `openspec/changes/fix-sidecar-ghost-position-safety/tasks.md`

- [ ] **Step 1: Replace old allow-stacking expectations**

In `tests/test_shadow_tactical_live_core.py`, rename `test_same_symbol_guard_ignores_sidecar_owned_exposure` to:

```python
def test_same_symbol_guard_blocks_sidecar_owned_exposure(tmp_path):
```

Change the assertions:

```python
    assert blocked is True
    assert reason == "same_symbol_sidecar_active"
```

Rename `test_same_symbol_guard_understands_internal_sidecar_rows` to:

```python
def test_same_symbol_guard_blocks_internal_sidecar_rows(tmp_path):
```

Change the assertions:

```python
    assert blocked is True
    assert reason == "same_symbol_sidecar_active"
```

- [ ] **Step 2: Run renamed tests and verify they fail**

Run:

```bash
pytest tests/test_shadow_tactical_live_core.py::test_same_symbol_guard_blocks_sidecar_owned_exposure tests/test_shadow_tactical_live_core.py::test_same_symbol_guard_blocks_internal_sidecar_rows -q
```

Expected: FAIL because `blocks_same_symbol_account_exposure()` currently continues when owners match.

- [ ] **Step 3: Add registry active-owner helper**

In `utils/shadow_tactical_live.py`, add:

```python
    def has_active_owner(self, symbol: str, side: str) -> bool:
        return self.active_for(symbol, side) is not None
```

Then update `matches_position()` to keep existing sync semantics unchanged:

```python
    def matches_position(self, symbol: str, side: str) -> bool:
        return self.active_for(symbol, side) is not None
```

- [ ] **Step 4: Tighten same-symbol guard**

Replace the owner-matching branch inside `blocks_same_symbol_account_exposure()`:

```python
        if owners.matches_position(pos.get("symbol", ""), pos_side):
            continue
        return True, "same_symbol_account_exposure"
```

with:

```python
        if owners.has_active_owner(pos.get("symbol", ""), pos_side):
            return True, "same_symbol_sidecar_active"
        return True, "same_symbol_account_exposure"
```

- [ ] **Step 5: Add active owner precheck before exchange positions**

At the start of `blocks_same_symbol_account_exposure()`, after `wanted` is computed, add:

```python
    if owners.has_active_owner(symbol, side):
        return True, "same_symbol_sidecar_active"
```

This covers owner rows even when exchange position fetch returns empty or fails open.

- [ ] **Step 6: Run core tests**

Run:

```bash
pytest tests/test_shadow_tactical_live_core.py -q
```

Expected: PASS.

- [ ] **Step 7: Confirm sidecar process audit already records guard reason**

Read `_process_event()` in `scripts/shadow_tactical_live_sidecar.py` and confirm it appends:

```python
{"shadow_id": shadow_id, "reason": guard_reason}
```

No code change is needed if this remains present. If tests reveal missing attribution, add a focused CLI/core test before changing code.

- [ ] **Step 8: Check off matching OpenSpec tasks and commit**

Update tasks:

```markdown
- [x] 1.2 Add sidecar admission regressions proving same-symbol sidecar opens are rejected in OKX `net_mode` when an active owner or present exchange exposure already exists.
- [x] 3.1 Tighten `blocks_same_symbol_account_exposure()` or sidecar admission logic so active same-symbol sidecar owner rows block new sidecar opens in OKX `net_mode`.
- [x] 3.2 Ensure exchange-present same-symbol exposure blocks sidecar opens unless a future aggregate-position model is explicitly available.
- [x] 3.3 Add sidecar audit rejection reasons for same-symbol active owner and unmodeled exchange exposure.
```

Commit:

```bash
git add utils/shadow_tactical_live.py scripts/shadow_tactical_live_sidecar.py tests/test_shadow_tactical_live_core.py openspec/changes/fix-sidecar-ghost-position-safety/tasks.md
git commit -m "fix: block same-symbol sidecar stacking"
```

## Task 3: Ghost Exposure Monitor Fails Closed

**Files:**
- Modify: `tests/test_shadow_tactical_exit_monitoring.py`
- Modify: `scripts/shadow_tactical_live_sidecar.py`
- Modify: `openspec/changes/fix-sidecar-ghost-position-safety/tasks.md`

- [ ] **Step 1: Add ghost exposure regression**

Append to `tests/test_shadow_tactical_exit_monitoring.py`:

```python
def test_unproven_owner_with_exchange_position_records_ghost_exposure(tmp_path):
    paths = _sidecar_paths(tmp_path)
    _write_open_owner(paths)
    ex = _executor_for_monitor(
        [{"symbol": SYMBOL, "side": "long", "contracts": 1.0}]
    )
    ex._halt_symbol = MagicMock()
    ex._list_pending_algos = MagicMock(return_value=[])

    summary = monitor_sidecar_owned_exposure(paths, ex)

    audit_events = [
        json.loads(line)
        for line in open(paths.audit).read().splitlines()
        if line.strip()
    ]
    assert summary["ghost_exposure"] == 1
    assert audit_events[-1]["event_type"] == "monitor_ghost_exposure"
    assert audit_events[-1]["exchange_state"] == "present"
    assert audit_events[-1]["operator_action_required"] is True
    ex.close_position.assert_not_called()
    ex.reduce_position.assert_not_called()
```

- [ ] **Step 2: Add ambiguous stacked-owner regression**

Append:

```python
def test_monitor_does_not_close_one_row_from_ambiguous_net_mode_stack(tmp_path):
    paths = _sidecar_paths(tmp_path)
    _write_open_owner(paths, shadow_id="owner-1")
    _write_open_owner(paths, shadow_id="owner-2")
    data = json.loads(open(paths.owners).read())
    data["owners"]["owner-1"]["shadow_id"] = "owner-1"
    data["owners"]["owner-2"]["shadow_id"] = "owner-2"
    open(paths.owners, "w").write(json.dumps(data))

    ex = _executor_for_monitor(
        [{"symbol": SYMBOL, "side": "long", "contracts": 2.0}]
    )
    ex.positions[SYMBOL] = {
        "symbol": SYMBOL,
        "internal_symbol": "ONDO-USDT",
        "exchange_symbol": SYMBOL,
        "shadow_id": "owner-2",
        "side": "long",
        "sidecar_source": "shadow_tactical_live",
        "entry_price": 1.25,
        "stop_loss": 1.20,
        "take_profit": 1.32,
    }
    ex.check_stop_loss_take_profit.return_value = "tactical_max_hold"
    ex._list_pending_algos = MagicMock(return_value=[])
    ex._halt_symbol = MagicMock()

    summary = monitor_sidecar_owned_exposure(paths, ex)

    owners = json.loads(open(paths.owners).read())["owners"]
    assert owners["owner-1"]["status"] == "open"
    assert owners["owner-2"]["status"] == "open"
    assert summary["ambiguous_stacks"] == 1
    ex.close_position.assert_not_called()
    ex.reduce_position.assert_not_called()
```

- [ ] **Step 3: Run monitor tests and verify they fail**

Run:

```bash
pytest tests/test_shadow_tactical_exit_monitoring.py::test_unproven_owner_with_exchange_position_records_ghost_exposure tests/test_shadow_tactical_exit_monitoring.py::test_monitor_does_not_close_one_row_from_ambiguous_net_mode_stack -q
```

Expected: FAIL because summary fields and ghost/ambiguous branches do not exist.

- [ ] **Step 4: Add owner grouping and pending protection helper**

In `scripts/shadow_tactical_live_sidecar.py`, add helper functions near `_owner_row_as_close_metadata()`:

```python
def _owner_group_key(row: dict) -> tuple[str, str]:
    return (
        row.get("exchange_symbol") or row.get("symbol") or "",
        row.get("side") or "",
    )


def _open_owner_group_counts(owners: dict) -> dict[tuple[str, str], int]:
    counts = {}
    for row in owners.values():
        if row.get("status") != "open":
            continue
        key = _owner_group_key(row)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _pending_protection_state(executor: ContractExecutor, symbol: str) -> str:
    lister = getattr(executor, "_list_pending_algos", None)
    if not callable(lister):
        return "unknown"
    try:
        algos = lister(symbol)
    except Exception:
        return "unknown"
    for algo in algos or []:
        has_sl = algo.get("sl_trigger") not in (None, "", "0")
        has_tp = algo.get("tp_trigger") not in (None, "", "0")
        if has_sl or has_tp:
            return "present"
    return "absent"
```

- [ ] **Step 5: Extend monitor summary**

In `monitor_sidecar_owned_exposure()`, add:

```python
        "ghost_exposure": 0,
        "ambiguous_stacks": 0,
```

Then compute group counts after loading data:

```python
    owner_group_counts = _open_owner_group_counts(data.get("owners", {}))
```

- [ ] **Step 6: Add ghost exposure branch**

Replace the current unproven present/unknown skip branch:

```python
            summary["skipped"] += 1
            append_audit_event(
                paths.audit,
                "monitor_skipped_unproven",
                {
                    "shadow_id": shadow_id,
                    "symbol": symbol,
                    "exchange_state": exchange_state,
                },
            )
            continue
```

with:

```python
            protection_state = _pending_protection_state(executor, symbol)
            operator_action_required = protection_state in ("absent", "unknown")
            if exchange_state in ("present", "unknown"):
                summary["ghost_exposure"] += 1
                summary["skipped"] += 1
                halt = getattr(executor, "_halt_symbol", None)
                if callable(halt):
                    halt(symbol, reason="sidecar_ghost_exposure")
                append_audit_event(
                    paths.audit,
                    "monitor_ghost_exposure",
                    {
                        "shadow_id": shadow_id,
                        "symbol": symbol,
                        "exchange_state": exchange_state,
                        "unproven_owner": True,
                        "pending_protection_state": protection_state,
                        "operator_action_required": operator_action_required,
                    },
                )
                continue
```

- [ ] **Step 7: Add ambiguous stack guard before exit actions**

Before `trigger = executor.check_stop_loss_take_profit(symbol)`, add:

```python
        group_count = owner_group_counts.get(_owner_group_key(row), 0)
        if group_count > 1:
            summary["ambiguous_stacks"] += 1
            summary["skipped"] += 1
            append_audit_event(
                paths.audit,
                "monitor_ambiguous_net_mode_stack",
                {
                    "shadow_id": shadow_id,
                    "symbol": symbol,
                    "owner_group_count": group_count,
                    "exchange_state": exchange_state,
                },
            )
            continue
```

- [ ] **Step 8: Run monitor tests**

Run:

```bash
pytest tests/test_shadow_tactical_exit_monitoring.py -q
```

Expected: PASS.

- [ ] **Step 9: Check off matching OpenSpec tasks and commit**

Update tasks:

```markdown
- [x] 1.3 Add sidecar monitor regressions proving ghost exposure emits fail-closed audit/halt behavior and does not silently loop on `monitor_skipped_unproven`.
- [x] 1.4 Add a net-mode stacked-owner regression proving monitor does not close one owner row while leaving remaining same-symbol exposure unproven and unmanaged.
- [x] 4.1 Detect ghost exposure in `monitor_sidecar_owned_exposure()` when owners are open, exchange exposure is present, and local sidecar position proof is missing.
- [x] 4.2 Emit fail-closed audit/halt metadata for ghost exposure while preserving the rule that unproven exchange exposure is not closed or reduced automatically.
- [x] 4.3 Detect ambiguous same-symbol net-mode owner stacks before applying close/reduce actions, and fail closed unless the whole net exposure is proven as one aggregate position.
```

Commit:

```bash
git add scripts/shadow_tactical_live_sidecar.py tests/test_shadow_tactical_exit_monitoring.py openspec/changes/fix-sidecar-ghost-position-safety/tasks.md
git commit -m "fix: fail closed on sidecar ghost exposure"
```

## Task 4: Sidecar Entry Drift Guard

**Files:**
- Modify: `tests/test_shadow_tactical_live_executor.py`
- Modify: `executor.py`
- Modify: `openspec/changes/fix-sidecar-ghost-position-safety/tasks.md`

- [ ] **Step 1: Update executor test fixture for drift helpers**

In `_executor()` in `tests/test_shadow_tactical_live_executor.py`, bind the drift helpers:

```python
    ex._pending_drift_alerts = []
    ex._enqueue_drift_alert = ContractExecutor._enqueue_drift_alert.__get__(
        ex, ContractExecutor
    )
    ex._recompute_plan_for_drift = ContractExecutor._recompute_plan_for_drift.__get__(
        ex, ContractExecutor
    )
    ex._classify_entry_drift = ContractExecutor._classify_entry_drift.__get__(
        ex, ContractExecutor
    )
    ex._record_drift_decision_event = MagicMock()
```

- [ ] **Step 2: Replace old no-drift test name**

Rename `test_open_sidecar_plan_places_order_without_drift_gate()` to:

```python
def test_open_sidecar_plan_records_accept_drift_metadata():
```

Change the final assertion to:

```python
    assert pos["gate_metadata"]["entry_drift"]["decision"] == "accept"
    assert pos["gate_metadata"]["entry_drift"]["band"] == "accept"
```

- [ ] **Step 3: Add large drift rejection test**

Append:

```python
def test_open_sidecar_plan_rejects_large_entry_drift_before_order():
    ex = _executor()
    ex.exchange.fetch_ticker.return_value = {"last": 1.40}

    assert ex.open_sidecar_plan(_plan(), size_usdt=30.0) is None

    ex.exchange.create_order.assert_not_called()
    assert ex._pending_drift_alerts[-1]["type"] == "sidecar_entry_drift_rejected"
```

- [ ] **Step 4: Add missing anchor rejection test**

Append:

```python
def test_open_sidecar_plan_rejects_when_sidecar_drift_anchors_missing():
    ex = _executor()
    plan = _plan(entry_ref=None)
    plan.pop("entry_price", None)

    assert ex.open_sidecar_plan(plan, size_usdt=30.0) is None

    ex.exchange.create_order.assert_not_called()
    assert ex._pending_drift_alerts[-1]["type"] == "sidecar_entry_drift_missing_anchor"
```

- [ ] **Step 5: Run drift sidecar tests and verify they fail**

Run:

```bash
pytest tests/test_shadow_tactical_live_executor.py::test_open_sidecar_plan_records_accept_drift_metadata tests/test_shadow_tactical_live_executor.py::test_open_sidecar_plan_rejects_large_entry_drift_before_order tests/test_shadow_tactical_live_executor.py::test_open_sidecar_plan_rejects_when_sidecar_drift_anchors_missing -q
```

Expected: FAIL because sidecar drift metadata and rejection paths are missing.

- [ ] **Step 6: Add sidecar drift anchor builder**

In `executor.py`, add helper before `open_sidecar_plan()`:

```python
    def _build_sidecar_drift_plan(self, plan: dict) -> Optional[dict]:
        entry_ref = plan.get("entry_ref") or plan.get("entry_price")
        stop_loss = plan.get("stop_loss")
        take_profit = plan.get("take_profit") or []
        if not entry_ref or not stop_loss or not take_profit:
            return None
        try:
            entry_ref = float(entry_ref)
            stop_loss = float(stop_loss)
            first_tp = float(take_profit[0])
        except (TypeError, ValueError, IndexError):
            return None
        if entry_ref <= 0:
            return None
        sl_pct = plan.get("sl_pct")
        tp_pct = plan.get("tp_pct")
        if not sl_pct:
            sl_pct = abs(entry_ref - stop_loss) / entry_ref
        if not tp_pct:
            tp_pct = [abs(first_tp - entry_ref) / entry_ref]
        return {
            "symbol": plan.get("symbol"),
            "side": plan.get("side"),
            "entry_ref": entry_ref,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "attribution": dict(plan.get("gate_metadata") or {}),
        }
```

- [ ] **Step 7: Add sidecar drift precheck**

Add:

```python
    def _check_sidecar_entry_drift(self, plan: dict, live_price: float) -> tuple[bool, dict]:
        drift_plan = self._build_sidecar_drift_plan(plan)
        if drift_plan is None:
            self._enqueue_drift_alert(
                "sidecar_entry_drift_missing_anchor",
                symbol=plan.get("symbol"),
                side=plan.get("side"),
                source="sidecar",
            )
            return False, {"decision": "missing_anchor"}

        decision = self._classify_entry_drift(drift_plan, live_price)
        metadata = {
            "band": decision.band,
            "drift_pct": decision.drift_pct,
            "decision": decision.decision,
            "reason": decision.reason,
        }
        if decision.decision != "accept":
            self._enqueue_drift_alert(
                "sidecar_entry_drift_rejected",
                symbol=plan.get("symbol"),
                side=plan.get("side"),
                drift_pct=decision.drift_pct,
                decision=decision.decision,
                reason=decision.reason,
                source="sidecar",
            )
            return False, metadata
        return True, metadata
```

- [ ] **Step 8: Wire precheck into `open_sidecar_plan()`**

After `current_price` is computed in `open_sidecar_plan()`, add:

```python
        drift_ok, drift_metadata = self._check_sidecar_entry_drift(plan, current_price)
        if not drift_ok:
            return None
```

When building `position`, change `gate_metadata` to:

```python
            "gate_metadata": {
                **dict(plan.get("gate_metadata") or {}),
                "entry_drift": drift_metadata,
            },
```

- [ ] **Step 9: Run sidecar executor and drift tests**

Run:

```bash
pytest tests/test_shadow_tactical_live_executor.py tests/test_entry_drift_hybrid_policy.py -q
```

Expected: PASS.

- [ ] **Step 10: Check off matching OpenSpec tasks and commit**

Update tasks:

```markdown
- [x] 1.5 Add sidecar entry-drift regressions proving large stale drift rejects before `create_order` and accepted opens record drift metadata.
- [x] 5.1 Derive sidecar drift anchors from `entry_ref`, `stop_loss`, and first `take_profit` when explicit `sl_pct`/`tp_pct` are absent.
- [x] 5.2 Reject large stale sidecar opens before exchange order submission and record drift rejection attribution.
- [x] 5.3 Persist drift admission metadata for accepted sidecar opens.
```

Commit:

```bash
git add executor.py tests/test_shadow_tactical_live_executor.py openspec/changes/fix-sidecar-ghost-position-safety/tasks.md
git commit -m "fix: gate stale sidecar entries"
```

## Task 5: Verification Report And Build Completion

**Files:**
- Modify: `openspec/changes/fix-sidecar-ghost-position-safety/tasks.md`
- Create: `docs/superpowers/reports/2026-07-22-fix-sidecar-ghost-position-safety-verify.md`

- [ ] **Step 1: Run focused verification**

Run:

```bash
pytest \
  tests/test_shadow_tactical_live_core.py \
  tests/test_shadow_tactical_live_executor.py \
  tests/test_shadow_tactical_owner_isolation.py \
  tests/test_shadow_tactical_exit_monitoring.py \
  tests/test_entry_drift_hybrid_policy.py \
  test_partial_tp_lifecycle.py -q
```

Expected: PASS.

- [ ] **Step 2: Run OpenSpec strict validation**

Run:

```bash
openspec validate fix-sidecar-ghost-position-safety --strict
```

Expected: `Change 'fix-sidecar-ghost-position-safety' is valid`.

- [ ] **Step 3: Write verification report**

Create `docs/superpowers/reports/2026-07-22-fix-sidecar-ghost-position-safety-verify.md`:

```markdown
# Verification Report: fix-sidecar-ghost-position-safety

Date: 2026-07-22
Change: `fix-sidecar-ghost-position-safety`

## Result

PASS.

## Checks

- PASS: Main migration preserves manual OCO/conditional protection for sidecar-owned present/unknown exposure.
- PASS: Sidecar admission blocks same-symbol sidecar stacking before order submission.
- PASS: Sidecar monitor records ghost exposure and does not close/reduce unproven present exposure.
- PASS: Ambiguous same-symbol net-mode owner stacks are not partially closed.
- PASS: Sidecar live opens enforce stale-entry drift protection before `create_order`.
- PASS: OpenSpec strict validation passes.

## Commands

```bash
pytest \
  tests/test_shadow_tactical_live_core.py \
  tests/test_shadow_tactical_live_executor.py \
  tests/test_shadow_tactical_owner_isolation.py \
  tests/test_shadow_tactical_exit_monitoring.py \
  tests/test_entry_drift_hybrid_policy.py \
  test_partial_tp_lifecycle.py -q
openspec validate fix-sidecar-ghost-position-safety --strict
```

## Rollout

Deploy Main migration preservation before resuming sidecar. Keep sidecar scale-up paused until sidecar `status` and owner files show no exchange-present ghost exposure requiring manual reconciliation.
```

- [ ] **Step 4: Check off final tasks**

Update:

```markdown
- [x] 6.1 Run focused tests for sidecar core, sidecar executor, owner isolation, exit monitoring, entry drift, and algo migration.
- [x] 6.2 Update or add verification report documenting the ADA failure-class reproduction, fixed behavior, and any remaining operational constraints.
- [x] 6.3 Document rollout ordering: deploy Main migration preservation before resuming sidecar, then verify sidecar status has no ghost exposure.
```

- [ ] **Step 5: Commit verification artifacts**

Run:

```bash
git add docs/superpowers/reports/2026-07-22-fix-sidecar-ghost-position-safety-verify.md openspec/changes/fix-sidecar-ghost-position-safety/tasks.md
git commit -m "docs: verify sidecar ghost position safety"
```

- [ ] **Step 6: Run build guard**

Run:

```bash
COMET_ENV="${COMET_ENV:-$(find . "$HOME"/.*/skills "$HOME/.config" "$HOME/.gemini" -path '*/comet/scripts/comet-env.sh' -type f -print -quit 2>/dev/null)}"
. "$COMET_ENV"
bash "$COMET_GUARD" fix-sidecar-ghost-position-safety build --apply
```

Expected: guard passes and `.comet.yaml` moves to `phase=verify`.

## Self-Review

- Spec coverage:
  - `protective-sl-owner-tag` preservation scenarios: Task 1.
  - `tactical-exit-track` sidecar hard veto scenarios: Task 2.
  - `shadow-tactical-sidecar-exit-monitoring` ghost and ambiguous stack scenarios: Task 3.
  - `entry-drift-policy` sidecar stale-entry scenarios: Task 4.
  - Verification and rollout notes: Task 5.
- Placeholder scan: no `TBD`, `TODO`, placeholder comments, or missing commands.
- Type consistency:
  - `sidecar_protected_algos` is introduced in Task 1 and used only as a migration summary field.
  - `same_symbol_sidecar_active` is the guard/audit reason for active sidecar owner rows.
  - `monitor_ghost_exposure` and `monitor_ambiguous_net_mode_stack` are monitor audit event names.
  - `sidecar_entry_drift_rejected` and `sidecar_entry_drift_missing_anchor` are drift alert types.
