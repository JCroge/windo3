"""持仓分析官 Agent - 7因子评分 + 裁决引擎（防遗憾优化版）

每1小时对所有持仓进行重新评估：
1. 规则评分（7因子，含入场逻辑验证）→ 输出建议
2. 发送给BehavioralCritic审视
3. 收到批判意见后执行裁决逻辑（方向正确时保护持仓）
4. 最终决策发送给Executor执行
"""

import time
import json
import os
import asyncio
from agents.base import BaseAgent
from utils.symbol import to_internal

REVIEW_INTERVAL = 3600  # 1小时


class PositionAnalyst(BaseAgent):
    name = "position_analyst"
    subscriptions = ["execution_result", "tech_analysis:*", "price_tick:*", "position_verdict:*"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._positions = {}
        self._tech_cache = {}
        self._prices = {}
        self._last_review_time = 0
        self._pending_reviews = {}
        self._tick_counter = 0

    async def setup(self):
        self._load_positions()
        if self._positions:
            # 有持仓时60秒后触发首次评估（等DataCollector/TechAnalyst先发一轮数据）
            self._last_review_time = time.time() - REVIEW_INTERVAL + 60
        else:
            self._last_review_time = time.time()
        self.logger.info(f"持仓分析官就绪 (评估周期={REVIEW_INTERVAL//3600}h, 持仓={len(self._positions)}个, 7因子防遗憾版)")

    def _load_positions(self):
        from utils.state_paths import get_state_paths
        positions_file = get_state_paths().positions
        if os.path.exists(positions_file):
            try:
                with open(positions_file, 'r') as f:
                    raw = json.load(f)
                self._positions = {}
                for k, v in raw.items():
                    sym = to_internal(k)
                    if isinstance(v, dict):
                        self._normalize_position_record(v)
                    self._positions[sym] = v
            except Exception as e:
                self.logger.error(f"加载持仓失败: {e}")

    @staticmethod
    def _normalize_position_record(v: dict):
        """Ensure attribution fields are top-level on position dict."""
        attr = v.get('attribution', {}) or {}
        v.setdefault('is_probe', attr.get('is_probe', False))
        v.setdefault('is_low_rr', attr.get('is_low_rr', False))
        v.setdefault('slot_type', attr.get('slot_type', 'main'))
        v.setdefault('entry_regime', attr.get('entry_regime', 'unknown'))

    async def on_message(self, msg: dict):
        if msg['type'] == 'execution_result':
            self._handle_execution(msg['payload'])
        elif msg['type'] == 'tech_analysis':
            symbol = msg.get('symbol') or msg['payload'].get('symbol')
            if symbol:
                self._tech_cache[to_internal(symbol)] = msg['payload']
        elif msg['type'] == 'price_tick':
            symbol = msg.get('symbol') or msg['payload'].get('symbol')
            price = msg['payload'].get('price')
            if symbol and price:
                self._prices[to_internal(symbol)] = price
        elif msg['type'] == 'position_verdict':
            await self._handle_critic_verdict(msg['payload'])

    def _handle_execution(self, payload: dict):
        result = payload.get('result', {})
        symbol = result.get('symbol') or payload.get('symbol')
        if not symbol:
            return
        # 统一为系统内部规范 BASE-USDT
        symbol = to_internal(symbol)
        status = payload.get('status')

        if status == 'executed':
            action = payload.get('action', '')
            if action in ('open_long', 'open_short'):
                if payload.get('is_add') and symbol in self._positions:
                    # 加仓：更新均价和总量，保留open_time
                    pos = self._positions[symbol]
                    pos['entry_price'] = result.get('new_entry_price', result.get('entry_price', pos['entry_price']))
                    if 'amount_usdt' in result:
                        pos['amount_usdt'] = result['amount_usdt']
                    elif 'add_amount_usdt' in result:
                        pos['amount_usdt'] = pos.get('amount_usdt', 0) + result['add_amount_usdt']
                    pos['stop_loss'] = result.get('new_stop_loss') or result.get('stop_loss') or pos.get('stop_loss')
                    pos['take_profit'] = result.get('new_take_profit') or result.get('take_profit') or pos.get('take_profit')
                else:
                    # 新开仓
                    attribution = result.get('attribution', {})
                    self._positions[symbol] = {
                        "symbol": symbol,
                        "side": 'long' if action == 'open_long' else 'short',
                        "entry_price": result.get('entry_price', 0),
                        "amount_usdt": result.get('amount_usdt', 0),
                        "leverage": result.get('leverage', 1),
                        "stop_loss": result.get('stop_loss'),
                        "take_profit": result.get('take_profit'),
                        "open_time": time.time(),
                        "is_probe": attribution.get('is_probe', False),
                        "is_low_rr": attribution.get('is_low_rr', False),
                        "slot_type": attribution.get('slot_type', 'main'),
                        "entry_regime": attribution.get('entry_regime', 'unknown'),
                    }
            elif action == 'close':
                self._positions.pop(symbol, None)
        elif status in ('force_closed', 'closed_externally'):
            if symbol in self._positions:
                self.logger.info(f"[持仓分析] {symbol} 已被外部平仓，移除记录")
            self._positions.pop(symbol, None)
            self._pending_reviews.pop(symbol, None)
        elif status == 'risk_reduced':
            if symbol in self._positions:
                reduce_pct = payload.get('reduce_pct', 0.5)
                self._positions[symbol]['amount_usdt'] *= (1 - reduce_pct)

    async def tick(self):
        await asyncio.sleep(1)
        self._tick_counter += 1

        now = time.time()

        # 检查超时的 pending reviews（批判官未在 30s 内回复）
        for symbol in list(self._pending_reviews.keys()):
            deadline = self._pending_reviews[symbol].get('_deadline', 0)
            if now >= deadline:
                verdict = self._pending_reviews.pop(symbol)
                self.logger.info(f"[持仓分析] {symbol} 批判官超时，直接采纳分析建议")
                final = self._arbitrate_no_critic(verdict)
                if final['final_action'] != 'hold':
                    await self._execute_final_decision(final)

        if now - self._last_review_time >= REVIEW_INTERVAL and self._positions:
            if not self._pending_reviews:
                evaluated = await self._evaluate_all_positions()
                if evaluated:
                    self._last_review_time = now
                else:
                    self._last_review_time = now - REVIEW_INTERVAL + 30

    async def _evaluate_all_positions(self) -> bool:
        """返回True表示至少评估了一个持仓，False表示全部因缺数据跳过"""
        from utils.state_paths import get_state_paths
        positions_file = get_state_paths().positions
        if os.path.exists(positions_file):
            try:
                with open(positions_file, 'r') as f:
                    file_positions = json.load(f)
                if file_positions:
                    # 统一为内部规范 BASE-USDT，避免 SWAP/ccxt 格式混入 state
                    for k, v in file_positions.items():
                        internal = to_internal(k)
                        if internal not in self._positions:
                            if isinstance(v, dict):
                                self._normalize_position_record(v)
                            self._positions[internal] = v
            except Exception:
                pass

        if not self._positions:
            return False

        self.logger.info(f"[持仓分析] 开始评估 {len(self._positions)} 个持仓")
        evaluated_any = False

        for symbol, pos in list(self._positions.items()):
            verdict = self._compute_position_score(symbol, pos)
            if verdict is None:
                continue
            evaluated_any = True

            override = self._check_hard_override(symbol, pos, verdict)
            if override:
                self.logger.warning(f"[持仓分析] {symbol} 硬性规则触发: {override['override_rule']}")
                await self._execute_final_decision(override)
                continue

            verdict['_deadline'] = time.time() + 30
            self._pending_reviews[symbol] = verdict
            await self.publish("position_review", verdict, symbol=symbol)
            self.logger.info(
                f"[持仓分析] {symbol} score={verdict['position_score']:.0f} "
                f"action={verdict['action']} conviction={verdict['conviction']:.0f}"
            )

        return evaluated_any

    def _compute_position_score(self, symbol: str, pos: dict) -> dict:
        """7因子持仓评分（防遗憾优化版）"""
        # symbol 已统一为 internal 格式（to_internal 处理过），直接查 _tech_cache
        lookup_key = to_internal(symbol)
        tech = self._tech_cache.get(lookup_key)
        if not tech:
            # 兼容旧 cache key（如 _tech_cache 中残留 SWAP 格式）
            for k, v in self._tech_cache.items():
                if to_internal(k) == lookup_key:
                    tech = v
                    break

        if not tech:
            return None

        entry_price = pos.get('entry_price', 0)
        if entry_price == 0:
            return None

        side = pos.get('side', 'long')
        leverage = pos.get('leverage', 1)
        stop_loss = pos.get('stop_loss', 0)
        take_profit = pos.get('take_profit', 0)
        open_time = pos.get('open_time', time.time())

        current_price = self._get_current_price(symbol)
        if not current_price:
            return None

        if side == 'long':
            pnl_pct = (current_price - entry_price) / entry_price * 100 * leverage
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100 * leverage

        hours_held = (time.time() - open_time) / 3600

        # === 7因子评分 ===
        trend_data = tech.get('trend', {})
        momentum = tech.get('momentum', {})

        # 1. 趋势对齐 (-20 ~ +20)
        trend_dir = trend_data.get('direction', 'neutral')
        if trend_dir == 'neutral':
            trend_alignment = 0
        elif (side == 'long' and trend_dir == 'bullish') or (side == 'short' and trend_dir == 'bearish'):
            trend_alignment = 20
        else:
            trend_alignment = -20

        # 2. 动量变化 (-20 ~ +15) — 结合MACD方向区分回调和反转
        rsi = momentum.get('rsi', 50)
        macd_trend = momentum.get('macd_histogram_trend', 'flat')

        if side == 'long':
            if rsi > 55:
                momentum_shift = 15
            elif rsi > 40:
                if macd_trend == 'rising':
                    momentum_shift = 10
                elif macd_trend == 'flat':
                    momentum_shift = 0
                else:
                    momentum_shift = -5
            elif rsi > 30:
                momentum_shift = -10
            else:
                momentum_shift = -20
        else:
            if rsi < 45:
                momentum_shift = 15
            elif rsi < 60:
                if macd_trend == 'falling':
                    momentum_shift = 10
                elif macd_trend == 'flat':
                    momentum_shift = 0
                else:
                    momentum_shift = -5
            elif rsi < 70:
                momentum_shift = -10
            else:
                momentum_shift = -20

        # 3. 时间衰减 (-15 ~ 0) — 盈利仓位不惩罚
        if pnl_pct > 0:
            time_decay = 0
        elif pnl_pct > -3:
            time_decay = -min(10, hours_held / 6)
        else:
            time_decay = -min(15, hours_held / 4)

        # 4. 浮盈状态 (-20 ~ +20) — 考虑杠杆的正常波动范围
        normal_fluctuation = min(5, leverage * 0.5)
        if pnl_pct > 5:
            pnl_status = 20
        elif pnl_pct > 1:
            pnl_status = 10
        elif pnl_pct > -normal_fluctuation:
            pnl_status = 0
        elif pnl_pct > -(normal_fluctuation * 2):
            pnl_status = -10
        else:
            pnl_status = -20

        # 5. 成交量确认 (-10 ~ +10)
        volume_confirm = 0
        vol_trend = tech.get('volume', {}).get('trend', 'normal')
        if vol_trend == 'increasing':
            volume_confirm = 10 if trend_alignment > 0 else -10
        elif vol_trend == 'decreasing':
            volume_confirm = -5

        # 6. 剩余R:R (-15 ~ +15)
        rr_bonus = 0
        if take_profit and stop_loss and current_price:
            if side == 'long':
                remaining_profit = take_profit - current_price
                remaining_risk = current_price - stop_loss
            else:
                remaining_profit = current_price - take_profit
                remaining_risk = stop_loss - current_price

            if remaining_risk > 0:
                remaining_rr = remaining_profit / remaining_risk
                if remaining_rr > 2:
                    rr_bonus = 15
                elif remaining_rr > 1:
                    rr_bonus = 5
                elif remaining_rr < 0.3:
                    rr_bonus = -15
                elif remaining_rr < 0.5:
                    rr_bonus = -10

        # 7. 入场逻辑验证 (-10 ~ +25) — 高时间框架方向保护
        entry_thesis_intact = 0
        htf_bias = trend_data.get('higher_tf_bias', 'neutral')
        daily_bias = trend_data.get('daily_bias', 'neutral')
        higher_trend = daily_bias if daily_bias != 'neutral' else htf_bias

        if (side == 'long' and higher_trend == 'bullish') or \
           (side == 'short' and higher_trend == 'bearish'):
            entry_thesis_intact = 25
        elif higher_trend == 'neutral':
            entry_thesis_intact = 10
        elif (side == 'long' and higher_trend == 'bearish') or \
             (side == 'short' and higher_trend == 'bullish'):
            entry_thesis_intact = -10

        # 总分
        position_score = (trend_alignment + momentum_shift + time_decay +
                          pnl_status + volume_confirm + rr_bonus + entry_thesis_intact)

        # action映射 — 提高close/reduce门槛
        if position_score >= 50:
            # Probe positions must not be added to
            if pos.get('is_probe'):
                action = 'hold'
                conviction = position_score
            # 加仓上限检查：总保证金不超过max_trade_amount×2
            elif pos.get('amount_usdt', 0) >= self.config.get('max_trade_amount', 10) * 2:
                action = 'hold'
                conviction = position_score
            else:
                action = 'add'
                conviction = min(95, position_score)
        elif position_score >= 10:
            action = 'hold'
            conviction = position_score
        elif position_score >= -30:
            action = 'hold'
            conviction = 50 - abs(position_score)
        elif position_score >= -60:
            action = 'reduce'
            conviction = abs(position_score)
        else:
            action = 'close'
            conviction = min(95, abs(position_score))

        regime_context = self._build_regime_context(pos)

        return {
            "symbol": symbol,
            "action": action,
            "conviction": conviction,
            "position_score": position_score,
            "factors": {
                "trend_alignment": trend_alignment,
                "momentum_shift": momentum_shift,
                "time_decay": round(time_decay, 1),
                "pnl_status": pnl_status,
                "volume_confirm": volume_confirm,
                "rr_bonus": rr_bonus,
                "entry_thesis_intact": entry_thesis_intact,
            },
            "context": {
                "side": side,
                "leverage": leverage,
                "entry_price": entry_price,
                "current_price": current_price,
                "pnl_pct": round(pnl_pct, 2),
                "hours_held": round(hours_held, 1),
                "rsi": rsi,
                "trend": trend_dir,
                "higher_trend": higher_trend,
                "r_multiple": self._compute_r_multiple(pos, current_price),
                "tf_15m_confirm_short": tech.get('entry_timing', {}).get('tf_15m_confirm_short', False),
                "entry_regime": regime_context['entry_regime'],
                "current_regime": regime_context['current_regime'],
                "regime_age_sec": regime_context['regime_age_sec'],
                "position_age_sec": regime_context['position_age_sec'],
                "regime_grace_active": regime_context['regime_grace_active'],
            },
            "regime_context": regime_context,
            "reasoning": self._build_reasoning(action, position_score, trend_alignment, momentum_shift, pnl_pct, entry_thesis_intact),
        }

    def _check_hard_override(self, symbol: str, pos: dict, verdict: dict) -> dict:
        """硬性覆盖规则 — 无论分析官和批判官怎么说（防遗憾优化版）"""
        ctx = verdict['context']
        pnl_pct = ctx['pnl_pct']
        hours_held = ctx['hours_held']
        side = pos.get('side', 'long')
        higher_trend = ctx.get('higher_trend', 'neutral')

        # Regime grace protects fresh low-R:R bullish longs from being reduced
        # solely because effective_regime temporarily flips away from bullish.
        regime_context = verdict.get('regime_context') or self._build_regime_context(pos)
        verdict['regime_context'] = regime_context
        verdict['context'].update(regime_context)
        regime_grace_active = regime_context['regime_grace_active']

        # 规则1: 浮亏超过SL预期水平 → close（SL失败时的第三道防线）
        # 正常触发顺序：交易所SL条件单(实时) → Executor兜底(5s) → PA规则1(1h)
        # 规则1只在前两道都失败时才有意义，阈值=SL含杠杆距离（不抢跑SL）
        leverage = pos.get('leverage', 1)
        stop_loss = pos.get('stop_loss', 0)
        entry_price = pos.get('entry_price', 0)
        if stop_loss and entry_price:
            sl_dist_pct = abs(stop_loss - entry_price) / entry_price * 100 * leverage
            hard_stop_threshold = -sl_dist_pct
        else:
            hard_stop_threshold = -30  # 无SL时兜底30%

        if pnl_pct < hard_stop_threshold:
            return self._make_final("close", 1.0, symbol, verdict['action'],
                                    None, None, f"硬性规则：浮亏{pnl_pct:.1f}%超SL水平({hard_stop_threshold:.1f}%)，SL可能失效")

        if regime_grace_active and verdict.get('action') in ('reduce', 'close'):
            verdict['reasoning'] = (
                f"{verdict.get('reasoning', '')}; regime_grace: "
                f"entry={regime_context['entry_regime']} current={regime_context['current_regime']}"
            )
            verdict['action'] = 'hold'
            verdict['conviction'] = max(verdict.get('conviction', 0), 40)

        # 规则2: 持仓>72h + 浮亏>3% → close（放宽：原48h+任何浮亏）
        if hours_held > 72 and pnl_pct < -3:
            return self._make_final("close", 1.0, symbol, verdict['action'],
                                    None, None, f"硬性规则：持仓{hours_held:.0f}h>72h且浮亏{pnl_pct:.1f}%")

        # 规则3: 高时间框架趋势反转 + 浮亏>5% → close（放宽：原1h反转+3%）
        # Regime grace: skip trend-based rules for low_rr within 60min of regime flap
        htf_reversed = (side == 'long' and higher_trend == 'bearish') or \
                       (side == 'short' and higher_trend == 'bullish')
        if htf_reversed and pnl_pct < -5 and not regime_grace_active:
            return self._make_final("close", 1.0, symbol, verdict['action'],
                                    None, None, f"硬性规则：高级别趋势反转+浮亏{pnl_pct:.1f}%")

        # 规则3b: 浮亏超过SL距离50% + 趋势非顺向 → close
        # 含义：价格已走过SL一半路程且趋势不再支持，入场逻辑失效的早期信号
        trend_aligned = (side == 'long' and higher_trend == 'bullish') or \
                        (side == 'short' and higher_trend == 'bearish')
        if stop_loss and entry_price:
            rule3b_threshold = -(sl_dist_pct * 0.5)
        else:
            rule3b_threshold = -20
        if pnl_pct < rule3b_threshold and not trend_aligned and not regime_grace_active:
            return self._make_final("close", 1.0, symbol, verdict['action'],
                                    None, None, f"硬性规则：浮亏{pnl_pct:.1f}%超SL半程({rule3b_threshold:.1f}%)且趋势非顺向({higher_trend})")

        # 规则4: 浮盈>15% + 动量反转 → reduce 50%（保持不变）
        momentum_reversed = verdict['factors']['momentum_shift'] <= -10
        if pnl_pct > 15 and momentum_reversed:
            return self._make_final("reduce", 0.5, symbol, verdict['action'],
                                    None, None, f"硬性规则：浮盈{pnl_pct:.1f}%+动量反转，锁定利润")

        # 规则5: 剩余R:R < 0.3 → close（保持不变）
        if verdict['factors']['rr_bonus'] == -15:
            return self._make_final("close", 1.0, symbol, verdict['action'],
                                    None, None, "硬性规则：剩余R:R<0.3，空间不足")

        # 规则6: probe_short 2h 生命周期退出
        # PRD: probe 持仓超过 2h 且未达 0.5R 且 15m 不继续走弱 → close
        if pos.get('is_probe') and hours_held > 2:
            r_multiple = ctx.get('r_multiple', 0)
            tf_15m_short_confirm = ctx.get('tf_15m_confirm_short', False)
            if r_multiple < 0.5 and not tf_15m_short_confirm:
                return self._make_final("close", 1.0, symbol, verdict['action'],
                                        None, None,
                                        f"硬性规则：probe_short持仓{hours_held:.1f}h>2h，R={r_multiple:.2f}<0.5且15m未继续走弱")

        return None

    def _compute_r_multiple(self, pos: dict, current_price: float) -> float:
        """Compute current R-multiple: profit / initial risk."""
        entry_price = pos.get('entry_price', 0)
        stop_loss = pos.get('stop_loss', 0)
        side = pos.get('side', 'long')
        if not entry_price or not stop_loss:
            return 0.0
        initial_risk = abs(entry_price - stop_loss)
        if initial_risk == 0:
            return 0.0
        if side == 'long':
            profit = current_price - entry_price
        else:
            profit = entry_price - current_price
        return profit / initial_risk

    async def _handle_critic_verdict(self, payload: dict):
        """收到批判官意见，执行裁决"""
        symbol = payload.get('symbol')
        if symbol not in self._pending_reviews:
            return

        analyst_verdict = self._pending_reviews.pop(symbol)
        final = self._arbitrate(analyst_verdict, payload)

        self.logger.info(
            f"[裁决] {symbol} 分析={analyst_verdict['action']} "
            f"批判={payload.get('bias_detected', 'none')}({payload.get('severity', 'none')}) "
            f"→ 最终={final['final_action']}"
        )

        if final['final_action'] != 'hold':
            await self._execute_final_decision(final)

    def _arbitrate(self, analyst: dict, critic: dict) -> dict:
        """核心裁决矩阵（防遗憾优化版：方向正确时保护持仓）"""
        a_action = analyst['action']
        a_conviction = analyst['conviction']
        bias = critic.get('bias_detected')
        severity = critic.get('severity', 'none')
        counter = critic.get('counter_recommendation') or critic.get('counter_action')
        symbol = analyst['symbol']
        pnl_pct = analyst['context']['pnl_pct']

        # 方向验证：高时间框架趋势仍与持仓方向一致
        side = analyst['context'].get('side', 'long')
        higher_trend = analyst['context'].get('higher_trend', 'neutral')
        trend_aligned = (side == 'long' and higher_trend == 'bullish') or \
                        (side == 'short' and higher_trend == 'bearish')

        # 方向保护：趋势仍顺向时，critic的平仓建议降级
        if trend_aligned and bias and severity in ('medium', 'high'):
            if counter == 'close':
                counter = 'reduce'
            elif counter == 'reduce' and severity == 'medium':
                counter = 'hold'

        # Case 1: 批判官无异议
        if not bias or severity == 'none':
            if a_action == 'add' and a_conviction < 70:
                return self._make_final("hold", 0, symbol, a_action, bias, severity, "加仓conviction不足70")
            return self._make_final(a_action, 0.5 if a_action == 'reduce' else 1.0,
                                    symbol, a_action, bias, severity, "批判官无异议，采纳分析建议")

        # Case 2: severity = low
        if severity == 'low':
            if a_action == 'add':
                return self._make_final("hold", 0, symbol, a_action, bias, severity, "轻微偏差，加仓降级为hold")
            return self._make_final(a_action, 0.5 if a_action == 'reduce' else 1.0,
                                    symbol, a_action, bias, severity, "轻微偏差，保守方向不阻止")

        # Case 3: severity = medium
        if severity == 'medium':
            if a_action == 'add':
                return self._make_final("hold", 0, symbol, a_action, bias, severity, "中度偏差，禁止加仓")
            if a_action == 'hold' and counter == 'close':
                return self._make_final("reduce", 0.5, symbol, a_action, bias, severity, "中度偏差，折中减仓50%")
            if a_action == 'hold' and counter == 'reduce':
                return self._make_final("reduce", 0.3, symbol, a_action, bias, severity, "中度偏差，折中减仓30%")
            if a_action in ('reduce', 'close') and bias == 'panic':
                if a_action == 'close':
                    return self._make_final("reduce", 0.5, symbol, a_action, bias, severity, "防恐慌抛售，折中减仓50%")
                return self._make_final("hold", 0, symbol, a_action, bias, severity, "防恐慌抛售，维持持仓")
            if a_action == 'close' and bias == 'disposition':
                return self._make_final("hold", 0, symbol, a_action, bias, severity, "防过早止盈，维持持仓")
            return self._make_final(a_action, 0.5 if a_action == 'reduce' else 1.0,
                                    symbol, a_action, bias, severity, "中度偏差，保守方向不阻止")

        # Case 4: severity = high
        if a_action == 'add':
            return self._make_final("hold", 0, symbol, a_action, bias, severity, "高度偏差，绝对禁止加仓")
        if a_action == 'hold' and bias in ('loss_aversion', 'sunk_cost') and counter == 'close':
            if trend_aligned:
                return self._make_final("hold", 0, symbol, a_action, bias, severity,
                                        f"高度{bias}偏差但方向仍正确，维持观察")
            return self._make_final("close", 1.0, symbol, a_action, bias, severity,
                                    f"高度{bias}偏差+方向失效，采纳批判官平仓建议")
        if a_action == 'hold' and counter == 'reduce':
            return self._make_final("reduce", 0.5, symbol, a_action, bias, severity, "高度偏差，减仓50%")
        if a_action == 'close' and bias == 'panic':
            if pnl_pct > -3:
                return self._make_final("hold", 0, symbol, a_action, bias, severity, "高度恐慌+浮亏<3%，可能是洗盘")
            return self._make_final("close", 1.0, symbol, a_action, bias, severity, "浮亏≥3%，不是恐慌是事实")

        return self._make_final(a_action, 0.5 if a_action == 'reduce' else 1.0,
                                symbol, a_action, bias, severity, "高度偏差，保守处理")

    def _arbitrate_no_critic(self, analyst: dict) -> dict:
        """批判官超时，直接采纳分析建议（加仓需高conviction）"""
        a_action = analyst['action']
        if a_action == 'add' and analyst['conviction'] < 70:
            return self._make_final("hold", 0, analyst['symbol'], a_action, None, None, "无批判官+conviction不足，不加仓")
        return self._make_final(a_action, 0.5 if a_action == 'reduce' else 1.0,
                                analyst['symbol'], a_action, None, None, "批判官超时，采纳分析建议")

    def _make_final(self, action: str, reduce_pct: float, symbol: str,
                    analyst_action: str, bias, severity, reasoning: str) -> dict:
        return {
            "symbol": symbol,
            "final_action": action,
            "reduce_pct": reduce_pct,
            "reasoning": reasoning,
            "analyst_action": analyst_action,
            "critic_bias": bias,
            "critic_severity": severity,
            "override_rule": reasoning if "硬性规则" in reasoning else None,
        }

    async def _execute_final_decision(self, final: dict):
        """将裁决结果转为trade_decision发送给Executor"""
        symbol = final['symbol']
        action = final['final_action']

        if action == 'close':
            decision = {
                "action": "close",
                "symbol": symbol,
                "confidence": 90,
                "size_pct": 1.0,
                "reasoning": f"[持仓管理] {final['reasoning']}",
                "source": "position_analyst",
            }
        elif action == 'reduce':
            decision = {
                "action": "close",
                "symbol": symbol,
                "confidence": 80,
                "size_pct": final['reduce_pct'],
                "reasoning": f"[持仓管理·减仓{int(final['reduce_pct']*100)}%] {final['reasoning']}",
                "source": "position_analyst",
            }
        elif action == 'add':
            decision = {
                "action": self._get_open_action(symbol),
                "symbol": symbol,
                "confidence": 70,
                "size_pct": 0.3,
                "reasoning": f"[持仓管理·加仓] {final['reasoning']}",
                "source": "position_analyst",
            }
        else:
            return

        self.logger.info(f"[持仓管理] {symbol} 执行: {action} — {final['reasoning']}")
        await self.publish("trade_decision", decision, symbol=symbol)

    def _get_open_action(self, symbol: str) -> str:
        pos = self._positions.get(symbol, {})
        return 'open_long' if pos.get('side') == 'long' else 'open_short'

    def _get_current_price(self, symbol: str) -> float:
        for k, v in self._prices.items():
            if symbol.split('-')[0] in k or k in symbol:
                return v
        pos = self._positions.get(symbol, {})
        return pos.get('entry_price', 0)

    def _get_current_regime(self) -> str:
        """Read current effective regime from RegimeManager's persisted state."""
        return self._get_current_regime_snapshot().get('effective_regime', 'mixed')

    def _get_current_regime_snapshot(self) -> dict:
        """Read current regime snapshot persisted by Judge/RegimeManager."""
        try:
            with open('data/regime_state.json', 'r') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {'effective_regime': 'mixed'}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {'effective_regime': 'mixed'}

    def _build_regime_context(self, pos: dict) -> dict:
        now = time.time()
        snap = self._get_current_regime_snapshot()
        current_regime = snap.get('effective_regime', 'mixed')
        entry_regime = pos.get('entry_regime', 'unknown')
        open_time = pos.get('open_time', now)
        last_changed_at = snap.get('last_changed_at') or snap.get('saved_at') or now
        position_age_sec = max(0, now - open_time)
        regime_age_sec = max(0, now - last_changed_at)
        grace_active = (
            pos.get('is_low_rr') and
            entry_regime == 'bullish' and
            current_regime != 'bullish' and
            position_age_sec < 3600
        )
        return {
            'entry_regime': entry_regime,
            'current_regime': current_regime,
            'regime_age_sec': round(regime_age_sec, 1),
            'position_age_sec': round(position_age_sec, 1),
            'regime_grace_active': bool(grace_active),
        }

    def _build_reasoning(self, action: str, score: float, trend: float, momentum: float, pnl: float, thesis: float = 0) -> str:
        parts = []
        if trend > 0:
            parts.append("趋势顺向")
        elif trend < 0:
            parts.append("趋势逆向")
        if momentum > 10:
            parts.append("动量加速")
        elif momentum < -10:
            parts.append("动量减速")
        if pnl > 5:
            parts.append(f"浮盈{pnl:.1f}%")
        elif pnl < -3:
            parts.append(f"浮亏{pnl:.1f}%")
        if thesis >= 25:
            parts.append("高级别方向正确")
        elif thesis <= -10:
            parts.append("入场逻辑失效")
        return f"score={score:.0f}, " + ", ".join(parts) if parts else f"score={score:.0f}, 信号中性"
