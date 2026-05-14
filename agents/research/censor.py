"""言官 Agent - 逆向思维，对研判结果进行风险挑战和二度审视"""

from agents.base import BaseAgent

CENSOR_PROMPT = """你是一个加密货币交易的"言官"（Devil's Advocate）。你的职责是对研判团队选出的标的进行风险审视，但要平衡风险与机会。

你的核心原则：
1. 识别明显的致命风险（流动性枯竭、项目方跑路、监管打击）
2. 只在有充分证据时才reject，否则给予proceed_with_caution
3. 市场中性（恐贪指数40-60）不是拒绝理由，而是正常交易环境
4. 小市值币（<5000万美元日交易量）才需要警惕操纵风险
5. 允许试错：小仓位交易是学习市场的必要成本

你要对每个被选中的标的提出风险警告，但不要过度保守。

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

        # 分批审查：每批最多4个标的，避免prompt过长触发中转站超时
        BATCH_SIZE = 4
        all_challenges = []
        all_systemic = []
        overall_verdict = ""

        for i in range(0, len(selected), BATCH_SIZE):
            batch = selected[i:i + BATCH_SIZE]
            user_msg = self._build_challenge_request(batch, market_context if i == 0 else "")

            try:
                result = await self.ask_claude_json(CENSOR_PROMPT, user_msg)
                all_challenges.extend(result.get('challenges', []))
                all_systemic.extend(result.get('systemic_risks', []))
                if not overall_verdict:
                    overall_verdict = result.get('overall_verdict', '')
            except Exception as e:
                self.logger.warning(f"言官LLM失败(batch {i//BATCH_SIZE+1})，规则降级: {e}")
                all_challenges.extend(self._rule_fallback_challenge(batch))

        if not all_challenges:
            all_challenges = self._rule_fallback_challenge(selected)
            overall_verdict = "规则降级审查"

        rejected = [c['symbol'] for c in all_challenges if c.get('recommendation') == 'reject']
        cautioned = [c['symbol'] for c in all_challenges if c.get('recommendation') == 'proceed_with_caution']

        self.logger.info(
            f"[言官] 审查完成: 驳回{rejected}, 警告{cautioned}, "
            f"系统性风险{len(all_systemic)}条"
        )

        await self.publish("research_challenge", {
            "challenges": all_challenges,
            "systemic_risks": list(set(all_systemic)),
            "overall_verdict": overall_verdict,
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
            # 负面催化剂否决：confidence=0是synthesizer标记的负面新闻信号
            if confidence == 0:
                objections.append("检测到负面催化剂新闻，风险不可控")
                risk_level = "high"
                recommendation = "reject"
            elif confidence < 40:
                objections.append(f"置信度{confidence}过低，信号不足以支撑入场")
                risk_level = "high"
                recommendation = "reject"
            elif confidence > 85:
                objections.append("置信度过高，可能存在确认偏误")
                risk_level = "medium"

            direction = s.get('direction_bias', '')
            if direction in ('long', 'short') and recommendation != "reject":
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
