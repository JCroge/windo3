"""Counterfactual Signal Ledger

Tracks rejected-but-planned signals to determine what would have happened
if they had been executed. Resolves shadow positions via price ticks.

Scope: Only measures rejected planned signals. Does NOT estimate opportunity
cost of symbols never planned by Judge.
"""

import time
import json
import uuid
from utils.atomic_io import atomic_write_json


class CounterfactualLedger:

    EXPIRY_SECONDS = 24 * 3600  # 24h max tracking

    def __init__(self, enabled: bool = True, logger=None):
        self._enabled = enabled
        self.logger = logger
        self._active = {}  # {id: record}
        self._events_path = "data/rejected_signal_events.jsonl"
        self._lifecycle_path = "data/rejected_signal_lifecycle.json"
        self._load_active()

    def record_rejection(self, symbol: str, side: str, plan: dict,
                         regime: str, score: float, confidence: float,
                         reject_reason: str, attribution: dict = None,
                         tech_context: dict = None):
        """Record a rejected plan for shadow tracking."""
        if not self._enabled:
            return

        record_id = str(uuid.uuid4())[:8]
        entry_price = plan.get('entry_price', plan.get('entry_zone', [0, 0])[0])
        if isinstance(plan.get('entry_zone'), list) and plan['entry_zone']:
            entry_price = sum(plan['entry_zone']) / len(plan['entry_zone'])
        if not entry_price:
            entry_price = plan.get('price', 0)

        record = {
            "id": record_id,
            "symbol": symbol,
            "side": side,
            "effective_regime": regime,
            "score": score,
            "confidence": confidence,
            "effective_rr": plan.get('effective_risk_reward_ratio',
                                    plan.get('risk_reward_ratio', 0)),
            "reject_reason": reject_reason,
            "entry_price": entry_price,
            "stop_loss": plan.get('stop_loss', 0),
            "take_profit": plan.get('take_profit', []),
            "leverage": plan.get('leverage', 1),
            "track": plan.get("track", (attribution or {}).get("track", "main")),
            "exit_profile": plan.get("exit_profile", (attribution or {}).get("exit_profile", "trend_runner")),
            "tactical_source": plan.get("tactical_source", (attribution or {}).get("tactical_source", "")),
            "tactical_effective_rr": plan.get("tactical_effective_rr"),
            "tactical_expected_value": plan.get("tactical_expected_value"),
            "tactical_track_gate": plan.get("tactical_track_gate"),
            "tactical_gate_failed": plan.get("tactical_gate_failed"),
            "tactical_min_rr_for_track": plan.get("tactical_min_rr_for_track"),
            "tactical_min_ev_for_track": plan.get("tactical_min_ev_for_track"),
            "tactical_max_hold_minutes": plan.get("tactical_max_hold_minutes",
                                                 plan.get("max_holding_minutes")),
            "created_at": time.time(),
            "status": "tracking",
        }

        if attribution:
            record["raw_regime"] = attribution.get("raw_regime", "")
            record["regime_confidence"] = attribution.get("regime_confidence", 0)
            record["rr_policy"] = attribution.get("rr_policy", "")
            record["slot_type"] = attribution.get("slot_type", "main")
            record["blocked_by"] = attribution.get("blocked_by", reject_reason)
            record["is_probe"] = attribution.get("is_probe", False)
            record["is_low_rr"] = attribution.get("is_low_rr", False)

        record["tech_context"] = dict(tech_context) if tech_context else {}

        self._active[record_id] = record
        self._append_event("rejected_plan_created", record)
        self._persist_lifecycle()

        if self.logger:
            self.logger.info(
                f"[Shadow] {symbol} {side} recorded: "
                f"entry={entry_price:.4f} sl={record['stop_loss']:.4f} "
                f"reason={reject_reason}"
            )
        return record_id

    def invalidate(self, symbol: str, reason: str = "signal_reversed"):
        """Invalidate active shadows for a symbol (e.g., reverse signal received)."""
        if not self._enabled:
            return
        now = time.time()
        resolved = []
        for rid, rec in list(self._active.items()):
            if rec['symbol'] != symbol or rec['status'] != 'tracking':
                continue
            rec['status'] = 'shadow_invalidated'
            rec['resolved_at'] = now
            rec['invalidate_reason'] = reason
            self._append_event("shadow_invalidated", rec)
            resolved.append(rid)
        for rid in resolved:
            self._active.pop(rid, None)
        if resolved:
            self._persist_lifecycle()
            if self.logger:
                self.logger.info(f"[Shadow] {symbol} invalidated {len(resolved)} shadow(s): {reason}")

    def check_price(self, symbol: str, price: float, timestamp: float):
        """Check if any active shadow for this symbol hit TP or SL."""
        if not self._enabled or not price:
            return

        # Normalize symbol
        symbol = symbol.replace('-SWAP', '').replace('/USDT:USDT', '-USDT')

        now = timestamp or time.time()
        resolved = []

        for rid, rec in list(self._active.items()):
            if rec['symbol'] != symbol:
                continue
            if rec['status'] != 'tracking':
                continue

            # Expiry check
            if now - rec['created_at'] > self.EXPIRY_SECONDS:
                rec['status'] = 'shadow_expired'
                rec['resolved_at'] = now
                self._append_event("shadow_expired", rec)
                resolved.append(rid)
                continue

            max_hold_min = rec.get('tactical_max_hold_minutes')
            if rec.get('exit_profile') == 'tactical_v1' and max_hold_min:
                if now - rec['created_at'] >= float(max_hold_min) * 60:
                    rec['status'] = 'shadow_tactical_max_hold'
                    rec['resolved_at'] = now
                    rec['resolved_price'] = price
                    if rec['side'] == 'long':
                        rec['pnl_pct'] = (price - rec['entry_price']) / rec['entry_price'] * 100 if rec['entry_price'] else 0
                    else:
                        rec['pnl_pct'] = (rec['entry_price'] - price) / rec['entry_price'] * 100 if rec['entry_price'] else 0
                    self._append_event("shadow_tactical_max_hold", rec)
                    resolved.append(rid)
                    continue

            entry = rec['entry_price']
            sl = rec['stop_loss']
            side = rec['side']

            # Determine TP (use first TP level)
            tp_list = rec.get('take_profit', [])
            tp = tp_list[0] if isinstance(tp_list, list) and tp_list else 0

            if side == 'long':
                if sl and price <= sl:
                    rec['status'] = 'shadow_sl'
                    rec['resolved_at'] = now
                    rec['resolved_price'] = price
                    rec['pnl_pct'] = (sl - entry) / entry * 100 if entry else 0
                    self._append_event("shadow_sl", rec)
                    resolved.append(rid)
                elif tp and price >= tp:
                    rec['status'] = 'shadow_tp'
                    rec['resolved_at'] = now
                    rec['resolved_price'] = price
                    rec['pnl_pct'] = (tp - entry) / entry * 100 if entry else 0
                    self._append_event("shadow_tp", rec)
                    resolved.append(rid)
            else:  # short
                if sl and price >= sl:
                    rec['status'] = 'shadow_sl'
                    rec['resolved_at'] = now
                    rec['resolved_price'] = price
                    rec['pnl_pct'] = (entry - sl) / entry * 100 if entry else 0
                    self._append_event("shadow_sl", rec)
                    resolved.append(rid)
                elif tp and price <= tp:
                    rec['status'] = 'shadow_tp'
                    rec['resolved_at'] = now
                    rec['resolved_price'] = price
                    rec['pnl_pct'] = (entry - tp) / entry * 100 if entry else 0
                    self._append_event("shadow_tp", rec)
                    resolved.append(rid)

        for rid in resolved:
            rec = self._active.pop(rid, None)
            if rec and self.logger:
                self.logger.info(
                    f"[Shadow] {rec['symbol']} {rec['side']} → {rec['status']} "
                    f"pnl={rec.get('pnl_pct', 0):.1f}%"
                )

        if resolved:
            self._persist_lifecycle()

    def get_stats(self, side: str = None, regime: str = None) -> dict:
        """Return shadow statistics from events file."""
        tp_count = 0
        sl_count = 0
        expired_count = 0
        max_hold_count = 0

        try:
            with open(self._events_path, 'r') as f:
                for line in f:
                    if not line.strip():
                        continue
                    evt = json.loads(line)
                    rec = evt.get('record', {})
                    if side and rec.get('side') != side:
                        continue
                    if regime and rec.get('effective_regime') != regime:
                        continue
                    evt_type = evt.get('event_type')
                    if evt_type == 'shadow_tp':
                        tp_count += 1
                    elif evt_type == 'shadow_sl':
                        sl_count += 1
                    elif evt_type == 'shadow_tactical_max_hold':
                        max_hold_count += 1
                    elif evt_type == 'shadow_expired':
                        expired_count += 1
        except FileNotFoundError:
            pass

        total_decided = tp_count + sl_count
        win_rate = tp_count / total_decided if total_decided > 0 else 0

        return {
            "tp": tp_count,
            "sl": sl_count,
            "max_hold": max_hold_count,
            "expired": expired_count,
            "total_decided": total_decided,
            "win_rate": win_rate,
            "sample_size": total_decided + max_hold_count + expired_count,
        }

    def active_count(self) -> int:
        return len(self._active)

    # ── Internal ────────────────────────────────────────────────────────────

    def _append_event(self, event_type: str, record: dict):
        try:
            event = {
                "event_type": event_type,
                "timestamp": time.time(),
                "record": record,
            }
            with open(self._events_path, 'a') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[Shadow] event write failed: {e}")

    def _persist_lifecycle(self):
        try:
            atomic_write_json(self._lifecycle_path, self._active)
        except Exception:
            pass

    def _load_active(self):
        try:
            with open(self._lifecycle_path, 'r') as f:
                data = json.load(f)
            now = time.time()
            for rid, rec in data.items():
                if rec.get('status') == 'tracking' and now - rec.get('created_at', 0) < self.EXPIRY_SECONDS:
                    self._active[rid] = rec
        except (FileNotFoundError, json.JSONDecodeError):
            pass
