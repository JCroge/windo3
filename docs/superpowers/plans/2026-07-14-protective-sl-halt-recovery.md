---
change: protective-sl-halt-recovery
design-doc: docs/superpowers/specs/2026-07-14-protective-sl-halt-recovery-design.md
base-ref: 35671ae7af2806abddc2700b01d07f778103f4ed
---

# Protective SL Halt Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep protection failures fail-closed while automatically recovering WLD-style protection halts once the position is closed or verified protected, and make Telegram status distinguish global halt from Tactical circuit state.

**Architecture:** Add one exact-match auto-clear method to `HaltState`, one bounded OKX attached-SL verification helper in `ContractExecutor`, and one sync/migration recovery helper that only clears allowlisted protection halt reasons after the risk is proven gone. Telegram `/status` remains read-only and gains a compact Tactical circuit line sourced from `riskguard_state.json`.

**Tech Stack:** Python 3, pytest, existing `ContractExecutor`, `HaltState`, Telegram notifier, OKX ccxt mocks.

---

## File Structure

- Modify: `utils/halt_state.py` — add exact-match `auto_clear_if_reason`.
- Modify: `executor.py` — add bounded attached-SL verification and protection-halt recovery helper.
- Modify: `agents/trading/telegram_notifier.py` — format global halt and Tactical circuit as separate `/status` lines.
- Test: `test_halt_resume_ownership.py` — exact-match auto-clear behavior.
- Test: `test_partial_tp_lifecycle.py` — attached SL verification and migration-to-protected recovery.
- Test: `tests/test_phantom_position_resync.py` — closed-symbol self-heal for `sl_algo_unresolved`.
- Test: `test_tg_status_enhancement.py` — Telegram status matrix.

## Task 1: Add Exact-Match HaltState Auto-Clear

**Files:**
- Modify: `utils/halt_state.py`
- Test: `test_halt_resume_ownership.py`

- [ ] **Step 1: Write failing HaltState tests**

Append to `test_halt_resume_ownership.py`:

```python
class TestHaltStateAutoClear:
    def test_auto_clear_if_reason_exact_match(self, clean_halt_state):
        state = HaltState()
        state.halt("okx_sl_algo_unresolved:WLD-USDT-SWAP", "executor")

        cleared = state.auto_clear_if_reason(
            "okx_sl_algo_unresolved:WLD-USDT-SWAP",
            cleared_by="self_heal:protection_resolved",
        )

        assert cleared is True
        assert state.halted is False
        assert state.can_open_new is True
        assert state.resume_by == "self_heal:protection_resolved"
        assert state.reconciliation_result == "auto_protection_resolved"

    def test_auto_clear_if_reason_mismatch_keeps_halt(self, clean_halt_state):
        state = HaltState()
        state.halt("manual", "telegram")

        cleared = state.auto_clear_if_reason(
            "okx_sl_algo_unresolved:WLD-USDT-SWAP",
            cleared_by="self_heal:protection_resolved",
        )

        assert cleared is False
        assert state.halted is True
        assert state.reason == "manual"
        assert state.can_open_new is False
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
python3 -m pytest -q test_halt_resume_ownership.py::TestHaltStateAutoClear
```

Expected: fails with `AttributeError: 'HaltState' object has no attribute 'auto_clear_if_reason'`.

- [ ] **Step 3: Implement exact-match auto-clear**

In `utils/halt_state.py`, add this method after `force_resume`:

```python
    def auto_clear_if_reason(self, expected_reason: str, cleared_by: str) -> bool:
        """Clear only when the current global halt reason exactly matches.

        This is for machine-proven protection recovery. It deliberately does
        not replace /resume or /force_resume.
        """
        if not self.halted or self.reason != expected_reason:
            return False
        self.halted = False
        self.resume_at = time.time()
        self.resume_by = cleared_by
        self.reconciliation_pending = False
        self.reconciliation_result = "auto_protection_resolved"
        self._save()
        return True
```

- [ ] **Step 4: Verify Task 1**

Run:

```bash
python3 -m pytest -q test_halt_resume_ownership.py::TestHaltStateAutoClear
```

Expected: `2 passed`.

- [ ] **Step 5: Commit Task 1**

```bash
git add utils/halt_state.py test_halt_resume_ownership.py
git commit -m "feat: add exact-match halt auto clear"
```

## Task 2: Add Bounded Attached-SL Verification

**Files:**
- Modify: `executor.py`
- Test: `test_partial_tp_lifecycle.py`

- [ ] **Step 1: Write attached-SL verification tests**

Add this test class near the existing OKX protection lifecycle tests in `test_partial_tp_lifecycle.py`:

```python
class TestAttachedSlVerification:
    def test_attached_sl_second_attempt_marks_protected_without_halt(self):
        ex = _make_executor()
        ex.exchange_id = "okx"
        ex.testnet = False
        ex._halt_symbol = MagicMock()
        ex._resolve_attached_sl_algo_id = MagicMock(side_effect=[None, "algo-1"])
        ex._list_pending_algos = MagicMock(return_value=[])

        algo_id = ex._verify_attached_sl_after_fill(
            "BTC-USDT-SWAP", "clord-1", attempts=2, sleep_sec=0
        )

        assert algo_id == "algo-1"
        ex._halt_symbol.assert_not_called()

    def test_attached_sl_fallback_matches_pending_algo(self):
        ex = _make_executor()
        ex.exchange_id = "okx"
        ex.testnet = False
        ex._resolve_attached_sl_algo_id = MagicMock(return_value=None)
        ex._list_pending_algos = MagicMock(return_value=[{
            "algoId": "algo-2",
            "algoClOrdId": "clord-2",
            "sl_trigger": "101.5",
            "tp_trigger": "",
        }])

        algo_id = ex._verify_attached_sl_after_fill(
            "BTC-USDT-SWAP", "clord-2", attempts=1, sleep_sec=0
        )

        assert algo_id == "algo-2"

    def test_attached_sl_missing_after_attempts_returns_none(self):
        ex = _make_executor()
        ex.exchange_id = "okx"
        ex.testnet = False
        ex._resolve_attached_sl_algo_id = MagicMock(return_value=None)
        ex._list_pending_algos = MagicMock(return_value=[])

        algo_id = ex._verify_attached_sl_after_fill(
            "BTC-USDT-SWAP", "clord-missing", attempts=2, sleep_sec=0
        )

        assert algo_id is None
        assert ex._resolve_attached_sl_algo_id.call_count == 2
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
python3 -m pytest -q test_partial_tp_lifecycle.py::TestAttachedSlVerification
```

Expected: fails because `_verify_attached_sl_after_fill` does not exist.

- [ ] **Step 3: Implement `_verify_attached_sl_after_fill`**

In `executor.py`, add imports if needed:

```python
import time
```

`time` is already imported in this file, so no new import should be needed.

Add this method near `_resolve_attached_sl_algo_id`:

```python
    def _verify_attached_sl_after_fill(self, symbol: str, clord_id: str,
                                       *, attempts: int = 3,
                                       sleep_sec: float = 0.5) -> Optional[str]:
        if not clord_id:
            return None
        attempts = max(1, int(attempts or 1))
        for idx in range(attempts):
            algo_id = self._resolve_attached_sl_algo_id(symbol, clord_id)
            if algo_id:
                return algo_id

            for algo in self._list_pending_algos(symbol):
                if algo.get("algoClOrdId") != clord_id:
                    continue
                has_sl = algo.get("sl_trigger") not in (None, "", "0")
                if algo.get("algoId") and has_sl:
                    return algo.get("algoId")

            if idx < attempts - 1 and sleep_sec > 0:
                time.sleep(sleep_sec)
        return None
```

- [ ] **Step 4: Use helper in open path**

In `open_position_with_plan`, replace:

```python
sl_algo_id_resolved = self._resolve_attached_sl_algo_id(symbol, sl_clord_id)
```

with:

```python
sl_algo_id_resolved = self._verify_attached_sl_after_fill(symbol, sl_clord_id)
```

Keep the existing failure branch that sets `sl_sync_state='failed'`, `protection_state='unknown'`, logs `[SL Resolve]`, and calls `_halt_symbol(symbol, reason='sl_algo_unresolved')`.

- [ ] **Step 5: Verify Task 2**

Run:

```bash
python3 -m pytest -q test_partial_tp_lifecycle.py::TestAttachedSlVerification
```

Expected: `3 passed`.

- [ ] **Step 6: Commit Task 2**

```bash
git add executor.py test_partial_tp_lifecycle.py
git commit -m "fix: verify attached OKX stop loss before protection halt"
```

## Task 3: Auto-Clear Protection Halt After Close Or Protection Recovery

**Files:**
- Modify: `executor.py`
- Test: `tests/test_phantom_position_resync.py`
- Test: `test_partial_tp_lifecycle.py`

- [ ] **Step 1: Add closed-symbol self-heal test for `sl_algo_unresolved`**

Append to `tests/test_phantom_position_resync.py`:

```python
def test_sl_algo_unresolved_halt_self_heals_on_removal(monkeypatch):
    import utils.halt_state as hs_mod

    ex = _mk_executor()
    ex.positions = {
        "WLD-USDT-SWAP": {
            "symbol": "WLD-USDT-SWAP",
            "amount": 261.0,
            "protection_state": "unknown",
        }
    }
    ex._halted_symbols = {
        "WLD-USDT-SWAP": {
            "reason": "sl_algo_unresolved",
            "halted_at": 1.0,
        }
    }
    halt_state = MagicMock()
    halt_state.auto_clear_if_reason.return_value = True
    monkeypatch.setattr(hs_mod, "get_halt_state", lambda: halt_state)
    ex._fetch_positions_with_retry = MagicMock(return_value=[])

    ex.sync_positions()

    assert "WLD-USDT-SWAP" not in ex.positions
    ex.clear_symbol_halt.assert_called_once_with(
        "WLD-USDT-SWAP", source="self_heal:protection_resolved"
    )
    halt_state.auto_clear_if_reason.assert_called_once_with(
        "okx_sl_algo_unresolved:WLD-USDT-SWAP",
        cleared_by="self_heal:protection_resolved",
    )
```

- [ ] **Step 2: Add non-allowlisted global halt regression test**

Append to `tests/test_phantom_position_resync.py`:

```python
def test_non_allowlisted_halt_does_not_auto_clear_global(monkeypatch):
    import utils.halt_state as hs_mod

    ex = _mk_executor()
    ex.positions = {"WLD-USDT-SWAP": {"symbol": "WLD-USDT-SWAP", "amount": 1.0}}
    ex._halted_symbols = {
        "WLD-USDT-SWAP": {"reason": "reconcile_conflict", "halted_at": 1.0}
    }
    halt_state = MagicMock()
    monkeypatch.setattr(hs_mod, "get_halt_state", lambda: halt_state)
    ex._fetch_positions_with_retry = MagicMock(return_value=[])

    ex.sync_positions()

    ex.clear_symbol_halt.assert_not_called()
    halt_state.auto_clear_if_reason.assert_not_called()
```

- [ ] **Step 3: Run failing removal tests**

Run:

```bash
python3 -m pytest -q tests/test_phantom_position_resync.py::test_sl_algo_unresolved_halt_self_heals_on_removal tests/test_phantom_position_resync.py::test_non_allowlisted_halt_does_not_auto_clear_global
```

Expected: the first test fails because only `migrate_missing_sl` self-heals today.

- [ ] **Step 4: Implement protection halt helpers**

In `executor.py`, add class-level or module-level allowlist close to `_halt_symbol`:

```python
PROTECTION_HALT_REASONS = {"sl_algo_unresolved", "migrate_missing_sl"}
```

Add helper methods near `clear_symbol_halt`:

```python
    def _is_protection_halt_reason(self, reason: str) -> bool:
        return reason in PROTECTION_HALT_REASONS

    def _global_halt_reason_for(self, symbol: str, reason: str) -> str:
        return f"okx_{reason}:{symbol}"

    def _maybe_auto_clear_protection_halt(self, symbol: str, reason: str,
                                          *, source: str) -> bool:
        if not self._is_protection_halt_reason(reason):
            return False
        pos = self.positions.get(symbol)
        if pos and pos.get("protection_state") in {"unknown", "pending"}:
            return False
        self.clear_symbol_halt(symbol, source=source)
        try:
            from utils.halt_state import get_halt_state
            expected = self._global_halt_reason_for(symbol, reason)
            cleared = get_halt_state().auto_clear_if_reason(
                expected, cleared_by=source
            )
        except Exception as e:
            self.logger.warning(
                f"[SelfHeal] {symbol} protection halt auto-clear failed: {e}"
            )
            return False
        if cleared:
            self.logger.info(
                f"[SelfHeal] {symbol} protection halt cleared "
                f"(reason={reason}, source={source})"
            )
        return bool(cleared)
```

- [ ] **Step 5: Replace removal-path special case**

In `sync_positions`, replace the existing `migrate_missing_sl` special case:

```python
halt_info = getattr(self, '_halted_symbols', {}).get(sym)
if halt_info and halt_info.get('reason') == 'migrate_missing_sl':
    self.clear_symbol_halt(sym, source='self_heal:phantom_removed')
    ...
```

with:

```python
halt_info = getattr(self, '_halted_symbols', {}).get(sym)
halt_reason = (halt_info or {}).get("reason", "")
if halt_reason:
    self._maybe_auto_clear_protection_halt(
        sym, halt_reason, source="self_heal:protection_resolved"
    )
```

- [ ] **Step 6: Verify removal-path tests**

Run:

```bash
python3 -m pytest -q tests/test_phantom_position_resync.py
```

Expected: all tests pass, including existing `migrate_missing_sl` self-heal and non-migrate non-clear cases.

- [ ] **Step 7: Add migration-to-protected recovery test**

In `test_partial_tp_lifecycle.py`, add a case near `test_single_sl_matches_position_and_cancels_tp`:

```python
    def test_sl_algo_unresolved_halt_clears_when_migration_finds_sl(self, monkeypatch):
        import utils.halt_state as hs_mod

        ex = _make_executor()
        ex.testnet = False
        self._local_long(ex)
        ex.positions["BTC-USDT-SWAP"]["protection_state"] = "unknown"
        ex._halted_symbols = {
            "BTC-USDT-SWAP": {"reason": "sl_algo_unresolved", "halted_at": 1.0}
        }
        halt_state = MagicMock()
        halt_state.auto_clear_if_reason.return_value = True
        monkeypatch.setattr(hs_mod, "get_halt_state", lambda: halt_state)
        ex._save_positions = MagicMock()
        ex.exchange.private_get_trade_orders_algo_pending = MagicMock(
            return_value={"data": [{
                "algoId": "sl-1",
                "algoClOrdId": "clord-1",
                "instId": "BTC-USDT-SWAP",
                "side": "sell",
                "tpTriggerPx": "0",
                "slTriggerPx": "94",
            }]}
        )

        summary = ex._migrate_okx_algos_for_symbol("BTC-USDT-SWAP")

        assert summary["matched_sl"] == "sl-1"
        assert ex.positions["BTC-USDT-SWAP"]["protection_state"] == "protected"
        assert "BTC-USDT-SWAP" not in ex._halted_symbols
        halt_state.auto_clear_if_reason.assert_called_once_with(
            "okx_sl_algo_unresolved:BTC-USDT-SWAP",
            cleared_by="self_heal:protection_resolved",
        )
```

- [ ] **Step 8: Call helper after migration marks protected**

In `_migrate_okx_algos_for_symbol`, after `position['protection_state'] = 'protected'` and before return, add:

```python
        halt_info = getattr(self, "_halted_symbols", {}).get(symbol)
        halt_reason = (halt_info or {}).get("reason", "")
        if halt_reason:
            self._maybe_auto_clear_protection_halt(
                symbol, halt_reason, source="self_heal:protection_resolved"
            )
```

- [ ] **Step 9: Verify Task 3**

Run:

```bash
python3 -m pytest -q tests/test_phantom_position_resync.py test_partial_tp_lifecycle.py::TestAlgoMigration
```

Expected: all selected tests pass.

- [ ] **Step 10: Commit Task 3**

```bash
git add executor.py tests/test_phantom_position_resync.py test_partial_tp_lifecycle.py
git commit -m "fix: auto-clear resolved protection halts"
```

## Task 4: Make Telegram Status Distinguish Global Halt And Tactical Circuit

**Files:**
- Modify: `agents/trading/telegram_notifier.py`
- Test: `test_tg_status_enhancement.py`

- [ ] **Step 1: Add status matrix tests**

Append to `test_tg_status_enhancement.py`:

```python
class TestTelegramStatusHaltMatrix(TestStatusEnhancement):
    def _write_status_files(self, tmp_path, halt_state, riskguard_state, health=None):
        os.makedirs(tmp_path / "data", exist_ok=True)
        with open(tmp_path / "data/testnet_positions.json", "w") as f:
            json.dump({}, f)
        with open(tmp_path / "data/testnet_halt_state.json", "w") as f:
            json.dump(halt_state, f)
        with open(tmp_path / "data/testnet_riskguard_state.json", "w") as f:
            json.dump(riskguard_state, f)
        with open(tmp_path / "data/testnet_agent_health.json", "w") as f:
            json.dump(health or {
                "agents_registered": 17,
                "tasks_alive": 17,
                "tasks_failed": 0,
                "halted_symbols": {},
                "bus_dlq_size": 0,
            }, f)

    @pytest.mark.asyncio
    async def test_global_protection_halt_tactical_not_paused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        import utils.halt_state as hs_mod
        reset_state_paths()
        hs_mod._instance = None
        monkeypatch.chdir(tmp_path)
        self._write_status_files(
            tmp_path,
            {
                "halted": True,
                "reason": "okx_sl_algo_unresolved:WLD-USDT-SWAP",
                "reconciliation_pending": False,
                "reconciliation_result": None,
            },
            {
                "tactical_circuit": {
                    "daily_pnl": -2.6721,
                    "loss_streak": 1,
                    "pause_until": 0,
                    "pause_reason": "",
                }
            },
            health={"halted_symbols": {"WLD-USDT-SWAP": {"reason": "sl_algo_unresolved"}}},
        )
        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_status()

        text = "\n".join(sent)
        assert "全局熔断: 是" in text
        assert "okx_sl_algo_unresolved:WLD-USDT-SWAP" in text
        assert "Per-symbol halt: 1" in text
        assert "Tactical circuit: 否" in text

    @pytest.mark.asyncio
    async def test_tactical_paused_global_clear(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        import utils.halt_state as hs_mod
        reset_state_paths()
        hs_mod._instance = None
        monkeypatch.chdir(tmp_path)
        self._write_status_files(
            tmp_path,
            {"halted": False, "reason": ""},
            {
                "tactical_circuit": {
                    "daily_pnl": -12.0,
                    "loss_streak": 3,
                    "pause_until": time.time() + 3600,
                    "pause_reason": "loss_streak",
                }
            },
        )
        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_status()

        text = "\n".join(sent)
        assert "全局熔断: 否" in text
        assert "Tactical circuit: 是" in text
        assert "loss_streak" in text
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
python3 -m pytest -q test_tg_status_enhancement.py::TestTelegramStatusHaltMatrix
```

Expected: fails because `/status` still uses `熔断:` and has no Tactical circuit line.

- [ ] **Step 3: Implement status helpers**

In `agents/trading/telegram_notifier.py`, add helper methods near `_cmd_status`:

```python
    def _read_tactical_circuit_state(self):
        try:
            with open(_riskguard_path(), "r") as f:
                state = json.load(f)
            return (state.get("tactical_circuit") or {})
        except Exception:
            return None

    def _format_tactical_circuit_line(self, tactical):
        if tactical is None:
            return "Tactical circuit: ?"
        now = time.time()
        pause_until = float(tactical.get("pause_until") or 0)
        reason = tactical.get("pause_reason") or ""
        daily_pnl = float(tactical.get("daily_pnl") or 0.0)
        loss_streak = int(tactical.get("loss_streak") or 0)
        if pause_until > now:
            until = time.strftime("%H:%M", time.localtime(pause_until))
            return (
                f"Tactical circuit: 是 ({reason or 'paused'}, until {until}, "
                f"daily_pnl={daily_pnl:+.2f}, loss_streak={loss_streak})"
            )
        return (
            f"Tactical circuit: 否 "
            f"(daily_pnl={daily_pnl:+.2f}, loss_streak={loss_streak})"
        )
```

- [ ] **Step 4: Update `_cmd_status` text**

Change the halt line in `_cmd_status` from:

```python
if halted:
    text += f"熔断: 是 ({halt_reason})\n"
else:
    text += f"熔断: 否\n"
```

to:

```python
if halted:
    text += f"全局熔断: 是 ({halt_reason})\n"
else:
    text += "全局熔断: 否\n"
text += f"{self._format_tactical_circuit_line(self._read_tactical_circuit_state())}\n"
```

Keep existing health/per-symbol halt lines unchanged.

- [ ] **Step 5: Verify Task 4**

Run:

```bash
python3 -m pytest -q test_tg_status_enhancement.py::TestTelegramStatusHaltMatrix test_tg_status_enhancement.py
```

Expected: all selected status tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add agents/trading/telegram_notifier.py test_tg_status_enhancement.py
git commit -m "feat: split global halt and tactical circuit in tg status"
```

## Task 5: Final Verification And Cloud Rollout

**Files:**
- Modify: `openspec/changes/protective-sl-halt-recovery/tasks.md`
- No code changes unless verification finds failures.

- [ ] **Step 1: Run focused test set**

Run:

```bash
python3 -m pytest -q test_halt_resume_ownership.py tests/test_phantom_position_resync.py test_partial_tp_lifecycle.py test_tg_status_enhancement.py
```

Expected: all pass.

- [ ] **Step 2: Run full test suite**

Run:

```bash
python3 -m pytest -q
```

Expected: full suite passes or only pre-existing skipped/deselected tests appear.

- [ ] **Step 3: Update OpenSpec tasks**

Mark all completed tasks in `openspec/changes/protective-sl-halt-recovery/tasks.md` from `- [ ]` to `- [x]`.

- [ ] **Step 4: Commit task completion**

```bash
git add openspec/changes/protective-sl-halt-recovery/tasks.md
git commit -m "docs: mark protective sl halt recovery tasks complete"
```

- [ ] **Step 5: Cloud rollout after local verification**

After local tests pass and commits are pushed, sync cloud repo, restart, then verify:

```bash
ssh root@45.77.27.55 'cd /opt/crypto-arbitrage && git pull --ff-only && pkill -f "python3 /opt/crypto-arbitrage/run_agents.py" || true'
ssh root@45.77.27.55 'cd /opt/crypto-arbitrage && nohup python3 /opt/crypto-arbitrage/run_agents.py > logs/run_$(date -u +%Y%m%d_%H%M%S)_protective_sl_halt_recovery.log 2>&1 &'
ssh root@45.77.27.55 'cd /opt/crypto-arbitrage && python3 - <<'"'"'PY'"'"'
import json, pathlib
for p in ["data/halt_state.json", "data/riskguard_state.json", "data/agent_health.json"]:
    path = pathlib.Path(p)
    print("\\n==", p, "==")
    print(path.read_text()[:1500] if path.exists() else "missing")
PY'
```

Expected: process restarts cleanly, `/status` shows separate global halt and Tactical circuit lines, and no active protection halt is present unless a real unresolved protection event exists.
