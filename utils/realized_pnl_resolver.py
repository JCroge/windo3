"""交易所真实已实现 PnL 解析器 (PRD docs/exchange_realized_pnl_ledger_prd.md §6.1)

外部平仓、SL/TP 触发、清算/ADL 等场景下,本地无法直接拿到成交价/手续费/资金费,
必须从 OKX `fills-history` + `bills` 拉取权威数据,产出净已实现 PnL:

    realized_pnl_net_usdt = Σ fillPnl + Σ fee + Σ funding + Σ realized_reduce_pnl_net_usdt

返回 PnlResolution dict (status ∈ {final, pending, estimated, mismatch, pending_fx});
只有 status=final 才允许进入 Reviewer trade history / Judge archetype cooldown /
RiskGuard daily realized PnL 等最终账本路径。

PRD/验收依据:
  docs/exchange_realized_pnl_ledger_prd.md §4 / §5 / §6.1 / §7
  docs/exchange_realized_pnl_ledger_acceptance.md AC-A2..A9
"""

import time
from typing import Optional, Dict, List, Any


PNL_STATUS_FINAL = "final"
PNL_STATUS_PENDING = "pending"
PNL_STATUS_ESTIMATED = "estimated"
PNL_STATUS_MISMATCH = "mismatch"
PNL_STATUS_PENDING_FX = "pending_fx"

DEFAULT_BILLS_GRACE_MS = 60_000
DEFAULT_LOOKBACK_MS = 30 * 60 * 1000  # 30 分钟


def make_resolution_id(resolution: Dict[str, Any],
                       correction: Optional[Dict[str, Any]] = None) -> str:
    """生成 pnl_resolved/pnl_mismatch 事件的幂等键。

    优先级链:
        corr:<event_id>      — 写 ledger correction 成功(全局唯一)
        sup:<supersedes_id>  — pending → final 链兜底
        key:<close_match_key> — resolver 内部对账键
        pos:<pid>|orders:<sorted>  — 兜底,基于 position_id + 排序后 order_ids

    Returns:
        非空字符串。下游账本类订阅者用此键 LRU 去重。
    """
    if correction:
        event_id = correction.get("event_id")
        if event_id:
            return f"corr:{event_id}"
        sup = correction.get("supersedes_event_id")
        if sup:
            return f"sup:{sup}"
    match_key = resolution.get("close_match_key")
    if match_key:
        return f"key:{match_key}"
    pos_id = resolution.get("position_id", "") or ""
    order_ids = sorted(resolution.get("order_ids") or [])
    return f"pos:{pos_id}|orders:{','.join(order_ids)}"


def _resolution_skeleton(symbol: str, side: str, position_id: str = "",
                         entry_request_id: str = "") -> Dict[str, Any]:
    return {
        "pnl_status": PNL_STATUS_PENDING,
        "pnl_source": "",
        "position_id": position_id,
        "entry_request_id": entry_request_id,
        "symbol": symbol,
        "side": side,
        "pos_side": side,
        "opened_at": 0.0,
        "closed_at": 0.0,
        "order_ids": [],
        "algo_ids": [],
        "bill_ids": [],
        "gross_close_pnl_usdt": 0.0,
        "fee_usdt": 0.0,
        "funding_usdt": 0.0,
        "realized_pnl_net_usdt": None,
        "estimated_pnl": None,
        "avg_exit_price": 0.0,
        "closed_size_contracts": 0.0,
        "match_confidence": 0.0,
        "warnings": [],
        "pnl_pending_reason": "",
        # FR-3C: 外部平仓 final close cause 证据(默认 external_unknown,fail-safe 不计 SL)
        "close_cause": "external_unknown",
        "final_close_cause": "external_unknown",
        "is_strategy_stop": False,
        "close_evidence": {
            "matched_algo_id": "",
            "matched_algo_clord_id": "",
            "matched_order_ids": [],
            "match_rule": "",
            "confidence": 0.0,
        },
    }


def _classify_close_evidence(close_fills: List[dict],
                              bills: Optional[List[dict]],
                              snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """FR-3C: 根据 close fills + bills + position snapshot 分类 final close cause

    优先级:
        sl_algo_id/sl_algo_clord_id 精确匹配 → exchange_sl(confidence=1.0)
        tp_algo_id/tp_algo_clord_id 精确匹配 → exchange_tp(confidence=1.0)
        bills subType 显示 liquidation/ADL → liquidation_or_adl(confidence=0.95)
        close_fills 存在但不属于本系统 algo/order → manual_close(confidence=0.6)
        其他 → external_unknown(confidence=0.0,fail-safe 不计 SL)
    """
    sl_algo_id = (snapshot.get("sl_algo_id") or "").strip()
    sl_algo_clord = (snapshot.get("sl_algo_clord_id") or "").strip()
    tp_algo_id = (snapshot.get("tp_algo_id") or "").strip()
    tp_algo_clord = (snapshot.get("tp_algo_clord_id") or "").strip()

    matched_algo_id = ""
    matched_clord = ""
    matched_orders: List[str] = []
    match_rule = ""
    cause = "external_unknown"
    confidence = 0.0

    for f in close_fills or []:
        algo_id = (f.get("algoId") or "").strip()
        clord_id = (f.get("algoClOrdId") or f.get("clOrdId") or "").strip()
        ord_id = (f.get("ordId") or "").strip()
        # SL 优先(亏损停损是 fail-safe 的关键证据)
        if sl_algo_id and algo_id and algo_id == sl_algo_id:
            cause = "exchange_sl"; match_rule = "sl_algo_id_exact"; confidence = 1.0
            matched_algo_id = algo_id; matched_clord = clord_id
            if ord_id and ord_id not in matched_orders:
                matched_orders.append(ord_id)
            break
        if sl_algo_clord and clord_id and clord_id == sl_algo_clord:
            cause = "exchange_sl"; match_rule = "sl_algo_clord_id_exact"; confidence = 1.0
            matched_algo_id = algo_id; matched_clord = clord_id
            if ord_id and ord_id not in matched_orders:
                matched_orders.append(ord_id)
            break
        if tp_algo_id and algo_id and algo_id == tp_algo_id:
            cause = "exchange_tp"; match_rule = "tp_algo_id_exact"; confidence = 1.0
            matched_algo_id = algo_id; matched_clord = clord_id
            if ord_id and ord_id not in matched_orders:
                matched_orders.append(ord_id)
            # 不 break,继续看是否还有 SL 证据(SL 优先)
        if tp_algo_clord and clord_id and clord_id == tp_algo_clord:
            if cause == "external_unknown":
                cause = "exchange_tp"; match_rule = "tp_algo_clord_id_exact"; confidence = 1.0
                matched_algo_id = algo_id; matched_clord = clord_id
                if ord_id and ord_id not in matched_orders:
                    matched_orders.append(ord_id)

    # liquidation / ADL 证据来自 bills subType(OKX: 100/101=部分爆仓平仓, 102/103/104=ADL)
    if cause == "external_unknown" and bills:
        for b in bills:
            sub = str(b.get("subType", ""))
            if sub in ("100", "101", "102", "103", "104"):
                cause = "liquidation_or_adl"; match_rule = "bills_liquidation_subtype"
                confidence = 0.95
                if b.get("ordId"):
                    matched_orders.append(b["ordId"])
                break

    # close fills 存在但不归属系统 → manual_close(弱证据)
    if cause == "external_unknown" and close_fills:
        cause = "manual_close"; match_rule = "close_fills_unmatched"; confidence = 0.6

    return {
        "close_cause": cause,
        "final_close_cause": cause,
        "is_strategy_stop": (cause == "exchange_sl" and confidence >= 0.9),
        "close_evidence": {
            "matched_algo_id": matched_algo_id,
            "matched_algo_clord_id": matched_clord,
            "matched_order_ids": matched_orders,
            "match_rule": match_rule,
            "confidence": confidence,
        },
    }


class RealizedPnlResolver:
    """OKX fills-history + bills 解析器 (PRD §6.1)

    Args:
        exchange: ccxt OKX 实例(必须有 private_get_trade_fills_history /
                  private_get_account_bills 隐式端点)
        logger: 可选 logger
        bills_grace_ms: bills 时间窗外延(ms),默认 60s
        mismatch_abs_threshold: bills 与 fills 净 PnL 绝对差阈值(USDT)
        mismatch_pct_threshold: 相对差阈值(占 |exchange_pnl|)
    """

    def __init__(self, exchange, logger=None,
                 bills_grace_ms: int = DEFAULT_BILLS_GRACE_MS,
                 mismatch_abs_threshold: float = 0.10,
                 mismatch_pct_threshold: float = 0.05):
        self.exchange = exchange
        self.logger = logger
        self.bills_grace_ms = bills_grace_ms
        self.mismatch_abs = mismatch_abs_threshold
        self.mismatch_pct = mismatch_pct_threshold

    # ── Public API ──────────────────────────────────────────────────────────

    def resolve_external_close(self, position_snapshot: Dict[str, Any],
                                close_window: Optional[Dict[str, Any]] = None
                                ) -> Dict[str, Any]:
        """根据 position 快照 + 时间窗口解析外部平仓 PnL

        position_snapshot 至少含: symbol, side, position_id, entry_request_id,
        opened_at, sl_algo_id (可选), tp_algo_id (可选), unrealized_pnl, stop_loss
        """
        symbol = position_snapshot.get("symbol", "")
        side = position_snapshot.get("side", "")
        pos_side = position_snapshot.get("pos_side", side)
        position_id = position_snapshot.get("position_id", "")
        entry_request_id = position_snapshot.get(
            "entry_request_id", position_snapshot.get("request_id", ""))
        opened_at = float(position_snapshot.get("opened_at",
                                                 position_snapshot.get("open_time", 0)) or 0)
        closed_at = float(close_window.get("closed_at", time.time())) \
            if close_window else time.time()

        resolution = _resolution_skeleton(symbol, side, position_id, entry_request_id)
        resolution["pos_side"] = pos_side
        resolution["opened_at"] = opened_at
        resolution["closed_at"] = closed_at
        # PRD §6.5: 透传入场标识与保护单 algo IDs,供 ledger correction 与下游
        # pnl_resolved/pnl_mismatch 总线事件使用(Reviewer/Judge 按 attribution
        # 聚类 archetype 与 SL hit)。
        resolution["entry_attribution"] = position_snapshot.get(
            "attribution", position_snapshot.get("entry_attribution", {})) or {}
        resolution["sl_algo_id"] = position_snapshot.get("sl_algo_id", "") or ""
        resolution["sl_algo_clord_id"] = position_snapshot.get(
            "sl_algo_clord_id", "") or ""
        resolution["tp_algo_id"] = position_snapshot.get("tp_algo_id", "") or ""
        resolution["tp_algo_clord_id"] = position_snapshot.get(
            "tp_algo_clord_id", "") or ""
        resolution["close_order_id"] = position_snapshot.get(
            "close_order_id", "") or ""
        resolution["close_client_id"] = position_snapshot.get(
            "close_client_id", "") or ""

        # 估算 PnL 兜底(始终先填,即使最后升级为 final 也保留 estimate 用于审计)
        estimated = self._estimate_local(position_snapshot)
        resolution["estimated_pnl"] = estimated

        # 时间窗口
        begin_ms = int((opened_at if opened_at > 0
                        else closed_at - DEFAULT_LOOKBACK_MS / 1000) * 1000) - self.bills_grace_ms
        end_ms = int(closed_at * 1000) + self.bills_grace_ms

        # 1. 拉 fills-history
        fills = self._fetch_fills(symbol, begin_ms, end_ms)
        if fills is None:
            resolution["pnl_status"] = PNL_STATUS_PENDING
            resolution["pnl_source"] = "estimated_local"
            resolution["pnl_pending_reason"] = "fills_history_unavailable"
            resolution["warnings"].append("fills_history_query_failed")
            return resolution

        # 2. 过滤本次平仓的 close fills(subType 在 close 类:long 平仓 sell, short 平仓 buy)
        close_fills = self._filter_close_fills(fills, side, opened_at, closed_at)

        if not close_fills:
            resolution["pnl_status"] = PNL_STATUS_PENDING
            resolution["pnl_source"] = "estimated_local"
            resolution["pnl_pending_reason"] = "no_close_fills_in_window"
            return resolution

        entry_fee_usdt = 0.0
        entry_order_ids: List[str] = []
        if self._is_tactical_v2(position_snapshot):
            owned_close_fills = self._filter_tactical_close_fills(
                close_fills,
                position_snapshot,
            )
            close_ownership = "deterministic_close_command"
            if not owned_close_fills:
                owned_close_fills = self._filter_isolated_tactical_close_fills(
                    close_fills,
                    position_snapshot,
                )
                close_ownership = "isolated_position_single_close_order"
                if not owned_close_fills:
                    resolution["pnl_pending_reason"] = "no_owned_close_fills_in_window"
                    return resolution
            close_fills = owned_close_fills
            entry_fills = [
                fill for fill in fills
                if str(fill.get("clOrdId") or "") == str(entry_request_id)
            ]
            if not entry_fills:
                resolution["pnl_pending_reason"] = "exact_entry_fills_missing"
                return resolution
            entry_agg = self._aggregate_fills(entry_fills)
            close_qty = self._aggregate_fills(close_fills)["total_size"]
            if not self._quantities_match(entry_agg["total_size"], close_qty):
                resolution["pnl_pending_reason"] = "entry_close_quantity_mismatch"
                return resolution
            if entry_agg["fee_currency_pending_fx"]:
                resolution["pnl_status"] = PNL_STATUS_PENDING_FX
                resolution["pnl_source"] = "okx_fills_history"
                resolution["pnl_pending_reason"] = "entry_fee_currency_pending_fx"
                resolution["warnings"].append(
                    f"entry_fee_currency_non_usdt:{entry_agg['non_usdt_fee_ccys']}"
                )
                return resolution
            entry_fee_usdt = float(entry_agg["fee_usdt"])
            entry_order_ids = list(entry_agg["order_ids"])
            resolution["entry_fee_usdt"] = round(entry_fee_usdt, 4)
            resolution["entry_order_ids"] = list(entry_order_ids)
            resolution["tactical_v2_proof"] = {
                "complete": True,
                "entry_request_id": str(entry_request_id),
                "entry_order_ids": list(entry_agg["order_ids"]),
                "close_order_ids": list(
                    self._aggregate_fills(close_fills)["order_ids"]
                ),
                "entry_qty": float(entry_agg["total_size"]),
                "close_qty": float(close_qty),
                "entry_fee_usdt": round(entry_fee_usdt, 4),
                "close_ownership": close_ownership,
            }

        # 3. 多候选检测——同一时间窗口同 side 出现多个 ordId 集且 fillTime 重叠时 ambiguous
        ambiguous = self._detect_ambiguous(close_fills, side, opened_at, closed_at)
        if ambiguous:
            resolution["pnl_status"] = PNL_STATUS_PENDING
            resolution["pnl_source"] = "estimated_local"
            resolution["pnl_pending_reason"] = "ambiguous_close_match"
            resolution["warnings"].append("ambiguous_close_match")
            return resolution

        # 4. 聚合 fills
        agg = self._aggregate_fills(close_fills)
        # FR-3C: 在拉 bills 之前先做一次 evidence 分类(只看 fills),用于 pending_fx 与
        # bills 不可用场景。bills 拿到后再覆盖一次以补 liquidation/ADL 判定。
        evidence = _classify_close_evidence(close_fills, None, position_snapshot)
        resolution["close_cause"] = evidence["close_cause"]
        resolution["final_close_cause"] = evidence["final_close_cause"]
        resolution["is_strategy_stop"] = evidence["is_strategy_stop"]
        resolution["close_evidence"] = evidence["close_evidence"]
        if agg["fee_currency_pending_fx"]:
            resolution["pnl_status"] = PNL_STATUS_PENDING_FX
            resolution["pnl_source"] = "okx_fills_history"
            resolution["warnings"].append(
                f"fee_currency_non_usdt:{agg['non_usdt_fee_ccys']}")
            resolution["gross_close_pnl_usdt"] = agg["gross_pnl"]
            resolution["fee_usdt"] = 0.0  # 不可换算,留空
            resolution["order_ids"] = agg["order_ids"]
            resolution["avg_exit_price"] = agg["avg_exit_price"]
            resolution["closed_size_contracts"] = agg["total_size"]
            return resolution

        total_fee_usdt = agg["fee_usdt"] + entry_fee_usdt
        net_from_fills = agg["gross_pnl"] + total_fee_usdt
        resolution["gross_close_pnl_usdt"] = round(agg["gross_pnl"], 4)
        resolution["fee_usdt"] = round(total_fee_usdt, 4)
        resolution["order_ids"] = agg["order_ids"]
        resolution["avg_exit_price"] = agg["avg_exit_price"]
        resolution["closed_size_contracts"] = agg["total_size"]

        # 5. 拉 bills 校验 + 资金费
        bills = self._fetch_bills(symbol, begin_ms, end_ms)
        if bills is None:
            # bills 不可用——只用 fills,标 final 但 pnl_source 不含 bills
            resolution["pnl_status"] = PNL_STATUS_FINAL
            resolution["pnl_source"] = "okx_fills_history"
            resolution["realized_pnl_net_usdt"] = round(net_from_fills, 4)
            resolution["match_confidence"] = 0.85
            resolution["warnings"].append("bills_query_failed_fills_only")
            return resolution

        bills_summary = self._aggregate_bills(
            bills,
            [*entry_order_ids, *agg["order_ids"]],
            symbol,
        )
        resolution["bill_ids"] = bills_summary["bill_ids"]
        resolution["funding_usdt"] = round(bills_summary["funding_usdt"], 4)

        # FR-3C: 用 bills 复算一次 evidence 以覆盖 liquidation/ADL
        evidence_with_bills = _classify_close_evidence(
            close_fills, bills, position_snapshot)
        # 仅当 bills 把 cause 升级(更高置信)时才覆盖,避免 manual_close 反盖 exchange_sl
        if evidence_with_bills["close_evidence"]["confidence"] > \
                resolution["close_evidence"]["confidence"]:
            resolution["close_cause"] = evidence_with_bills["close_cause"]
            resolution["final_close_cause"] = evidence_with_bills["final_close_cause"]
            resolution["is_strategy_stop"] = evidence_with_bills["is_strategy_stop"]
            resolution["close_evidence"] = evidence_with_bills["close_evidence"]

        # bills 与 fills 一致性校验
        bills_net = bills_summary["bills_net_pnl"]  # 含 pnl + fee subType 5/6/7
        delta = abs(net_from_fills - bills_net)
        threshold = max(self.mismatch_abs, abs(bills_net) * self.mismatch_pct)
        if bills_summary["matched_any"] and delta > threshold:
            resolution["pnl_status"] = PNL_STATUS_MISMATCH
            resolution["pnl_source"] = "okx_fills_history+okx_bills"
            resolution["realized_pnl_net_usdt"] = None
            resolution["exchange_pnl_usdt"] = round(bills_net, 4)
            resolution["fills_pnl_usdt"] = round(net_from_fills, 4)
            resolution["warnings"].append(
                f"fills_bills_mismatch:fills={net_from_fills:.4f},bills={bills_net:.4f}")
            return resolution

        # 终值:fills 净值 + funding(bills 提供)
        final_net = net_from_fills + bills_summary["funding_usdt"]
        resolution["pnl_status"] = PNL_STATUS_FINAL
        resolution["pnl_source"] = "okx_fills_history+okx_bills"
        resolution["realized_pnl_net_usdt"] = round(final_net, 4)
        resolution["match_confidence"] = 0.98 if bills_summary["matched_any"] else 0.9
        return resolution

    def resolve_by_order_id(self, symbol: str, order_id: str,
                             position_id: str = "") -> Dict[str, Any]:
        """精确按 ordId 查 fills+bills(用于已知主动平仓 ID 的场景)"""
        resolution = _resolution_skeleton(symbol, "", position_id)
        try:
            params = {"instType": "SWAP", "instId": symbol, "ordId": order_id}
            resp = self.exchange.private_get_trade_fills_history(params)
            fills = resp.get("data", []) if isinstance(resp, dict) else []
        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"[Resolver] fills-history(ordId={order_id}) 失败: {e}")
            resolution["pnl_pending_reason"] = "fills_history_unavailable"
            resolution["warnings"].append("fills_history_query_failed")
            return resolution

        if not fills:
            resolution["pnl_pending_reason"] = "no_fills_for_order"
            return resolution

        agg = self._aggregate_fills(fills)
        resolution["gross_close_pnl_usdt"] = round(agg["gross_pnl"], 4)
        resolution["fee_usdt"] = round(agg["fee_usdt"], 4)
        resolution["order_ids"] = agg["order_ids"]
        resolution["avg_exit_price"] = agg["avg_exit_price"]
        resolution["closed_size_contracts"] = agg["total_size"]
        if agg["fee_currency_pending_fx"]:
            resolution["pnl_status"] = PNL_STATUS_PENDING_FX
            resolution["pnl_source"] = "okx_fills_history"
            return resolution
        resolution["pnl_status"] = PNL_STATUS_FINAL
        resolution["pnl_source"] = "okx_fills_history"
        resolution["realized_pnl_net_usdt"] = round(
            agg["gross_pnl"] + agg["fee_usdt"], 4)
        resolution["match_confidence"] = 0.85
        return resolution

    def backfill_pending(self, since_ts: float,
                          until_ts: Optional[float] = None
                          ) -> List[Dict[str, Any]]:
        """占位:实际遍历由 LiveLedger.find_pending_external_closes 驱动,
        Reconciler 调用本类的 resolve_external_close 完成升级。这里仅返回空列表,
        留给 scripts/backfill_realized_pnl.py(Phase 3)使用。"""
        return []

    # ── Internal: OKX REST ──────────────────────────────────────────────────

    def _fetch_fills(self, symbol: str, begin_ms: int,
                      end_ms: int) -> Optional[List[dict]]:
        try:
            resp = self.exchange.private_get_trade_fills_history({
                "instType": "SWAP",
                "instId": symbol,
                "begin": str(begin_ms),
                "end": str(end_ms),
                "limit": "100",
            })
            return resp.get("data", []) if isinstance(resp, dict) else []
        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"[Resolver] fills-history({symbol}) 失败: {e}")
            return None

    def _fetch_bills(self, symbol: str, begin_ms: int,
                      end_ms: int) -> Optional[List[dict]]:
        try:
            resp = self.exchange.private_get_account_bills({
                "instType": "SWAP",
                "instId": symbol,
                "begin": str(begin_ms),
                "end": str(end_ms),
                "limit": "100",
            })
            return resp.get("data", []) if isinstance(resp, dict) else []
        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"[Resolver] account/bills({symbol}) 失败: {e}")
            return None

    # ── Internal: aggregation ───────────────────────────────────────────────

    def _filter_close_fills(self, fills: List[dict], side: str,
                             opened_at: float, closed_at: float) -> List[dict]:
        """筛选本次 lifecycle 的 close fills

        long 平仓 = sell, short 平仓 = buy。fillTime 必须落在 [opened_at, closed_at+grace]。
        OKX subType 的 close 类:103=买入平空,104=卖出平多。这里用 side 推导更稳。
        """
        close_side = "sell" if side == "long" else "buy"
        out = []
        end_ts = closed_at + self.bills_grace_ms / 1000
        start_ts = max(opened_at, 0)
        for f in fills:
            try:
                fill_time = float(f.get("fillTime", 0)) / 1000
            except Exception:
                continue
            if start_ts > 0 and fill_time < start_ts:
                continue
            if fill_time > end_ts:
                continue
            f_side = (f.get("side") or "").lower()
            if f_side != close_side:
                continue
            # close 类的 fillPnl 通常非零;open 类也可能有 fillPnl=0,这里靠 side 已经分流
            out.append(f)
        return out

    def _detect_ambiguous(self, close_fills: List[dict], side: str,
                           opened_at: float, closed_at: float) -> bool:
        """同一时间窗口出现多组明显独立的平仓 ordId 视为多候选"""
        if len(close_fills) <= 1:
            return False
        order_ids = {f.get("ordId") for f in close_fills if f.get("ordId")}
        if len(order_ids) <= 1:
            return False
        # 多 ordId 但都在 close_at 前后 ±grace 内可能是同一次平仓多腿——这里保守:
        # 若任意两个 ordId 的 fillTime 跨度 > grace*2,视为 ambiguous
        times = sorted(float(f.get("fillTime", 0)) for f in close_fills)
        if not times:
            return False
        span_ms = times[-1] - times[0]
        return span_ms > 2 * self.bills_grace_ms

    def _aggregate_fills(self, fills: List[dict]) -> Dict[str, Any]:
        gross_pnl = 0.0
        fee_usdt = 0.0
        order_ids: List[str] = []
        total_size = 0.0
        weighted_px = 0.0
        non_usdt_fee_ccys: List[str] = []
        fee_currency_pending_fx = False

        for f in fills:
            try:
                fill_pnl = float(f.get("fillPnl", 0) or 0)
            except Exception:
                fill_pnl = 0.0
            try:
                fee = float(f.get("fee", 0) or 0)  # OKX fee 通常负数
            except Exception:
                fee = 0.0
            try:
                px = float(f.get("fillPx", 0) or 0)
            except Exception:
                px = 0.0
            try:
                sz = float(f.get("fillSz", 0) or 0)
            except Exception:
                sz = 0.0
            fee_ccy = (f.get("feeCcy") or "USDT").upper()
            if fee_ccy != "USDT":
                non_usdt_fee_ccys.append(fee_ccy)
                fee_currency_pending_fx = True
            else:
                fee_usdt += fee  # 负数直接累加
            gross_pnl += fill_pnl
            total_size += sz
            weighted_px += px * sz
            ord_id = f.get("ordId")
            if ord_id and ord_id not in order_ids:
                order_ids.append(ord_id)

        avg_exit_price = (weighted_px / total_size) if total_size > 0 else 0.0
        return {
            "gross_pnl": gross_pnl,
            "fee_usdt": fee_usdt,
            "order_ids": order_ids,
            "total_size": round(total_size, 6),
            "avg_exit_price": round(avg_exit_price, 8),
            "non_usdt_fee_ccys": non_usdt_fee_ccys,
            "fee_currency_pending_fx": fee_currency_pending_fx,
        }

    @staticmethod
    def _is_tactical_v2(position_snapshot: Dict[str, Any]) -> bool:
        attribution = position_snapshot.get("attribution")
        attributed_owner = (
            attribution.get("strategy_owner")
            if isinstance(attribution, dict)
            else ""
        )
        return str(
            position_snapshot.get("strategy_owner") or attributed_owner or ""
        ) == "tactical_v2"

    @staticmethod
    def _quantities_match(entry_qty: Any, close_qty: Any) -> bool:
        try:
            entry_value = float(entry_qty)
            close_value = float(close_qty)
        except (TypeError, ValueError):
            return False
        tolerance = max(abs(entry_value), abs(close_value), 1.0) * 1e-9
        return (
            entry_value > 0
            and close_value > 0
            and abs(entry_value - close_value) <= tolerance
        )

    @staticmethod
    def _filter_tactical_close_fills(
        close_fills: List[dict],
        position_snapshot: Dict[str, Any],
    ) -> List[dict]:
        expected_ids = {
            str(position_snapshot.get(key) or "")
            for key in (
                "tp_algo_clord_id",
                "sl_algo_clord_id",
                "tp_client_id",
                "sl_client_id",
                "close_client_id",
            )
            if position_snapshot.get(key)
        }
        expected_algos = {
            str(value)
            for key in ("tp_algo_ids", "sl_algo_ids")
            for value in position_snapshot.get(key) or ()
            if value
        }
        for key in ("tp_algo_id", "sl_algo_id"):
            value = position_snapshot.get(key)
            if value:
                expected_algos.add(str(value))
        expected_order_ids = {
            str(value)
            for key in ("close_order_ids",)
            for value in position_snapshot.get(key) or ()
            if value
        }
        close_order_id = position_snapshot.get("close_order_id")
        if close_order_id:
            expected_order_ids.add(str(close_order_id))
        return [
            fill
            for fill in close_fills
            if str(fill.get("clOrdId") or fill.get("algoClOrdId") or "")
            in expected_ids
            or str(fill.get("algoId") or "") in expected_algos
            or str(fill.get("ordId") or "") in expected_order_ids
        ]

    @staticmethod
    def _filter_isolated_tactical_close_fills(
        close_fills: List[dict],
        position_snapshot: Dict[str, Any],
    ) -> List[dict]:
        attribution = position_snapshot.get("attribution")
        attributed_intent = (
            attribution.get("intent_id")
            if isinstance(attribution, dict)
            else ""
        )
        intent_id = str(
            position_snapshot.get("intent_id") or attributed_intent or ""
        )
        entry_request_id = str(position_snapshot.get("entry_request_id") or "")
        entry_client_id = str(
            position_snapshot.get("entry_client_id") or entry_request_id
        )
        has_protection_identity = bool(
            (
                position_snapshot.get("tp_client_id")
                or position_snapshot.get("tp_algo_clord_id")
            )
            and (
                position_snapshot.get("sl_client_id")
                or position_snapshot.get("sl_algo_clord_id")
            )
        ) or bool(
            (position_snapshot.get("tp_algo_ids") or position_snapshot.get("tp_algo_id"))
            and (position_snapshot.get("sl_algo_ids") or position_snapshot.get("sl_algo_id"))
        )
        order_ids = {
            str(fill.get("ordId") or "")
            for fill in close_fills
            if fill.get("ordId")
        }
        all_anonymous = all(
            not (
                fill.get("clOrdId")
                or fill.get("algoClOrdId")
                or fill.get("algoId")
            )
            for fill in close_fills
        )
        if not (
            intent_id
            and position_snapshot.get("position_id") == f"tv2:{intent_id}"
            and entry_request_id
            and entry_client_id == entry_request_id
            and has_protection_identity
            and all_anonymous
            and len(order_ids) == 1
        ):
            return []
        return list(close_fills)

    def _aggregate_bills(self, bills: List[dict], close_order_ids: List[str],
                          symbol: str) -> Dict[str, Any]:
        """汇总 bills:
        - subType ∈ {1,2,3,4,5,6,171,174}: matched trade PnL/fee
        - subType ∈ {7}: funding fee
        - 其他不计
        交易账单仅按精确 entry/close ordId 纳入，funding 单独归属 lifecycle。
        """
        bills_net_pnl = 0.0
        funding = 0.0
        bill_ids: List[str] = []
        matched_any = False
        order_id_set = set(close_order_ids)

        for b in bills:
            bill_symbol = str(b.get("instId") or "")
            if bill_symbol and bill_symbol != symbol:
                continue
            sub_type = str(b.get("subType", ""))
            try:
                pnl = float(b.get("pnl", 0) or 0)
            except Exception:
                pnl = 0.0
            try:
                fee = float(b.get("fee", 0) or 0)
            except Exception:
                fee = 0.0
            ord_id = b.get("ordId", "")
            bill_id = b.get("billId", "")
            if sub_type in {"1", "2", "3", "4", "5", "6", "171", "174"}:
                if not ord_id or ord_id not in order_id_set:
                    continue
                bills_net_pnl += pnl + fee
                matched_any = True
                if bill_id:
                    bill_ids.append(bill_id)
            elif sub_type in ("173", "7"):  # 173 = funding fee
                funding += pnl + fee
                if bill_id:
                    bill_ids.append(bill_id)
        return {
            "bills_net_pnl": bills_net_pnl,
            "funding_usdt": funding,
            "bill_ids": bill_ids,
            "matched_any": matched_any,
        }

    # ── Internal: estimation fallback ───────────────────────────────────────

    @staticmethod
    def _estimate_local(pos: Dict[str, Any]) -> Optional[float]:
        """Fallback 估算(与 ContractExecutorAgent._estimate_close_pnl 同口径)"""
        if not pos:
            return None
        unrealized = pos.get("unrealized_pnl")
        if unrealized is not None and unrealized != 0:
            try:
                return round(float(unrealized), 4)
            except Exception:
                return None
        entry = pos.get("entry_price", 0) or 0
        sl = pos.get("stop_loss", 0) or 0
        side = pos.get("side", "")
        amount_usdt = pos.get("amount_usdt", 0) or 0
        leverage = pos.get("leverage", 1) or 1
        if not entry or not sl or not amount_usdt:
            return None
        if side == "long":
            pnl_pct = (sl - entry) / entry
        else:
            pnl_pct = (entry - sl) / entry
        return round(amount_usdt * pnl_pct * leverage, 4)
