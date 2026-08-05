"""智能交易执行 Agent - 消费Judge plan，支持动态杠杆/限价单/交易所止损/仓位同步"""

import os
import math
import numbers
import time
import uuid
import asyncio
from dotenv import load_dotenv
from agents.base import BaseAgent
from executor import ContractExecutor, ENTRY_DRIFT_SMALL_PCT
from utils.halt_state import get_halt_state
from utils.reconciliation import Reconciler
from utils.realized_pnl_resolver import (
    RealizedPnlResolver,
    PNL_STATUS_FINAL,
    PNL_STATUS_PENDING,
    PNL_STATUS_PENDING_FX,
    PNL_STATUS_MISMATCH,
    make_resolution_id,
)
from utils.symbol import to_internal
from utils.state_paths import get_state_paths
from utils.event_journal import get_event_journal
from utils.tactical_v2.controller import TacticalV2Controller

load_dotenv()


_TACTICAL_V2_METADATA_KEYS = (
    "strategy_owner",
    "intent_id",
    "episode_id",
    "plan_hash",
    "source_shadow_id",
    "tactical_source",
    "position_id",
    "entry_request_id",
    "entry_client_id",
    "close_order_id",
    "close_client_id",
    "tp_client_id",
    "sl_client_id",
    "tp_algo_id",
    "sl_algo_id",
    "tp_algo_ids",
    "sl_algo_ids",
    "protection_state",
    "tactical_close_reason",
    "close_reason",
)


def _tactical_v2_metadata(*sources) -> dict:
    """Collect owner metadata without inferring ownership from track names."""
    metadata = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        attribution = source.get("attribution") or source.get("entry_attribution")
        if isinstance(attribution, dict):
            for key in _TACTICAL_V2_METADATA_KEYS:
                value = attribution.get(key)
                if value not in (None, "", [], {}):
                    metadata[key] = value
        for key in _TACTICAL_V2_METADATA_KEYS:
            value = source.get(key)
            if value not in (None, "", [], {}):
                metadata[key] = value
    return metadata


class MultiExecutor(BaseAgent):
    name = "executor"
    subscriptions = [
        "trade_decision:*", "tech_analysis:*", "risk_alert",
        "daily_hard_stop_triggered", "system_command", "tactical_candidate.v2",
        "price_tick:*", "pnl_resolved", "pnl_mismatch",
    ]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.executor = None
        self.min_confidence = config.get('min_confidence', 60) if config else 60
        self._sync_counter = 0
        self._trading_halted = False
        self._open_fail_cooldown = {}  # {symbol: expire_timestamp}
        self._halt_state = get_halt_state()
        self._reconciler = None
        self._pnl_resolver = None
        self._tactical_v2_controller = None
        self._tactical_pnl_route_lock = asyncio.Lock()

    async def setup(self):
        exchange_id = self.config.get('exchange', 'okx')
        leverage = self.config.get('leverage', 3)

        self.executor = ContractExecutor(
            exchange_id=exchange_id,
            api_key=os.getenv('OKX_API_KEY') if exchange_id == 'okx' else os.getenv('BINANCE_API_KEY'),
            secret=os.getenv('OKX_SECRET') if exchange_id == 'okx' else os.getenv('BINANCE_SECRET'),
            password=os.getenv('OKX_PASSWORD') if exchange_id == 'okx' else None,
            testnet=self.config.get('use_testnet', False),
            leverage=leverage
        )
        self._reconciler = None
        self._pnl_resolver = None
        try:
            self._pnl_resolver = RealizedPnlResolver(
                self.executor.exchange, logger=self.logger,
            )
        except Exception as e:
            self.logger.warning(f"[Executor] RealizedPnlResolver 初始化失败: {e}")
        if self.executor.ledger:
            self._reconciler = Reconciler(
                self.executor.exchange, self.executor.ledger,
                logger=self.logger, resolver=self._pnl_resolver,
            )
        self._tactical_v2_controller = TacticalV2Controller(
            executor=self.executor,
            config=self.config,
            paths=get_state_paths(),
            logger=self.logger,
            publish=self.publish,
        )
        await self._recover_tactical_v2_startup()
        try:
            replayed = get_event_journal().replay_messages(
                "tactical_candidate.v2",
                namespace=get_state_paths().namespace,
                now=time.time(),
                max_age_seconds=900,
            )
            for replay in replayed:
                await self._tactical_v2_controller.handle_candidate(
                    replay.get("payload") or {},
                    replayed=True,
                )
        except Exception as e:
            self.logger.warning(f"[Tactical V2] 候选重放失败: {e}")
        self.logger.info(f"智能执行Agent就绪: {exchange_id}, 默认杠杆{leverage}x")

    async def on_message(self, msg: dict):
        if msg['type'] == 'daily_hard_stop_triggered':
            self._trading_halted = True
            reason = msg.get('payload', {}).get('reason', 'unknown')
            self._halt_state.halt(reason=reason, triggered_by="reviewer")
            self.logger.critical(f"[熔断] Daily Hard Stop 触发: {reason} — 停止新交易并全平持仓")
            await self._close_all_positions(f"daily_hard_stop_{reason}")
            return

        if msg['type'] == 'system_command':
            cmd = msg.get('payload', {}).get('command', '')
            source = msg.get('payload', {}).get('source', 'telegram')
            if cmd == 'halt':
                self._trading_halted = True
                self._halt_state.halt(reason="manual", triggered_by=source)
                self.logger.warning(f"[手动熔断] 通过{source}触发")
            elif cmd == 'resume':
                await self._handle_resume(source, msg.get('payload', {}))
            elif cmd == 'force_resume':
                # F-TG-001: 先快照,后清除,再 publish audit
                halted_snapshot = (
                    self.executor.get_halted_symbols()
                    if getattr(self, 'executor', None) and hasattr(self.executor, 'get_halted_symbols')
                    else {}
                )
                self._halt_state.force_resume(resume_by=source)
                self._trading_halted = False
                cleared_n = self._safe_clear_symbol_halt(
                    None, source=f"force_resume:{source}"
                )
                if cleared_n > 0:
                    cleared_symbols = [
                        f"{sym} ({info.get('reason', '?')})"
                        for sym, info in halted_snapshot.items()
                    ]
                    self.logger.warning(
                        f"[强制解除熔断 audit] {source} 同时清除 {cleared_n} 个 "
                        f"per-symbol halt: {cleared_symbols} — 请确认根因已排除"
                    )
                    await self.publish('risk_alert', {
                        'type': 'force_resume_cleared_symbol_halts',
                        'cleared_symbols': cleared_symbols,
                        'source': source,
                    })
                self.logger.warning(f"[强制解除熔断] 通过{source}触发，跳过对账")
            elif cmd == 'resume_symbol':
                # F-TG-002: 单 symbol 解锁
                symbol_raw = msg.get('payload', {}).get('symbol', '').strip()
                if not symbol_raw:
                    return
                normalized = self.executor._normalize_symbol(symbol_raw)
                cleared = self.executor.clear_symbol_halt(
                    normalized, source=f"resume_symbol:{source}"
                )
                if cleared > 0:
                    # P2-02: per-symbol halt 已清，但 clear_symbol_halt 不动全局
                    # halt_state。若全局仍 halt，附 global_halt_active=True 让 TG
                    # 诚实回显"全局仍 halt，请用 /resume"，消除恢复语义陷阱。
                    # 防御性读取：缺 _halt_state（不应发生）默认 False，绝不让该提示
                    # 特性拖垮核心 resume 流程。
                    _hs = getattr(self, '_halt_state', None)
                    global_halt_active = bool(_hs and not _hs.can_open_new)
                    await self.publish('risk_alert', {
                        'type': 'symbol_halt_cleared',
                        'symbol': normalized,
                        'source': source,
                        'global_halt_active': global_halt_active,
                    })
                    self.logger.info(
                        f"[ResumeSymbol] {source} 解除 {normalized} per-symbol halt"
                        f"（全局仍 halt={global_halt_active}）"
                    )
                else:
                    await self.publish('risk_alert', {
                        'type': 'symbol_halt_not_found',
                        'symbol': normalized,
                        'source': source,
                    })
            return

        controller = getattr(self, '_tactical_v2_controller', None)
        if msg['type'] == 'tactical_candidate.v2':
            if controller is not None:
                await controller.handle_candidate(msg.get('payload') or {})
        elif msg['type'] == 'price_tick':
            payload = msg.get('payload') or {}
            symbol = msg.get('symbol') or payload.get('symbol')
            if symbol and controller is not None:
                await controller.handle_quote(symbol, payload)
        elif msg['type'] == 'pnl_resolved':
            if controller is not None:
                await controller.handle_pnl_resolution(msg.get('payload') or {})
        elif msg['type'] == 'pnl_mismatch':
            if controller is not None:
                await controller.handle_pnl_mismatch(msg.get('payload') or {})
        elif msg['type'] == 'trade_decision':
            decision = msg['payload']
            symbol = msg.get('symbol') or decision.get('symbol')
            if symbol:
                decision['symbol'] = symbol
            await self._execute_decision(decision)
        elif msg['type'] == 'tech_analysis':
            symbol = msg.get('symbol') or msg.get('payload', {}).get('symbol')
            tech = msg.get('payload') or {}
            self._mark_tactical_thesis_from_tech(symbol, tech)
            if symbol and controller is not None:
                await controller.handle_structure(symbol, tech)
        elif msg['type'] == 'risk_alert':
            await self._handle_risk_alert(msg['payload'])

    async def _route_pnl_event(
        self,
        topic: str,
        payload: dict,
        *,
        symbol: str = "",
    ) -> bool:
        """Apply V2 risk truth before the same final event reaches other agents."""
        correction_event_id = str(payload.get("correction_event_id") or "")
        ledger = getattr(getattr(self, "executor", None), "ledger", None)
        durable_outbox = (
            topic == "pnl_resolved"
            and correction_event_id
            and ledger is not None
            and hasattr(ledger, "mark_pnl_correction_published")
        )

        async def deliver() -> bool:
            controller = getattr(self, '_tactical_v2_controller', None)
            if controller is not None:
                if topic == "pnl_resolved":
                    await controller.handle_pnl_resolution(payload)
                elif topic == "pnl_mismatch":
                    await controller.handle_pnl_mismatch(payload)
            await self.publish(topic, payload, symbol=symbol)
            if durable_outbox:
                await asyncio.to_thread(
                    ledger.mark_pnl_correction_published,
                    correction_event_id,
                    str(payload.get("resolution_id") or ""),
                )
            return True

        if not durable_outbox:
            return await deliver()

        route_lock = getattr(self, "_tactical_pnl_route_lock", None)
        if route_lock is None:
            route_lock = asyncio.Lock()
            self._tactical_pnl_route_lock = route_lock
        async with route_lock:
            published_check = getattr(
                ledger,
                "is_pnl_correction_published",
                None,
            )
            if callable(published_check):
                already_published = await asyncio.to_thread(
                    published_check,
                    correction_event_id,
                )
                if already_published is True:
                    return False
            return await deliver()

    async def _recover_tactical_v2_startup(self) -> None:
        await self._replay_unconsumed_tactical_v2_finals()
        await self._tactical_v2_controller.recover()

    async def _replay_unconsumed_tactical_v2_finals(self) -> None:
        """Replay ledger finals missing from the durable V2 governor after a crash."""
        controller = getattr(self, "_tactical_v2_controller", None)
        ledger = getattr(getattr(self, "executor", None), "ledger", None)
        if (
            controller is None
            or ledger is None
            or not hasattr(ledger, "find_unpublished_final_pnl_corrections")
        ):
            return
        try:
            corrections = await asyncio.to_thread(
                ledger.find_unpublished_final_pnl_corrections,
                strategy_owner="tactical_v2",
            )
        except Exception as exc:
            self.logger.warning(
                f"[Tactical V2] durable final 扫描失败: {exc}"
            )
            return

        replayed = 0
        for correction in corrections:
            payload = self._pnl_payload_from_correction(correction)
            resolution_id = make_resolution_id(correction, correction)
            should_replay = controller.should_replay_durable_pnl_final(payload)
            durable_resolution = controller.governor.resolution_by_id(
                resolution_id
            )
            if not should_replay:
                if (
                    correction.get("pnl_delivery_required") is not True
                    and durable_resolution is not None
                    and hasattr(ledger, "mark_pnl_correction_published")
                ):
                    try:
                        await asyncio.to_thread(
                            ledger.mark_pnl_correction_published,
                            str(correction.get("event_id") or ""),
                            resolution_id,
                        )
                    except Exception as exc:
                        self.logger.warning(
                            f"[Tactical V2] legacy final ack 迁移失败 "
                            f"event_id={correction.get('event_id', '')}: {exc}"
                        )
                continue
            try:
                await self._route_pnl_event(
                    "pnl_resolved",
                    payload,
                    symbol=str(correction.get("symbol") or ""),
                )
            except Exception as exc:
                self.logger.warning(
                    f"[Tactical V2] final correction 重放失败 "
                    f"event_id={correction.get('event_id', '')}: {exc}"
                )
                continue
            replayed += 1
        if replayed:
            self.logger.warning(
                f"[Tactical V2] 启动重放 {replayed} 条未消费 final correction"
            )

    @staticmethod
    def _pnl_payload_from_correction(correction: dict) -> dict:
        attribution = dict(correction.get("entry_attribution") or {})
        payload = {
            "schema_version": 1,
            "symbol": correction.get("symbol", ""),
            "request_id": correction.get("entry_request_id", ""),
            "correlation_id": correction.get("entry_request_id", ""),
            "position_id": correction.get("position_id", ""),
            "entry_request_id": correction.get("entry_request_id", ""),
            "side": correction.get("side", ""),
            "direction": correction.get("side", ""),
            "pnl_status": PNL_STATUS_FINAL,
            "pnl_is_final": True,
            "pnl_source": correction.get("pnl_source", correction.get("source", "")),
            "realized_pnl_net_usdt": correction.get("realized_pnl_net_usdt"),
            "pnl": correction.get("realized_pnl_net_usdt"),
            "estimated_pnl": correction.get("estimated_pnl"),
            "exchange_pnl_usdt": correction.get("exchange_pnl_usdt"),
            "fills_pnl_usdt": correction.get("fills_pnl_usdt"),
            "gross_close_pnl_usdt": correction.get("gross_close_pnl_usdt", 0),
            "fee_usdt": correction.get("fee_usdt", correction.get("fee", 0)),
            "funding_usdt": correction.get("funding_usdt", 0),
            "order_ids": list(correction.get("order_ids") or ()),
            "bill_ids": list(correction.get("bill_ids") or ()),
            "match_confidence": correction.get("match_confidence", 0),
            "warnings": list(correction.get("warnings") or ()),
            "sl_algo_id": correction.get("sl_algo_id", ""),
            "sl_algo_clord_id": correction.get("sl_algo_clord_id", ""),
            "tp_algo_id": correction.get("tp_algo_id", ""),
            "tp_algo_clord_id": correction.get("tp_algo_clord_id", ""),
            "attribution": attribution,
            "supersedes_event_id": correction.get("supersedes_event_id", ""),
            "correction_event_id": correction.get("event_id", ""),
            "close_cause": correction.get("close_cause", ""),
            "final_close_cause": correction.get("final_close_cause", ""),
            "is_strategy_stop": bool(correction.get("is_strategy_stop", False)),
            "close_evidence": correction.get("close_evidence", {}),
            "tactical_v2_proof": correction.get("tactical_v2_proof", {}),
            "pnl_delivery_required": bool(
                correction.get("pnl_delivery_required", False)
            ),
            "resolution_id": make_resolution_id(correction, correction),
            "timestamp": correction.get("ts", time.time()),
        }
        metadata = _tactical_v2_metadata(correction)
        if metadata.get("strategy_owner") == "tactical_v2":
            payload.update(metadata)
            payload["attribution"].update({
                key: value
                for key, value in metadata.items()
                if key in {
                    "strategy_owner",
                    "intent_id",
                    "episode_id",
                    "plan_hash",
                    "source_shadow_id",
                    "tactical_source",
                }
            })
        return payload

    def _position_for_symbol(self, symbol: str):
        positions = getattr(getattr(self, 'executor', None), 'positions', {}) or {}
        if not symbol:
            return None
        internal = to_internal(symbol)
        candidates = (symbol, internal, f"{internal}-SWAP")
        for key in candidates:
            if key in positions:
                return positions[key]
        return None

    def _mark_tactical_thesis_from_tech(self, symbol: str, tech: dict):
        position = self._position_for_symbol(symbol)
        if (
            not position
            or position.get('track') != 'tactical'
            or position.get('strategy_owner') == 'tactical_v2'
        ):
            return

        side = position.get('side')
        timing = (tech or {}).get('entry_timing', {}) or {}
        block_key = 'tf_15m_block_long' if side == 'long' else 'tf_15m_block_short'
        if not timing.get(block_key):
            return

        position['tactical_thesis_state'] = 'invalidated'
        position['tactical_thesis_reason'] = timing.get('tf_15m_reason', '15m_opposing_block')
        position['tactical_thesis_event_at'] = time.time()
        logger = getattr(self, 'logger', None)
        if logger:
            logger.info(
                f"[Tactical] {symbol} thesis invalidated by 15m block: "
                f"{position['tactical_thesis_reason']}"
            )

    async def _execute_decision(self, decision: dict):
        action = decision.get('action', 'hold')
        confidence = decision.get('confidence', 0)
        symbol = decision.get('symbol')
        plan = decision.get('plan')
        source = decision.get('source', '')
        size_pct = decision.get('size_pct', 1.0)
        request_id = decision.get('request_id', '')

        if self._trading_halted or not self._halt_state.can_open_new:
            if action in ('open_long', 'open_short'):
                reason = "halted" if self._trading_halted else "reconciliation_pending"
                self.logger.warning(f"[熔断] 拒绝执行: {symbol} {action} ({reason})")
                await self.publish("execution_result", self._build_execution_result(
                    status="rejected", action=action, symbol=symbol,
                    source="executor_reject", reason=reason, request_id=request_id,
                ), symbol=symbol)
                return

        if action == 'hold' or not symbol:
            return

        if confidence < self.min_confidence:
            self.logger.info(f"[执行] {symbol} 跳过：置信度不足 ({confidence} < {self.min_confidence})")
            if action in ('open_long', 'open_short'):
                await self.publish("execution_result", self._build_execution_result(
                    status="rejected", action=action, symbol=symbol,
                    source="executor_reject", reason="low_confidence", request_id=request_id,
                ), symbol=symbol)
            return

        # normalize symbol 确保与持仓key一致
        norm_symbol = self.executor._normalize_symbol(symbol)

        controller = getattr(self, '_tactical_v2_controller', None)
        if (action in ('open_long', 'open_short')
                and controller is not None
                and controller.blocks_main_symbol(norm_symbol)):
            await self.publish("execution_result", self._build_execution_result(
                status="rejected", action=action, symbol=symbol,
                source="executor_reject", reason="tactical_pending_symbol",
                request_id=request_id,
            ), symbol=symbol)
            return

        # 只对新增风险动作（open/add）执行回撤风控检查；close/reduce 不拦截
        if action in ('open_long', 'open_short'):
            balance = self._get_balance()
            if balance < 0:
                self.logger.warning(f"[执行] {symbol} 跳过：余额获取失败")
                await self.publish("execution_result", self._build_execution_result(
                    status="rejected", action=action, symbol=symbol,
                    source="executor_reject", reason="balance_fetch_failed", request_id=request_id,
                ), symbol=symbol)
                return
            can_trade, reason = self.executor.risk_manager.check_can_trade(balance)
            if not can_trade:
                self.logger.warning(f"[执行] {symbol} 风控拒绝: {reason}")
                await self.publish("execution_result", self._build_execution_result(
                    status="rejected", action=action, symbol=symbol,
                    source="executor_reject", reason=reason, request_id=request_id,
                ), symbol=symbol)
                return

        try:
            position = self.executor.get_position(norm_symbol)
        except Exception as e:
            self.logger.error(f"[执行] {symbol} 获取持仓失败: {e}")
            await self.publish("execution_result", self._build_execution_result(
                status="error", action=action, symbol=symbol,
                source="executor_reject", reason=str(e), request_id=request_id,
            ), symbol=symbol)
            return

        if (
            position is not None
            and position.get('strategy_owner') == 'tactical_v2'
        ):
            self.logger.warning(
                f"[OwnerIsolation] {symbol} Tactical V2 拒绝 Main trade_decision {action}"
            )
            await self.publish("execution_result", self._build_execution_result(
                status="rejected", action=action, symbol=symbol,
                source="executor_reject", reason="strategy_owner_isolated",
                request_id=request_id,
                strategy_owner="tactical_v2",
                intent_id=position.get("intent_id"),
            ), symbol=symbol)
            return

        result = None

        if action in ('open_long', 'open_short') and position is None:
            if source == 'position_analyst':
                self.logger.warning(f"[执行] {symbol} PA加仓信号但无持仓（已被外部平仓），忽略")
                await self.publish("execution_result", self._build_execution_result(
                    status="rejected", action=action, symbol=symbol,
                    source="executor_reject", reason="no_position_for_add", request_id=request_id,
                ), symbol=symbol)
                return
            cooldown_until = self._open_fail_cooldown.get(norm_symbol, 0)
            if time.time() < cooldown_until:
                self.logger.info(f"[执行] {symbol} 开仓失败冷却中，跳过")
                await self.publish("execution_result", self._build_execution_result(
                    status="rejected", action=action, symbol=symbol,
                    source="executor_reject", reason="open_cooldown", request_id=request_id,
                ), symbol=symbol)
                return
            side = 'long' if action == 'open_long' else 'short'
            try:
                if plan:
                    plan['request_id'] = request_id
                    result = await self._execute_with_plan(symbol, side, plan)
                else:
                    result = await self._execute_legacy(symbol, side, decision)
            except Exception as e:
                self._open_fail_cooldown[norm_symbol] = time.time() + 120
                self.logger.error(f"[执行] {symbol} 开仓失败(120s冷却): {e}")
                await self.publish("execution_result", self._build_execution_result(
                    status="error", action=action, symbol=symbol,
                    source="executor_open", reason=str(e), request_id=request_id,
                ), symbol=symbol)
                return

        elif action in ('open_long', 'open_short') and position is not None and source == 'position_analyst':
            side = 'long' if action == 'open_long' else 'short'
            if position.get('track') == 'tactical':
                await self.publish("execution_result", self._build_execution_result(
                    status="rejected", action="add", symbol=symbol,
                    source="executor_reject", reason="tactical_no_add", request_id=request_id,
                ), symbol=symbol)
                return
            if position.get('side') != side:
                self.logger.warning(f"[执行] {symbol} 加仓方向冲突: 持仓{position['side']} vs 请求{side}，跳过")
                await self.publish("execution_result", self._build_execution_result(
                    status="rejected", action="add", symbol=symbol,
                    source="executor_reject", reason="add_direction_conflict", request_id=request_id,
                ), symbol=symbol)
                return
            cooldown_until = self._open_fail_cooldown.get(norm_symbol, 0)
            if time.time() < cooldown_until:
                self.logger.info(f"[执行] {symbol} 开仓失败冷却中，跳过加仓")
                await self.publish("execution_result", self._build_execution_result(
                    status="rejected", action="add", symbol=symbol,
                    source="executor_reject", reason="add_cooldown", request_id=request_id,
                ), symbol=symbol)
                return
            try:
                result = await self._execute_add_position(norm_symbol, side, position, size_pct)
            except Exception as e:
                self._open_fail_cooldown[norm_symbol] = time.time() + 120
                self.logger.error(f"[执行] {symbol} 加仓失败(120s冷却): {e}")
                await self.publish("execution_result", self._build_execution_result(
                    status="error", action="add", symbol=symbol,
                    source="executor_open", reason=str(e), request_id=request_id,
                ), symbol=symbol)
                return

        elif action == 'close' and position is not None:
            try:
                if size_pct < 1.0 and source == 'position_analyst':
                    # F4-001: 部分平仓走 _classify_reduce_outcome 分流，失败结果不再
                    # 被广播为 risk_reduced。该路径自己 publish + return，不走下方通用块。
                    result = await asyncio.to_thread(
                        self.executor.reduce_position, norm_symbol, size_pct
                    )
                    classification = self._classify_reduce_outcome(result, size_pct)
                    override_action = classification["action_override"] or action

                    if isinstance(result, dict):
                        result['entry_request_id'] = (position or {}).get('request_id', '')
                        result['exit_request_id'] = request_id

                    payload = self._build_execution_result(
                        status=classification["status"],
                        action=override_action,
                        symbol=symbol,
                        source="executor_close",
                        request_id=request_id,
                        result=result if isinstance(result, dict) else {},
                        reason=classification["reason"],
                    )
                    payload["confidence"] = confidence
                    payload["used_plan"] = plan is not None
                    attribution = decision.get('attribution')
                    if attribution:
                        payload['attribution'] = attribution
                        if isinstance(result, dict):
                            result['attribution'] = attribution

                    if classification["status"] == "risk_reduced":
                        payload["reduce_pct"] = classification["actual_reduce_pct"]
                        payload["protection_state"] = classification["protection_state"]
                        payload["protective_update_state"] = classification["protective_update_state"]
                        if classification["protection_failed"]:
                            payload["protection_failed"] = True
                    elif classification["status"] == "executed" and override_action == "close":
                        # dust_closed: 视为平仓终态
                        payload["protection_state"] = classification["protection_state"]
                        payload["protective_update_state"] = classification["protective_update_state"]
                        payload["reduce_origin"] = True
                    # rejected / reduce_failed: 不带 reduce_pct，reason 已 set

                    await self.publish("execution_result", payload, symbol=symbol)
                    self.logger.info(
                        f"[执行] {symbol} reduce {classification['status']} "
                        f"reason={classification['reason']} "
                        f"actual_pct={classification['actual_reduce_pct']:.4f}"
                    )
                    return
                else:
                    # 全平 — FR-003: close_position() 内部清理保护单,
                    # Agent 不再直接撤 sl_order_id
                    result = self.executor.close_position(norm_symbol)
                    if result:
                        self.logger.info(f"[执行] {symbol} 平仓 PnL={result.get('pnl', 0):.2f}")
            except Exception as e:
                self.logger.error(f"[执行] {symbol} 平仓失败: {e}")
                await self.publish("execution_result", self._build_execution_result(
                    status="error", action=action, symbol=symbol,
                    source="executor_close", reason=str(e), request_id=request_id,
                ), symbol=symbol)
                return

        elif action in ('open_long', 'open_short') and position is not None:
            pos_side = position.get('side', 'unknown')
            req_side = 'long' if action == 'open_long' else 'short'
            reason = 'position_exists_same_side' if pos_side == req_side else 'position_exists_opposite_side'
            self.logger.debug(f"[执行] {symbol} 已有持仓({pos_side})，拒绝开仓信号(source={source})")
            await self.publish("execution_result", self._build_execution_result(
                status="rejected", action=action, symbol=symbol,
                source="executor_reject", reason=reason, request_id=request_id,
            ), symbol=symbol)
            return

        if result:
            # AC2-04: write request_id into result for downstream consumers
            if action in ('open_long', 'open_short'):
                result['request_id'] = request_id
                result['entry_request_id'] = request_id
            elif action == 'close':
                result['entry_request_id'] = (position or {}).get('request_id', '')
                result['exit_request_id'] = request_id
            exec_source = "executor_open" if action in ('open_long', 'open_short') else "executor_close"
            payload = self._build_execution_result(
                status="executed", action=action, symbol=symbol,
                source=exec_source, request_id=request_id, result=result,
            )
            payload["confidence"] = confidence
            payload["used_plan"] = plan is not None
            # RQ-07: 传递归因字段
            attribution = decision.get('attribution')
            if attribution:
                payload['attribution'] = attribution
                result['attribution'] = attribution
            if source == 'position_analyst' and action in ('open_long', 'open_short'):
                payload["is_add"] = True
            # NOTE: partial close (size_pct < 1.0, source=position_analyst) now publishes
            # its own payload via _classify_reduce_outcome and returns early — it never
            # reaches this common block. The old status="risk_reduced" patch is removed.
            await self.publish("execution_result", payload, symbol=symbol)
            await self._drain_drift_alerts()
        elif action in ('open_long', 'open_short'):
            self.logger.warning(f"[执行] {symbol} 底层返回None，发布rejected终态")
            # Inspect pending drift alerts BEFORE draining to override reject reason
            pending = getattr(self.executor, '_pending_drift_alerts', None) or []
            drift_reason = None
            for alert in pending:
                if alert.get('type') == 'entry_drift_abandoned':
                    drift_reason = 'drift_too_large'
                    break
                if alert.get('type') == 'entry_drift_rr_fail':
                    drift_reason = 'drift_rr_floor_fail'
                    break
            reject_reason = drift_reason or "unknown_none_result"
            drift_attr = self._build_drift_attribution(pending)
            extra_kwargs = {'attribution': {'entry_drift': drift_attr}} if drift_attr else {}
            await self.publish("execution_result", self._build_execution_result(
                status="rejected", action=action, symbol=symbol,
                source="executor_open", reason=reject_reason, request_id=request_id,
                **extra_kwargs,
            ), symbol=symbol)
            await self._drain_drift_alerts()

    @staticmethod
    def _build_drift_attribution(pending_alerts: list):
        """From the buffered alerts, derive the attribution.entry_drift dict."""
        for alert in pending_alerts or []:
            t = alert.get('type')
            if t == 'entry_drift_abandoned':
                return {
                    'band': 'abandon', 'decision': 'abandon',
                    'drift_pct': alert.get('drift_pct'),
                    'reason': 'drift_too_large',
                    'gate': alert.get('gate'),
                }
            if t == 'entry_drift_rr_fail':
                return {
                    'band': 'medium' if (alert.get('drift_pct') or 0) > ENTRY_DRIFT_SMALL_PCT else 'small',
                    'decision': 'recalc_fail',
                    'drift_pct': alert.get('drift_pct'),
                    'reason': 'drift_rr_floor_fail',
                    'rr_actual': alert.get('rr_actual'),
                    'rr_floor_used': alert.get('rr_floor_used'),
                    'gate': alert.get('gate'),
                }
        return None

    async def _drain_drift_alerts(self) -> None:
        """Drain root executor's pending drift alerts and publish them as risk_alert events."""
        ex = getattr(self, 'executor', None)
        pending = getattr(ex, '_pending_drift_alerts', None) if ex else None
        if not pending:
            return
        alerts = list(pending)
        pending.clear()
        for alert in alerts:
            atype = alert.get('type', '')
            if atype == 'pullback_unfilled':
                self.logger.info(
                    f"[Pullback] {alert.get('symbol')} {alert.get('side')} "
                    f"limit @ {alert.get('limit_price')} "
                    f"timeout={alert.get('timeout_sec')}s 未成交（live）"
                )
            try:
                await self.publish('risk_alert', alert)
            except Exception as e:
                self.logger.warning(f"[Drift Alert] publish failed: {e}")

    async def _execute_with_plan(self, symbol: str, side: str, plan: dict) -> dict:
        """基于Judge plan智能执行（限价单可能阻塞30s，用线程池避免冻结事件循环）"""
        self.logger.info(
            f"[执行] {symbol} 智能开仓: {side}, "
            f"杠杆={plan.get('leverage')}x, "
            f"类型={plan.get('order_type')}, "
            f"仓位={plan.get('size_usdt')} USDT"
        )
        result = await asyncio.to_thread(
            self.executor.open_position_with_plan, symbol, side, plan
        )
        return result

    async def _execute_legacy(self, symbol: str, side: str, decision: dict) -> dict:
        """兼容旧格式（无plan时）"""
        size_pct = decision.get('size_pct', 0.5)
        max_amount = self.config.get('max_trade_amount', 10)
        amount = max_amount * size_pct

        if side == 'long':
            result = await asyncio.to_thread(self.executor.open_long, symbol, amount)
        else:
            result = await asyncio.to_thread(self.executor.open_short, symbol, amount)

        if result:
            self.logger.info(f"[执行] {symbol} 旧模式开{side} {amount:.2f} USDT")
        return result

    async def _execute_add_position(self, symbol: str, side: str, position: dict, size_pct: float) -> dict:
        """加仓：在已有持仓方向上追加仓位"""
        result = await asyncio.to_thread(
            self.executor.add_to_position, symbol, side, size_pct
        )
        if result:
            self.logger.info(
                f"[执行] {symbol} 加仓{int(size_pct*100)}%: "
                f"新增{result.get('add_amount_usdt', 0):.2f} USDT"
            )
        return result

    def _safe_clear_symbol_halt(self, symbol=None, source: str = "unknown") -> int:
        """F-TG-001: 调用 root executor.clear_symbol_halt 的 fail-soft 包装。

        如果 self.executor 不存在(单测/启动早期场景),返回 0 不抛 AttributeError。
        生产环境 self.executor 必然存在,这只是防御性单测兜底。
        """
        ex = getattr(self, 'executor', None)
        if ex is None:
            return 0
        try:
            return ex.clear_symbol_halt(symbol, source=source)
        except AttributeError:
            # ex 可能是 mock 不实现 clear_symbol_halt
            return 0

    async def _handle_resume(self, source: str, payload: dict):
        """Executor 是 resume 的唯一 owner：执行对账后决定是否恢复交易"""
        reconciliation_result = payload.get('reconciliation_result')

        if reconciliation_result and reconciliation_result.get('status') == 'matched':
            self._halt_state.confirm_resume(resume_by=source, reconcile_ok=True)
            self._trading_halted = False
            self._safe_clear_symbol_halt(None, source=f"_handle_resume:{source}")  # F-TG-001
            self.logger.info(f"[解除熔断] 通过{source}触发，对账通过")
            return

        # P2-03: 非 matched 对账结果一律 fail-closed 维持熔断。常规 resume 的唯一恢复
        # 条件是带 matched 对账结果（见上分支）；想绕过对账恢复须走 /force_resume。
        # 不再调用 PnL 账本 Reconciler 上不存在的 reconcile（历史重构遗留 bug，AttributeError
        # 被 except 吞），也不再在无 reconciler 时无条件恢复（旧 else 的 fail-open 隐患）。
        self._halt_state.confirm_resume(resume_by=source, reconcile_ok=False)
        self.logger.warning(
            f"[熔断维持] {source} resume 未带 matched 对账结果，维持熔断；"
            f"如需跳过对账请用 /force_resume"
        )

    async def _handle_risk_alert(self, alert: dict):
        """处理RiskGuard风险警报"""
        # P2-06: 结构性 source 守卫。paper 与 live 共用 risk_alert topic，
        # paper 来源事件 MUST NOT 驱动任何 live 平仓/缩仓/halt——隔离靠此守卫，
        # 不依赖"paper alert type 恰好不在 live 白名单内"的脆性巧合。
        if alert.get('source') == 'paper_executor':
            return
        alert_type = alert.get('type', '')
        scope = alert.get('scope', 'symbol')  # 'symbol' 或 'market'
        self.logger.warning(f"[风控警报] 收到: {alert_type} scope={scope}")

        if alert_type == 'emergency_close':
            symbol = alert.get('symbol')
            reason = alert.get('reason', 'emergency_close')
            norm_sym = self.executor._normalize_symbol(symbol) if symbol else None
            if not norm_sym:
                return
            position = self.executor.get_position(norm_sym)
            if position is None:
                self.logger.debug(f"[风控] {norm_sym} emergency_close 时持仓已不存在（主路径已平），忽略")
                return
            if position.get('strategy_owner') == 'tactical_v2':
                await self._close_tactical_v2_for_safety(norm_sym, reason)
                return
            self.logger.critical(f"[风控兜底平仓] {norm_sym} 因 {reason}")
            pos = self.executor.positions.get(norm_sym)
            entry_req_id = (pos or {}).get('request_id', '')
            # FR-003: close_position() 内部撤保护单,Agent 不再直接 cancel
            result = self.executor.close_position(norm_sym)
            if result:
                result['entry_request_id'] = entry_req_id
                payload = self._build_execution_result(
                    status="force_closed", action="close", symbol=symbol,
                    source="risk_alert", reason=reason, result=result,
                    request_id=entry_req_id,
                )
                await self.publish("execution_result", payload, symbol=symbol)
            return

        if alert_type == 'flash_move':
            symbol = alert.get('symbol')
            if scope == 'market':
                await self._close_all_positions("flash_move_market")
                return
            norm_sym = self.executor._normalize_symbol(symbol) if symbol else None
            if norm_sym and self.executor.get_position(norm_sym):
                position = self.executor.get_position(norm_sym)
                if position.get('strategy_owner') == 'tactical_v2':
                    await self._close_tactical_v2_for_safety(norm_sym, "flash_move")
                    return
                self.logger.warning(f"[风控平仓] {norm_sym} 因闪崩警报")
                pos = self.executor.positions.get(norm_sym)
                entry_req_id = (pos or {}).get('request_id', '')
                # FR-003: close_position() 内部撤保护单,Agent 不再直接 cancel
                result = self.executor.close_position(norm_sym)
                if result:
                    result['entry_request_id'] = entry_req_id
                    payload = self._build_execution_result(
                        status="force_closed", action="close", symbol=symbol,
                        source="risk_alert", reason="flash_move", result=result,
                        request_id=entry_req_id,
                    )
                    await self.publish("execution_result", payload, symbol=symbol)

        elif alert_type == 'max_drawdown':
            await self._close_all_positions("最大回撤触发")

        elif alert_type in ('position_danger', 'high_leverage_danger', 'trailing_stop'):
            symbol = alert.get('symbol')
            norm_sym = self.executor._normalize_symbol(symbol) if symbol else None
            if norm_sym and self.executor.get_position(norm_sym):
                position = self.executor.get_position(norm_sym)
                if position.get('strategy_owner') == 'tactical_v2':
                    await self._close_tactical_v2_for_safety(norm_sym, alert_type)
                    return
                self.logger.warning(f"[风控] {alert_type}: 平仓 {norm_sym}")
                pos = self.executor.positions.get(norm_sym)
                entry_req_id = (pos or {}).get('request_id', '')
                # FR-003: close_position() 内部撤保护单,Agent 不再直接 cancel
                result = self.executor.close_position(norm_sym)
                if result:
                    result['entry_request_id'] = entry_req_id
                    payload = self._build_execution_result(
                        status="force_closed", action="close", symbol=symbol,
                        source="risk_alert", reason=alert_type, result=result,
                        request_id=entry_req_id,
                    )
                    await self.publish("execution_result", payload, symbol=symbol)

        elif alert_type in ('portfolio_exposure', 'correlation_risk'):
            positions = self.executor.get_all_positions()
            positions = {
                symbol: position
                for symbol, position in positions.items()
                if position.get('strategy_owner') != 'tactical_v2'
            }
            if not positions:
                return
            largest_sym = max(positions, key=lambda s: positions[s].get('amount_usdt', 0))
            self.logger.warning(f"[风控] {alert_type}: 减仓 {largest_sym} 50%")
            pos = positions.get(largest_sym, {})
            entry_req_id = pos.get('request_id', '')
            result = self.executor.reduce_position(largest_sym, 0.5)
            classification = self._classify_reduce_outcome(result, 0.5)
            override_action = classification["action_override"] or "reduce"

            if isinstance(result, dict):
                result['entry_request_id'] = entry_req_id

            payload = self._build_execution_result(
                status=classification["status"],
                action=override_action,
                symbol=largest_sym,
                source="risk_alert",
                reason=classification["reason"] or alert_type,
                result=result if isinstance(result, dict) else {},
                request_id=entry_req_id,
            )

            if classification["status"] == "risk_reduced":
                payload["reduce_pct"] = classification["actual_reduce_pct"]
                payload["protection_state"] = classification["protection_state"]
                payload["protective_update_state"] = classification["protective_update_state"]
                if classification["protection_failed"]:
                    payload["protection_failed"] = True
            elif classification["status"] == "executed" and override_action == "close":
                payload["protection_state"] = classification["protection_state"]
                payload["protective_update_state"] = classification["protective_update_state"]
                payload["reduce_origin"] = True
            # rejected / reduce_failed: 不带 reduce_pct,reason 已 set

            await self.publish("execution_result", payload, symbol=largest_sym)
            self.logger.info(
                f"[执行] {largest_sym} risk_alert reduce {classification['status']} "
                f"reason={classification['reason']} "
                f"actual_pct={classification['actual_reduce_pct']:.4f}"
            )

        elif alert_type == 'stale_position':
            symbol = alert.get('symbol')
            self.logger.info(f"[风控] 持仓超时告警: {symbol} (仅日志，不自动执行)")

    async def _close_tactical_v2_for_safety(self, symbol: str, source: str):
        controller = getattr(self, '_tactical_v2_controller', None)
        if controller is None:
            self.logger.critical(
                f"[OwnerIsolation] {symbol} Tactical V2 safety close 缺 controller"
            )
            return None
        return await controller.close_for_safety(symbol, source=source)

    async def _close_all_positions(self, reason: str):
        """全部平仓 — 并发执行，失败收集后告警

        设计说明：
        - 串行平仓 10-15s 内系统持续亏损，故改并发
        - 单个标的平仓失败不影响其他
        - 失败的标的写入日志告警（RiskGuard 兜底 emergency_close 会再次尝试）
        """
        positions = self.executor.get_all_positions()
        if not positions:
            self.logger.info(f"[全平] 无持仓需平仓 ({reason})")
            return

        self.logger.critical(f"[全平] 开始并发平仓 {len(positions)} 个持仓 ({reason})")

        async def close_one(symbol: str, pos: dict):
            try:
                if pos.get('strategy_owner') == 'tactical_v2':
                    result = await self._close_tactical_v2_for_safety(symbol, reason)
                    return (bool(result), symbol, None if result else "v2 safety close unavailable")
                entry_req_id = pos.get('request_id', '')
                # FR-003: close_position() 内部撤保护单,Agent 不再直接 cancel
                result = await asyncio.to_thread(self.executor.close_position, symbol)
                if result:
                    self.logger.warning(f"[全平] {symbol} 已平仓, PnL={result.get('pnl', 0):.2f}")
                    result['entry_request_id'] = entry_req_id
                    payload = self._build_execution_result(
                        status="force_closed", action="close", symbol=symbol,
                        source="close_all", reason=reason, result=result,
                        request_id=entry_req_id,
                    )
                    await self.publish("execution_result", payload, symbol=symbol)
                    return True, symbol, None
                else:
                    return False, symbol, "close_position returned None"
            except Exception as e:
                return False, symbol, str(e)

        tasks = [close_one(sym, pos) for sym, pos in positions.items()]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        failed = [(sym, err) for ok, sym, err in results if not ok]
        if failed:
            self.logger.error(f"[全平] {len(failed)} 个标的平仓失败，等待 RiskGuard 兜底: {failed}")
        else:
            self.logger.critical(f"[全平] 完成 ({reason}) — 全部 {len(positions)} 个持仓已平")

    def _get_balance(self) -> float:
        try:
            if self.executor and self.executor.balance_adapter:
                val = self.executor.balance_adapter.get_total()
            else:
                balance = self.executor.exchange.fetch_balance()
                from utils.balance_adapter import BalanceAdapter
                val = BalanceAdapter._parse(balance)[1]

            # 第六轮 P1-2：余额是下单前硬闸，必须是有限实数；拒绝 MagicMock/bool/NaN/Inf
            if isinstance(val, bool) or not isinstance(val, numbers.Real):
                self.logger.error(f"余额非实数类型: {type(val).__name__}")
                return -1.0
            result = float(val)
            if not math.isfinite(result):
                self.logger.error(f"余额非有限值: {result}")
                return -1.0
            return result
        except Exception as e:
            self.logger.error(f"获取余额失败: {e}")
            return -1.0

    def _make_correlation_id(self, symbol: str) -> str:
        return f"{time.strftime('%Y%m%d')}-{symbol}-{uuid.uuid4().hex[:8]}"

    def _build_execution_result(self, *, status, action, symbol, source, reason="",
                                result=None, request_id="", correlation_id="",
                                reduce_pct=None, **extra) -> dict:
        payload = {
            "schema_version": "execution_result.v2",
            "status": status,
            "action": action,
            "symbol": symbol,
            "source": source,
            "request_id": request_id,
            "correlation_id": correlation_id or (self._make_correlation_id(symbol) if not request_id else ""),
            "reason": reason,
            "result": result or {},
            "timestamp": time.time(),
        }
        if reduce_pct is not None:
            payload["reduce_pct"] = reduce_pct
        # FR-004: 任何 close 类终态必须带 close cause 字段,Judge/Reviewer/Telegram
        # 据此判断是否计 SL hit / 是否进入策略冷却 / 文案展示
        if action == 'close' or status in ('force_closed', 'closed_externally'):
            cause = self._classify_close_cause(source, reason)
            payload['exit_reason'] = cause['exit_reason']
            payload['close_cause'] = cause['close_cause']
            payload['is_strategy_stop'] = cause['is_strategy_stop']
            payload['is_risk_forced'] = cause['is_risk_forced']
            inner = payload['result']
            if isinstance(inner, dict):
                inner.setdefault('exit_reason', cause['exit_reason'])
                inner.setdefault('close_cause', cause['close_cause'])
                inner.setdefault('is_strategy_stop', cause['is_strategy_stop'])
                inner.setdefault('is_risk_forced', cause['is_risk_forced'])
                inner.setdefault('protective_cleanup_state',
                                 inner.get('protective_cleanup_state', 'unknown'))
                self._inject_pnl_quality(inner, source)
        # close 之外的 risk_reduced (reduce) 也带 PnL 质量字段——partial reduce 由本地
        # 平仓单产生,默认 final;tp_advance 走相同 reduce 路径
        elif status == 'risk_reduced' and isinstance(payload.get('result'), dict):
            self._inject_pnl_quality(payload['result'], source)
        if isinstance(payload.get('result'), dict):
            inner_attr = payload['result'].get('attribution') or {}
            if inner_attr:
                payload.setdefault('attribution', {}).update(inner_attr)
            for key in ('track', 'exit_profile', 'tactical_close_reason'):
                val = payload['result'].get(key) or inner_attr.get(key)
                if val:
                    payload[key] = val
                    payload.setdefault('attribution', {})[key] = val
        payload.update(extra)
        metadata = _tactical_v2_metadata(payload.get('result'), payload)
        if metadata.get("strategy_owner") == "tactical_v2":
            payload.update(metadata)
            payload.setdefault('attribution', {}).update({
                key: value
                for key, value in metadata.items()
                if key in {
                    "strategy_owner",
                    "intent_id",
                    "episode_id",
                    "plan_hash",
                    "source_shadow_id",
                    "tactical_source",
                }
            })
        return payload

    @staticmethod
    def _classify_close_cause(source: str, reason: str) -> dict:
        """FR-004: source/reason → (exit_reason, close_cause, is_strategy_stop,
        is_risk_forced) 单一映射。Judge SL hit cooldown 只对 is_strategy_stop=True
        生效,is_risk_forced=True 不污染策略 SL 统计。"""
        src = source or ''
        rsn = reason or ''
        # 默认值
        out = {
            'exit_reason': rsn or 'unknown',
            'close_cause': rsn or src,
            'is_strategy_stop': False,
            'is_risk_forced': False,
        }
        if src == 'local_stop':
            if rsn == 'stop_loss':
                out['exit_reason'] = 'local_stop_loss'
                out['is_strategy_stop'] = True
            elif rsn == 'take_profit':
                out['exit_reason'] = 'local_take_profit'
            elif rsn == 'price_fetch_failed':
                out['exit_reason'] = 'price_fetch_failed'
                out['is_risk_forced'] = True
            elif rsn in ('partial_tp_1', 'partial_tp_2'):
                out['exit_reason'] = 'partial_tp'
        elif src == 'risk_alert':
            out['is_risk_forced'] = True
            if rsn == 'emergency_close':
                out['exit_reason'] = 'risk_emergency'
            elif rsn == 'flash_move':
                out['exit_reason'] = 'risk_flash_move'
            elif rsn in ('position_danger', 'high_leverage_danger', 'trailing_stop'):
                out['exit_reason'] = f'risk_{rsn}'
            else:
                out['exit_reason'] = f'risk_{rsn or "unknown"}'
        elif src == 'close_all':
            out['exit_reason'] = 'system_close_all'
            out['is_risk_forced'] = True
        elif src == 'partial_tp':
            out['exit_reason'] = 'partial_tp'
        elif src == 'external_close':
            # 外部平仓:PRD §6.2 #5 + §6.8 P1-d 双载荷契约
            # - external_pending(待 Resolver 升级):exchange_unknown_pending,绝不计 SL hit
            # - exchange_sl(Resolver final 后明确归因到本 owner SL):算 SL hit
            # - exchange_tp / external_unknown(final 但非 SL):不计 SL hit
            # 历史 reason='exchange_sl_tp_triggered' 因 pending 阶段无法判别,
            # 已废弃,fail-safe 同 external_pending(不计 SL hit)
            if rsn == 'external_pending' or rsn == 'exchange_sl_tp_triggered':
                out['exit_reason'] = 'external_pending'
                out['close_cause'] = 'exchange_unknown_pending'
                out['is_strategy_stop'] = False
            elif rsn == 'exchange_sl':
                out['exit_reason'] = 'exchange_sl'
                out['close_cause'] = 'exchange_sl'
                out['is_strategy_stop'] = True
            elif rsn == 'exchange_tp':
                out['exit_reason'] = 'exchange_tp'
                out['close_cause'] = 'exchange_tp'
                out['is_strategy_stop'] = False
            else:
                out['exit_reason'] = 'external_unknown'
                out['is_strategy_stop'] = False
        elif src == 'executor_close':
            out['exit_reason'] = 'manual_close'
        return out

    @staticmethod
    def _classify_reduce_outcome(result, requested_pct):
        """F4-001: 把 root reduce_position() 返回的结构化 dict 折成 Agent 终态分类。

        输出字段:
            status: 'rejected' | 'reduce_failed' | 'risk_reduced' | 'executed'
            reason: 来自 result.reason 或派生
            actual_reduce_pct: 实际成交占请求的百分比(用于 RiskGuard 缩敞口)
            protection_failed: 是否需要 risk_alert{type=protection_failed}
            protection_state: 'protected' | 'unknown' | 'closed' | 'no_op'
            protective_update_state: 透传 result['protective_update_state']
            action_override: 'close' (dust_closed 路径) 或 None
            warnings: list,透传 result['warnings']

        分支:
          1. result is None  → rejected, reason=executor_returned_none
          2. reduce_ok=False, reason in {sl_cancel_failed, sl_restore_failed}
                              → rejected (pre-trade)
          3. reduce_ok=False, 其他 reason → reduce_failed (exchange reject)
          4. protective_update_state=dust_closed → executed, action_override=close
          5. reduce_ok=True, ok=False → risk_reduced + protection_failed=True
          6. ok=True → 干净 risk_reduced
        """
        if result is None:
            return {
                "status": "rejected", "reason": "executor_returned_none",
                "actual_reduce_pct": 0.0, "protection_failed": False,
                "protection_state": "unknown",
                "protective_update_state": "no_op",
                "action_override": None, "warnings": [],
            }

        reduce_ok = bool(result.get("reduce_ok", False))
        ok = bool(result.get("ok", False))
        reason = result.get("reason", "") or ""
        pus = result.get("protective_update_state", "") or ""
        actual_amt = float(result.get("actual_reduce_amount") or 0.0)
        requested_amt = float(result.get("requested_reduce_amount") or 0.0)
        if requested_amt > 0 and actual_amt >= 0:
            actual_pct = (actual_amt / requested_amt) * float(requested_pct)
        else:
            actual_pct = float(requested_pct) if reduce_ok else 0.0
        warnings = list(result.get("warnings") or [])

        # 分支 2: pre-trade 失败
        if not reduce_ok and reason in ("sl_cancel_failed", "sl_restore_failed"):
            return {
                "status": "rejected", "reason": reason,
                "actual_reduce_pct": 0.0, "protection_failed": False,
                "protection_state": result.get("protection_state", "unknown"),
                "protective_update_state": pus,
                "action_override": None, "warnings": warnings,
            }

        # 分支 3: 交易所 reject
        if not reduce_ok:
            return {
                "status": "reduce_failed", "reason": reason or "reduce_rejected",
                "actual_reduce_pct": 0.0, "protection_failed": False,
                "protection_state": result.get("protection_state", "unknown"),
                "protective_update_state": pus,
                "action_override": None, "warnings": warnings,
            }

        # 分支 4: dust_closed 视为平仓
        if pus == "dust_closed":
            return {
                "status": "executed", "reason": reason or "dust_closed",
                "actual_reduce_pct": actual_pct,
                "protection_failed": False,
                "protection_state": "closed",
                "protective_update_state": pus,
                "action_override": "close", "warnings": warnings,
            }

        # 分支 5: reduce 成交但 SL 重挂失败
        if reduce_ok and not ok:
            return {
                "status": "risk_reduced", "reason": reason or "protection_failed",
                "actual_reduce_pct": actual_pct,
                "protection_failed": True,
                "protection_state": "unknown",
                "protective_update_state": pus,
                "action_override": None, "warnings": warnings,
            }

        # 分支 6: 干净 risk_reduced
        return {
            "status": "risk_reduced", "reason": "ok",
            "actual_reduce_pct": actual_pct,
            "protection_failed": False,
            "protection_state": result.get("protection_state", "protected"),
            "protective_update_state": pus,
            "action_override": None, "warnings": warnings,
        }

    @staticmethod
    def _inject_pnl_quality(inner: dict, source: str) -> None:
        """注入 PnL 质量字段(PRD §7.3)。Reviewer/Judge/RiskGuard 只在 pnl_is_final=true
        时把 result.pnl 当作真实 PnL 进入策略学习/最终账本。

        默认策略:
        - external_close:默认 pending(本地拿不到 final,等 Resolver 异步回写)
        - 其他 close 路径:默认 final(本地平仓单已通过 _calc_realized_pnl 算出净 PnL)
        - 调用方可通过预填字段覆盖默认值(例如 Resolver 已 resolve 的事件)
        """
        if 'pnl_status' in inner:
            # 调用方已显式标注(Resolver 输出/pending 升级 final)
            pass
        elif source == 'external_close':
            inner['pnl_status'] = 'pending'
            inner.setdefault('pnl_pending_reason', 'awaiting_exchange_resolution')
        else:
            inner['pnl_status'] = 'final'

        is_final = inner['pnl_status'] == 'final'
        inner['pnl_is_final'] = is_final
        inner.setdefault('pnl_source',
                         'okx_fill' if is_final else 'estimated_local')

        # final:realized_pnl_net_usdt 镜像 result.pnl;pending:pnl 置空,值挂到 estimated_pnl
        if is_final:
            pnl_val = inner.get('pnl', inner.get('realized_pnl', 0))
            inner.setdefault('realized_pnl_net_usdt', pnl_val)
        else:
            existing_pnl = inner.get('pnl')
            if existing_pnl not in (None, 0, 0.0):
                inner.setdefault('estimated_pnl', existing_pnl)
            inner['pnl'] = None
            inner.setdefault('realized_pnl_net_usdt', None)

        inner.setdefault('position_id', inner.get('position_id', ''))
        inner.setdefault('entry_request_id',
                         inner.get('entry_request_id', inner.get('request_id', '')))

    async def tick(self):
        await asyncio.sleep(5)
        self._sync_counter += 1

        controller = getattr(self, '_tactical_v2_controller', None)
        if controller is not None:
            await controller.tick()

        if self._sync_counter % 6 == 0:
            await asyncio.to_thread(self.executor.sync_positions)
            await self._notify_synced_positions()
            await self._notify_removed_positions()

        if self._reconciler and self._reconciler.should_run(interval_sec=600):
            await self._run_reconciliation()

        await self._check_all_positions()

    async def _run_reconciliation(self):
        """定时 PnL 对账 + pending 自动升级 (PRD §6.1)"""
        await self._replay_unconsumed_tactical_v2_finals()
        # Step 1: 升级 pending external_close
        try:
            summaries = await asyncio.to_thread(self._reconciler.auto_resolve_pending)
        except Exception as e:
            self.logger.warning(f"[Reconciler] auto_resolve_pending 异常: {e}")
            summaries = []
        for s in summaries:
            pnl_status = s.get("pnl_status")
            if pnl_status == PNL_STATUS_FINAL:
                topic = "pnl_resolved"
            elif pnl_status == PNL_STATUS_MISMATCH:
                topic = "pnl_mismatch"
            else:
                continue
            payload = {
                "schema_version": 1,
                "symbol": s.get("symbol", ""),
                "request_id": s.get("entry_request_id", ""),
                "correlation_id": s.get("entry_request_id", ""),
                "position_id": s.get("position_id", ""),
                "entry_request_id": s.get("entry_request_id", ""),
                "pnl_status": s.get("pnl_status", ""),
                "pnl_is_final": s.get("pnl_status") == PNL_STATUS_FINAL,
                "pnl_source": s.get("pnl_source", ""),
                "realized_pnl_net_usdt": s.get("realized_pnl_net_usdt"),
                "pnl": s.get("realized_pnl_net_usdt"),
                "estimated_pnl": s.get("estimated_pnl"),
                "exchange_pnl_usdt": s.get("exchange_pnl_usdt"),
                "fills_pnl_usdt": s.get("fills_pnl_usdt"),
                "gross_close_pnl_usdt": s.get("gross_close_pnl_usdt", 0),
                "fee_usdt": s.get("fee_usdt", 0),
                "funding_usdt": s.get("funding_usdt", 0),
                "order_ids": s.get("order_ids", []),
                "bill_ids": s.get("bill_ids", []),
                "match_confidence": s.get("match_confidence", 0),
                "warnings": s.get("warnings", []),
                "sl_algo_id": s.get("sl_algo_id", ""),
                "sl_algo_clord_id": s.get("sl_algo_clord_id", ""),
                "tp_algo_id": s.get("tp_algo_id", ""),
                "tp_algo_clord_id": s.get("tp_algo_clord_id", ""),
                "attribution": s.get("entry_attribution", {}),
                "supersedes_event_id": s.get("supersedes_event_id", ""),
                "correction_event_id": s.get("correction_event_id", ""),
                # F4-002: final close cause 证据 + 幂等键
                "close_cause": s.get("close_cause", ""),
                "final_close_cause": s.get("final_close_cause", ""),
                "is_strategy_stop": bool(s.get("is_strategy_stop", False)),
                "close_evidence": s.get("close_evidence", {}),
                "tactical_v2_proof": s.get("tactical_v2_proof", {}),
                "resolution_id": s.get("resolution_id", ""),
                "timestamp": time.time(),
            }
            metadata = _tactical_v2_metadata(s.get("entry_attribution"), s)
            if metadata.get("strategy_owner") == "tactical_v2":
                payload.update(metadata)
            await self._route_pnl_event(
                topic,
                payload,
                symbol=s.get("symbol", ""),
            )
        # Step 2: 全局账单 vs 本地账本 advisory 对账
        try:
            report = await asyncio.to_thread(self._reconciler.run_and_report)
            if report:
                await self.publish("risk_alert", {
                    "type": "reconciliation_mismatch",
                    "message": report,
                    "timestamp": time.time(),
                })
        except Exception as e:
            self.logger.warning(f"[Reconciler] 对账异常: {e}")
        # F-TG-004: 同步 publish halts_snapshot 给 Orchestrator
        await self._publish_halts_snapshot()

    async def _publish_halts_snapshot(self):
        """F-TG-004: 周期性 publish halts_snapshot 事件供 Orchestrator 写 agent_health.json。

        payload schema:
            halted_symbols: dict {symbol -> {reason, halted_at}}
            ts: 当前时戳
        """
        try:
            halts = self.executor.get_halted_symbols()
            await self.publish('halts_snapshot', {
                'halted_symbols': halts,
                'ts': time.time(),
            })
        except Exception as e:
            self.logger.warning(f"[HaltsSnapshot] publish 失败: {e}")

    async def _notify_synced_positions(self):
        """将同步发现的新持仓通知RiskGuard"""
        newly_synced = self.executor.get_newly_synced()
        for pos in newly_synced:
            symbol = pos['symbol']
            action = 'open_long' if pos['side'] == 'long' else 'open_short'
            payload = self._build_execution_result(
                status="executed", action=action, symbol=symbol,
                source="sync", result=pos,
                confidence=0, used_plan=False,
            )
            await self.publish("execution_result", payload, symbol=symbol)

    async def _notify_removed_positions(self):
        """通知下游：持仓已被交易所平仓（SL/TP触发），PRD §6.2 双载荷:
        1. 立即发 closed_externally(pending),携带 estimated_pnl 仅做监控
        2. 后台异步调 RealizedPnlResolver,拿到 final 后发 pnl_resolved 升级
        """
        removed = self.executor.get_removed_symbols()
        removed_data = self.executor.get_removed_positions_data()
        data_map = {d['symbol']: d for d in removed_data}

        for symbol in removed:
            pos_data = data_map.get(symbol, {})
            estimated_pnl = self._estimate_close_pnl(pos_data)
            entry_req_id = (
                pos_data.get('entry_request_id')
                or pos_data.get('request_id', '')
            )
            position_id = pos_data.get('position_id', '')
            metadata = _tactical_v2_metadata(pos_data)
            entry_attribution = dict(pos_data.get('attribution') or {})
            if metadata.get("strategy_owner") == "tactical_v2":
                entry_attribution.update({
                    key: value
                    for key, value in metadata.items()
                    if key in {
                        "strategy_owner",
                        "intent_id",
                        "episode_id",
                        "plan_hash",
                        "source_shadow_id",
                        "tactical_source",
                    }
                })

            # Step 1: 立即写 pending 账本(不再调 fetch_orders 兜底)
            ledger = getattr(self.executor, 'ledger', None)
            pending_event = None
            if ledger:
                try:
                    pending_event = ledger.record_pending_external_close(
                        symbol=symbol,
                        side=pos_data.get('side', 'long'),
                        entry_price=pos_data.get('entry_price', 0),
                        amount_usdt=pos_data.get('amount_usdt', 0),
                        leverage=pos_data.get('leverage', 1) or 1,
                        estimated_pnl=estimated_pnl,
                        position_id=position_id or None,
                        entry_request_id=entry_req_id or None,
                        opened_at=pos_data.get('opened_at', pos_data.get('open_time', 0)) or None,
                        sl_algo_id=pos_data.get('sl_order_id', '') or pos_data.get('sl_algo_id', ''),
                        sl_algo_clord_id=pos_data.get('sl_algo_clord_id', ''),
                        tp_algo_id=pos_data.get('tp_order_id', '') or pos_data.get('tp_algo_id', ''),
                        tp_algo_clord_id=pos_data.get('tp_algo_clord_id', ''),
                        close_order_id=pos_data.get('close_order_id', ''),
                        close_client_id=pos_data.get('close_client_id', ''),
                        entry_attribution=entry_attribution,
                    )
                except Exception as e:
                    self.logger.warning(f"[Ledger] pending external_close 写入失败: {e}")

            self.logger.info(
                f"[执行] {symbol} 被交易所平仓(pending),estimated_pnl={estimated_pnl:+.3f} USDT")
            result = {
                "pnl": None,
                "estimated_pnl": estimated_pnl,
                "realized_pnl_net_usdt": None,
                "symbol": symbol,
                "side": pos_data.get('side', ''),
                "entry_price": pos_data.get('entry_price', 0),
                "amount_usdt": pos_data.get('amount_usdt', 0),
                "attribution": pos_data.get('attribution', {}),
                "entry_request_id": entry_req_id,
                "position_id": position_id,
                "pnl_status": PNL_STATUS_PENDING,
                "pnl_is_final": False,
                "pnl_source": "estimated_local",
                "pnl_pending_reason": "awaiting_exchange_resolution",
                "pending_event_id": (pending_event or {}).get("event_id", ""),
            }
            if metadata.get("strategy_owner") == "tactical_v2":
                result.update(metadata)
                result["attribution"] = entry_attribution
            payload = self._build_execution_result(
                status="closed_externally", action="close", symbol=symbol,
                source="external_close", reason="external_pending",
                result=result, request_id=entry_req_id,
            )
            await self.publish("execution_result", payload, symbol=symbol)

            # Step 2: 后台异步解析并发布 pnl_resolved
            if self._pnl_resolver:
                snapshot = dict(pos_data)
                snapshot.setdefault('symbol', symbol)
                snapshot.setdefault('position_id', position_id)
                snapshot.setdefault('entry_request_id', entry_req_id)
                close_window = {
                    'closed_at': time.time(),
                }
                asyncio.create_task(self._resolve_external_close_async(
                    snapshot, close_window, entry_req_id))

    async def _resolve_external_close_async(self, snapshot: dict,
                                              close_window: dict,
                                              entry_req_id: str):
        """后台异步:调 Resolver → apply 到账本 → 发 pnl_resolved/pnl_mismatch

        PRD §6.8 P0-c:apply_pnl_resolution 内部按 status 严格分流:
        final → 写 correction;pending/pending_fx → 只更新 retry metadata;
        mismatch → 写独立告警事件。这里只对 final/mismatch 发布总线事件,
        pending 不广播(下一轮 Reconciler 重试)。
        """
        symbol = snapshot.get('symbol', '')
        try:
            resolution = await asyncio.to_thread(
                self._pnl_resolver.resolve_external_close,
                snapshot, close_window,
            )
        except Exception as e:
            self.logger.warning(f"[Resolver] {symbol} 解析失败: {e}")
            return

        ledger = getattr(self.executor, 'ledger', None)
        correction = None
        if ledger:
            try:
                correction = await asyncio.to_thread(
                    ledger.apply_pnl_resolution, resolution,
                )
            except Exception as e:
                self.logger.warning(f"[Ledger] apply_pnl_resolution 失败: {e}")

        # F4-002: correction=None 且非 final/mismatch 时跳过发布,避免脏事件
        status = resolution.get("pnl_status", "")
        if correction is None and status not in (PNL_STATUS_FINAL, PNL_STATUS_MISMATCH):
            self.logger.warning(
                f"[Resolver] {symbol} 跳过 pnl_resolved 发布: "
                f"correction=None status={status} "
                f"position_id={resolution.get('position_id', '')}"
            )
            return

        if status == PNL_STATUS_FINAL:
            topic = "pnl_resolved"
        elif status == "mismatch":
            topic = "pnl_mismatch"
        else:
            # pending/pending_fx:不广播,Reconciler 下一轮重试
            self.logger.info(
                f"[Resolver] {symbol} status={status},pending 重试中,不广播总线事件")
            return
        # PRD §6.2 #5: final 升级后,close_cause 取自 resolver(若有)。
        # 当前 resolver 不区分 exchange_sl/exchange_tp,默认 external_unknown,
        # is_strategy_stop=False(fail-safe,Judge 不会误计 SL hit)。
        close_cause = resolution.get("close_cause", "external_unknown")
        is_strategy_stop = bool(resolution.get("is_strategy_stop",
                                                close_cause == "exchange_sl"))
        payload = {
            "schema_version": 1,
            "symbol": symbol,
            "request_id": entry_req_id,
            "correlation_id": entry_req_id,
            "position_id": resolution.get("position_id", ""),
            "entry_request_id": resolution.get("entry_request_id", entry_req_id),
            "side": snapshot.get("side", ""),
            "direction": snapshot.get("side", ""),
            "pnl_status": resolution.get("pnl_status", ""),
            "pnl_is_final": resolution.get("pnl_status") == PNL_STATUS_FINAL,
            "pnl_source": resolution.get("pnl_source", ""),
            "realized_pnl_net_usdt": resolution.get("realized_pnl_net_usdt"),
            "pnl": resolution.get("realized_pnl_net_usdt"),
            "estimated_pnl": resolution.get("estimated_pnl"),
            "gross_close_pnl_usdt": resolution.get("gross_close_pnl_usdt", 0),
            "fee_usdt": resolution.get("fee_usdt", 0),
            "funding_usdt": resolution.get("funding_usdt", 0),
            "order_ids": resolution.get("order_ids", []),
            "bill_ids": resolution.get("bill_ids", []),
            "match_confidence": resolution.get("match_confidence", 0),
            "warnings": resolution.get("warnings", []),
            "close_cause": close_cause,
            "is_strategy_stop": is_strategy_stop,
            "attribution": resolution.get("entry_attribution",
                                            snapshot.get("attribution", {})),
            "sl_algo_id": resolution.get("sl_algo_id",
                                           snapshot.get("sl_algo_id", "")),
            "sl_algo_clord_id": resolution.get("sl_algo_clord_id",
                                                  snapshot.get("sl_algo_clord_id", "")),
            "tp_algo_id": resolution.get("tp_algo_id",
                                           snapshot.get("tp_algo_id", "")),
            "tp_algo_clord_id": resolution.get("tp_algo_clord_id",
                                                  snapshot.get("tp_algo_clord_id", "")),
            "estimated_pnl": resolution.get("estimated_pnl",
                                              snapshot.get("unrealized_pnl")),
            "exchange_pnl_usdt": resolution.get("exchange_pnl_usdt"),
            "fills_pnl_usdt": resolution.get("fills_pnl_usdt"),
            "supersedes_event_id": (correction or {}).get("supersedes_event_id", ""),
            "correction_event_id": (correction or {}).get("event_id", ""),
            "final_close_cause": resolution.get("final_close_cause", close_cause),
            "close_evidence": resolution.get("close_evidence", {}),
            "tactical_v2_proof": resolution.get("tactical_v2_proof", {}),
            "resolution_id": make_resolution_id(resolution, correction),
            "timestamp": time.time(),
        }
        metadata = _tactical_v2_metadata(snapshot, resolution)
        if metadata.get("strategy_owner") == "tactical_v2":
            payload.update(metadata)
            payload.setdefault("attribution", {}).update({
                key: value
                for key, value in metadata.items()
                if key in {
                    "strategy_owner",
                    "intent_id",
                    "episode_id",
                    "plan_hash",
                    "source_shadow_id",
                    "tactical_source",
                }
            })
        await self._route_pnl_event(topic, payload, symbol=symbol)
        if resolution.get("pnl_status") == PNL_STATUS_FINAL:
            net = resolution.get("realized_pnl_net_usdt", 0) or 0
            self.logger.info(
                f"[Resolver] {symbol} 升级 final: net_pnl={net:+.4f} USDT "
                f"(fills={resolution.get('gross_close_pnl_usdt', 0):+.4f}, "
                f"fee={resolution.get('fee_usdt', 0):+.4f}, "
                f"funding={resolution.get('funding_usdt', 0):+.4f})")
        else:
            self.logger.warning(
                f"[Resolver] {symbol} 升级失败: status={resolution.get('pnl_status')} "
                f"warnings={resolution.get('warnings', [])}")

    def _get_external_close_pnl(self, symbol: str, pos_data: dict) -> float:
        """[Deprecated] 保留兼容,仅返回 estimated;新代码走 dual-payload。"""
        return self._estimate_close_pnl(pos_data)

    def _estimate_close_pnl(self, pos_data: dict) -> float:
        """从本地持仓数据估算平仓PnL
        优先用最后一次sync的unrealized_pnl（最接近真实值，~30s误差）
        降级用stop_loss作为exit price计算
        """
        if not pos_data:
            return 0.0

        # 优先：最后一次sync记录的浮动盈亏（最准确，因为sync周期30s）
        unrealized = pos_data.get('unrealized_pnl')
        if unrealized is not None and unrealized != 0:
            return round(unrealized, 4)

        # 降级：用stop_loss估算（假设SL触发）
        entry = pos_data.get('entry_price', 0)
        sl = pos_data.get('stop_loss', 0)
        side = pos_data.get('side', '')
        amount_usdt = pos_data.get('amount_usdt', 0)  # 保证金 (margin)
        leverage = pos_data.get('leverage', 1) or 1

        if not entry or not sl or not amount_usdt:
            return 0.0

        if side == 'long':
            pnl_pct = (sl - entry) / entry
        else:
            pnl_pct = (entry - sl) / entry

        # 保证金 × 价格变化率 × 杠杆 = 实际 PnL（与 ContractExecutor 内部平仓口径一致）
        pnl = amount_usdt * pnl_pct * leverage
        return round(pnl, 4)

    async def _check_all_positions(self):
        """兜底止损检查（交易所条件单失败时的安全网）+ RQ-04 早期复核"""
        positions = self.executor.get_all_positions()
        for symbol in list(positions.keys()):
            # RQ-04: 早期持仓复核（前30分钟内）
            pos = positions[symbol]
            if pos.get('strategy_owner') == 'tactical_v2':
                continue
            early_review_enabled = self.config.get('early_review_enabled', True)
            if early_review_enabled:
                open_time = pos.get('open_time', 0)
                minutes_held = (time.time() - open_time) / 60 if open_time else 999
                if 0 < minutes_held <= 30:
                    early_action = await self._early_review(symbol, pos, minutes_held)
                    if early_action:
                        continue

            trigger = await asyncio.to_thread(self.executor.check_stop_loss_take_profit, symbol)
            if not trigger:
                continue

            if trigger == 'price_fetch_failed':
                self.logger.error(f"[兜底] {symbol} 价格获取连续失败，强制平仓保护资金!")
                pos = self.executor.positions.get(symbol)
                entry_req_id = (pos or {}).get('request_id', '')
                # FR-003: close_position() 内部撤保护单,Agent 不再直接 cancel
                result = self.executor.close_position(symbol)
                if result:
                    result['entry_request_id'] = entry_req_id
                    payload = self._build_execution_result(
                        status="force_closed", action="close", symbol=symbol,
                        source="local_stop", reason=trigger, result=result,
                        request_id=entry_req_id,
                    )
                    await self.publish("execution_result", payload, symbol=symbol)

            elif trigger in ('partial_tp_1', 'partial_tp_2', 'tactical_tp1'):
                await self._handle_partial_tp_trigger(symbol, trigger)

            else:
                self.logger.info(f"[兜底] {symbol} 触发{trigger}，本地平仓")
                pos = self.executor.positions.get(symbol)
                entry_req_id = (pos or {}).get('request_id', '')
                # FR-003: close_position() 内部撤保护单,Agent 不再直接 cancel
                result = self.executor.close_position(symbol)
                if result:
                    result['entry_request_id'] = entry_req_id
                    if trigger in ('tactical_max_hold', 'tactical_invalidated', 'tactical_weakened_no_progress'):
                        result['tactical_close_reason'] = trigger
                        result.setdefault('attribution', {}).update({
                            'track': (pos or {}).get('track', 'tactical'),
                            'exit_profile': (pos or {}).get('exit_profile', 'tactical_v1'),
                            'tactical_close_reason': trigger,
                        })
                    payload = self._build_execution_result(
                        status="force_closed", action="close", symbol=symbol,
                        source="local_stop", reason=trigger, result=result,
                        request_id=entry_req_id,
                    )
                    if trigger in ('tactical_max_hold', 'tactical_invalidated', 'tactical_weakened_no_progress'):
                        payload['tactical_close_reason'] = trigger
                        payload.setdefault('attribution', {}).update(result.get('attribution', {}))
                    await self.publish("execution_result", payload, symbol=symbol)

    async def _handle_partial_tp_trigger(self, symbol: str, trigger: str):
        """F4-001: partial TP 锁利路径分流。trigger ∈ {partial_tp_1, partial_tp_2}。

        减仓结果经 _classify_reduce_outcome 分流，actual reduce_pct 写入 payload，
        protection_failed=True 时下游可见。partial_tp 默认 action='close'（与原逻辑一致）。
        """
        pos = self.executor.positions.get(symbol)
        if pos and pos.get('strategy_owner') == 'tactical_v2':
            return
        pct = 0.5 if trigger in ('partial_tp_1', 'tactical_tp1') else 0.25
        tp_advance = 1 if trigger in ('partial_tp_1', 'tactical_tp1') else 2
        self.logger.info(f"[Trailing] {symbol} {trigger}，减仓{int(pct*100)}%")
        pos = self.executor.positions.get(symbol)
        entry_req_id = (pos or {}).get('request_id', '')
        result = await asyncio.to_thread(
            self.executor.reduce_position, symbol, pct, tp_advance
        )
        classification = self._classify_reduce_outcome(result, pct)
        override_action = classification["action_override"] or "close"

        if isinstance(result, dict):
            result['entry_request_id'] = entry_req_id

        payload = self._build_execution_result(
            status=classification["status"],
            action=override_action,
            symbol=symbol,
            source="partial_tp",
            reason=trigger if classification["reason"] in ("ok", "", None) else classification["reason"],
            result=result if isinstance(result, dict) else {},
            request_id=entry_req_id,
        )
        if classification["status"] == "risk_reduced":
            payload["reduce_pct"] = classification["actual_reduce_pct"]
            payload["protection_state"] = classification["protection_state"]
            payload["protective_update_state"] = classification["protective_update_state"]
            if classification["protection_failed"]:
                payload["protection_failed"] = True
        elif classification["status"] == "executed" and override_action == "close":
            payload["protection_state"] = classification["protection_state"]
            payload["protective_update_state"] = classification["protective_update_state"]
            payload["reduce_origin"] = True

        await self.publish("execution_result", payload, symbol=symbol)
        self.logger.info(
            f"[Trailing] {symbol} {trigger} {classification['status']} "
            f"reason={classification['reason']} "
            f"actual_pct={classification['actual_reduce_pct']:.4f}"
        )

    async def _early_review(self, symbol: str, pos: dict, minutes_held: float) -> bool:
        """RQ-04: 早期持仓复核（入场后30分钟内）。

        返回 True 表示已执行平仓动作，False 表示无动作。
        """
        if pos.get('strategy_owner') == 'tactical_v2':
            return False
        if not hasattr(self, '_early_review_times'):
            self._early_review_times = {}
        review_key = f"_er_{symbol}"
        last_review = self._early_review_times.get(review_key, 0)
        if time.time() - last_review < 120:
            return False

        entry_price = pos.get('entry_price', 0)
        stop_loss = pos.get('stop_loss', 0)
        side = pos.get('side', 'long')

        if not entry_price or not stop_loss:
            return False

        try:
            ticker = await asyncio.to_thread(self.executor.exchange.fetch_ticker, symbol)
            current_price = ticker['last']
        except Exception:
            return False

        sl_dist = abs(entry_price - stop_loss)
        if sl_dist == 0:
            return False

        pnl_dist = (current_price - entry_price) if side == 'long' else (entry_price - current_price)
        r_multiple = pnl_dist / sl_dist

        # 20min+: 达到 -0.5R → 收紧止损到 -0.7R 位置
        # FR-001: 必须通过 root executor.move_protective_sl 单一入口替换交易所
        # 保护单,替换成功后由 root executor 同步更新本地 stop_loss/sl_algo_id;
        # 不得直接写 pos['stop_loss'] + _save_positions(),否则本地显示已收紧
        # 但交易所仍是旧 SL,行情真把价格打到旧 SL 之外时无保护。
        if minutes_held >= 20 and r_multiple <= -0.5:
            tighter_sl_dist = sl_dist * 0.7
            if side == 'long':
                new_sl = entry_price - tighter_sl_dist
                tighter = new_sl > pos['stop_loss']
            else:
                new_sl = entry_price + tighter_sl_dist
                tighter = new_sl < pos['stop_loss']

            if tighter:
                result = self.executor.move_protective_sl(
                    symbol, new_sl,
                    reason=f'early_review_tighten_{minutes_held:.0f}min_R{r_multiple:.2f}',
                )
                if result.get('ok'):
                    self.logger.info(
                        f"[EarlyReview] {symbol} {minutes_held:.0f}min "
                        f"R={r_multiple:.2f}, 收紧SL → {new_sl:.4f}"
                    )
                else:
                    self.logger.warning(
                        f"[EarlyReview] {symbol} {minutes_held:.0f}min "
                        f"R={r_multiple:.2f}, 收紧SL失败 "
                        f"reason={result.get('reason')} "
                        f"state={result.get('protection_state')}"
                    )

            self._early_review_times[review_key] = time.time()

        return False
