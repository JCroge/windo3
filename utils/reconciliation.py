"""定时对账模块 — 比对本地账本与 OKX 账单，发现偏差时告警

PRD §6.1: Reconciler 兼具两个职责
1. check_recent_bills: 拉 OKX 账单与本地 events 对比,advisory 报告(per ordId)
2. auto_resolve_pending: 扫描 ledger 中 pnl_status=pending 的 external_close 事件,
   调 RealizedPnlResolver 升级 final,通过 ledger.apply_pnl_resolution 落账,
   返回升级摘要供调用方发布 pnl_resolved/pnl_mismatch 总线事件。
"""

import time
import datetime
from typing import Optional, List, Dict, Any
from utils.realized_pnl_resolver import make_resolution_id


class ReconcileResult:
    """对账结果：区分 query_ok 和 mismatches"""

    def __init__(self, query_ok: bool, mismatches: List[dict] = None, error: str = None):
        self.query_ok = query_ok
        self.mismatches = mismatches or []
        self.error = error

    @property
    def has_mismatches(self) -> bool:
        return len(self.mismatches) > 0


class Reconciler:

    def __init__(self, exchange, ledger, logger=None, resolver=None):
        self.exchange = exchange
        self.ledger = ledger
        self.logger = logger
        self.resolver = resolver
        self._last_check_ts = time.time() - 300

    def check_recent_bills(self, hours: float = 1.0) -> ReconcileResult:
        """拉取最近 N 小时的 OKX 账单，与本地账本对比

        PRD §6.1: 不再硬编码 type='5'(只 P&L),改聚合所有相关 subType
        (5/174 realized close pnl + 6/171 trading fee + 7/173 funding fee),
        与 fills+bills resolver 同口径。

        Returns:
            ReconcileResult with query_ok=True/False and mismatches list
        """
        try:
            since_ms = int((time.time() - hours * 3600) * 1000)
            bills = self.exchange.private_get_account_bills({
                'instType': 'SWAP',
                'begin': str(since_ms),
            })

            bill_list = bills.get('data', [])
            if not bill_list:
                return ReconcileResult(query_ok=True, mismatches=[])

            # 按 ordId 聚合 bills(realized pnl + fee 相关 subType)
            order_pnl: Dict[str, dict] = {}
            for bill in bill_list:
                ord_id = bill.get('ordId', '')
                sub_type = str(bill.get('subType', ''))
                pnl = float(bill.get('pnl', 0) or 0)
                fee = float(bill.get('fee', 0) or 0)
                symbol = bill.get('instId', '')
                if not ord_id:
                    continue
                if ord_id not in order_pnl:
                    order_pnl[ord_id] = {'pnl': 0.0, 'symbol': symbol}
                if sub_type in ('5', '174'):
                    order_pnl[ord_id]['pnl'] += pnl + fee
                elif sub_type in ('6', '171'):
                    order_pnl[ord_id]['pnl'] += fee
                elif sub_type in ('7', '173'):
                    pass  # funding 单独处理,不计入 close PnL 对账
                else:
                    # 兼容老 mock 测试:无 subType 时按 P&L 计
                    order_pnl[ord_id]['pnl'] += pnl

            # 与本地 events 对比
            events = self.ledger.read_events_since(time.time() - hours * 3600)
            local_by_order: Dict[str, dict] = {}
            for ev in events:
                oid = ev.get('order_id')
                if oid and ev.get('event_type') in (
                        'close', 'reduce', 'force_close', 'external_close',
                        'external_close_correction'):
                    # correction 优先于 pending(后写覆盖前写)
                    local_by_order[oid] = ev

            mismatches = []
            for ord_id, bill_info in order_pnl.items():
                local_ev = local_by_order.get(ord_id)
                exchange_pnl = bill_info['pnl']

                if local_ev:
                    local_pnl = local_ev.get(
                        'realized_pnl_net_usdt', local_ev.get('realized_pnl', 0))
                    if local_pnl is None:
                        local_pnl = 0.0
                    delta = abs(local_pnl - exchange_pnl)
                    threshold = max(0.1, abs(exchange_pnl) * 0.05)
                    if delta > threshold:
                        mismatches.append({
                            'symbol': bill_info['symbol'],
                            'order_id': ord_id,
                            'local_pnl': local_pnl,
                            'exchange_pnl': exchange_pnl,
                            'delta': delta,
                            'status': 'mismatch',
                        })
                else:
                    mismatches.append({
                        'symbol': bill_info['symbol'],
                        'order_id': ord_id,
                        'local_pnl': None,
                        'exchange_pnl': exchange_pnl,
                        'delta': abs(exchange_pnl),
                        'status': 'missing_local',
                    })

            return ReconcileResult(query_ok=True, mismatches=mismatches)

        except Exception as e:
            if self.logger:
                self.logger.warning(f"[Reconciler] 对账查询失败: {e}")
            return ReconcileResult(query_ok=False, error=str(e))

    def should_run(self, interval_sec: float = 600) -> bool:
        """是否到了执行对账的时间"""
        if time.time() - self._last_check_ts >= interval_sec:
            self._last_check_ts = time.time()
            return True
        return False

    def run_and_report(self) -> Optional[str]:
        """执行对账并返回告警摘要（无差异返回 None，查询失败返回错误告警）"""
        result = self.check_recent_bills()

        if not result.query_ok:
            report = f"[对账告警] API查询失败: {result.error}，无法确认PnL一致性"
            if self.logger:
                self.logger.warning(report)
            return report

        if not result.has_mismatches:
            return None

        lines = [f"[对账告警] 发现 {len(result.mismatches)} 笔偏差:"]
        for m in result.mismatches:
            local_str = f"{m['local_pnl']:+.4f}" if m['local_pnl'] is not None else "N/A"
            lines.append(
                f"  {m['symbol']} order={m['order_id']}: "
                f"local={local_str} exchange={m['exchange_pnl']:+.4f} "
                f"delta={m['delta']:.4f} [{m['status']}]"
            )

        report = "\n".join(lines)
        if self.logger:
            self.logger.warning(report)
        return report

    def auto_resolve_pending(self, since_ts: Optional[float] = None,
                              max_attempts: int = 50) -> List[Dict[str, Any]]:
        """PRD §6.5 + AC-A5b: 扫描 pnl_status=pending 的 external_close 事件,
        调 RealizedPnlResolver 尝试升级。返回升级摘要列表(供调用方发布总线事件)。

        retry schedule(秒): 10 -> 30 -> 120 -> 600 -> 1800,通过 next_retry_at 控制;
        24h 仍未 final → needs_manual_reconcile=True,跳过自动重试。

        Reconciler 严格门控:仅 status=final 调 apply_pnl_resolution(写 correction
        + 升级 lifecycle);pending/pending_fx 调 update_pending_resolution_attempt
        只更新重试 metadata,绝不 supersede 原 pending(否则 retry 链断)。
        mismatch 由 apply_pnl_resolution 写独立告警事件,不动 lifecycle。

        每条 summary 含: symbol, position_id, entry_request_id, pnl_status,
        realized_pnl_net_usdt, gross/fee/funding, supersedes_event_id,
        correction_event_id, warnings, pending_event_id。
        """
        if not self.resolver or not self.ledger:
            return []
        if not hasattr(self.ledger, 'find_pending_external_closes'):
            return []
        pending_events = self.ledger.find_pending_external_closes(since_ts=since_ts)
        if not pending_events:
            return []

        results: List[Dict[str, Any]] = []
        now = time.time()
        for ev in pending_events[:max_attempts]:
            # 跳过 needs_manual_reconcile(超 24h 未 resolved)
            if ev.get('needs_manual_reconcile'):
                continue
            # 跳过未到 next_retry_at 的事件
            next_retry = ev.get('next_retry_at')
            if next_retry and now < float(next_retry):
                continue
            opened_at = float(ev.get('opened_at', 0) or 0)
            closed_at = float(ev.get('closed_at', 0) or ev.get('ts', time.time()) or time.time())
            snapshot = {
                'symbol': ev.get('symbol', ''),
                'side': ev.get('side', ''),
                'position_id': ev.get('position_id', ''),
                'entry_request_id': ev.get('entry_request_id', ''),
                # PRD §6.5 #8: opened_at 缺失时 resolver 退回 closed_at - lookback,
                # 若仍无候选会返回 pending + missing_lifecycle_window
                'opened_at': opened_at,
                'unrealized_pnl': ev.get('estimated_pnl', 0),
                'entry_price': ev.get('entry_price', 0),
                'amount_usdt': ev.get('amount_usdt', 0),
                'leverage': ev.get('leverage', 1),
                'sl_algo_id': ev.get('sl_algo_id', ''),
                'sl_algo_clord_id': ev.get('sl_algo_clord_id', ''),
                'tp_algo_id': ev.get('tp_algo_id', ''),
                'tp_algo_clord_id': ev.get('tp_algo_clord_id', ''),
                'attribution': ev.get('entry_attribution', {}),
            }
            close_window = {'closed_at': closed_at}
            try:
                resolution = self.resolver.resolve_external_close(
                    snapshot, close_window)
            except Exception as e:
                if self.logger:
                    self.logger.warning(
                        f"[Reconciler] resolver 失败 {snapshot['symbol']}: {e}")
                continue

            correction = None
            status = resolution.get('pnl_status', '')
            try:
                # PRD §6.8 P0-c:仅 final 调 apply_pnl_resolution(写 correction)。
                # pending/pending_fx 调 update_pending_resolution_attempt 只更新
                # retry metadata。mismatch 由 apply_pnl_resolution 写独立告警。
                if status == 'final':
                    correction = self.ledger.apply_pnl_resolution(resolution)
                elif status in ('pending', 'pending_fx'):
                    if hasattr(self.ledger, 'update_pending_resolution_attempt'):
                        match_key = ev.get('close_match_key', '')
                        self.ledger.update_pending_resolution_attempt(
                            match_key=match_key,
                            symbol=snapshot['symbol'],
                            side=snapshot['side'],
                            position_id=snapshot['position_id'],
                            reason=resolution.get('pnl_pending_reason',
                                                    'exchange_data_not_ready'),
                        )
                else:
                    # mismatch 等终态:写独立告警
                    correction = self.ledger.apply_pnl_resolution(resolution)
            except Exception as e:
                if self.logger:
                    self.logger.warning(
                        f"[Reconciler] apply_pnl_resolution 失败: {e}")

            results.append({
                'symbol': resolution.get('symbol', snapshot['symbol']),
                'position_id': resolution.get('position_id', ''),
                'entry_request_id': resolution.get('entry_request_id', ''),
                'pnl_status': status,
                'pnl_source': resolution.get('pnl_source', ''),
                'realized_pnl_net_usdt': resolution.get('realized_pnl_net_usdt'),
                'estimated_pnl': ev.get('estimated_pnl'),
                'exchange_pnl_usdt': resolution.get('exchange_pnl_usdt'),
                'fills_pnl_usdt': resolution.get('fills_pnl_usdt'),
                'gross_close_pnl_usdt': resolution.get('gross_close_pnl_usdt', 0),
                'fee_usdt': resolution.get('fee_usdt', 0),
                'funding_usdt': resolution.get('funding_usdt', 0),
                'order_ids': resolution.get('order_ids', []),
                'bill_ids': resolution.get('bill_ids', []),
                'match_confidence': resolution.get('match_confidence', 0),
                'warnings': resolution.get('warnings', []),
                'sl_algo_id': resolution.get('sl_algo_id',
                                               ev.get('sl_algo_id', '')),
                'sl_algo_clord_id': resolution.get('sl_algo_clord_id',
                                                      ev.get('sl_algo_clord_id', '')),
                'tp_algo_id': resolution.get('tp_algo_id',
                                               ev.get('tp_algo_id', '')),
                'tp_algo_clord_id': resolution.get('tp_algo_clord_id',
                                                      ev.get('tp_algo_clord_id', '')),
                'entry_attribution': resolution.get('entry_attribution',
                                                       ev.get('entry_attribution', {})),
                'supersedes_event_id': (correction or {}).get('supersedes_event_id', ''),
                'correction_event_id': (correction or {}).get('event_id', ''),
                'pending_event_id': ev.get('event_id', ''),
                # F4-002: final close cause 证据 + 幂等键
                'close_cause': resolution.get('close_cause', ''),
                'final_close_cause': resolution.get('final_close_cause', ''),
                'is_strategy_stop': bool(resolution.get('is_strategy_stop', False)),
                'close_evidence': resolution.get('close_evidence', {}),
                'resolution_id': make_resolution_id(resolution, correction),
            })
        return results
