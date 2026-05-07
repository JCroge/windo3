"""裁判决策 Agent - 综合多方分析做最终交易决策"""

import time
from agents.base import BaseAgent


JUDGE_PROMPT = """你是一个加密货币交易裁判。你的职责是综合多个分析师的独立意见，做出最终交易决策。

决策原则：
1. 如果所有分析师意见一致且置信度高 → 果断执行
2. 如果有分歧但多数一致 → 跟随多数，但降低仓位
3. 如果严重分歧（方向完全相反且置信度都高）→ 不开仓，观望
4. 风控优先：任何情况下都不能违反风控规则
5. 反人性：市场极度贪婪时谨慎，极度恐惧时寻找机会

以JSON格式回复：
{
    "action": "open_long/open_short/close/hold",
    "confidence": 0-100,
    "size_pct": 0.0-1.0,
    "reasoning": "决策理由",
    "dissent_analysis": "分歧分析（如有）"
}"""


class JudgeAgent(BaseAgent):
    name = "judge"
    subscriptions = ["tech_analysis", "sentiment_analysis", "prediction"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._pending_analyses = {}
        self._last_decision_time = 0
        self._decision_cooldown = 55
        self._required_sources = {"tech_analysis"}
        self._optional_sources = {"sentiment_analysis", "prediction"}

    async def setup(self):
        self.init_llm()
        self.logger.info("裁判Agent就绪，等待分析结果...")

    async def on_message(self, msg: dict):
        msg_type = msg['type']
        self._pending_analyses[msg_type] = {
            "data": msg['payload'],
            "received_at": time.time()
        }

        if self._can_decide():
            await self._make_decision()

    def _can_decide(self) -> bool:
        now = time.time()
        if now - self._last_decision_time < self._decision_cooldown:
            return False

        for src in self._required_sources:
            if src not in self._pending_analyses:
                return False
            if now - self._pending_analyses[src]['received_at'] > 120:
                return False

        return True

    async def _make_decision(self):
        self._last_decision_time = time.time()

        tech = self._pending_analyses.get('tech_analysis', {}).get('data', {})
        sentiment = self._pending_analyses.get('sentiment_analysis', {}).get('data', {})
        prediction = self._pending_analyses.get('prediction', {}).get('data', {})

        analyses_summary = self._build_summary(tech, sentiment, prediction)
        decision = await self._ask_judge(analyses_summary, tech)

        decision['symbol'] = tech.get('symbol', self.config.get('symbol'))
        decision['timestamp'] = time.time()
        decision['source_analyses'] = {
            "tech": bool(tech),
            "sentiment": bool(sentiment),
            "prediction": bool(prediction),
        }

        await self.publish("trade_decision", decision)
        self.logger.info(f"[决策] {decision['action']} 置信度={decision['confidence']} "
                        f"理由: {decision.get('reasoning', '')[:50]}")

        self._pending_analyses.clear()

    def _build_summary(self, tech: dict, sentiment: dict, prediction: dict) -> str:
        parts = []

        if tech:
            llm = tech.get('llm_analysis', {})
            rule = tech.get('rule_signal', {})
            indicators = tech.get('indicators', {})
            parts.append(f"""【技术分析师】
- 方向: {llm.get('direction', '未知')}，置信度: {llm.get('confidence', 0)}
- 形态: {llm.get('pattern', '无')}
- 规则信号: 做多={rule.get('entry_long',0)} 做空={rule.get('entry_short',0)}
- RSI: {indicators.get('rsi', 0):.1f}
- 价格: {indicators.get('price', 0):.2f}""")

        if sentiment:
            parts.append(f"""【情绪分析师】
- 情绪评分: {sentiment.get('score', 0)} (-100到+100)
- 资金费率: {sentiment.get('funding_rate', 'N/A')}
- 判断: {sentiment.get('summary', '未知')}""")

        if prediction:
            parts.append(f"""【趋势预测师】
- 预测方向: {prediction.get('direction', '未知')}
- 置信度: {prediction.get('confidence', 0)}
- 目标价: {prediction.get('target_price', 'N/A')}""")

        if not parts:
            return "暂无分析数据"

        return "\n\n".join(parts)

    async def _ask_judge(self, summary: str, tech: dict) -> dict:
        user_msg = f"""以下是各分析师的独立意见：

{summary}

请综合以上意见，做出最终交易决策。
当前无持仓。最大交易额: {self.config.get('max_trade_amount', 10)} USDT。"""

        try:
            result = await self.ask_claude_json(JUDGE_PROMPT, user_msg)
            if 'action' not in result:
                result['action'] = 'hold'
            if 'confidence' not in result:
                result['confidence'] = 0
            if 'size_pct' not in result:
                result['size_pct'] = 0.5
            return result

        except Exception as e:
            self.logger.warning(f"LLM决策失败，使用规则降级: {e}")
            return self._rule_fallback(tech)

    def _rule_fallback(self, tech: dict) -> dict:
        rule = tech.get('rule_signal', {})
        if rule.get('entry_long'):
            return {"action": "open_long", "confidence": 60, "size_pct": 0.5,
                    "reasoning": "规则引擎降级：技术面做多信号"}
        elif rule.get('entry_short'):
            return {"action": "open_short", "confidence": 60, "size_pct": 0.5,
                    "reasoning": "规则引擎降级：技术面做空信号"}
        else:
            return {"action": "hold", "confidence": 50, "size_pct": 0,
                    "reasoning": "规则引擎降级：无明确信号"}
