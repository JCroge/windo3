"""交易复盘与策略衰减检测 Agent"""

import asyncio
import json
import os
import time
import datetime
from agents.base import BaseAgent


def _payload_pnl_is_final(payload: dict, result: dict) -> bool:
    """PRD §6.2: 仅 pnl_is_final=True 才允许进入 trade_history。
    无字段(老 payload)默认按 final 兼容,但 closed_externally 必须显式 final。
    """
    explicit = result.get('pnl_is_final')
    if explicit is None:
        explicit = payload.get('pnl_is_final')
    if explicit is not None:
        return bool(explicit)
    status_text = result.get('pnl_status') or payload.get('pnl_status')
    if status_text == 'pending' or status_text == 'pending_fx' or status_text == 'mismatch':
        return False
    if payload.get('status') == 'closed_externally':
        return False  # external_close 默认 pending,直到 pnl_resolved 升级
    return True  # 其他兼容老 payload(reduce/internal close)按 final


def _payload_pnl_value(result: dict) -> float:
    """优先取 realized_pnl_net_usdt(final),降级 pnl/realized_pnl。"""
    net = result.get('realized_pnl_net_usdt')
    if net is not None:
        return float(net)
    return float(result.get('pnl') or result.get('realized_pnl') or 0)


class ReviewerAgent(BaseAgent):
    name = "reviewer"
    subscriptions = ["execution_result", "research_trigger", "risk_alert",
                     "pnl_resolved", "pnl_mismatch"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.trade_history = []
        self.history_file = 'data/trade_history.json'

        # 滚动窗口配置
        self.rolling_window_size = config.get('rolling_window_size', 20) if config else 20

        # 策略衰减阈值
        self.decay_threshold_win_rate = config.get('decay_threshold_win_rate', 0.50) if config else 0.50
        self.decay_threshold_profit_factor = config.get('decay_threshold_profit_factor', 1.5) if config else 1.5

        # Daily hard stop阈值（与 utils/config_loader.py 默认值保持一致）
        self.daily_pnl_hard_stop = config.get('daily_pnl_hard_stop', -50.0) if config else -50.0
        self.consecutive_loss_limit = config.get('consecutive_loss_limit', 3) if config else 3
        # latch：当日已触发则不重复发，UTC 日切时重置
        self._hard_stop_triggered_date: str = ''
        # FR-3C: pnl_resolved 幂等去重(按 correction_event_id),防止 Resolver 重发
        # 同一升级事件导致重复 append/upsert trade_history。
        self._processed_resolution_ids: set = set()
        self._processed_resolution_max = 1024

    async def setup(self):
        self._load_trade_history()
        self.logger.info(f"交易复盘Agent就绪 (历史{len(self.trade_history)}笔)")

    async def on_message(self, msg: dict):
        if msg['type'] == 'execution_result':
            await self._process_trade_result(msg)
            await self._check_daily_hard_stop()
        elif msg['type'] in ('pnl_resolved', 'pnl_mismatch'):
            await self._apply_pnl_resolution(msg)
            await self._check_daily_hard_stop()
        elif msg['type'] == 'research_trigger':
            await self._run_strategy_review()
        elif msg['type'] == 'risk_alert':
            # 组合回撤超限时，将浮亏计入当日PnL触发熔断
            if msg['payload'].get('type') == 'max_drawdown':
                today_str = datetime.datetime.utcnow().strftime('%Y-%m-%d')
                if self._hard_stop_triggered_date != today_str:
                    unrealized = msg['payload'].get('total_pnl_usdt', 0)
                    daily_pnl = self._calculate_daily_pnl() + unrealized
                    if daily_pnl <= self.daily_pnl_hard_stop:
                        self._hard_stop_triggered_date = today_str
                        self.logger.critical(f"[熔断] 含浮亏当日亏损{daily_pnl:.2f} USDT 超过限制")
                        await self.publish("daily_hard_stop_triggered", {
                            "reason": "daily_loss_limit_with_unrealized",
                            "daily_pnl": daily_pnl,
                            "limit": self.daily_pnl_hard_stop,
                            "timestamp": time.time()
                        })

    async def tick(self):
        await asyncio.sleep(60)

    async def _process_trade_result(self, msg: dict):
        """处理交易结果，记录到历史"""
        payload = msg['payload']
        status = payload.get('status')

        # 记录减仓 PnL（risk_reduced 状态）
        if status == 'risk_reduced':
            result = payload.get('result', {})
            # PRD §6.2: pending PnL 不落 trade_history(等 pnl_resolved 升级 final 再 upsert)
            if not _payload_pnl_is_final(payload, result):
                self.logger.info(
                    f"[复盘] reduce pending(等待 final): "
                    f"{msg.get('symbol') or payload.get('symbol')}")
                return
            pnl = _payload_pnl_value(result)
            if pnl != 0:
                symbol = msg.get('symbol') or payload.get('symbol')
                trade_record = {
                    'timestamp': msg['timestamp'],
                    'symbol': symbol,
                    'status': 'risk_reduced',
                    'pnl': pnl,
                    'event_type': 'reduce',
                    'confidence': payload.get('confidence', 0),
                    'source': payload.get('source', ''),
                    'correlation_id': payload.get('correlation_id', ''),
                    'pnl_status': 'final',
                    'pnl_source': result.get('pnl_source', payload.get('pnl_source', '')),
                    'entry_request_id': result.get('entry_request_id', ''),
                    'position_id': result.get('position_id', ''),
                }
                self.trade_history.append(trade_record)
                self._save_trade_history()
                self.logger.info(f"[复盘] 记录减仓: {symbol} PnL={pnl:+.4f} USDT")
            return

        # 只记录已完成的交易（executed或force_closed或closed_externally）
        if status not in ('executed', 'force_closed', 'closed_externally'):
            return

        action = payload.get('action', '')
        result = payload.get('result', {})

        # 开仓：不记录 PnL
        if action in ('open_long', 'open_short') and status == 'executed':
            pass
        elif action == 'close' or status in ('force_closed', 'closed_externally'):
            # PRD §6.2: pending external_close 不进 trade_history,
            # 等 pnl_resolved 异步事件 upsert 才记。
            if not _payload_pnl_is_final(payload, result):
                self.logger.info(
                    f"[复盘] close pending(等待 final): "
                    f"{msg.get('symbol') or payload.get('symbol')} "
                    f"reason={result.get('pnl_pending_reason', '')}")
                return
            symbol = msg.get('symbol') or payload.get('symbol')
            pnl = _payload_pnl_value(result)
            attribution = result.get('attribution') or payload.get('attribution', {})
            side = result.get('side') or attribution.get('side', '')
            if not side:
                side = 'long' if action == 'open_long' else ('short' if action == 'open_short' else '')

            trade_record = {
                'timestamp': msg['timestamp'],
                'symbol': symbol,
                'status': status,
                'pnl': pnl,
                'side': side,
                'entry_price': result.get('entry_price', 0),
                'exit_price': result.get('exit_price', 0),
                'event_type': 'close',
                'confidence': payload.get('confidence', 0),
                'exit_reason': result.get('exit_reason', status),
                'entry_request_id': result.get('entry_request_id') or result.get('request_id', payload.get('request_id', '')),
                'exit_request_id': result.get('exit_request_id') or payload.get('request_id', ''),
                'dispatch_path': (payload.get('attribution') or {}).get('dispatch_path', ''),
                'source': payload.get('source', ''),
                'correlation_id': payload.get('correlation_id', ''),
                'position_id': result.get('position_id', ''),
                'pnl_status': 'final',
                'pnl_source': result.get('pnl_source', payload.get('pnl_source', '')),
                'realized_pnl_net_usdt': result.get('realized_pnl_net_usdt', pnl),
            }

            # RQ-07: 归因字段（从 execution_result 中提取）
            if attribution:
                trade_record['entry_type'] = attribution.get('entry_type', 'unknown')
                trade_record['rule_signal_type'] = attribution.get('rule_signal_type', 'none')
                trade_record['signal_score'] = attribution.get('signal_score', 0)
                trade_record['llm_relation'] = attribution.get('llm_relation', 'neutral')
                trade_record['htf_votes'] = attribution.get('htf_votes', 0)
                trade_record['liquidity_bucket'] = attribution.get('liquidity_bucket', 'unknown')
                trade_record['rr_bucket'] = attribution.get('rr_bucket', 'unknown')
                trade_record['provenance_bucket'] = self._provenance_bucket(attribution)
                trade_record['ev_at_entry'] = attribution.get('ev_at_entry', 0)
                trade_record['p_win_used'] = attribution.get('p_win_used', 0)
                trade_record['p_win_source'] = attribution.get('p_win_source', 'unknown')
                # Phase 8: Regime attribution
                trade_record['entry_regime'] = attribution.get('entry_regime', 'unknown')
                trade_record['raw_regime'] = attribution.get('raw_regime', 'unknown')
                trade_record['regime_confidence'] = attribution.get('regime_confidence', 0)
                trade_record['rr_policy'] = attribution.get('rr_policy', 'default')
                trade_record['slot_type'] = attribution.get('slot_type', 'main')
                trade_record['is_probe'] = attribution.get('is_probe', False)
                trade_record['is_low_rr'] = attribution.get('is_low_rr', False)

            self.trade_history.append(trade_record)
            self._save_trade_history()

            self.logger.info(f"[复盘] 记录交易: {symbol} PnL={pnl:.2f} USDT")

    async def _apply_pnl_resolution(self, msg: dict):
        """PRD §6.2: pnl_resolved/pnl_mismatch 异步事件 upsert trade_history。

        - status=final: 按 entry_request_id/position_id 找已有 close 记录,有则更新 pnl,
          无则 append 新 record(交易所主动平仓本就没经过 trade_decision)
        - status=mismatch/pending_fx: 不计入,只记 warning
        """
        payload = msg['payload']
        status = payload.get('pnl_status', '')
        symbol = msg.get('symbol') or payload.get('symbol')
        if status != 'final':
            self.logger.warning(
                f"[复盘] pnl_resolution status={status} 不入账: "
                f"{symbol} warnings={payload.get('warnings', [])}")
            return

        # F4-002: resolution_id 是 make_resolution_id 生成的强幂等键(优先级 corr/sup/key/pos),
        # 优先使用;老 payload 缺 resolution_id 时回退到 correction_event_id / supersedes_event_id。
        resolution_key = (payload.get('resolution_id')
                          or payload.get('correction_event_id')
                          or payload.get('supersedes_event_id') or '')
        if resolution_key:
            pos_id = payload.get('position_id', '')
            idem_key = f"{resolution_key}|{pos_id}"
            if idem_key in self._processed_resolution_ids:
                self.logger.info(
                    f"[复盘] pnl_resolved 幂等跳过 key={idem_key}")
                return
            self._processed_resolution_ids.add(idem_key)
            if len(self._processed_resolution_ids) > self._processed_resolution_max:
                self._processed_resolution_ids = set(
                    list(self._processed_resolution_ids)[-self._processed_resolution_max // 2:])

        pnl = payload.get('realized_pnl_net_usdt')
        if pnl is None:
            pnl = payload.get('pnl', 0)
        pnl = float(pnl or 0)
        entry_req_id = payload.get('entry_request_id', '')
        position_id = payload.get('position_id', '')

        # 找已有 close 记录(可能是先发的 risk_reduced 或 closed_externally pending,
        # 但 pending 我们没记;这里主要兜住将来 partial reduce 的最终升级)
        target = None
        for rec in reversed(self.trade_history):
            if rec.get('event_type') not in ('close', 'reduce'):
                continue
            if entry_req_id and rec.get('entry_request_id') == entry_req_id:
                target = rec
                break
            if position_id and rec.get('position_id') == position_id:
                target = rec
                break

        if target is not None:
            target['pnl'] = pnl
            target['realized_pnl_net_usdt'] = pnl
            target['pnl_status'] = 'final'
            target['pnl_source'] = payload.get('pnl_source', '')
            target['updated_at'] = time.time()
            self._save_trade_history()
            self.logger.info(
                f"[复盘] upsert pnl_resolved: {symbol} PnL={pnl:+.4f} USDT "
                f"(req={entry_req_id})")
            return

        # external_close pending 没进 trade_history,这里 append 一笔
        trade_record = {
            'timestamp': msg.get('timestamp', time.time()),
            'symbol': symbol,
            'status': 'closed_externally',
            'pnl': pnl,
            'realized_pnl_net_usdt': pnl,
            'event_type': 'close',
            'exit_reason': 'pnl_resolved',
            'entry_request_id': entry_req_id,
            'position_id': position_id,
            'pnl_status': 'final',
            'pnl_source': payload.get('pnl_source', ''),
            'correlation_id': payload.get('correlation_id', ''),
        }
        self.trade_history.append(trade_record)
        self._save_trade_history()
        self.logger.info(
            f"[复盘] append pnl_resolved: {symbol} PnL={pnl:+.4f} USDT")

    async def _check_daily_hard_stop(self):
        """检查daily hard stop触发条件"""
        today_str = datetime.datetime.utcnow().strftime('%Y-%m-%d')
        # latch：当日已触发则不重复发；UTC 日切时自动重置
        if self._hard_stop_triggered_date == today_str:
            return

        daily_pnl = self._calculate_daily_pnl()
        consecutive_losses = self._track_consecutive_losses()

        reason = None
        payload = {}
        if daily_pnl <= self.daily_pnl_hard_stop:
            reason = "daily_loss_limit"
            payload = {"daily_pnl": daily_pnl, "limit": self.daily_pnl_hard_stop}
        elif consecutive_losses >= self.consecutive_loss_limit:
            reason = "consecutive_losses"
            payload = {"count": consecutive_losses, "limit": self.consecutive_loss_limit}

        if reason:
            self._hard_stop_triggered_date = today_str
            self.logger.critical(f"[熔断] Daily hard stop触发: {reason} {payload}")
            await self.publish("daily_hard_stop_triggered", {
                "reason": reason,
                "timestamp": time.time(),
                **payload,
            })

    @staticmethod
    def _provenance_bucket(attribution: dict) -> str:
        """Derive a provenance segmentation bucket from the attribution dict.

        Returns one of: 'cross_exchange/low', 'cross_exchange/high',
        'native/low', 'native/high', or 'unknown'.
        Legacy records without a provenance summary yield 'unknown'.
        """
        prov = attribution.get('provenance') or {}
        if prov.get('quality') == 'known':
            wc = prov.get('weakest_confidence')
            band = 'low' if (wc is not None and wc < 0.5) else 'high'
            venue = 'cross_exchange' if prov.get('has_cross_exchange') else 'native'
            return f'{venue}/{band}'
        return 'unknown'

    def _calculate_daily_pnl(self) -> float:
        """计算当日累计盈亏"""
        if not self.trade_history:
            return 0.0

        today = datetime.datetime.utcnow().date()
        daily_trades = [
            t for t in self.trade_history
            if datetime.datetime.utcfromtimestamp(t['timestamp']).date() == today
        ]

        return sum(t['pnl'] for t in daily_trades)

    def _track_consecutive_losses(self) -> int:
        """追踪最近24h内的连续亏损次数"""
        if not self.trade_history:
            return 0

        cutoff = time.time() - 24 * 3600
        recent_trades = [t for t in self.trade_history if t.get('timestamp', 0) > cutoff]

        consecutive_count = 0
        for trade in reversed(recent_trades):
            if trade['pnl'] < 0:
                consecutive_count += 1
            else:
                break

        return consecutive_count

    async def _run_strategy_review(self):
        """策略复盘（每12h触发一次）"""
        if len(self.trade_history) < 5:
            self.logger.info("[复盘] 交易历史不足5笔，跳过复盘")
            return

        recent_metrics = self._calculate_rolling_metrics()
        segmented_metrics = self._calculate_segmented_metrics()
        decay_signals = self._detect_strategy_decay()
        consecutive_losses = self._track_consecutive_losses()
        daily_pnl = self._calculate_daily_pnl()

        review_report = {
            'timestamp': time.time(),
            'recent_metrics': recent_metrics,
            'segmented_metrics': segmented_metrics,
            'decay_signals': decay_signals,
            'consecutive_losses': consecutive_losses,
            'daily_pnl': daily_pnl,
            'total_trades': len(self.trade_history),
        }
        # Keep the grouped metrics available at top level for downstream agents
        # that do not want to understand the nested report shape yet.
        review_report.update(segmented_metrics)

        # 生成建议
        if decay_signals:
            self.logger.warning(
                f"[复盘] 策略衰减检测: {len(decay_signals)}个指标异常"
            )
            for signal in decay_signals:
                self.logger.warning(
                    f"  - {signal['metric']}: {signal['recent']:.2f} "
                    f"(历史{signal['historical']:.2f}, 阈值{signal['threshold']:.2f})"
                )

        self.logger.info(
            f"[复盘] 近{self.rolling_window_size}笔: "
            f"胜率{recent_metrics['win_rate']:.1%}, "
            f"盈亏比{recent_metrics['profit_factor']:.2f}, "
            f"总盈亏{recent_metrics['total_pnl']:.2f} USDT"
        )

        await self.publish("strategy_review", review_report)

    def _calculate_rolling_metrics(self) -> dict:
        """计算滚动窗口指标"""
        window_size = min(self.rolling_window_size, len(self.trade_history))
        if window_size == 0:
            return {
                'win_rate': 0, 'profit_factor': 0, 'total_pnl': 0,
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0
            }

        recent_trades = self.trade_history[-window_size:]
        winning_trades = [t for t in recent_trades if t['pnl'] > 0]
        losing_trades = [t for t in recent_trades if t['pnl'] < 0]

        win_rate = len(winning_trades) / len(recent_trades) if recent_trades else 0

        gross_profit = sum(t['pnl'] for t in winning_trades) if winning_trades else 0
        gross_loss = abs(sum(t['pnl'] for t in losing_trades)) if losing_trades else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0)

        total_pnl = sum(t['pnl'] for t in recent_trades)

        return {
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'total_pnl': total_pnl,
            'total_trades': len(recent_trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'gross_profit': gross_profit,
            'gross_loss': gross_loss,
        }

    def _calculate_segmented_metrics(self) -> dict:
        """Compute metrics segmented by side, regime, and slot_type."""
        min_sample = 5
        window_size = min(self.rolling_window_size, len(self.trade_history))
        if window_size == 0:
            return {}
        recent = self.trade_history[-window_size:]

        def _metrics_for(trades):
            if not trades:
                return None
            wins = [t for t in trades if t['pnl'] > 0]
            losses = [t for t in trades if t['pnl'] < 0]
            gp = sum(t['pnl'] for t in wins)
            gl = abs(sum(t['pnl'] for t in losses)) if losses else 0
            wr = len(wins) / len(trades)
            return {
                'trade_count': len(trades),
                'win_rate': wr,
                'win_rate_ratio': wr,
                'win_rate_pct': round(wr * 100, 1),
                'profit_factor': gp / gl if gl > 0 else (gp if gp > 0 else 0),
                'total_pnl': sum(t['pnl'] for t in trades),
                'gross_profit': gp,
                'gross_loss': gl,
                'insufficient_sample': len(trades) < min_sample,
            }

        result = {}
        # By side
        by_side = {}
        for side in ('long', 'short'):
            subset = [t for t in recent if t.get('side') == side]
            m = _metrics_for(subset)
            if m:
                by_side[side] = m
        if by_side:
            result['metrics_by_side'] = by_side

        # By regime
        by_regime = {}
        for regime in ('bullish', 'mixed', 'bearish', 'choppy'):
            subset = [t for t in recent if t.get('entry_regime') == regime]
            m = _metrics_for(subset)
            if m:
                by_regime[regime] = m
        if by_regime:
            result['metrics_by_regime'] = by_regime

        # By slot_type
        by_slot = {}
        for slot in ('main', 'low_rr_extra', 'probe_short', 'probe_long'):
            subset = [t for t in recent if t.get('slot_type') == slot]
            m = _metrics_for(subset)
            if m:
                by_slot[slot] = m
        if by_slot:
            result['metrics_by_slot_type'] = by_slot

        # By side x regime
        by_side_regime = {}
        for side in ('long', 'short'):
            for regime in ('bullish', 'mixed', 'bearish', 'choppy'):
                subset = [
                    t for t in recent
                    if t.get('side') == side and t.get('entry_regime') == regime
                ]
                m = _metrics_for(subset)
                if m:
                    by_side_regime[f'{side}_{regime}'] = m
        if by_side_regime:
            result['metrics_by_side_regime'] = by_side_regime

        # Phase 2 EPIC D: full cross-product bucket (side_regime_entry_type_slot_type)
        by_bucket = {}
        for t in recent:
            side = t.get('side', 'long')
            regime = t.get('entry_regime', 'mixed')
            entry_type = t.get('entry_type', 'unknown')
            slot_type = t.get('slot_type', 'main')
            key = f"{side}_{regime}_{entry_type}_{slot_type}"
            by_bucket.setdefault(key, []).append(t)
        metrics_by_bucket = {}
        for key, trades in by_bucket.items():
            m = _metrics_for(trades)
            if m:
                metrics_by_bucket[key] = m
        if metrics_by_bucket:
            result['metrics_by_bucket'] = metrics_by_bucket

        return result

    def _detect_strategy_decay(self):
        """检测策略衰减（对比近期 vs 历史基线）"""
        if len(self.trade_history) < self.rolling_window_size * 2:
            return None

        # 历史基线：前半部分交易
        mid_point = len(self.trade_history) // 2
        historical_trades = self.trade_history[:mid_point]
        historical_metrics = self._calculate_metrics_for_trades(historical_trades)

        # 近期表现
        recent_metrics = self._calculate_rolling_metrics()

        decay_signals = []

        # 胜率衰减
        if recent_metrics['win_rate'] < self.decay_threshold_win_rate:
            decay_signals.append({
                'metric': 'win_rate',
                'historical': historical_metrics['win_rate'],
                'recent': recent_metrics['win_rate'],
                'threshold': self.decay_threshold_win_rate,
                'severity': 'high' if recent_metrics['win_rate'] < 0.40 else 'medium'
            })

        # 盈亏比衰减
        if recent_metrics['profit_factor'] < self.decay_threshold_profit_factor:
            decay_signals.append({
                'metric': 'profit_factor',
                'historical': historical_metrics['profit_factor'],
                'recent': recent_metrics['profit_factor'],
                'threshold': self.decay_threshold_profit_factor,
                'severity': 'high' if recent_metrics['profit_factor'] < 1.0 else 'medium'
            })

        return decay_signals if decay_signals else None

    def _calculate_metrics_for_trades(self, trades: list) -> dict:
        """计算指定交易列表的指标"""
        if not trades:
            return {'win_rate': 0, 'profit_factor': 0}

        winning = [t for t in trades if t['pnl'] > 0]
        losing = [t for t in trades if t['pnl'] < 0]

        win_rate = len(winning) / len(trades)
        gross_profit = sum(t['pnl'] for t in winning) if winning else 0
        gross_loss = abs(sum(t['pnl'] for t in losing)) if losing else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0)

        return {'win_rate': win_rate, 'profit_factor': profit_factor}

    def _load_trade_history(self):
        """加载交易历史"""
        if not os.path.exists(self.history_file):
            return

        try:
            with open(self.history_file, 'r') as f:
                self.trade_history = json.load(f)
            self.logger.info(f"加载交易历史: {len(self.trade_history)}笔")
        except Exception as e:
            self.logger.error(f"加载交易历史失败: {e}")

    def _save_trade_history(self):
        """保存交易历史（原子写入）"""
        try:
            from utils.atomic_io import atomic_write_json
            atomic_write_json(self.history_file, self.trade_history)
        except Exception as e:
            self.logger.error(f"保存交易历史失败: {e}")
