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
                         reject_reason: str):
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
            "created_at": time.time(),
            "status": "tracking",
        }

        self._active[record_id] = record
        self._append_event("rejected_plan_created", record)
        self._persist_lifecycle()

        if self.logger:
            self.logger.info(
                f"[Shadow] {symbol} {side} recorded: "
                f"entry={entry_price:.4f} sl={record['stop_loss']:.4f} "
                f"reason={reject_reason}"
            )

    def check_price(self, symbol: str, price: float, timestamp: float):
        """Check if any active shadow for this symbol hit TP or SL."""
        if not self._enabled or not price:
            return

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
                    elif evt_type == 'shadow_expired':
                        expired_count += 1
        except FileNotFoundError:
            pass

        total_decided = tp_count + sl_count
        win_rate = tp_count / total_decided if total_decided > 0 else 0

        return {
            "tp": tp_count,
            "sl": sl_count,
            "expired": expired_count,
            "total_decided": total_decided,
            "win_rate": win_rate,
            "sample_size": total_decided + expired_count,
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
