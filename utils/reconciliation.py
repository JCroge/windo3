"""定时对账模块 — 比对本地账本与 OKX 账单，发现偏差时告警"""

import time
import datetime
from typing import Optional, List, Dict


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

    def __init__(self, exchange, ledger, logger=None):
        self.exchange = exchange
        self.ledger = ledger
        self.logger = logger
        self._last_check_ts = time.time() - 300

    def check_recent_bills(self, hours: float = 1.0) -> ReconcileResult:
        """拉取最近 N 小时的 OKX 账单，与本地账本对比

        Returns:
            ReconcileResult with query_ok=True/False and mismatches list
        """
        try:
            since_ms = int((time.time() - hours * 3600) * 1000)
            bills = self.exchange.private_get_account_bills({
                'instType': 'SWAP',
                'type': '5',
                'begin': str(since_ms),
            })

            bill_list = bills.get('data', [])
            if not bill_list:
                return ReconcileResult(query_ok=True, mismatches=[])

            # 按 ordId 聚合 bills
            order_pnl = {}
            for bill in bill_list:
                ord_id = bill.get('ordId', '')
                pnl = float(bill.get('pnl', 0))
                symbol = bill.get('instId', '')
                if ord_id:
                    if ord_id not in order_pnl:
                        order_pnl[ord_id] = {'pnl': 0.0, 'symbol': symbol}
                    order_pnl[ord_id]['pnl'] += pnl

            # 与本地 events 对比
            events = self.ledger.read_events_since(time.time() - hours * 3600)
            local_by_order = {}
            for ev in events:
                oid = ev.get('order_id')
                if oid and ev.get('event_type') in ('close', 'reduce', 'force_close', 'external_close'):
                    local_by_order[oid] = ev

            mismatches = []
            for ord_id, bill_info in order_pnl.items():
                local_ev = local_by_order.get(ord_id)
                exchange_pnl = bill_info['pnl']

                if local_ev:
                    local_pnl = local_ev.get('realized_pnl', 0)
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
