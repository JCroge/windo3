"""多标的裁判决策 Agent - 精确交易计划输出

消费 TechAnalyst 的9维度信号，输出精确交易计划：
入场区间、多级止盈、止损位、杠杆倍数、仓位大小
"""

import os
import time
import ccxt
from agents.base import BaseAgent
from dotenv import load_dotenv

load_dotenv()


JUDGE_PROMPT = """你是加密货币合约交易的最终裁判。基于技术分析师提供的多维度市场信号，做出交易决策。

决策原则：
1. 多维度共振才开仓——至少3个维度方向一致
2. 风控优先：止损必须在关键支撑/阻力位外侧
3. 反人性：散户极度贪婪时谨慎，极度恐惧时寻找机会
4. 杠杆风险高时降低仓位和杠杆
5. 没有明确信号时果断hold——不交易也是决策

【关键禁令——违反即亏损】
- RSI < 30 时禁止做空：这是超卖区域，反弹概率极高。即使趋势看空，也不追空
- RSI > 70 时禁止做多：这是超买区域，回调概率极高。即使趋势看多，也不追多
- RSI < 25 + bullish divergence = 强烈做多信号，不是做空信号
- RSI > 75 + bearish divergence = 强烈做空信号，不是做多信号
- 趋势强度 > 90 往往是趋势末期，此时顺势开仓风险极高
- 散户反指在RSI极端区域失效：超卖时散户做多可能是正确的抄底

以JSON格式回复：
{
    "action": "open_long/open_short/close/hold",
    "confidence": 0-100,
    "reasoning": "决策理由（中文，2-3句话）",
    "key_factors": ["因素1", "因素2", "因素3"],
    "risk_warnings": ["风险1"]
}"""


class MultiJudge(BaseAgent):
    name = "judge"
    subscriptions = ["tech_analysis:*", "symbol_update", "execution_result:*", "news_snapshot"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._symbol_state = {}
        self._decision_cooldown = 55
        self._force_close_cooldown = 300  # 强平后5分钟禁止同标的开仓
        self._max_trade_amount = config.get('max_trade_amount', 10) if config else 10
        self.exchange = None
        self._available_balance = 0.0
        self._news_snapshot = {}  # {base: [headlines]} 最新新闻快照

    def _get_state(self, symbol: str) -> dict:
        if symbol not in self._symbol_state:
            self._symbol_state[symbol] = {
                "last_decision_time": 0,
                "last_tech": None,
                "last_force_close_time": 0,
                "trend_streak": 0,
                "trend_streak_dir": None,
            }
        return self._symbol_state[symbol]

    async def setup(self):
        self.init_llm()
        exchange_id = self.config.get('exchange', 'okx')
        ex_config = {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}
        if exchange_id == 'okx':
            ex_config['apiKey'] = os.getenv('OKX_API_KEY')
            ex_config['secret'] = os.getenv('OKX_SECRET')
            ex_config['password'] = os.getenv('OKX_PASSWORD')
            self.exchange = ccxt.okx(ex_config)
        else:
            ex_config['apiKey'] = os.getenv('BINANCE_API_KEY')
            ex_config['secret'] = os.getenv('BINANCE_SECRET')
            self.exchange = ccxt.binance(ex_config)
        self.logger.info("精确决策裁判Agent就绪")

    async def on_message(self, msg: dict):
        if msg['type'] == 'symbol_update':
            for s in msg['payload'].get('removed', []):
                self._symbol_state.pop(s, None)
            return

        if msg['type'] == 'news_snapshot':
            self._news_snapshot = msg['payload'].get('symbol_news', {})
            return

        if msg['type'] == 'execution_result':
            if msg['payload'].get('status') == 'force_closed':
                symbol = msg.get('symbol') or msg['payload'].get('symbol')
                if symbol:
                    state = self._get_state(symbol)
                    state["last_force_close_time"] = time.time()
                    self.logger.warning(f"[Judge] {symbol} 强平冷却启动，{self._force_close_cooldown}s内禁止开仓")
            return

        if msg['type'] != 'tech_analysis':
            return

        symbol = msg.get('symbol') or msg['payload'].get('symbol')
        if not symbol:
            return

        state = self._get_state(symbol)
        now = time.time()
        if now - state["last_decision_time"] < self._decision_cooldown:
            return

        if now - state["last_force_close_time"] < self._force_close_cooldown:
            remaining = int(self._force_close_cooldown - (now - state["last_force_close_time"]))
            self.logger.info(f"[Judge] {symbol} 强平冷却中，剩余{remaining}s")
            return

        state["last_tech"] = msg['payload']
        state["last_decision_time"] = now

        await self._make_decision(symbol, msg['payload'])

    async def _make_decision(self, symbol: str, tech: dict):
        await self._update_balance()

        score = self._compute_score(tech)
        self.logger.info(f"[Judge] {symbol} 原始score={score:.1f} RSI={tech.get('momentum',{}).get('rsi',0):.0f}")
        price = tech.get('indicators', {}).get('price', 0)

        # 趋势持续性加分：连续多轮同方向强趋势时累加
        trend = tech.get('trend', {})
        state = self._get_state(symbol)
        cur_dir = trend.get('direction', 'neutral')
        cur_strength = trend.get('strength', 50)
        if cur_dir in ('bullish', 'bearish') and cur_strength >= 75:
            if cur_dir == state.get('trend_streak_dir'):
                state['trend_streak'] += 1
            else:
                state['trend_streak'] = 1
                state['trend_streak_dir'] = cur_dir
        else:
            state['trend_streak'] = 0
            state['trend_streak_dir'] = None

        if state['trend_streak'] >= 5:
            streak_bonus = 10
            if cur_dir == 'bearish':
                score -= streak_bonus
            else:
                score += streak_bonus
            self.logger.info(f"[Judge] {symbol} 趋势持续{state['trend_streak']}轮({cur_dir}/{cur_strength})，加分±{streak_bonus}")

        # 日线阻力区反欺骗：价格接近日线高点时禁止追多（假突破陷阱）
        if score > 0 and trend.get('daily_near_resistance'):
            score *= 0.3
            self.logger.info(f"[Judge] {symbol} 接近日线阻力区，做多信号衰减70%（防假突破）")
        # 日线支撑区：价格接近日线低点时禁止追空（反弹陷阱）
        if score < 0 and trend.get('daily_near_support'):
            score *= 0.3
            self.logger.info(f"[Judge] {symbol} 接近日线支撑区，做空信号衰减70%（防反弹陷阱）")

        # price-in衰减：催化剂已被价格消化，信号可靠性下降
        action_hint = "open_long" if score > 0 else "open_short"
        if abs(score) >= 30 and self._check_price_in(symbol, action_hint, tech):
            score *= 0.5

        if abs(score) < 25:
            hold_reason = self._hold_reason(tech, score)
            decision = {
                "symbol": symbol, "timestamp": time.time(),
                "action": "hold", "confidence": 50 - abs(score),
                "plan": None, "size_pct": 0,
                "reasoning": hold_reason,
                "key_factors": [], "risk_warnings": [],
            }
        else:
            action = "open_long" if score > 0 else "open_short"
            confidence = min(95, abs(score))
            plan = self._build_plan(tech, action, price, confidence)

            llm_result = await self._ask_llm(symbol, tech, score)

            # LLM作为修正因子，不作为否决权
            # rule_signal触发时（score含±35基础分），LLM只能降低仓位，不能阻止入场
            rule = tech.get('rule_signal', {})
            has_rule_signal = rule.get('entry_long') or rule.get('entry_short') or \
                             rule.get('ma_aligned_long') or rule.get('ma_aligned_short')

            if llm_result.get('action') == 'hold' and not has_rule_signal and confidence < 55:
                decision = {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": "hold", "confidence": llm_result.get('confidence', 40),
                    "plan": None, "size_pct": 0,
                    "reasoning": llm_result.get('reasoning', ''),
                    "key_factors": llm_result.get('key_factors', []),
                    "risk_warnings": llm_result.get('risk_warnings', []),
                }
            else:
                final_action = action  # rule_signal有时，锁定方向
                if not has_rule_signal:
                    final_action = llm_result.get('action', action)
                    if final_action not in ('open_long', 'open_short', 'close', 'hold'):
                        final_action = action

                if final_action != action and final_action in ('open_long', 'open_short'):
                    plan = self._build_plan(tech, final_action, price, confidence)

                final_conf = llm_result.get('confidence', confidence)

                # LLM同意开仓方向：confidence至少65（已过score门槛+LLM方向确认）
                llm_action = llm_result.get('action', 'hold')
                if llm_action in ('open_long', 'open_short') and final_conf < 65:
                    self.logger.info(f"[Judge] {symbol} LLM同意{llm_action}但confidence={final_conf}偏低，提升至65")
                    final_conf = 65

                # LLM反对但rule_signal触发：降低仓位30%而非阻止入场
                if has_rule_signal and llm_action == 'hold':
                    final_conf = max(40, int(confidence * 0.7))
                    self.logger.info(f"[Judge] {symbol} rule_signal触发但LLM观望，仓位衰减30%")

                if final_action in ('open_long', 'open_short'):
                    # R:R门槛：基于期望值 E = win_rate * rr - (1 - win_rate) > 0
                    # win_rate由confidence代理（confidence/100），要求正期望且rr不低于0.3
                    rr = plan.get('risk_reward_ratio', 0)
                    win_rate = final_conf / 100.0
                    expected_value = win_rate * rr - (1 - win_rate)
                    min_rr = max(0.2, (1 - win_rate) / win_rate)  # 保本线：rr >= (1-w)/w，0.2为手续费保底
                    if rr < min_rr or expected_value < 0:
                        self.logger.info(f"[Judge] {symbol} R:R={rr:.2f} 期望值={expected_value:.3f} 不足(需rr>={min_rr:.2f})，放弃")
                        decision = {
                            "symbol": symbol, "timestamp": time.time(),
                            "action": "hold", "confidence": 0,
                            "plan": None, "size_pct": 0,
                            "reasoning": f"R:R={rr:.2f}期望值={expected_value:.3f}<0，赔率不满足入场条件",
                            "key_factors": [], "risk_warnings": [f"R:R={rr:.2f}"],
                        }
                        await self.publish("trade_decision", decision, symbol=symbol)
                        return

                    plan['size_usdt'] = self._calc_size(final_conf)
                    required_margin = plan['size_usdt'] / plan['leverage']
                    if self._available_balance < required_margin * 1.1:
                        adjusted_size = self._available_balance * 0.9 * plan['leverage']
                        if adjusted_size < 1.0:
                            self.logger.warning(f"[{symbol}] 余额不足({self._available_balance:.2f} USDT)，放弃交易")
                            decision = {
                                "symbol": symbol, "timestamp": time.time(),
                                "action": "hold", "confidence": 0,
                                "plan": None, "size_pct": 0,
                                "reasoning": f"余额不足，需要{required_margin:.2f} USDT保证金",
                                "key_factors": [], "risk_warnings": ["余额不足"],
                            }
                            await self.publish("trade_decision", decision, symbol=symbol)
                            return
                        plan['size_usdt'] = round(adjusted_size, 2)
                        self.logger.info(f"[{symbol}] 调整仓位: {plan['size_usdt']} USDT (余额{self._available_balance:.2f})")

                    decision = {
                        "symbol": symbol, "timestamp": time.time(),
                        "action": final_action,
                        "confidence": final_conf,
                        "plan": plan,
                        "size_pct": plan['size_usdt'] / self._max_trade_amount,
                        "reasoning": llm_result.get('reasoning', ''),
                        "key_factors": llm_result.get('key_factors', []),
                        "risk_warnings": llm_result.get('risk_warnings', []),
                    }
                else:
                    decision = {
                        "symbol": symbol, "timestamp": time.time(),
                        "action": final_action,
                        "confidence": final_conf,
                        "plan": None,
                        "size_pct": 0,
                        "reasoning": llm_result.get('reasoning', ''),
                        "key_factors": llm_result.get('key_factors', []),
                        "risk_warnings": llm_result.get('risk_warnings', []),
                    }

        await self.publish("trade_decision", decision, symbol=symbol)
        self.logger.info(
            f"[决策] {symbol} {decision['action']} "
            f"置信度={decision['confidence']} "
            f"{'plan='+str(decision['plan']['leverage'])+'x' if decision.get('plan') else ''} "
            f"理由: {decision.get('reasoning', '')[:60]}"
        )

    # ═══ 信号聚合评分 ═══

    def _hold_reason(self, tech: dict, score: float) -> str:
        trend = tech.get('trend', {})
        direction = trend.get('direction', 'neutral')
        strength = trend.get('strength', 50)
        rsi = tech.get('momentum', {}).get('rsi', 50)
        if direction == 'bearish' and strength > 70 and rsi < 35:
            return f"趋势bearish/{strength}但RSI={rsi:.0f}超卖，追空风险高，观望"
        if direction == 'bullish' and strength > 70 and rsi > 65:
            return f"趋势bullish/{strength}但RSI={rsi:.0f}超买，追多风险高，观望"
        if direction == 'neutral':
            return "趋势中性，无明确方向，观望"
        if abs(score) < 10:
            return "多空信号对冲，净得分接近零，观望"
        return "信号强度不足，观望"

    def _compute_score(self, tech: dict) -> float:
        """多空评分: +100=极度看多, -100=极度看空, 0=中性

        架构：rule_signal（回测验证83%胜率）为主驱动，其他维度为辅助加减分。
        rule_signal触发时基础分±35，确保能过30分入场门槛。
        """
        score = 0.0

        # ═══ 0. 回测验证信号（主驱动）═══
        rule = tech.get('rule_signal', {})
        if rule.get('entry_long'):
            score += 35
        elif rule.get('entry_short'):
            score -= 35
        # MA alignment持续信号（次驱动）：趋势已建立≥3根K线，无crossover时提供基础分
        elif rule.get('ma_aligned_long'):
            score += 20
        elif rule.get('ma_aligned_short'):
            score -= 20

        momentum = tech.get('momentum', {})
        rsi = momentum.get('rsi', 50)
        div = momentum.get('rsi_divergence')

        # ═══ 极端值保护：RSI超买超卖区域的硬性约束 ═══
        # RSI < 25: 极度超卖，禁止做空，给予强烈看多偏置
        # RSI > 75: 极度超买，禁止做多，给予强烈看空偏置
        rsi_extreme_bullish = rsi < 25
        rsi_extreme_bearish = rsi > 75
        rsi_oversold = rsi < 35
        rsi_overbought = rsi > 65

        # ═══ 1. 趋势 ═══
        trend = tech.get('trend', {})
        direction = trend.get('direction', 'neutral')
        strength = trend.get('strength', 50)

        # 趋势强度衰减：强度>90时打折（趋势末期信号）
        effective_strength = strength
        if strength > 90:
            effective_strength = 90 - (strength - 90) * 2  # 98→74, 95→80

        if direction == 'bullish' and effective_strength > 70:
            trend_score = 20 * (effective_strength / 100)
            # 超买区域趋势做多打折
            if rsi_overbought:
                trend_score *= 0.3
            score += trend_score
        elif direction == 'bearish' and effective_strength > 70:
            trend_score = 20 * (effective_strength / 100)
            # 超卖区域趋势做空打折
            if rsi_oversold:
                trend_score *= 0.3
            score -= trend_score

        # ═══ 2. RSI背离（权重提升：背离是反转的强信号）═══
        if div == 'bullish_div':
            div_score = 20
            if rsi_extreme_bullish:
                div_score = 35  # 极度超卖+背离=强烈反转信号
            elif rsi_oversold:
                div_score = 28
            score += div_score
        elif div == 'bearish_div':
            div_score = 20
            if rsi_extreme_bearish:
                div_score = 35
            elif rsi_overbought:
                div_score = 28
            score -= div_score

        # ═══ 3. OI背离 ═══
        mf = tech.get('money_flow', {})
        oi_div = mf.get('oi_price_divergence')
        if oi_div == 'bullish':
            score += 12
        elif oi_div == 'bearish':
            score -= 12

        # ═══ 4. 鲸鱼方向 ═══
        micro = tech.get('microstructure', {})
        whale = micro.get('whale_direction', 'neutral')
        if whale == 'accumulating':
            score += 15
        elif whale == 'distributing':
            score -= 15

        # ═══ 5. 散户反指（条件化：只在趋势中段有效）═══
        crowd = tech.get('crowd', {})
        contrarian = crowd.get('contrarian_signal', 'neutral')
        # 散户反指在RSI极端区域失效：
        # 超卖时散户做多可能是正确的抄底，不应反指做空
        # 超买时散户做空可能是正确的逃顶，不应反指做多
        if contrarian == 'bullish' and not rsi_extreme_bearish:
            score += 8
        elif contrarian == 'bearish' and not rsi_extreme_bullish:
            score -= 8

        # ═══ 6. Taker压力 ═══
        taker = mf.get('taker_pressure', 'neutral')
        if taker == 'buy':
            score += 8
        elif taker == 'sell':
            score -= 8

        # ═══ 7. 高时间框架偏向 ═══
        htf = trend.get('higher_tf_bias', 'neutral')
        if htf == 'bullish':
            score += 10
        elif htf == 'bearish':
            score -= 10

        # ═══ 极端值硬性保护 ═══
        # RSI极度超卖时，无论其他信号如何，不允许做空（score不能低于-15）
        if rsi_extreme_bullish:
            if score < -15:
                score = -15
            # 如果有背离，直接给正分
            if div == 'bullish_div':
                score = max(score, 25)

        # RSI极度超买时，无论其他信号如何，不允许做多
        if rsi_extreme_bearish:
            if score > 15:
                score = 15
            if div == 'bearish_div':
                score = min(score, -25)

        return score

    def _check_price_in(self, symbol: str, action: str, tech: dict) -> bool:
        """检测催化剂是否已price-in：新闻发布后4h内价格已同向移动>3%则认为已消化

        设计参考：QuantConnect Alpha Streams研究——新闻情绪在4h后衰减至噪音
        price-in判断：新闻方向 × 价格变动方向一致 且 变动幅度>3%
        """
        base = symbol.split('-')[0].upper()
        headlines = self._news_snapshot.get(base, [])
        if not headlines:
            return False

        now = time.time()
        window = 4 * 3600
        recent = [h for h in headlines if h.get('published_ts') and now - h['published_ts'] <= window]
        if not recent:
            return False

        # 用24h涨跌幅作为价格移动代理（tech已包含此数据）
        change_pct = abs(tech.get('trend', {}).get('change_24h_pct', 0))
        if change_pct < 3.0:
            return False

        # 方向一致性检查：新闻利好+价格已大涨 或 新闻利空+价格已大跌
        price_direction = 'up' if tech.get('trend', {}).get('direction') == 'bullish' else 'down'
        # 简单启发：有近期新闻 + 价格已大幅移动 = 催化剂已price-in
        self.logger.info(
            f"[Judge] {symbol} 检测到price-in: 近{len(recent)}条新闻 + 24h变动{change_pct:.1f}%，score衰减50%"
        )
        return True

    # ═══ 交易计划构建 ═══

    def _build_plan(self, tech: dict, action: str, price: float, confidence: int) -> dict:
        levels = tech.get('levels', {})
        risk = tech.get('risk', {})
        micro = tech.get('microstructure', {})
        momentum = tech.get('momentum', {})
        trend = tech.get('trend', {})

        is_long = (action == 'open_long')

        stop_loss = self._calc_stop_loss(levels, price, is_long, trend)
        take_profit = self._calc_take_profit(levels, price, is_long, trend, momentum, stop_loss)
        leverage = self._calc_leverage(risk)
        entry_zone = self._calc_entry_zone(price, micro, momentum)
        order_type = self._calc_order_type(momentum, micro)
        size_usdt = self._calc_size(confidence)

        sl_dist = abs(price - stop_loss) / price
        tp_dist = abs(take_profit[0] - price) / price if take_profit else sl_dist
        rr_ratio = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 1.0
        self.logger.info(f"[Plan] price={price:.4f} sl={stop_loss:.4f}({sl_dist:.3f}) tp={take_profit[0]:.4f}({tp_dist:.3f}) atr={momentum.get('atr_pct',0):.4f} R:R={rr_ratio}")

        def price_round(x):
            # 动态精度：保留4位有效数字，避免低价币被截断
            from math import log10, floor
            if x <= 0:
                return x
            digits = max(2, 4 - int(floor(log10(abs(x)))) - 1)
            return round(x, digits)

        return {
            "entry_zone": [price_round(e) for e in entry_zone],
            "stop_loss": price_round(stop_loss),
            "take_profit": [price_round(tp) for tp in take_profit],
            "leverage": leverage,
            "size_usdt": size_usdt,
            "order_type": order_type,
            "risk_reward_ratio": rr_ratio,
            "max_holding_hours": 24,
        }

    def _calc_stop_loss(self, levels: dict, price: float, is_long: bool, trend: dict = None) -> float:
        min_sl_pct = 0.015
        max_sl_pct = 0.10
        strength = (trend or {}).get('strength', 50)
        if strength >= 80:
            min_sl_pct = 0.025
        elif strength >= 60:
            min_sl_pct = 0.02

        if is_long:
            for key in ['daily_support', 'h4_support', 'support']:
                candidates = [s for s in levels.get(key, []) if price * (1 - max_sl_pct) < s < price * (1 - min_sl_pct)]
                if candidates:
                    return candidates[0] * 0.995
            return price * (1 - min_sl_pct)
        else:
            for key in ['daily_resistance', 'h4_resistance', 'resistance']:
                candidates = [r for r in levels.get(key, []) if price * (1 + min_sl_pct) < r < price * (1 + max_sl_pct)]
                if candidates:
                    return candidates[0] * 1.005
            return price * (1 + min_sl_pct)

    def _calc_take_profit(self, levels: dict, price: float, is_long: bool,
                           trend: dict = None, momentum: dict = None, stop_loss: float = None) -> list:
        trend = trend or {}
        momentum = momentum or {}
        strength = trend.get('strength', 50)
        direction = trend.get('direction', 'neutral')
        atr_pct = max(momentum.get('atr_pct', 0.02), 0.01)

        # 强趋势模式：趋势强度>80且方向与开仓一致时，用ATR倍数止盈
        strong_trend = (strength >= 80 and
                        ((direction == 'bearish' and not is_long) or
                         (direction == 'bullish' and is_long)))

        rsi = momentum.get('rsi', 50)

        # 止盈下限：不能小于止损距离的60%（保证R:R >= 0.6）
        sl_dist = abs(price - stop_loss) / price if stop_loss else 0.015
        min_tp_pct = sl_dist * 0.6

        if is_long:
            if strong_trend or rsi < 20:
                tps = [price * (1 + atr_pct * m) for m in [1.5, 2.5, 3.5]]
                if abs(tps[0] - price) / price < min_tp_pct:
                    tps = [price * (1 + min_tp_pct * m) for m in [1.0, 2.0, 3.0]]
                return tps
            resistances = levels.get('resistance', [])
            wall = levels.get('orderbook_wall_above')
            min_tp_dist = price * 0.005
            tps = []
            for r in resistances[:3]:
                if r <= price + min_tp_dist:
                    continue
                if wall and r >= wall:
                    tp = wall * 0.998
                    if tp > price + min_tp_dist:
                        tps.append(tp)
                    break
                tps.append(r)
            if not tps:
                tps = [price * 1.02, price * 1.04, price * 1.06]
            # ATR保底：止盈距离不能低于1×ATR
            if tps and atr_pct > 0 and abs(tps[0] - price) / price < atr_pct:
                tps = [price * (1 + atr_pct * m) for m in [1.0, 2.0, 3.0]]
            # 止损关联保底：TP1不能小于SL距离的60%
            if tps and abs(tps[0] - price) / price < min_tp_pct:
                tps = [price * (1 + min_tp_pct * m) for m in [1.0, 2.0, 3.0]]
            return tps
        else:
            if strong_trend or rsi > 80:
                tps = [price * (1 - atr_pct * m) for m in [1.5, 2.5, 3.5]]
                if abs(tps[0] - price) / price < min_tp_pct:
                    tps = [price * (1 - min_tp_pct * m) for m in [1.0, 2.0, 3.0]]
                return tps
            supports = levels.get('support', [])
            wall = levels.get('orderbook_wall_below')
            min_tp_dist = price * 0.005
            tps = []
            for s in supports[:3]:
                if s >= price - min_tp_dist:
                    continue
                if wall and s <= wall:
                    tp = wall * 1.002
                    if tp < price - min_tp_dist:
                        tps.append(tp)
                    break
                tps.append(s)
            if not tps:
                tps = [price * 0.98, price * 0.96, price * 0.94]
            # ATR保底
            if tps and atr_pct > 0 and abs(tps[0] - price) / price < atr_pct:
                tps = [price * (1 - atr_pct * m) for m in [1.0, 2.0, 3.0]]
            # 止损关联保底
            if tps and abs(tps[0] - price) / price < min_tp_pct:
                tps = [price * (1 - min_tp_pct * m) for m in [1.0, 2.0, 3.0]]
            return tps

    def _calc_leverage(self, risk: dict) -> int:
        """动态杠杆 1-20x，基于风险等级+波动率+流动性综合判断"""
        lev_risk = risk.get('leverage_risk', 'low')
        vol = risk.get('volatility_regime', 'normal')
        liquidity = risk.get('liquidity_score', 80)

        # 基础杠杆：风险越低越敢加
        if lev_risk == 'high':
            base = 3
        elif lev_risk == 'medium':
            base = 7
        else:
            base = 12

        # 波动率调节
        vol_mult = {"low": 1.5, "normal": 1.0, "high": 0.6, "extreme": 0.3}
        base = int(base * vol_mult.get(vol, 1.0))

        # 流动性调节：流动性差时砍杠杆
        if liquidity < 30:
            base = int(base * 0.5)
        elif liquidity < 60:
            base = int(base * 0.75)

        leverage = max(1, min(20, base))

        # 圆整到OKX允许的杠杆倍数：1,2,3,5,10,20
        allowed = [1, 2, 3, 5, 10, 20]
        for lev in allowed:
            if leverage <= lev:
                return lev
        return 20

    def _calc_entry_zone(self, price: float, micro: dict, momentum: dict) -> list:
        spread = micro.get('spread_pct', 0.01)
        margin = max(spread * 2, 0.02) / 100 * price
        return [price - margin, price + margin]

    def _calc_order_type(self, momentum: dict, micro: dict) -> str:
        if momentum.get('volume_anomaly') or micro.get('liquidation_intensity') == 'high':
            return "market"
        return "limit"

    def _calc_size(self, confidence: int) -> float:
        if confidence >= 80:
            factor = 1.0
        elif confidence >= 70:
            factor = 0.7
        elif confidence >= 60:
            factor = 0.5
        else:
            factor = 0.3
        return round(self._max_trade_amount * factor, 2)

    # ═══ LLM决策 ═══

    async def _ask_llm(self, symbol: str, tech: dict, rule_score: float) -> dict:
        trend = tech.get('trend', {})
        levels = tech.get('levels', {})
        momentum = tech.get('momentum', {})
        mf = tech.get('money_flow', {})
        micro = tech.get('microstructure', {})
        crowd = tech.get('crowd', {})
        risk = tech.get('risk', {})
        llm_ta = tech.get('llm_analysis', {})
        price = tech.get('indicators', {}).get('price', 0)

        user_msg = f"""交易对: {symbol} | 当前价: {price:.2f} | 规则评分: {rule_score:+.0f}/100

【趋势】{trend.get('direction')} 强度={trend.get('strength')} 1h均线={trend.get('ma_alignment')} 4h={trend.get('higher_tf_bias')}
【价位】支撑={levels.get('support',[])} 阻力={levels.get('resistance',[])}
【动量】RSI={momentum.get('rsi',50):.1f} 背离={momentum.get('rsi_divergence')} 量比={momentum.get('volume_ratio',1)}
【资金】费率={mf.get('funding_rate',0):.6f}({mf.get('funding_trend')}) OI 1h={mf.get('oi_delta_1h_pct',0):+.1f}% OI背离={mf.get('oi_price_divergence')}
【微观】鲸鱼={micro.get('whale_direction')} 大单比={micro.get('big_trade_ratio',1):.1f} 爆仓={micro.get('liquidation_pressure')}({micro.get('liquidation_intensity')})
【散户】多头={crowd.get('long_ratio',0.5):.0%} 反指={crowd.get('contrarian_signal')}
【风险】杠杆风险={risk.get('leverage_risk')} 波动={risk.get('volatility_regime')} 流动性={risk.get('liquidity_score')}
【技术分析师研判】{llm_ta.get('key_insight','')}

最大交易额: {self._max_trade_amount} USDT。请做出最终决策。"""

        try:
            result = await self.ask_claude_json(JUDGE_PROMPT, user_msg)
            if 'action' not in result:
                result['action'] = 'hold'
            if 'confidence' not in result:
                result['confidence'] = 50
            return result
        except Exception as e:
            self.logger.warning(f"LLM决策失败({symbol})，规则降级: {e}")
            return self._rule_fallback(tech, rule_score)

    def _rule_fallback(self, tech: dict, score: float) -> dict:
        if score > 25:
            action = "open_long"
            reasoning = "规则引擎：多维度看多共振"
        elif score < -25:
            action = "open_short"
            reasoning = "规则引擎：多维度看空共振"
        else:
            action = "hold"
            reasoning = "规则引擎：信号不足"

        return {
            "action": action,
            "confidence": min(90, int(abs(score) + 20)),
            "reasoning": reasoning,
            "key_factors": [f"综合评分={score:+.0f}"],
            "risk_warnings": ["LLM不可用，仅规则判断"],
        }

    async def _update_balance(self):
        """查询可用USDT余额"""
        try:
            import asyncio
            balance = await asyncio.to_thread(self.exchange.fetch_balance)
            self._available_balance = float(balance.get('USDT', {}).get('free', 0))
            self.logger.info(f"余额查询成功: {self._available_balance:.2f} USDT")
        except Exception as e:
            self.logger.error(f"查询余额失败: {e}")
            self._available_balance = 0.0
