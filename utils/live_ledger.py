"""实盘订单事件账本 — 以交易所真实成交为准的 PnL 记录系统

每次开仓/减仓/平仓后调用 exchange.fetch_order 获取真实成交均价和手续费，
写入 JSONL 事件流 + 持仓生命周期聚合。

外部平仓走 PRD §6.2 双载荷模型:
  1. record_pending_external_close: 立即写 pending 事件 + lifecycle reconcile_status=pending
  2. apply_pnl_resolution: 拿到 RealizedPnlResolver final 解析后写 correction 事件
     (supersedes_event_id 指向原 pending; correction_seq 单调 +1; lifecycle 升级 final)
"""

import json
import os
import threading
import time
import uuid
import datetime
from typing import Optional, Dict, List, Any

try:
    import fcntl
except ImportError:  # pragma: no cover - production and CI are POSIX
    fcntl = None


PNL_STATUS_FINAL = "final"
PNL_STATUS_PENDING = "pending"
PNL_STATUS_ESTIMATED = "estimated"
PNL_STATUS_MISMATCH = "mismatch"
PNL_STATUS_PENDING_FX = "pending_fx"


_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: Dict[str, "_PathLedgerLock"] = {}


class _PathLedgerLock:
    """Reentrant thread/process lock shared by every ledger on one JSONL path."""

    def __init__(self, events_path: str):
        self.events_path = os.path.abspath(events_path)
        self.lock_path = self.events_path + ".lock"
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self):
        self._thread_lock.acquire()
        depth = int(getattr(self._local, "depth", 0))
        try:
            if depth == 0 and fcntl is not None:
                os.makedirs(os.path.dirname(self.lock_path) or ".", exist_ok=True)
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                except Exception:
                    os.close(descriptor)
                    raise
                self._local.descriptor = descriptor
            self._local.depth = depth + 1
            return self
        except Exception:
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback):
        depth = int(getattr(self._local, "depth", 1)) - 1
        self._local.depth = depth
        try:
            if depth == 0 and fcntl is not None:
                descriptor = getattr(self._local, "descriptor", None)
                if descriptor is not None:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)
                        del self._local.descriptor
        finally:
            self._thread_lock.release()


def _shared_ledger_lock(events_path: str) -> _PathLedgerLock:
    key = os.path.abspath(events_path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = _PathLedgerLock(key)
            _PATH_LOCKS[key] = lock
        return lock


class LiveLedger:

    def __init__(self, exchange, events_path: Optional[str] = None,
                 lifecycle_path: Optional[str] = None,
                 logger=None):
        self.exchange = exchange
        if events_path is None or lifecycle_path is None:
            from utils.state_paths import get_state_paths
            sp = get_state_paths()
            events_path = events_path or sp.live_order_events
            lifecycle_path = lifecycle_path or sp.live_position_lifecycle
        self.events_path = events_path
        self.lifecycle_path = lifecycle_path
        self.logger = logger
        self._ledger_lock = _shared_ledger_lock(events_path)
        self._lifecycle: Dict[str, dict] = self._load_lifecycle()
        os.makedirs(os.path.dirname(events_path) or '.', exist_ok=True)

    def _event_io_lock(self) -> _PathLedgerLock:
        lock = getattr(self, "_ledger_lock", None)
        if lock is None:
            lock = _shared_ledger_lock(self.events_path)
            self._ledger_lock = lock
        return lock

    # ── Public API ──────────────────────────────────────────────────────────

    def record_open(self, order_id: str, symbol: str, side: str,
                    amount_usdt: float, leverage: int,
                    estimated_price: float) -> dict:
        fill = self._fetch_fill(order_id, symbol, estimated_price)
        position_id = f"{symbol}-{uuid.uuid4().hex[:8]}-{side}"

        event = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "position_id": position_id,
            "symbol": symbol,
            "event_type": "open",
            "side": side,
            "order_id": order_id,
            "fill_price": fill["fill_price"],
            "filled_amount": fill.get("filled_amount"),
            "fee": fill["fee"],
            "fee_currency": fill.get("fee_currency", "USDT"),
            "amount_usdt": amount_usdt,
            "leverage": leverage,
            "realized_pnl": 0.0,
            "source": fill["source"],
        }
        self._write_event(event)
        self._open_lifecycle(event)
        return event

    def record_add(self, order_id: str, symbol: str, side: str,
                   amount_usdt: float, leverage: int,
                   estimated_price: float) -> dict:
        """加仓记录 — 追加到已有 open lifecycle，不新建"""
        fill = self._fetch_fill(order_id, symbol, estimated_price)
        position_id = self._find_open_position_id(symbol, side)

        if not position_id:
            return self.record_open(order_id, symbol, side,
                                    amount_usdt, leverage, estimated_price)

        event = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "position_id": position_id,
            "symbol": symbol,
            "event_type": "add",
            "side": side,
            "order_id": order_id,
            "fill_price": fill["fill_price"],
            "filled_amount": fill.get("filled_amount"),
            "fee": fill["fee"],
            "fee_currency": fill.get("fee_currency", "USDT"),
            "amount_usdt": amount_usdt,
            "leverage": leverage,
            "realized_pnl": 0.0,
            "source": fill["source"],
        }
        self._write_event(event)
        self._update_lifecycle_pnl(event)
        return event

    def record_reduce(self, order_id: str, symbol: str, side: str,
                      entry_price: float, reduce_usdt: float, leverage: int,
                      estimated_price: float) -> dict:
        fill = self._fetch_fill(order_id, symbol, estimated_price)
        pnl = self._calc_realized_pnl(side, entry_price, fill["fill_price"],
                                       reduce_usdt, leverage, fill["fee"])
        position_id = self._find_open_position_id(symbol, side)

        event = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "position_id": position_id,
            "symbol": symbol,
            "event_type": "reduce",
            "side": side,
            "order_id": order_id,
            "fill_price": fill["fill_price"],
            "filled_amount": fill.get("filled_amount"),
            "fee": fill["fee"],
            "fee_currency": fill.get("fee_currency", "USDT"),
            "amount_usdt": reduce_usdt,
            "leverage": leverage,
            "realized_pnl": pnl,
            "source": fill["source"],
        }
        self._write_event(event)
        self._update_lifecycle_pnl(event)
        return event

    def record_close(self, order_id: str, symbol: str, side: str,
                     entry_price: float, amount_usdt: float, leverage: int,
                     estimated_price: float,
                     close_type: str = "close") -> dict:
        fill = self._fetch_fill(order_id, symbol, estimated_price)
        pnl = self._calc_realized_pnl(side, entry_price, fill["fill_price"],
                                       amount_usdt, leverage, fill["fee"])
        position_id = self._find_open_position_id(symbol, side)

        event = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "position_id": position_id,
            "symbol": symbol,
            "event_type": close_type,
            "side": side,
            "order_id": order_id,
            "fill_price": fill["fill_price"],
            "filled_amount": fill.get("filled_amount"),
            "fee": fill["fee"],
            "fee_currency": fill.get("fee_currency", "USDT"),
            "amount_usdt": amount_usdt,
            "leverage": leverage,
            "realized_pnl": pnl,
            "source": fill["source"],
        }
        self._write_event(event)
        self._close_lifecycle(event)
        return event

    def record_entry_drift_decision(self, *, symbol: str, side: str, gate: str,
                                    band: str, drift_pct: float, decision: str,
                                    reason: Optional[str],
                                    rr_actual: Optional[float],
                                    rr_floor_used: Optional[float],
                                    plan_entry_ref: Optional[float] = None,
                                    live_price: Optional[float] = None,
                                    request_id: str = "") -> None:
        """Record an entry drift gate decision to the live order events jsonl.

        This is observational — does NOT mutate ledger state, does NOT affect PnL.
        Used for downstream slicing of recompute vs original-plan win rates.
        """
        event = {
            'event': 'entry_drift_decision',
            'ts': time.time(),
            'symbol': symbol,
            'side': side,
            'gate': gate,
            'band': band,
            'drift_pct': drift_pct,
            'decision': decision,
            'reason': reason,
            'rr_actual': rr_actual,
            'rr_floor_used': rr_floor_used,
            'plan_entry_ref': plan_entry_ref,
            'live_price': live_price,
            'request_id': request_id,
        }
        self._write_event(event)

    def record_external_close(self, symbol: str, side: str,
                              entry_price: float, amount_usdt: float,
                              leverage: int,
                              order_info: Optional[dict] = None) -> dict:
        """[Deprecated 入口] 等价于 record_pending_external_close。

        历史调用方使用此名称。新代码请改用 record_pending_external_close
        + apply_pnl_resolution(由 RealizedPnlResolver 升级 final)。
        """
        return self.record_pending_external_close(
            symbol=symbol, side=side, entry_price=entry_price,
            amount_usdt=amount_usdt, leverage=leverage,
            estimated_pnl=None,
            position_id=None, entry_request_id=None,
        )

    def record_pending_external_close(self, symbol: str, side: str,
                                       entry_price: float, amount_usdt: float,
                                       leverage: int,
                                       estimated_pnl: Optional[float] = None,
                                       position_id: Optional[str] = None,
                                       entry_request_id: Optional[str] = None,
                                       opened_at: Optional[float] = None,
                                       closed_at: Optional[float] = None,
                                       sl_algo_id: Optional[str] = None,
                                       sl_algo_clord_id: Optional[str] = None,
                                       tp_algo_id: Optional[str] = None,
                                       tp_algo_clord_id: Optional[str] = None,
                                       close_order_id: Optional[str] = None,
                                       close_client_id: Optional[str] = None,
                                       entry_attribution: Optional[dict] = None,
                                       ) -> dict:
        with self._event_io_lock():
            return self._record_pending_external_close_unlocked(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                amount_usdt=amount_usdt,
                leverage=leverage,
                estimated_pnl=estimated_pnl,
                position_id=position_id,
                entry_request_id=entry_request_id,
                opened_at=opened_at,
                closed_at=closed_at,
                sl_algo_id=sl_algo_id,
                sl_algo_clord_id=sl_algo_clord_id,
                tp_algo_id=tp_algo_id,
                tp_algo_clord_id=tp_algo_clord_id,
                close_order_id=close_order_id,
                close_client_id=close_client_id,
                entry_attribution=entry_attribution,
            )

    def _record_pending_external_close_unlocked(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        amount_usdt: float,
        leverage: int,
        estimated_pnl: Optional[float] = None,
        position_id: Optional[str] = None,
        entry_request_id: Optional[str] = None,
        opened_at: Optional[float] = None,
        closed_at: Optional[float] = None,
        sl_algo_id: Optional[str] = None,
        sl_algo_clord_id: Optional[str] = None,
        tp_algo_id: Optional[str] = None,
        tp_algo_clord_id: Optional[str] = None,
        close_order_id: Optional[str] = None,
        close_client_id: Optional[str] = None,
        entry_attribution: Optional[dict] = None,
    ) -> dict:
        """PRD §6.2 + §6.4: 外部平仓立即写 pending 事件,等待 Resolver 升级 final。

        - 不调 fetch_orders/fetch_order(OKX/CCXT 不可靠),realized_pnl 留空
        - close_match_key = position_id 或 (symbol|side|opened_at);
          升级 final 时按此 key 查找 supersedes_event_id
        - lifecycle.reconcile_status 标 pending,realized_pnl 留 0
        - PRD §6.4 + AC-A13:必须持久化 opened_at/closed_at/sl_algo_id/
          sl_algo_clord_id/tp_algo_id/tp_algo_clord_id/entry_attribution,
          否则 Reconciler 无法精确匹配,Reviewer/Judge 无法归因。
        """
        pid = position_id or self._find_open_position_id(symbol, side)
        ts = closed_at or time.time()
        match_key = self._build_close_match_key(
            pid=pid, symbol=symbol, side=side, opened_at=opened_at)
        existing = self._find_pending_event(
            match_key,
            symbol,
            side,
            str(pid or ""),
        )
        if existing and existing.get("close_match_key") == match_key:
            return {**existing, "status": "existing"}

        event = {
            "event_id": str(uuid.uuid4()),
            "ts": ts,
            "position_id": pid,
            "symbol": symbol,
            "event_type": "external_close",
            "side": side,
            "order_id": None,
            "fill_price": None,
            "filled_amount": None,
            "fee": 0.0,
            "fee_currency": "USDT",
            "amount_usdt": amount_usdt,
            "leverage": leverage,
            "realized_pnl": 0.0,            # legacy 字段,pending 留空
            "estimated_pnl": estimated_pnl,
            "realized_pnl_net_usdt": None,
            "source": "estimated",
            "pnl_status": PNL_STATUS_PENDING,
            "pnl_source": "estimated_local",
            "pnl_pending_reason": "awaiting_exchange_resolution",
            "reconcile_status": "pending",
            "close_match_key": match_key,
            "entry_request_id": entry_request_id or "",
            "entry_price": entry_price,
            "correction_seq": 0,
            "opened_at": float(opened_at or 0),
            "closed_at": float(ts),
            "sl_algo_id": sl_algo_id or "",
            "sl_algo_clord_id": sl_algo_clord_id or "",
            "tp_algo_id": tp_algo_id or "",
            "tp_algo_clord_id": tp_algo_clord_id or "",
            "close_order_id": close_order_id or "",
            "close_client_id": close_client_id or "",
            "entry_attribution": entry_attribution or {},
            "attempt_count": 0,
            "last_attempt_at": 0,
            "next_retry_at": 0,
            "last_pending_reason": "",
            "needs_manual_reconcile": False,
        }
        self._write_event(event)
        self._close_lifecycle_pending(event)
        return event

    def apply_pnl_resolution(self, resolution: Dict[str, Any]) -> Optional[dict]:
        with self._event_io_lock():
            return self._apply_pnl_resolution_unlocked(resolution)

    def _apply_pnl_resolution_unlocked(
        self,
        resolution: Dict[str, Any],
    ) -> Optional[dict]:
        """PRD §6.2 + §6.4 + §6.8 P0-a/P0-b: 把 Resolver 结果落到账本

        关键约束(否则违反 §6.8 P0-a/P0-b):
        - status != final 时绝不写 supersede correction(否则 find_pending_external_closes
          会把原 pending 误认为已 superseded,后续 retry/backfill 失效)。
          pending/pending_fx 调 update_pending_resolution_attempt 更新重试 metadata;
          mismatch 写独立告警事件,不带 supersedes_event_id。
        - status=final 严格幂等:相同 (position_id, close_match_key, sorted(order_ids),
          sorted(bill_ids), realized_pnl_net_usdt) 已存在时返回 existing,
          不重复写 correction、不重复加 lifecycle.total_realized_pnl。

        Returns: correction event(成功)/ existing(幂等命中)/ pending update / mismatch alert / None
        """
        status = resolution.get("pnl_status", "")
        symbol = resolution.get("symbol", "")
        side = resolution.get("side", "")
        position_id = resolution.get("position_id", "")
        match_key = self._build_close_match_key(
            pid=position_id, symbol=symbol, side=side,
            opened_at=resolution.get("opened_at"))

        # 非 final 路径:绝不写 supersede,绝不动 lifecycle total_realized_pnl
        if status != PNL_STATUS_FINAL:
            if status in (PNL_STATUS_PENDING, PNL_STATUS_PENDING_FX):
                # pending 路径:只更新原 pending event 的重试 metadata
                return self.update_pending_resolution_attempt(
                    match_key=match_key, symbol=symbol, side=side,
                    position_id=position_id,
                    reason=resolution.get("pnl_pending_reason", "exchange_data_not_ready"),
                    next_retry_at=resolution.get("next_retry_at"),
                )
            # mismatch 路径:写独立告警事件,不 supersede pending,不动 lifecycle total
            alert = {
                "event_id": str(uuid.uuid4()),
                "ts": time.time(),
                "position_id": position_id,
                "symbol": symbol,
                "event_type": "pnl_mismatch_alert",
                "side": side,
                "pnl_status": status,
                "pnl_source": resolution.get("pnl_source", ""),
                "estimated_pnl": resolution.get("estimated_pnl"),
                "exchange_pnl_net_usdt": resolution.get("realized_pnl_net_usdt"),
                "close_match_key": match_key,
                "entry_request_id": resolution.get("entry_request_id", ""),
                "order_ids": resolution.get("order_ids", []),
                "bill_ids": resolution.get("bill_ids", []),
                "warnings": resolution.get("warnings", []),
                "needs_manual_reconcile": True,
            }
            self._write_event(alert)
            if self.logger:
                self.logger.warning(
                    f"[Ledger] mismatch 告警 {symbol} side={side} "
                    f"pid={position_id} warnings={resolution.get('warnings', [])}")
            return alert

        # status == final:严格幂等
        original = self._find_pending_event(match_key, symbol, side, position_id)
        if not original:
            if self.logger:
                self.logger.warning(
                    f"[Ledger] apply_pnl_resolution 未找到 pending: "
                    f"symbol={symbol} side={side} pid={position_id}")
            supersedes = ""
            seq = 1
        else:
            supersedes = original.get("event_id", "")
            seq = int(original.get("correction_seq", 0)) + 1

        realized_net = resolution.get("realized_pnl_net_usdt")
        gross = resolution.get("gross_close_pnl_usdt", 0.0)
        fee = resolution.get("fee_usdt", 0.0)
        funding = resolution.get("funding_usdt", 0.0)

        # 幂等键:重复 final resolution 不得写第二条 correction、不得重复加 lifecycle
        idem_key = self._final_resolution_idem_key(
            position_id=position_id, match_key=match_key,
            order_ids=resolution.get("order_ids", []),
            bill_ids=resolution.get("bill_ids", []),
            realized_net=realized_net,
        )
        existing = self._find_existing_final_correction(idem_key)
        if existing:
            if self.logger:
                self.logger.info(
                    f"[Ledger] final resolution 幂等命中 {symbol} pid={position_id} "
                    f"event_id={existing.get('event_id')}")
            return {**existing, "status": "existing"}

        correction = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "position_id": position_id or (original or {}).get("position_id"),
            "symbol": symbol,
            "event_type": "external_close_correction",
            "side": side,
            "order_id": (resolution.get("order_ids") or [None])[0],
            "fill_price": resolution.get("avg_exit_price", 0) or None,
            "filled_amount": resolution.get("closed_size_contracts", 0) or None,
            "fee": fee,
            "fee_usdt": fee,
            "fee_currency": "USDT",
            "amount_usdt": (original or {}).get("amount_usdt", 0),
            "leverage": (original or {}).get("leverage", 1),
            "realized_pnl": round(realized_net, 4) if realized_net is not None else 0.0,
            "realized_pnl_net_usdt": realized_net,
            "estimated_pnl": (original or {}).get("estimated_pnl"),
            "gross_close_pnl_usdt": gross,
            "funding_usdt": funding,
            "source": resolution.get("pnl_source", "okx_fills_history"),
            "pnl_status": status,
            "pnl_source": resolution.get("pnl_source", ""),
            "pnl_pending_reason": resolution.get("pnl_pending_reason", ""),
            "reconcile_status": "matched",
            "close_match_key": match_key,
            "resolution_id": idem_key,
            "entry_request_id": resolution.get("entry_request_id",
                                               (original or {}).get("entry_request_id", "")),
            "entry_attribution": resolution.get("entry_attribution",
                                                  (original or {}).get("entry_attribution", {})),
            "sl_algo_id": resolution.get("sl_algo_id",
                                          (original or {}).get("sl_algo_id", "")),
            "sl_algo_clord_id": resolution.get("sl_algo_clord_id",
                                                 (original or {}).get("sl_algo_clord_id", "")),
            "tp_algo_id": resolution.get("tp_algo_id",
                                          (original or {}).get("tp_algo_id", "")),
            "tp_algo_clord_id": resolution.get("tp_algo_clord_id",
                                                 (original or {}).get("tp_algo_clord_id", "")),
            "supersedes_event_id": supersedes,
            "correction_seq": seq,
            "order_ids": resolution.get("order_ids", []),
            "bill_ids": resolution.get("bill_ids", []),
            "match_confidence": resolution.get("match_confidence", 0),
            "warnings": resolution.get("warnings", []),
            "exchange_pnl_usdt": resolution.get("exchange_pnl_usdt"),
            "fills_pnl_usdt": resolution.get("fills_pnl_usdt"),
            "close_cause": resolution.get("close_cause", ""),
            "final_close_cause": resolution.get("final_close_cause", ""),
            "is_strategy_stop": bool(resolution.get("is_strategy_stop", False)),
            "close_evidence": resolution.get("close_evidence", {}),
            "tactical_v2_proof": resolution.get("tactical_v2_proof", {}),
            "pnl_delivery_required": True,
        }
        self._write_event(correction)
        self._apply_correction_to_lifecycle(correction, status)
        return correction

    def update_pending_resolution_attempt(self, match_key: str, symbol: str,
                                            side: str, position_id: str,
                                            reason: str,
                                            next_retry_at: Optional[float] = None,
                                            ) -> Optional[dict]:
        with self._event_io_lock():
            return self._update_pending_resolution_attempt_unlocked(
                match_key,
                symbol,
                side,
                position_id,
                reason,
                next_retry_at,
            )

    def _update_pending_resolution_attempt_unlocked(
        self,
        match_key: str,
        symbol: str,
        side: str,
        position_id: str,
        reason: str,
        next_retry_at: Optional[float] = None,
    ) -> Optional[dict]:
        """PRD §6.4 + AC-A5b: pending 期间只更新原 pending event 的重试 metadata,
        绝不写 supersede correction,find_pending_external_closes 仍能查到。

        retry schedule(秒): 10 -> 30 -> 120 -> 600 -> 1800
        attempt_count > 5 且距 opened_at 超 24h → needs_manual_reconcile=True
        """
        original = self._find_pending_event(match_key, symbol, side, position_id)
        if not original:
            if self.logger:
                self.logger.info(
                    f"[Ledger] update_pending_resolution_attempt 未找到 pending: {symbol}")
            return None
        events = self._read_events()
        target_id = original.get("event_id")
        attempt = int(original.get("attempt_count", 0)) + 1
        now = time.time()
        # retry schedule
        retry_delays = [10, 30, 120, 600, 1800]
        idx = min(attempt - 1, len(retry_delays) - 1)
        scheduled = next_retry_at if next_retry_at else now + retry_delays[idx]
        opened_at = original.get("opened_at") or original.get("ts", 0)
        needs_manual = (now - float(opened_at or 0)) > 86400 if opened_at else False

        # 重写整个事件流(JSONL atomic):只修改 target event 的 retry metadata
        try:
            with open(self.events_path, 'r') as f:
                lines = f.readlines()
        except Exception as e:
            if self.logger:
                self.logger.error(f"[Ledger] update_pending_resolution_attempt read fail: {e}")
            return None
        new_lines = []
        updated_event = None
        for line in lines:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except Exception:
                new_lines.append(line + "\n")
                continue
            if ev.get("event_id") == target_id:
                ev["attempt_count"] = attempt
                ev["last_attempt_at"] = now
                ev["next_retry_at"] = scheduled
                ev["last_pending_reason"] = reason
                if needs_manual:
                    ev["needs_manual_reconcile"] = True
                updated_event = ev
                new_lines.append(json.dumps(ev, ensure_ascii=False) + "\n")
            else:
                new_lines.append(line + "\n")
        try:
            tmp_path = self.events_path + ".tmp"
            with open(tmp_path, 'w') as f:
                f.writelines(new_lines)
            os.replace(tmp_path, self.events_path)
        except Exception as e:
            if self.logger:
                self.logger.error(f"[Ledger] update_pending_resolution_attempt write fail: {e}")
            return None
        if self.logger:
            self.logger.info(
                f"[Ledger] {symbol} pending retry#{attempt} reason={reason} "
                f"next_retry_at={scheduled:.0f} manual={needs_manual}")
        return updated_event

    @staticmethod
    def _final_resolution_idem_key(position_id: str, match_key: str,
                                    order_ids: list, bill_ids: list,
                                    realized_net: Optional[float]) -> str:
        """生成 final resolution 幂等键(PRD §6.4 + AC-A5a)。
        相同 position_id + match_key + sorted(order_ids) + sorted(bill_ids) +
        realized_pnl_net_usdt 视为同一笔修正。
        """
        import hashlib
        ords = ",".join(sorted([str(x) for x in (order_ids or [])]))
        bills = ",".join(sorted([str(x) for x in (bill_ids or [])]))
        net = f"{float(realized_net or 0):.6f}"
        raw = f"{position_id}|{match_key}|{ords}|{bills}|{net}"
        return "rid_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _find_existing_final_correction(self, resolution_id: str) -> Optional[dict]:
        if not resolution_id:
            return None
        for ev in self._read_events():
            if ev.get("event_type") != "external_close_correction":
                continue
            if ev.get("pnl_status") != PNL_STATUS_FINAL:
                continue
            if ev.get("resolution_id") == resolution_id:
                return ev
        return None

    def find_pending_external_closes(self,
                                       since_ts: Optional[float] = None,
                                       until_ts: Optional[float] = None,
                                       ) -> List[dict]:
        """返回所有 pnl_status=pending 且未被 correction superseded 的 external_close 事件

        Reconciler / backfill 脚本用此发现待解析的 pending 流水。
        """
        events = self._read_events()
        superseded = {ev.get("supersedes_event_id") for ev in events
                      if ev.get("supersedes_event_id")}
        pending = []
        for ev in events:
            if ev.get("event_type") != "external_close":
                continue
            if ev.get("pnl_status", "") != PNL_STATUS_PENDING:
                continue
            if ev.get("event_id") in superseded:
                continue
            ts = ev.get("ts", 0)
            if since_ts is not None and ts < since_ts:
                continue
            if until_ts is not None and ts > until_ts:
                continue
            pending.append(ev)
        return pending

    def find_final_pnl_corrections(
        self,
        *,
        strategy_owner: Optional[str] = None,
    ) -> List[dict]:
        """Return durable final corrections for restart-safe downstream replay."""
        with self._event_io_lock():
            corrections = []
            for event in self._read_events():
                if event.get("event_type") != "external_close_correction":
                    continue
                if event.get("pnl_status") != PNL_STATUS_FINAL:
                    continue
                if strategy_owner:
                    attribution = event.get("entry_attribution") or {}
                    owner = event.get("strategy_owner") or (
                        attribution.get("strategy_owner")
                        if isinstance(attribution, dict)
                        else ""
                    )
                    if owner != strategy_owner:
                        continue
                corrections.append(dict(event))
            return corrections

    def find_unpublished_final_pnl_corrections(
        self,
        *,
        strategy_owner: Optional[str] = None,
    ) -> List[dict]:
        """Return final corrections without a durable bus-publication ack."""
        with self._event_io_lock():
            events = self._read_events()
            published_ids = {
                str(event.get("correction_event_id") or "")
                for event in events
                if event.get("event_type") == "pnl_correction_published"
            }
            return [
                event
                for event in self.find_final_pnl_corrections(
                    strategy_owner=strategy_owner,
                )
                if str(event.get("event_id") or "") not in published_ids
            ]

    def mark_pnl_correction_published(
        self,
        correction_event_id: str,
        resolution_id: str,
    ) -> Optional[dict]:
        """Append an idempotent outbox ack after the final reaches the bus."""
        correction_event_id = str(correction_event_id or "")
        resolution_id = str(resolution_id or "")
        if not correction_event_id or not resolution_id:
            return None
        with self._event_io_lock():
            for event in self._read_events():
                if (
                    event.get("event_type") == "pnl_correction_published"
                    and event.get("correction_event_id") == correction_event_id
                ):
                    return {**event, "status": "existing"}
            event = {
                "event_id": str(uuid.uuid4()),
                "ts": time.time(),
                "event_type": "pnl_correction_published",
                "correction_event_id": correction_event_id,
                "resolution_id": resolution_id,
            }
            self._write_event(event)
            return event

    def is_pnl_correction_published(self, correction_event_id: str) -> bool:
        correction_event_id = str(correction_event_id or "")
        if not correction_event_id:
            return False
        with self._event_io_lock():
            return any(
                event.get("event_type") == "pnl_correction_published"
                and event.get("correction_event_id") == correction_event_id
                for event in self._read_events()
            )

    def daily_realized_pnl(self, date_str: Optional[str] = None,
                           exclude_paper: bool = True,
                           final_only: bool = True) -> float:
        """计算指定日期的已实现 PnL（默认今天 UTC）

        PRD §6.2/§7: 默认 final_only=True,只把 pnl_status=final 的事件计入；
        external_close pending 事件 realized_pnl=0 也会被过滤。
        若该 pending 已被 correction superseded,以 correction 为准。
        """
        if date_str is None:
            date_str = datetime.datetime.utcnow().strftime('%Y-%m-%d')

        events = self._read_events()
        superseded_ids = {ev.get("supersedes_event_id") for ev in events
                          if ev.get("supersedes_event_id")}
        total = 0.0
        for event in events:
            if event.get("paper"):
                continue
            if event.get("event_id") in superseded_ids:
                continue
            event_date = datetime.datetime.utcfromtimestamp(
                event.get("ts", 0)).strftime('%Y-%m-%d')
            if event_date != date_str:
                continue
            if final_only:
                pnl_status = event.get("pnl_status")
                if pnl_status is None:
                    # 内部 close/reduce 默认 final(从 fill 取真实数据);
                    # external_close 必须显式 pnl_status=final 才计
                    if event.get("event_type") == "external_close":
                        continue
                elif pnl_status != PNL_STATUS_FINAL:
                    continue
            net = event.get("realized_pnl_net_usdt")
            if net is None:
                net = event.get("realized_pnl", 0.0)
            total += float(net or 0.0)
        return round(total, 4)

    def get_lifecycle(self, symbol: str, side: str = None) -> Optional[dict]:
        """获取指定标的的当前/最近生命周期"""
        for pid, lc in reversed(list(self._lifecycle.items())):
            if lc["symbol"] == symbol:
                if side is None or lc["side"] == side:
                    return lc
        return None

    def get_all_closed_lifecycles(self) -> List[dict]:
        return [lc for lc in self._lifecycle.values() if lc["status"] == "closed"]

    # ── Internal: Fill Fetching ─────────────────────────────────────────────

    def _fetch_fill(self, order_id: str, symbol: str,
                    estimated_price: float) -> dict:
        """从交易所获取真实成交数据，失败时降级为估算"""
        try:
            order = self.exchange.fetch_order(order_id, symbol)
            avg_price = order.get("average")
            if avg_price and float(avg_price) > 0:
                fee_info = order.get("fee") or {}
                fee_cost = float(fee_info.get("cost", 0) or 0)
                return {
                    "fill_price": float(avg_price),
                    "fee": abs(fee_cost),
                    "fee_currency": fee_info.get("currency", "USDT"),
                    "filled_amount": float(order.get("filled", 0) or 0),
                    "source": "okx_fill",
                }
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[Ledger] fetch_order 失败 ({symbol} {order_id}): {e}")

        return {
            "fill_price": estimated_price,
            "fee": 0.0,
            "fee_currency": "USDT",
            "filled_amount": None,
            "source": "estimated",
        }

    def _fetch_recent_close_order(self, symbol: str, side: str) -> Optional[dict]:
        """[Deprecated] OKX/CCXT 路径不可靠,新代码走 RealizedPnlResolver。
        保留仅供外部 patch 兼容,主链路已不调用。
        """
        return None

    # ── Internal: PnL Calculation ───────────────────────────────────────────

    def _calc_realized_pnl(self, side: str, entry_price: float,
                           exit_price: float, amount_usdt: float,
                           leverage: int, fee: float) -> float:
        """计算真实已实现 PnL（含手续费）"""
        if entry_price <= 0:
            return 0.0
        notional = amount_usdt * leverage
        if side == 'long':
            gross = (exit_price - entry_price) / entry_price * notional
        else:
            gross = (entry_price - exit_price) / entry_price * notional
        net = gross - fee
        return round(net, 4)

    def _extract_fee(self, order_info: dict) -> float:
        fee_info = order_info.get("fee") or {}
        return abs(float(fee_info.get("cost", 0) or 0))

    # ── Internal: Event I/O ─────────────────────────────────────────────────

    def _write_event(self, event: dict) -> None:
        with self._event_io_lock():
            try:
                with open(self.events_path, 'a') as f:
                    f.write(json.dumps(event, ensure_ascii=False) + '\n')
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[Ledger] 写入事件失败: {e}")

    def _read_events(self) -> List[dict]:
        with self._event_io_lock():
            events = []
            if not os.path.exists(self.events_path):
                return events
            try:
                with open(self.events_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                events.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except Exception:
                pass
            return events

    def read_events_since(self, since_ts: float) -> List[dict]:
        """读取指定时间戳之后的所有事件"""
        return [ev for ev in self._read_events() if ev.get("ts", 0) >= since_ts]

    # ── Internal: Lifecycle Management ──────────────────────────────────────

    def _open_lifecycle(self, event: dict) -> None:
        pid = event["position_id"]
        self._lifecycle[pid] = {
            "position_id": pid,
            "symbol": event["symbol"],
            "side": event["side"],
            "opened_at": event["ts"],
            "closed_at": None,
            "status": "open",
            "entry_price": event["fill_price"],
            "avg_entry_price": event["fill_price"],
            "total_amount_usdt": event.get("amount_usdt", 0),
            "adds_count": 0,
            "events": ["open"],
            "total_realized_pnl": 0.0,
            "total_fee": event["fee"],
            "source": event["source"],
            "reconcile_status": "matched" if event["source"] != "estimated" else "pending",
        }
        self._save_lifecycle()

    def _update_lifecycle_pnl(self, event: dict) -> None:
        pid = event.get("position_id")
        if pid and pid in self._lifecycle:
            lc = self._lifecycle[pid]
            lc["events"].append(event["event_type"])
            lc["total_realized_pnl"] = round(
                lc["total_realized_pnl"] + event["realized_pnl"], 4)
            lc["total_fee"] = round(lc["total_fee"] + event["fee"], 4)
            if event["event_type"] == "add":
                old_total = lc.get("total_amount_usdt", 0)
                add_amount = event.get("amount_usdt", 0)
                new_total = old_total + add_amount
                if new_total > 0:
                    old_avg = lc.get("avg_entry_price", lc.get("entry_price", 0))
                    lc["avg_entry_price"] = round(
                        (old_avg * old_total + event["fill_price"] * add_amount) / new_total, 8)
                lc["total_amount_usdt"] = round(new_total, 4)
                lc["adds_count"] = lc.get("adds_count", 0) + 1
            if event["source"] == "estimated":
                lc["reconcile_status"] = "pending"
            self._save_lifecycle()

    def _close_lifecycle(self, event: dict) -> None:
        pid = event.get("position_id")
        if pid and pid in self._lifecycle:
            lc = self._lifecycle[pid]
            lc["events"].append(event["event_type"])
            lc["total_realized_pnl"] = round(
                lc["total_realized_pnl"] + event["realized_pnl"], 4)
            lc["total_fee"] = round(lc["total_fee"] + event["fee"], 4)
            lc["closed_at"] = event["ts"]
            lc["status"] = "closed"
            if event["source"] == "estimated":
                lc["reconcile_status"] = "pending"
            elif lc.get("reconcile_status") != "pending":
                lc["reconcile_status"] = "matched"
            self._save_lifecycle()

    def _close_lifecycle_pending(self, event: dict) -> None:
        """PRD §6.2: external_close pending 仅落 closed_at + reconcile_status=pending,
        不污染 total_realized_pnl(等 correction 升级)
        """
        pid = event.get("position_id")
        if pid and pid in self._lifecycle:
            lc = self._lifecycle[pid]
            lc["events"].append(event["event_type"])
            lc["closed_at"] = event["ts"]
            lc["status"] = "closed"
            lc["reconcile_status"] = "pending"
            lc["pnl_status"] = PNL_STATUS_PENDING
            lc.setdefault("estimated_pnl", event.get("estimated_pnl"))
            self._save_lifecycle()

    def _apply_correction_to_lifecycle(self, correction: dict, status: str) -> None:
        """把 correction 写回 lifecycle:
        - status=final: total_realized_pnl 累加 realized_pnl_net_usdt,reconcile_status=matched
        - status=mismatch/pending_fx: 只记 warnings,不动 total
        """
        pid = correction.get("position_id")
        if not pid or pid not in self._lifecycle:
            return
        lc = self._lifecycle[pid]
        lc["events"].append(correction["event_type"])
        if status == PNL_STATUS_FINAL:
            net = correction.get("realized_pnl_net_usdt", 0) or 0
            lc["total_realized_pnl"] = round(
                lc.get("total_realized_pnl", 0) + float(net), 4)
            lc["total_fee"] = round(
                lc.get("total_fee", 0) + abs(float(correction.get("fee", 0) or 0)), 4)
            lc["reconcile_status"] = "matched"
            lc["pnl_status"] = PNL_STATUS_FINAL
            lc["pnl_source"] = correction.get("pnl_source", "")
            lc["realized_pnl_net_usdt"] = net
            lc["funding_usdt"] = correction.get("funding_usdt", 0)
        else:
            lc["pnl_status"] = status
            warnings = lc.setdefault("warnings", [])
            for w in correction.get("warnings", []):
                if w not in warnings:
                    warnings.append(w)
        self._save_lifecycle()

    @staticmethod
    def _build_close_match_key(pid: Optional[str], symbol: str, side: str,
                                opened_at: Optional[float]) -> str:
        """生成稳定的匹配 key:优先 position_id,降级 symbol|side|opened_at(秒级 floor)"""
        if pid:
            return f"pid:{pid}"
        ts_bucket = int(opened_at or 0)
        return f"sso:{symbol}|{side}|{ts_bucket}"

    def _find_pending_event(self, match_key: str, symbol: str, side: str,
                             position_id: str = "") -> Optional[dict]:
        """根据 close_match_key 在事件流中找到对应 pending,
        若 key 未命中,降级用 symbol+side+position_id 反向找最近未升级 pending。
        """
        events = self._read_events()
        superseded_ids = {ev.get("supersedes_event_id") for ev in events
                          if ev.get("supersedes_event_id")}
        candidates: List[dict] = []
        for ev in events:
            if ev.get("event_type") != "external_close":
                continue
            if ev.get("pnl_status") != PNL_STATUS_PENDING:
                continue
            if ev.get("event_id") in superseded_ids:
                continue
            if ev.get("close_match_key") == match_key:
                return ev
            if (ev.get("symbol") == symbol and ev.get("side") == side
                    and (not position_id or ev.get("position_id") == position_id)):
                candidates.append(ev)
        if candidates:
            candidates.sort(key=lambda e: e.get("ts", 0), reverse=True)
            return candidates[0]
        return None

    def _find_open_position_id(self, symbol: str, side: str) -> Optional[str]:
        """查找当前 open 状态的 position_id"""
        for pid, lc in self._lifecycle.items():
            if lc["symbol"] == symbol and lc["side"] == side and lc["status"] == "open":
                return pid
        return None

    def _load_lifecycle(self) -> dict:
        if not os.path.exists(self.lifecycle_path):
            return {}
        try:
            with open(self.lifecycle_path, 'r') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_lifecycle(self) -> None:
        try:
            from utils.atomic_io import atomic_write_json
            atomic_write_json(self.lifecycle_path, self._lifecycle)
        except Exception as e:
            if self.logger:
                self.logger.error(f"[Ledger] 保存生命周期失败: {e}")
