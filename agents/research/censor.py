"""言官 Agent - 逆向思维，对研判结果进行风险挑战和二度审视"""

from agents.base import BaseAgent

CENSOR_PROMPT = """你是一个加密货币交易的"言官"（Devil's Advocate）。你的职责是对研判团队选出的标的进行逆向思维审视，找出他们可能忽略的风险和盲点。

你的核心原则：
1. 假设研判团队是错的，寻找反面证据
2. 识别"共识陷阱"：当所有信号都指向同一方向时，反而最危险
3. 检查时间维度：这个机会是不是已经过了最佳入场点？
4. 质疑数据质量：资金费率可能被操纵、成交量可能是刷量、新闻可能是庄家放出的
5. 考虑黑天鹅：监管风险、项目方跑路、合约漏洞、流动性枯竭
6. 评估拥挤度：如果太多人看到同样的信号，这个交易就不再有优势

你要对每个被选中的标的提出具体的质疑和风险警告。不要泛泛而谈，要针对具体数据点反驳。

以JSON格式回复：
{
    "challenges": [
        {
            "symbol": "SOL-USDT",
            "risk_level": "high/medium/low",
            "objections": ["具体反对理由1", "具体反对理由2"],
            "blind_spots": ["研判可能忽略的点"],
            "worst_case": "最坏情况描述",
            "recommendation": "reject/reduce_size/proceed_with_caution/accept"
        }
    ],
    "systemic_risks": ["影响所有标的的系统性风险"],
    "overall_verdict": "研判质量评价（1-2句）"
}"""


class Censor(BaseAgent):
    name = "censor"
    subscriptions = ["research_preliminary"]

    def __init__(self, config: dict = None):
        super().__init__(config)

    async def setup(self):
        self.init_llm()
        self.logger.info("言官Agent就绪（逆向研判）")

    async def on_message(self, msg: dict):
        if msg['type'] == 'research_preliminary':
            await self._challenge(msg['payload'])

    async def _challenge(self, payload: dict):
        selected = payload.get('selected', [])
        market_context = payload.get('market_context', '')

        if not selected:
            return

        user_msg = self._build_challenge_request(selected, market_context)

        try:
            result = await self.ask_claude_json(CENSOR_PROMPT, user_msg)
            challenges = result.get('challenges', [])
        except Exception as e:
            self.logger.warning(f"言官LLM失败，规则降级: {e}")
            challenges = self._rule_fallback_challenge(selected)
            result = {"challenges": challenges, "systemic_risks": [], "overall_verdict": "规则降级审查"}

        rejected = [c['symbol'] for c in challenges if c.get('recommendation') == 'reject']
        cautioned = [c['symbol'] for c in challenges if c.get('recommendation') == 'proceed_with_caution']

        self.logger.info(
            f"[言官] 审查完成: 驳回{rejected}, 警告{cautioned}, "
            f"系统性风险{len(result.get('systemic_risks', []))}条"
        )

        await self.publish("research_challenge", {
            "challenges": challenges,
            "systemic_risks": result.get('systemic_risks', []),
            "overall_verdict": result.get('overall_verdict', ''),
            "original_selected": selected,
        })

    def _build_challenge_request(self, selected: list, market_context: str) -> str:
        parts = ["研判团队选出了以下标的，请进行逆向审视：\n"]

        for s in selected:
            parts.append(
                f"【{s['symbol']}】\n"
                f"  方向偏好: {s.get('direction_bias', '未知')}\n"
                f"  置信度: {s.get('confidence', 0)}\n"
                f"  选择理由: {s.get('reasoning', '无')}\n"
                f"  关键信号: {s.get('key_signal', '无')}\n"
                f"  已识别风险: {s.get('risk_factor', '无')}\n"
            )

        if market_context:
            parts.append(f"\n原始市场数据摘要:\n{market_context}")

        parts.append("\n请对每个标的提出具体质疑，找出研判团队可能的盲点。")
        return "\n".join(parts)

    def _rule_fallback_challenge(self, selected: list) -> list:
        """规则降级：基于简单启发式提出质疑"""
        challenges = []
        for s in selected:
            objections = []
            risk_level = "medium"
            recommendation = "proceed_with_caution"

            confidence = s.get('confidence', 0)
            if confidence > 85:
                objections.append("置信度过高，可能存在确认偏误")
                risk_level = "medium"

            direction = s.get('direction_bias', '')
            if direction in ('long', 'short'):
                objections.append(f"单边{direction}信号可能是趋势末端")

            if not objections:
                objections.append("缺乏足够的逆向验证数据")
                recommendation = "accept"

            challenges.append({
                "symbol": s['symbol'],
                "risk_level": risk_level,
                "objections": objections,
                "blind_spots": ["规则降级模式无法深度分析"],
                "worst_case": "无法评估",
                "recommendation": recommendation,
            })

        return challenges
