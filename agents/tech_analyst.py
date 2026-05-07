"""技术分析 Agent - 计算指标并用 Claude 分析形态"""

import pandas as pd
from agents.base import BaseAgent
from indicators import TechnicalIndicators
from optimize_1h import RobustStrategy


TECH_ANALYSIS_PROMPT = """你是一个专业的加密货币技术分析师。基于以下技术指标数据，给出你的分析判断。

要求：
1. 判断当前趋势方向（bullish/bearish/neutral）
2. 给出置信度（0-100）
3. 识别关键形态（如有）
4. 给出支撑位和阻力位

以JSON格式回复：
{
    "direction": "bullish/bearish/neutral",
    "confidence": 0-100,
    "pattern": "形态名称或null",
    "support": 价格,
    "resistance": 价格,
    "reasoning": "简短分析理由"
}"""


class TechAnalystAgent(BaseAgent):
    name = "tech_analyst"
    subscriptions = ["market_data"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.strategy = RobustStrategy(
            ma_fast=7, ma_slow=25,
            rsi_period=14, rsi_threshold=75,
            volume_factor=1.0
        )

    async def setup(self):
        self.init_llm()
        self.logger.info("技术分析Agent就绪")

    async def on_message(self, msg: dict):
        if msg['type'] != 'market_data':
            return

        payload = msg['payload']
        klines = payload['klines']

        df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
        df_analyzed = self.strategy.analyze(df)

        latest = df_analyzed.iloc[-2]  # 已闭合K线

        rule_signal = {
            "entry_long": int(latest.get('entry_long', 0)),
            "entry_short": int(latest.get('entry_short', 0)),
            "exit_long": int(latest.get('exit_long', 0)),
            "exit_short": int(latest.get('exit_short', 0)),
        }

        indicators = {
            "price": float(latest['close']),
            "rsi": float(latest.get('rsi', 50)),
            "ma_fast": float(latest.get('ma_fast', 0)),
            "ma_slow": float(latest.get('ma_slow', 0)),
            "volume": float(latest.get('volume', 0)),
            "volume_ma": float(latest.get('volume_ma', 0)),
        }

        llm_analysis = await self._llm_analyze(indicators, df_analyzed)

        result = {
            "symbol": payload['symbol'],
            "rule_signal": rule_signal,
            "indicators": indicators,
            "llm_analysis": llm_analysis,
        }

        await self.publish("tech_analysis", result)
        self.logger.info(f"[技术分析] 方向={llm_analysis.get('direction','?')} "
                        f"置信度={llm_analysis.get('confidence',0)} "
                        f"规则信号: L={rule_signal['entry_long']} S={rule_signal['entry_short']}")

    async def _llm_analyze(self, indicators: dict, df: pd.DataFrame) -> dict:
        recent = df.tail(10)[['close', 'high', 'low', 'volume']].to_dict('records')

        user_msg = f"""当前技术指标：
- 价格: {indicators['price']:.2f}
- RSI(14): {indicators['rsi']:.1f}
- MA(7): {indicators['ma_fast']:.2f}
- MA(25): {indicators['ma_slow']:.2f}
- 成交量: {indicators['volume']:.2f} (均量: {indicators['volume_ma']:.2f})
- MA关系: {'快线在上（多头排列）' if indicators['ma_fast'] > indicators['ma_slow'] else '快线在下（空头排列）'}

最近10根K线收盘价走势: {[round(r['close'], 2) for r in recent]}"""

        try:
            return await self.ask_claude_json(TECH_ANALYSIS_PROMPT, user_msg)
        except Exception as e:
            self.logger.warning(f"LLM分析失败，使用规则降级: {e}")
            direction = "bullish" if indicators['ma_fast'] > indicators['ma_slow'] else "bearish"
            return {
                "direction": direction,
                "confidence": 50,
                "pattern": None,
                "support": indicators['price'] * 0.98,
                "resistance": indicators['price'] * 1.02,
                "reasoning": "LLM不可用，基于MA排列的规则判断"
            }
