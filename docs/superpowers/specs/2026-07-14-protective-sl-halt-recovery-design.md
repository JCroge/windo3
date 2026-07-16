---
comet_change: protective-sl-halt-recovery
role: technical-design
canonical_spec: openspec
archived-with: 2026-07-15-protective-sl-halt-recovery
status: final
---

# Technical Design: Protective SL Halt Recovery

Upstream requirements live in `openspec/changes/protective-sl-halt-recovery/`. This document describes the implementation design for approved approach A.

## Context

The live WLD Tactical order on 2026-07-14 exposed a narrow but expensive failure mode:

1. OKX order filled.
2. The attached stop-loss `algoId` could not be resolved by `attachAlgoClOrdId`.
3. `executor.py` marked protection as unknown and called `_halt_symbol(reason='sl_algo_unresolved')`.
4. `_halt_symbol` also wrote persistent global halt reason `okx_sl_algo_unresolved:WLD-USDT-SWAP`.
5. WLD later closed, but global halt stayed active until manual `/resume`.
6. Telegram `/status` displayed one generic `熔断` line, which made this look like a Tactical circuit or loss halt.

The current safety posture is right: a live position that might lack exchange-side SL must stop new risk. The part that needs tightening is recovery once the protection risk is proven gone.

## Goals

- Preserve fail-closed behavior while a live position may be unprotected.
- Avoid manual global outages when the affected position is later closed or verified protected.
- Keep auto-clear limited to explicit protection halt reasons.
- Make `/status` distinguish global halt, per-symbol halt, and Tactical circuit.

## Non-Goals

- No Tactical parameter changes.
- No Tactical circuit threshold changes.
- No auto-clear for manual halt, daily hard stop, reconciliation mismatch, or unknown halt reasons.
- No change to realized PnL attribution.

## Design

### 1. Bounded Attached-SL Verification

Today `open_position_with_plan` calls `_resolve_attached_sl_algo_id(symbol, sl_clord_id)` once after fill. If it returns `None`, the code immediately marks:

```python
sl_sync_state = "failed"
protection_state = "unknown"
_halt_symbol(symbol, reason="sl_algo_unresolved")
```

Replace the single lookup with a helper:

```python
def _verify_attached_sl_after_fill(self, symbol, clord_id, *, attempts=3, sleep_sec=0.5) -> Optional[str]:
    ...
```

The helper should:

- try `_resolve_attached_sl_algo_id`;
- on miss, call `_list_pending_algos(symbol)` and match `algoClOrdId == clord_id`;
- accept only rows with a valid `algoId` and SL trigger;
- sleep between attempts only in live execution, while tests can set `sleep_sec=0`.

The open call remains synchronous. During this short window no additional position can be processed from that same execution path, and if verification fails the existing global halt still blocks future opens through `halt_state.can_open_new`.

Recommended default: 3 attempts, 0.5s apart. Downstream impact: a successful open can take about one extra second when OKX is slow to expose attached algos; true missing-SL risk still halts.

### 2. Protection Halt Reason Model

Add small helpers in `executor.py`:

```python
PROTECTION_HALT_REASONS = {"sl_algo_unresolved", "migrate_missing_sl"}

def _is_protection_halt_reason(reason: str) -> bool:
    return reason in PROTECTION_HALT_REASONS

def _global_halt_reason_for(symbol: str, reason: str) -> str:
    return f"okx_{reason}:{symbol}"
```

Do not broaden this allowlist casually. Reasons like `migrate_multiple_sl`, `migrate_sl_side_conflict`, `direction_conflict_close`, `sl_cancel_failed`, and `sl_replace_failed` represent ambiguous or failed control operations and must stay sticky.

### 3. HaltState Auto-Clear API

Add a narrow method to `utils/halt_state.py`:

```python
def auto_clear_if_reason(self, expected_reason: str, cleared_by: str) -> bool:
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

This keeps manual `/resume` semantics intact and prevents accidental clearing of unrelated halt reasons. The method clears only by exact reason match.

Downstream impact: a protection halt may clear without operator action only when the code can name the exact reason it is clearing.

### 4. Sync-Time Protection Recovery

Reuse the existing `sync_positions` removal path around `executor.py` where symbols missing from exchange are removed locally. It already self-heals `migrate_missing_sl` per-symbol halt after a phantom position disappears.

Extend that path through a helper:

```python
def _maybe_auto_clear_protection_halt(self, symbol: str, reason: str, *, source: str) -> bool:
    ...
```

For removed symbols:

- if `_halted_symbols[symbol].reason` is `sl_algo_unresolved` or `migrate_missing_sl`, clear the per-symbol halt;
- call `get_halt_state().auto_clear_if_reason(f"okx_{reason}:{symbol}", ...)`;
- log `[SelfHeal]` with symbol, reason, and source.

For symbols that remain open but migration later matches a valid SL:

- after `_migrate_okx_algos_for_symbol` sets `protection_state="protected"`, call the same helper for `sl_algo_unresolved` or `migrate_missing_sl`;
- exact global reason match still protects unrelated halts.

Do not auto-clear if any local position for that symbol remains `protection_state in {"unknown", "pending"}`.

### 5. Telegram Status Matrix

Change `/status` from:

```text
熔断: 是 (...)
```

to separate lines:

```text
全局熔断: 是 (okx_sl_algo_unresolved:WLD-USDT-SWAP)
Per-symbol halt: 1 (WLD)
Tactical circuit: 否 (daily_pnl=-2.67, loss_streak=1)
```

If Tactical circuit has `pause_until > now`, show:

```text
Tactical circuit: 是 (loss_streak, until HH:MM)
```

Read Tactical state from `riskguard_state.json["tactical_circuit"]`. If unavailable, show `Tactical circuit: ?`.

This is observability only. It must not change trading behavior.

## Test Strategy

Add or extend focused tests before implementation:

- attached SL lookup first misses, then `_list_pending_algos` finds the matching SL and no halt is written;
- attached SL remains missing after bounded verification and `sl_algo_unresolved` halt is written;
- `sl_algo_unresolved` per-symbol and global halt auto-clear when sync confirms the symbol is closed;
- global halt does not auto-clear if the current reason is manual, daily hard stop, or non-allowlisted;
- global halt does not auto-clear if reason does not exactly match the symbol/reason being cleared;
- `/status` shows global protection halt and Tactical circuit not paused as separate lines;
- `/status` shows Tactical circuit paused while global halt is clear.

Focused commands:

```bash
python3 -m pytest -q test_partial_tp_lifecycle.py tests/test_phantom_position_resync.py test_tg_status_enhancement.py test_halt_resume_ownership.py
```

Then run the full suite or the agreed equivalent before cloud deployment.

## Risks

- Auto-clear hiding a true unprotected position: mitigated by exact reason matching and requiring closed/protected state.
- OKX visibility still delayed beyond retry budget: existing fail-closed halt remains.
- Status output becoming noisy: keep the status matrix compact and line-oriented.

## Implementation Notes

- Prefer adding helper methods over embedding new string parsing in multiple locations.
- Keep `clear_symbol_halt` return type as `int`; existing TG symbol halt tests rely on it.
- Avoid changing `_halt_symbol` behavior globally. Its broad fail-closed behavior is relied on by several safety paths.
- Use exact `halt_state.reason` matching for global auto-clear; do not infer by prefix alone.
