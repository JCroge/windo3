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
    subscriptions = ["tech_analysis:*", "symbol_update"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._symbol_state = {}
        self._decision_cooldown = 55
        self._max_trade_amount = config.get('max_trade_amount', 10) if config else 10
        self.exchange = None
        self._available_balance = 0.0

    def _get_state(self, symbol: str) -> dict:
        if symbol not in self._symbol_state:
            self._symbol_state[symbol] = {
                "last_decision_time": 0,
                "last_tech": None,
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

        if msg['type'] != 'tech_analysis':
            return

        symbol = msg.get('symbol') or msg['payload'].get('symbol')
        if not symbol:
            return

        state = self._get_state(symbol)
        now = time.time()
        if now - state["last_decision_time"] < self._decision_cooldown:
            return

        state["last_tech"] = msg['payload']
        state["last_decision_time"] = now

        await self._make_decision(symbol, msg['payload'])

    async def _make_decision(self, symbol: str, tech: dict):
        await self._update_balance()

        score = self._compute_score(tech)
        price = tech.get('indicators', {}).get('price', 0)

        if abs(score) < 30:
            decision = {
                "symbol": symbol, "timestamp": time.time(),
                "action": "hold", "confidence": 50 - abs(score),
                "plan": None, "size_pct": 0,
                "reasoning": "信号分歧或强度不足，观望",
                "key_factors": [], "risk_warnings": [],
            }
        else:
            action = "open_long" if score > 0 else "open_short"
            confidence = min(95, abs(score))
            plan = self._build_plan(tech, action, price, confidence)

            llm_result = await self._ask_llm(symbol, tech, score)

            if llm_result.get('action') == 'hold' and confidence < 75:
                decision = {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": "hold", "confidence": llm_result.get('confidence', 40),
                    "plan": None, "size_pct": 0,
                    "reasoning": llm_result.get('reasoning', ''),
                    "key_factors": llm_result.get('key_factors', []),
                    "risk_warnings": llm_result.get('risk_warnings', []),
                }
            else:
                final_action = llm_result.get('action', action)
                if final_action not in ('open_long', 'open_short', 'close', 'hold'):
                    final_action = action

                if final_action != action and final_action in ('open_long', 'open_short'):
                    plan = self._build_plan(tech, final_action, price, confidence)

                final_conf = llm_result.get('confidence', confidence)

                if final_action in ('open_long', 'open_short'):
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

    def _compute_score(self, tech: dict) -> float:
        """多空评分: +100=极度看多, -100=极度看空, 0=中性"""
        score = 0.0

        trend = tech.get('trend', {})
        direction = trend.get('direction', 'neutral')
        strength = trend.get('strength', 50)
        if direction == 'bullish' and strength > 60:
            score += 25 * (strength / 100)
        elif direction == 'bearish' and strength > 60:
            score -= 25 * (strength / 100)

        momentum = tech.get('momentum', {})
        div = momentum.get('rsi_divergence')
        if div == 'bullish_div':
            score += 15
        elif div == 'bearish_div':
            score -= 15

        mf = tech.get('money_flow', {})
        oi_div = mf.get('oi_price_divergence')
        if oi_div == 'bullish':
            score += 15
        elif oi_div == 'bearish':
            score -= 15

        micro = tech.get('microstructure', {})
        whale = micro.get('whale_direction', 'neutral')
        if whale == 'accumulating':
            score += 15
        elif whale == 'distributing':
            score -= 15

        crowd = tech.get('crowd', {})
        contrarian = crowd.get('contrarian_signal', 'neutral')
        if contrarian == 'bullish':
            score += 10
        elif contrarian == 'bearish':
            score -= 10

        taker = mf.get('taker_pressure', 'neutral')
        if taker == 'buy':
            score += 10
        elif taker == 'sell':
            score -= 10

        htf = trend.get('higher_tf_bias', 'neutral')
        if htf == 'bullish':
            score += 10
        elif htf == 'bearish':
            score -= 10

        return score

    # ═══ 交易计划构建 ═══

    def _build_plan(self, tech: dict, action: str, price: float, confidence: int) -> dict:
        levels = tech.get('levels', {})
        risk = tech.get('risk', {})
        micro = tech.get('microstructure', {})
        momentum = tech.get('momentum', {})

        is_long = (action == 'open_long')

        stop_loss = self._calc_stop_loss(levels, price, is_long)
        take_profit = self._calc_take_profit(levels, price, is_long)
        leverage = self._calc_leverage(risk)
        entry_zone = self._calc_entry_zone(price, micro, momentum)
        order_type = self._calc_order_type(momentum, micro)
        size_usdt = self._calc_size(confidence)

        sl_dist = abs(price - stop_loss) / price
        tp_dist = abs(take_profit[0] - price) / price if take_profit else sl_dist
        rr_ratio = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 1.0

        return {
            "entry_zone": entry_zone,
            "stop_loss": round(stop_loss, 2),
            "take_profit": [round(tp, 2) for tp in take_profit],
            "leverage": leverage,
            "size_usdt": size_usdt,
            "order_type": order_type,
            "risk_reward_ratio": rr_ratio,
            "max_holding_hours": 24,
        }

    def _calc_stop_loss(self, levels: dict, price: float, is_long: bool) -> float:
        if is_long:
            supports = [s for s in levels.get('support', []) if s < price]
            if supports:
                return supports[0] * 0.995
            return price * 0.97
        else:
            resistances = [r for r in levels.get('resistance', []) if r > price]
            if resistances:
                return resistances[0] * 1.005
            return price * 1.03

    def _calc_take_profit(self, levels: dict, price: float, is_long: bool) -> list:
        if is_long:
            resistances = levels.get('resistance', [])
            wall = levels.get('orderbook_wall_above')
            tps = []
            for r in resistances[:3]:
                if r <= price:  # 多单止盈必须高于入场价
                    continue
                if wall and r > wall:
                    tps.append(wall * 0.998)
                    break
                tps.append(r)
            if not tps:
                tps = [price * 1.02, price * 1.04, price * 1.06]
            return tps
        else:
            supports = levels.get('support', [])
            wall = levels.get('orderbook_wall_below')
            tps = []
            for s in supports[:3]:
                if s >= price:  # 空单止盈必须低于入场价
                    continue
                if wall and s < wall:
                    tps.append(wall * 1.002)
                    break
                tps.append(s)
            if not tps:
                tps = [price * 0.98, price * 0.96, price * 0.94]
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

        leverage = max(1, min(10, base))  # 小账户(<50 USDT)上限10x

        # 圆整到OKX允许的杠杆倍数：1,2,3,5,10
        allowed = [1, 2, 3, 5, 10]
        for lev in allowed:
            if leverage <= lev:
                return lev
        return 20

    def _calc_entry_zone(self, price: float, micro: dict, momentum: dict) -> list:
        spread = micro.get('spread_pct', 0.01)
        margin = max(spread * 2, 0.02) / 100 * price
        return [round(price - margin, 2), round(price + margin, 2)]

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
        if score > 30:
            action = "open_long"
            reasoning = "规则引擎：多维度看多共振"
        elif score < -30:
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
