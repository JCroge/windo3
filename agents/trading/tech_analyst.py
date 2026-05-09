"""多标的技术分析 Agent - 9维度信号解读

消费 DataCollector 的全量 market_data，输出结构化多层信号：
趋势结构、关键价位、动量、资金流向、微观结构、散户情绪、风险评估、规则信号、LLM综合研判
"""

import time
import pandas as pd
from agents.base import BaseAgent
from optimize_1h import RobustStrategy


TECH_PROMPT = """你是加密货币衍生品市场的技术分析师。基于以下多维度市场数据，给出综合研判。

你的职责是"看到什么"——识别模式和信号，不做交易建议。

重点关注：
1. 多周期共振与矛盾：1h/4h/日线方向是否一致？矛盾时以大周期为准
2. 跨维度组合信号（如价涨+OI跌+大单卖出=诱多陷阱；日线阻力位附近+1h超买=假突破风险）
3. 反欺骗识别：价格在日线关键阻力区附近的突破往往是诱多；散户极度看多时往往是顶部
4. 背离信号（价格vs指标、价格vsOI、散户vs鲸鱼）
5. 当前最值得关注的1-2个关键事实

以JSON格式回复：
{
    "direction": "bullish/bearish/neutral",
    "confidence": 0-100,
    "pattern": "识别到的形态或组合信号",
    "key_insight": "一句话总结当前最关键的市场事实（中文）",
    "reasoning": "简短推理过程（中文）"
}"""


class MultiTechAnalyst(BaseAgent):
    name = "tech_analyst"
    subscriptions = ["market_data:*", "symbol_update"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._symbol_state = {}

    def _get_strategy(self, symbol: str) -> RobustStrategy:
        if symbol not in self._symbol_state:
            self._symbol_state[symbol] = {
                "strategy": RobustStrategy(
                    ma_fast=7, ma_slow=25,
                    rsi_period=14, rsi_threshold=75,
                    volume_factor=1.0
                )
            }
        return self._symbol_state[symbol]["strategy"]

    async def setup(self):
        self.init_llm()
        self.logger.info("9维度技术分析Agent就绪")

    async def on_message(self, msg: dict):
        if msg['type'] == 'symbol_update':
            removed = msg['payload'].get('removed', [])
            for s in removed:
                self._symbol_state.pop(s, None)
            return

        if msg['type'] != 'market_data':
            return

        symbol = msg.get('symbol') or msg['payload'].get('symbol')
        if not symbol:
            return

        payload = msg['payload']
        klines = payload.get('klines', [])
        if len(klines) < 30:
            return

        df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
        strategy = self._get_strategy(symbol)
        df = strategy.analyze(df)

        trend = self._analyze_trend(df, payload.get('klines_4h', []), payload.get('klines_1d', []))
        levels = self._analyze_levels(df, payload.get('orderbook', {}), payload.get('klines_1d', []))
        momentum = self._analyze_momentum(df)
        money_flow = self._analyze_money_flow(payload)
        microstructure = self._analyze_microstructure(payload)
        crowd = self._analyze_crowd(payload.get('long_short_account', {}))
        risk = self._analyze_risk(payload)

        latest = df.iloc[-2]
        rule_signal = {
            "entry_long": int(latest.get('entry_long', 0)),
            "entry_short": int(latest.get('entry_short', 0)),
            "exit_long": int(latest.get('exit_long', 0)),
            "exit_short": int(latest.get('exit_short', 0)),
        }

        llm_analysis = await self._llm_synthesize(
            symbol, trend, levels, momentum, money_flow,
            microstructure, crowd, risk, float(latest['close'])
        )

        result = {
            "symbol": symbol,
            "timestamp": time.time(),
            "trend": trend,
            "levels": levels,
            "momentum": momentum,
            "money_flow": money_flow,
            "microstructure": microstructure,
            "crowd": crowd,
            "risk": risk,
            "rule_signal": rule_signal,
            "indicators": {
                "price": float(latest['close']),
                "rsi": float(latest.get('rsi', 50)),
                "ma_fast": float(latest.get('ma_fast', 0)),
                "ma_slow": float(latest.get('ma_slow', 0)),
            },
            "llm_analysis": llm_analysis,
        }

        await self.publish("tech_analysis", result, symbol=symbol)
        self.logger.info(
            f"[技术分析] {symbol} 趋势={trend['direction']} "
            f"强度={trend['strength']} 杠杆风险={risk['leverage_risk']} "
            f"LLM={llm_analysis.get('direction','?')}/{llm_analysis.get('confidence',0)}"
        )

    # ═══ 1. 趋势结构 ═══

    def _analyze_trend(self, df: pd.DataFrame, klines_4h: list, klines_1d: list = None) -> dict:
        latest = df.iloc[-2]
        ma_fast = float(latest.get('ma_fast', 0))
        ma_slow = float(latest.get('ma_slow', 0))
        price = float(latest['close'])

        if ma_fast > ma_slow and price > ma_fast:
            ma_alignment = "bullish"
        elif ma_fast < ma_slow and price < ma_fast:
            ma_alignment = "bearish"
        else:
            ma_alignment = "neutral"

        closes = df['close'].tail(20).values
        higher_highs = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
        trend_pct = higher_highs / (len(closes) - 1) if len(closes) > 1 else 0.5

        if trend_pct > 0.6 and ma_alignment == "bullish":
            direction = "bullish"
            strength = int(min(100, trend_pct * 100 + 20))
        elif trend_pct < 0.4 and ma_alignment == "bearish":
            direction = "bearish"
            strength = int(min(100, (1 - trend_pct) * 100 + 20))
        else:
            direction = "neutral"
            strength = int(50 - abs(trend_pct - 0.5) * 60)

        # 4h偏向：MA方向 + RSI状态
        higher_tf_bias = "neutral"
        tf_4h_rsi = None
        if klines_4h and len(klines_4h) >= 10:
            df_4h = pd.DataFrame(klines_4h, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
            ma7_4h = df_4h['close'].tail(7).mean()
            ma25_4h = df_4h['close'].tail(25).mean() if len(df_4h) >= 25 else ma7_4h
            # 4h RSI
            delta = df_4h['close'].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, 1e-9)
            tf_4h_rsi = float(100 - 100 / (1 + rs.iloc[-1])) if not rs.empty else 50
            if ma7_4h > ma25_4h:
                higher_tf_bias = "bullish"
            elif ma7_4h < ma25_4h:
                higher_tf_bias = "bearish"

        # 日线偏向：大趋势方向 + 是否处于日线关键阻力区
        daily_bias = "neutral"
        daily_near_resistance = False
        daily_near_support = False
        if klines_1d and len(klines_1d) >= 10:
            df_1d = pd.DataFrame(klines_1d, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
            ma7_1d = df_1d['close'].tail(7).mean()
            ma25_1d = df_1d['close'].tail(25).mean() if len(df_1d) >= 25 else ma7_1d
            if ma7_1d > ma25_1d:
                daily_bias = "bullish"
            elif ma7_1d < ma25_1d:
                daily_bias = "bearish"
            # 检测价格是否接近日线高点（潜在阻力）或低点（潜在支撑）
            recent_high = df_1d['high'].tail(20).max()
            recent_low = df_1d['low'].tail(20).min()
            if price > recent_high * 0.97:  # 距日线高点3%以内
                daily_near_resistance = True
            if price < recent_low * 1.03:   # 距日线低点3%以内
                daily_near_support = True

        # 多周期共振：1h+4h+日线方向一致时提升强度，矛盾时降低
        tf_votes = [direction, higher_tf_bias, daily_bias]
        bullish_votes = tf_votes.count("bullish")
        bearish_votes = tf_votes.count("bearish")
        if bullish_votes == 3:
            strength = min(100, strength + 20)  # 三周期共振，强度提升
        elif bearish_votes == 3:
            strength = min(100, strength + 20)
        elif bullish_votes == 1 and bearish_votes == 1:
            strength = max(20, strength - 20)   # 周期矛盾，强度下降

        return {
            "direction": direction,
            "strength": strength,
            "ma_alignment": ma_alignment,
            "higher_tf_bias": higher_tf_bias,
            "daily_bias": daily_bias,
            "daily_near_resistance": daily_near_resistance,
            "daily_near_support": daily_near_support,
            "tf_4h_rsi": round(tf_4h_rsi, 1) if tf_4h_rsi else None,
        }

    # ═══ 2. 关键价位 ═══

    def _analyze_levels(self, df: pd.DataFrame, orderbook: dict, klines_1d: list = None) -> dict:
        price = float(df.iloc[-2]['close'])
        highs = df['high'].tail(20).values
        lows = df['low'].tail(20).values

        swing_highs = []
        swing_lows = []
        for i in range(1, len(highs) - 1):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                swing_highs.append(float(highs[i]))
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                swing_lows.append(float(lows[i]))

        resistance = sorted([h for h in swing_highs if h > price])[:3]
        support = sorted([l for l in swing_lows if l < price], reverse=True)[:3]

        # 日线关键价位：比1h swing更可靠的止损止盈锚点
        daily_resistance = []
        daily_support = []
        if klines_1d and len(klines_1d) >= 5:
            df_1d = pd.DataFrame(klines_1d, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
            d_highs = df_1d['high'].tail(20).values
            d_lows = df_1d['low'].tail(20).values
            for i in range(1, len(d_highs) - 1):
                if d_highs[i] > d_highs[i-1] and d_highs[i] > d_highs[i+1]:
                    if d_highs[i] > price:
                        daily_resistance.append(float(d_highs[i]))
                if d_lows[i] < d_lows[i-1] and d_lows[i] < d_lows[i+1]:
                    if d_lows[i] < price:
                        daily_support.append(float(d_lows[i]))
            daily_resistance = sorted(daily_resistance)[:2]
            daily_support = sorted(daily_support, reverse=True)[:2]

        if not resistance:
            resistance = [round(price * 1.02, 2), round(price * 1.04, 2)]
        if not support:
            support = [round(price * 0.98, 2), round(price * 0.96, 2)]

        wall_above = None
        wall_below = None
        if orderbook.get('asks') and orderbook.get('bids'):
            asks = orderbook['asks']
            bids = orderbook['bids']
            if asks:
                max_ask = max(asks, key=lambda x: x[0] * x[1])
                wall_above = max_ask[0]
            if bids:
                max_bid = max(bids, key=lambda x: x[0] * x[1])
                wall_below = max_bid[0]

        return {
            "support": support,
            "resistance": resistance,
            "daily_support": daily_support,
            "daily_resistance": daily_resistance,
            "orderbook_wall_above": wall_above,
            "orderbook_wall_below": wall_below,
        }

    # ═══ 3. 动量信号 ═══

    def _analyze_momentum(self, df: pd.DataFrame) -> dict:
        latest = df.iloc[-2]
        rsi = float(latest.get('rsi', 50))
        volume = float(latest.get('volume', 0))
        volume_ma = float(latest.get('volume_ma', 1))
        volume_ratio = round(volume / volume_ma, 2) if volume_ma > 0 else 1.0

        rsi_div = self._detect_rsi_divergence(df)

        closes = df['close'].tail(5).values
        if len(closes) >= 3:
            recent_momentum = closes[-1] - closes[-3]
            prev_momentum = closes[-3] - closes[-5] if len(closes) >= 5 else 0
            if recent_momentum > 0 and recent_momentum > prev_momentum:
                macd_trend = "rising"
            elif recent_momentum < 0 and recent_momentum < prev_momentum:
                macd_trend = "falling"
            else:
                macd_trend = "flat"
        else:
            macd_trend = "flat"

        return {
            "rsi": rsi,
            "rsi_divergence": rsi_div,
            "macd_histogram_trend": macd_trend,
            "volume_anomaly": volume_ratio > 2.5,
            "volume_ratio": volume_ratio,
        }

    def _detect_rsi_divergence(self, df: pd.DataFrame) -> str:
        if len(df) < 14:
            return None
        recent = df.tail(14)
        prices = recent['close'].values
        rsis = recent['rsi'].values if 'rsi' in recent.columns else None
        if rsis is None:
            return None

        price_low_idx = prices.argmin()
        price_high_idx = prices.argmax()

        if price_low_idx > len(prices) // 2:
            prev_lows = prices[:len(prices)//2]
            if len(prev_lows) > 0 and prices[price_low_idx] < prev_lows.min():
                if rsis[price_low_idx] > rsis[:len(prices)//2].min():
                    return "bullish_div"

        if price_high_idx > len(prices) // 2:
            prev_highs = prices[:len(prices)//2]
            if len(prev_highs) > 0 and prices[price_high_idx] > prev_highs.max():
                if rsis[price_high_idx] < rsis[:len(prices)//2].max():
                    return "bearish_div"

        return None

    # ═══ 4. 资金流向 ═══

    def _analyze_money_flow(self, payload: dict) -> dict:
        funding_rate = payload.get('funding_rate') or 0
        funding_history = payload.get('funding_history', [])
        oi_data = payload.get('oi_data', {})
        taker = payload.get('taker_ratio', {})

        funding_trend = "flat"
        if len(funding_history) >= 4:
            rates = [f['rate'] for f in funding_history[:4]]
            if all(rates[i] >= rates[i+1] for i in range(len(rates)-1)):
                funding_trend = "rising"
            elif all(rates[i] <= rates[i+1] for i in range(len(rates)-1)):
                funding_trend = "falling"

        funding_extreme = abs(funding_rate) > 0.0003

        oi_delta_1h = oi_data.get('delta_1h_pct', 0)

        oi_div = None
        if oi_delta_1h < -1 and payload.get('latest_price'):
            klines = payload.get('klines', [])
            if len(klines) >= 2 and klines[-1][4] > klines[-2][4]:
                oi_div = "bearish"
        elif oi_delta_1h > 1 and payload.get('klines'):
            klines = payload['klines']
            if len(klines) >= 2 and klines[-1][4] < klines[-2][4]:
                oi_div = "bullish"

        ratio = taker.get('buy_sell_ratio', 1.0)
        if ratio > 1.2:
            taker_pressure = "buy"
        elif ratio < 0.8:
            taker_pressure = "sell"
        else:
            taker_pressure = "neutral"

        return {
            "funding_rate": funding_rate,
            "funding_trend": funding_trend,
            "funding_extreme": funding_extreme,
            "oi_delta_1h_pct": oi_delta_1h,
            "oi_price_divergence": oi_div,
            "taker_buy_sell_ratio": ratio,
            "taker_pressure": taker_pressure,
        }

    # ═══ 5. 市场微观结构 ═══

    def _analyze_microstructure(self, payload: dict) -> dict:
        ob = payload.get('orderbook', {})
        big = payload.get('big_trades', {})
        liq = payload.get('liquidations', {})

        spread = ob.get('spread_pct', 0)
        bid_depth = ob.get('bid_depth_usd', 0)
        ask_depth = ob.get('ask_depth_usd', 0)
        total_depth = bid_depth + ask_depth
        imbalance = round((bid_depth - ask_depth) / total_depth, 3) if total_depth > 0 else 0

        whale_dir = big.get('whale_direction', 'neutral')
        big_ratio = big.get('big_ratio', 1.0)

        liq_dir = liq.get('net_direction', 'neutral')
        long_liq = liq.get('long_vol_usd', 0)
        short_liq = liq.get('short_vol_usd', 0)
        total_liq = long_liq + short_liq
        if total_liq > 10_000_000:
            liq_intensity = "high"
        elif total_liq > 1_000_000:
            liq_intensity = "medium"
        else:
            liq_intensity = "low"

        return {
            "spread_pct": spread,
            "bid_ask_imbalance": imbalance,
            "whale_direction": whale_dir,
            "big_trade_ratio": big_ratio,
            "liquidation_pressure": liq_dir,
            "liquidation_intensity": liq_intensity,
        }

    # ═══ 6. 散户情绪 ═══

    def _analyze_crowd(self, long_short: dict) -> dict:
        long_ratio = long_short.get('long_ratio', 0.5)
        sentiment = long_short.get('crowd_sentiment', 'neutral')

        if long_ratio > 0.65:
            contrarian = "bearish"
        elif long_ratio < 0.35:
            contrarian = "bullish"
        else:
            contrarian = "neutral"

        return {
            "long_ratio": long_ratio,
            "sentiment": sentiment,
            "contrarian_signal": contrarian,
        }

    # ═══ 7. 风险评估 ═══

    def _analyze_risk(self, payload: dict) -> dict:
        funding = abs(payload.get('funding_rate') or 0)
        oi_delta = abs(payload.get('oi_data', {}).get('delta_1h_pct', 0))
        liq = payload.get('liquidations', {})
        ob = payload.get('orderbook', {})
        total_liq = liq.get('long_vol_usd', 0) + liq.get('short_vol_usd', 0)

        if funding > 0.0005 and oi_delta > 5 and total_liq > 5_000_000:
            leverage_risk = "high"
        elif funding > 0.0002 and oi_delta > 2:
            leverage_risk = "medium"
        else:
            leverage_risk = "low"

        klines = payload.get('klines', [])
        if len(klines) >= 10:
            recent_ranges = [(k[2] - k[3]) / k[4] * 100 for k in klines[-10:] if k[4] > 0]
            avg_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 1
            if avg_range > 5:
                vol_regime = "extreme"
            elif avg_range > 3:
                vol_regime = "high"
            elif avg_range > 1:
                vol_regime = "normal"
            else:
                vol_regime = "low"
        else:
            vol_regime = "normal"

        bid_depth = ob.get('bid_depth_usd', 0)
        ask_depth = ob.get('ask_depth_usd', 0)
        spread = ob.get('spread_pct', 1)
        depth_score = min(100, int((bid_depth + ask_depth) / 100_000))
        spread_penalty = min(50, int(spread * 1000))
        liquidity = max(0, depth_score - spread_penalty)

        return {
            "leverage_risk": leverage_risk,
            "volatility_regime": vol_regime,
            "liquidity_score": liquidity,
        }

    # ═══ LLM综合研判 ═══

    async def _llm_synthesize(self, symbol, trend, levels, momentum,
                              money_flow, micro, crowd, risk, price) -> dict:
        summary = f"""交易对: {symbol} | 当前价格: {price:.4g}

【多周期趋势】1h={trend['direction']} 强度{trend['strength']} | 4h偏向={trend['higher_tf_bias']} 4h_RSI={trend.get('tf_4h_rsi','?')} | 日线={trend['daily_bias']}
【日线位置】接近日线阻力={trend['daily_near_resistance']} 接近日线支撑={trend['daily_near_support']}
【1h均线】{trend['ma_alignment']}
【价位-1h】支撑={levels['support'][:2]} 阻力={levels['resistance'][:2]}
【价位-日线】日线支撑={levels['daily_support']} 日线阻力={levels['daily_resistance']}
【挂单墙】↑{levels['orderbook_wall_above']} ↓{levels['orderbook_wall_below']}
【动量】RSI={momentum['rsi']:.1f} 背离={momentum['rsi_divergence']} | 量比={momentum['volume_ratio']} 异常={momentum['volume_anomaly']}
【资金】费率={money_flow['funding_rate']:.6f}({money_flow['funding_trend']}) 极值={money_flow['funding_extreme']} | OI 1h={money_flow['oi_delta_1h_pct']:+.1f}% OI背离={money_flow['oi_price_divergence']}
【Taker】买卖比={money_flow['taker_buy_sell_ratio']:.2f} 压力={money_flow['taker_pressure']}
【微观】价差={micro['spread_pct']}% 深度偏向={micro['bid_ask_imbalance']:+.3f} | 鲸鱼={micro['whale_direction']} 大单比={micro['big_trade_ratio']:.1f}
【爆仓】方向={micro['liquidation_pressure']} 强度={micro['liquidation_intensity']}
【散户】多头占比={crowd['long_ratio']:.0%} 情绪={crowd['sentiment']} 反指={crowd['contrarian_signal']}
【风险】杠杆={risk['leverage_risk']} 波动={risk['volatility_regime']} 流动性={risk['liquidity_score']}"""

        try:
            return await self.ask_claude_json(TECH_PROMPT, summary)
        except Exception as e:
            self.logger.warning(f"LLM分析失败({symbol})，规则降级: {e}")
            direction = trend['direction']
            confidence = trend['strength']
            if crowd['contrarian_signal'] != "neutral" and crowd['contrarian_signal'] != direction:
                confidence = max(30, confidence - 15)
            return {
                "direction": direction,
                "confidence": confidence,
                "pattern": None,
                "key_insight": f"趋势{direction} 强度{trend['strength']} (LLM不可用，规则降级)",
                "reasoning": "基于趋势+均线排列的规则判断",
            }
