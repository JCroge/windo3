"""持仓分析官 Agent - 6因子评分 + 裁决引擎

每30分钟对所有持仓进行重新评估：
1. 规则评分（6因子）→ 输出建议
2. 发送给BehavioralCritic审视
3. 收到批判意见后执行裁决逻辑
4. 最终决策发送给Executor执行
"""

import time
import json
import os
import asyncio
from agents.base import BaseAgent

REVIEW_INTERVAL = 3600  # 60分钟


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
        self._last_review_time = time.time()
        self.logger.info(f"持仓分析官就绪 (评估周期={REVIEW_INTERVAL//60}min, 持仓={len(self._positions)}个)")
        # 备选：事件触发评估（价格单小时移动>3%时插入额外评估），当前未启用

    def _load_positions(self):
        positions_file = 'data/positions.json'
        if os.path.exists(positions_file):
            try:
                with open(positions_file, 'r') as f:
                    self._positions = json.load(f)
            except Exception as e:
                self.logger.error(f"加载持仓失败: {e}")

    async def on_message(self, msg: dict):
        if msg['type'] == 'execution_result':
            self._handle_execution(msg['payload'])
        elif msg['type'] == 'tech_analysis':
            symbol = msg.get('symbol') or msg['payload'].get('symbol')
            if symbol:
                self._tech_cache[symbol] = msg['payload']
        elif msg['type'] == 'price_tick':
            symbol = msg.get('symbol') or msg['payload'].get('symbol')
            price = msg['payload'].get('price')
            if symbol and price:
                self._prices[symbol] = price
        elif msg['type'] == 'position_verdict':
            await self._handle_critic_verdict(msg['payload'])

    def _handle_execution(self, payload: dict):
        result = payload.get('result', {})
        symbol = result.get('symbol') or payload.get('symbol')
        if not symbol:
            return
        status = payload.get('status')

        if status == 'executed':
            action = payload.get('action', '')
            if action in ('open_long', 'open_short'):
                self._positions[symbol] = {
                    "symbol": symbol,
                    "side": 'long' if action == 'open_long' else 'short',
                    "entry_price": result.get('entry_price', 0),
                    "amount_usdt": result.get('amount_usdt', 0),
                    "leverage": result.get('leverage', 1),
                    "stop_loss": result.get('stop_loss'),
                    "take_profit": result.get('take_profit'),
                    "open_time": time.time(),
                }
            elif action == 'close':
                self._positions.pop(symbol, None)
        elif status in ('force_closed',):
            self._positions.pop(symbol, None)
        elif status == 'risk_reduced':
            if symbol in self._positions:
                self._positions[symbol]['amount_usdt'] *= 0.5

    async def tick(self):
        await asyncio.sleep(10)
        self._tick_counter += 1

        now = time.time()
        if now - self._last_review_time >= REVIEW_INTERVAL and self._positions:
            self._last_review_time = now
            await self._evaluate_all_positions()

    async def _evaluate_all_positions(self):
        self._load_positions()
        if not self._positions:
            return

        self.logger.info(f"[持仓分析] 开始评估 {len(self._positions)} 个持仓")

        for symbol, pos in list(self._positions.items()):
            verdict = self._compute_position_score(symbol, pos)
            if verdict is None:
                continue

            override = self._check_hard_override(symbol, pos, verdict)
            if override:
                self.logger.warning(f"[持仓分析] {symbol} 硬性规则触发: {override['override_rule']}")
                await self._execute_final_decision(override)
                continue

            self._pending_reviews[symbol] = verdict
            await self.publish("position_review", verdict, symbol=symbol)
            self.logger.info(
                f"[持仓分析] {symbol} score={verdict['position_score']:.0f} "
                f"action={verdict['action']} conviction={verdict['conviction']:.0f}"
            )

        await asyncio.sleep(30)
        for symbol, verdict in list(self._pending_reviews.items()):
            if symbol in self._pending_reviews:
                self.logger.info(f"[持仓分析] {symbol} 批判官超时，直接采纳分析建议")
                final = self._arbitrate_no_critic(verdict)
                if final['final_action'] != 'hold':
                    await self._execute_final_decision(final)
                del self._pending_reviews[symbol]

    def _compute_position_score(self, symbol: str, pos: dict) -> dict:
        """6因子持仓评分"""
        # 转换symbol格式用于查找tech_cache
        lookup_key = symbol.replace('-SWAP', '').replace('/', '-').replace(':USDT', '')
        tech = None
        for k, v in self._tech_cache.items():
            if lookup_key in k or k in lookup_key:
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

        # 获取当前价格
        current_price = self._get_current_price(symbol)
        if not current_price:
            return None

        # 计算浮盈
        if side == 'long':
            pnl_pct = (current_price - entry_price) / entry_price * 100 * leverage
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100 * leverage

        hours_held = (time.time() - open_time) / 3600

        # === 6因子评分 ===
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

        # 2. 动量变化 (-20 ~ +20)
        rsi = momentum.get('rsi', 50)
        if side == 'long':
            if rsi > 60:
                momentum_shift = 15
            elif rsi > 45:
                momentum_shift = 5
            elif rsi > 35:
                momentum_shift = -10
            else:
                momentum_shift = -20
        else:
            if rsi < 40:
                momentum_shift = 15
            elif rsi < 55:
                momentum_shift = 5
            elif rsi > 65:
                momentum_shift = -10
            else:
                momentum_shift = -20

        # 3. 时间衰减 (-15 ~ 0)
        time_decay = -min(15, hours_held / 4)

        # 4. 浮盈状态 (-20 ~ +20)
        if pnl_pct > 5:
            pnl_status = 20
        elif pnl_pct > 1:
            pnl_status = 10
        elif pnl_pct > -1:
            pnl_status = 0
        elif pnl_pct > -5:
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

        # 总分
        position_score = trend_alignment + momentum_shift + time_decay + pnl_status + volume_confirm + rr_bonus

        # action映射
        if position_score >= 50:
            action = 'add'
            conviction = min(95, position_score)
        elif position_score >= 20:
            action = 'hold'
            conviction = position_score
        elif position_score >= -20:
            action = 'hold'
            conviction = 50 - abs(position_score)
        elif position_score >= -50:
            action = 'reduce'
            conviction = abs(position_score)
        else:
            action = 'close'
            conviction = min(95, abs(position_score))

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
            },
            "reasoning": self._build_reasoning(action, position_score, trend_alignment, momentum_shift, pnl_pct),
        }

    def _check_hard_override(self, symbol: str, pos: dict, verdict: dict) -> dict:
        """硬性覆盖规则 — 无论分析官和批判官怎么说"""
        ctx = verdict['context']
        pnl_pct = ctx['pnl_pct']
        hours_held = ctx['hours_held']
        trend = ctx['trend']
        side = pos.get('side', 'long')

        # 规则1: 浮亏>12% → close
        if pnl_pct < -12:
            return self._make_final("close", 1.0, symbol, verdict['action'],
                                    None, None, f"硬性规则：浮亏{pnl_pct:.1f}%>12%")

        # 规则2: 持仓>48h + 浮亏 → close
        if hours_held > 48 and pnl_pct < 0:
            return self._make_final("close", 1.0, symbol, verdict['action'],
                                    None, None, f"硬性规则：持仓{hours_held:.0f}h>48h且浮亏")

        # 规则3: 趋势完全反转 + 浮亏>3% → close
        trend_reversed = (side == 'long' and trend == 'bearish') or (side == 'short' and trend == 'bullish')
        if trend_reversed and pnl_pct < -3:
            return self._make_final("close", 1.0, symbol, verdict['action'],
                                    None, None, f"硬性规则：趋势反转+浮亏{pnl_pct:.1f}%")

        # 规则4: 浮盈>15% + 动量反转 → reduce 50%
        momentum_reversed = verdict['factors']['momentum_shift'] <= -10
        if pnl_pct > 15 and momentum_reversed:
            return self._make_final("reduce", 0.5, symbol, verdict['action'],
                                    None, None, f"硬性规则：浮盈{pnl_pct:.1f}%+动量反转，锁定利润")

        # 规则5: 剩余R:R < 0.3 → close
        if verdict['factors']['rr_bonus'] == -15:
            return self._make_final("close", 1.0, symbol, verdict['action'],
                                    None, None, "硬性规则：剩余R:R<0.3，空间不足")

        return None

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
        """核心裁决矩阵"""
        a_action = analyst['action']
        a_conviction = analyst['conviction']
        bias = critic.get('bias_detected')
        severity = critic.get('severity', 'none')
        counter = critic.get('counter_recommendation')
        symbol = analyst['symbol']
        pnl_pct = analyst['context']['pnl_pct']

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
            return self._make_final("close", 1.0, symbol, a_action, bias, severity,
                                    f"高度{bias}偏差，采纳批判官平仓建议")
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

    def _build_reasoning(self, action: str, score: float, trend: float, momentum: float, pnl: float) -> str:
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
        return f"score={score:.0f}, " + ", ".join(parts) if parts else f"score={score:.0f}, 信号中性"
