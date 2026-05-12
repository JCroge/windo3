"""行为批判官 Agent - 从行为金融学角度审视持仓建议

检测7种认知偏差：
1. 损失厌恶 (loss_aversion)
2. 沉没成本 (sunk_cost)
3. 锚定效应 (anchoring)
4. FOMO (fomo)
5. 处置效应 (disposition)
6. 过度自信 (overconfidence)
7. 恐慌 (panic)
"""

from agents.base import BaseAgent

CRITIC_PROMPT = """你是一个行为金融学专家。你的任务是审视交易分析师的持仓建议，检测其中可能存在的认知偏差。

你必须站在"人性弱点检测者"的角度，而非交易分析师的角度。你的价值在于发现分析师可能因为心理偏差而做出的非理性建议。

## 当前持仓状态
- 标的：{symbol}
- 方向：{side}，杠杆：{leverage}x
- 入场价：{entry_price}，当前价：{current_price}
- 浮盈/亏：{pnl_pct}%（含杠杆）
- 持仓时间：{hours_held}小时
- 趋势状态：{trend}
- RSI：{rsi}

## 分析师建议
- 动作：{analyst_action}
- 确信度：{analyst_conviction}
- 评分：{position_score}
- 理由：{analyst_reasoning}
- 评分因子：{factors}

## 你需要检测的偏差

1. **损失厌恶**(loss_aversion)：浮亏中建议hold，是否在回避"承认错误"的痛苦？
2. **沉没成本**(sunk_cost)：持仓很久+浮亏，是否因为"已经投入了时间"而不愿放手？
3. **锚定效应**(anchoring)：是否执着于入场价，而忽视了从当前价出发的R:R？
4. **FOMO**(fomo)：浮盈中建议加仓，是否因为"赚钱了"就想追加？风险在增加。
5. **处置效应**(disposition)：小幅浮盈就建议平仓，是否害怕利润回吐而过早止盈？
6. **过度自信**(overconfidence)：高杠杆+建议加仓，是否忽视了尾部风险？
7. **恐慌**(panic)：短期急跌就建议平仓，是否被洗盘吓到了？

## 判断标准
- 如果分析师的建议在当前市场结构下是合理的，输出bias_detected=null
- 只有当你有充分理由认为存在偏差时才标记
- severity: low=轻微倾向, medium=明显偏差, high=严重非理性

## 输出格式（严格JSON）
```json
{{
    "symbol": "...",
    "bias_detected": null或"loss_aversion"/"sunk_cost"/"anchoring"/"fomo"/"disposition"/"overconfidence"/"panic",
    "severity": "none"/"low"/"medium"/"high",
    "challenge": "你的质疑理由（1-2句话）",
    "counter_recommendation": null或"hold"/"add"/"reduce"/"close",
    "confidence_in_challenge": 0-100
}}
```"""


class BehavioralCritic(BaseAgent):
    name = "behavioral_critic"
    subscriptions = ["position_review:*"]

    def __init__(self, config: dict = None):
        super().__init__(config)

    async def setup(self):
        self.init_llm()
        self.logger.info("行为批判官就绪")

    async def on_message(self, msg: dict):
        if msg['type'] == 'position_review':
            await self._critique(msg['payload'])

    async def _critique(self, review: dict):
        symbol = review.get('symbol', '')
        ctx = review.get('context', {})

        prompt_filled = CRITIC_PROMPT.format(
            symbol=symbol,
            side=ctx.get('side', '?'),
            leverage=ctx.get('leverage', 1),
            entry_price=ctx.get('entry_price', 0),
            current_price=ctx.get('current_price', 0),
            pnl_pct=ctx.get('pnl_pct', 0),
            hours_held=ctx.get('hours_held', 0),
            trend=ctx.get('trend', 'neutral'),
            rsi=ctx.get('rsi', 50),
            analyst_action=review.get('action', 'hold'),
            analyst_conviction=review.get('conviction', 0),
            position_score=review.get('position_score', 0),
            analyst_reasoning=review.get('reasoning', ''),
            factors=review.get('factors', {}),
        )

        try:
            result = await self.ask_claude_json(
                prompt_filled,
                f"请审视分析师对{symbol}的{review.get('action', 'hold')}建议，检测认知偏差。",
                temperature=0.3,
            )

            if not result or not isinstance(result, dict):
                result = self._rule_fallback(review)

            result['symbol'] = symbol
            self.logger.info(
                f"[批判官] {symbol}: bias={result.get('bias_detected', 'none')} "
                f"severity={result.get('severity', 'none')} "
                f"challenge={result.get('challenge', '')[:50]}"
            )

        except Exception as e:
            self.logger.warning(f"[批判官] {symbol} LLM失败: {e}，规则降级")
            result = self._rule_fallback(review)

        await self.publish("position_verdict", result, symbol=symbol)

    def _rule_fallback(self, review: dict) -> dict:
        """LLM不可用时的规则降级"""
        ctx = review.get('context', {})
        action = review.get('action', 'hold')
        pnl_pct = ctx.get('pnl_pct', 0)
        hours_held = ctx.get('hours_held', 0)
        leverage = ctx.get('leverage', 1)
        symbol = review.get('symbol', '')

        bias = None
        severity = 'none'
        challenge = ''
        counter = None
        confidence = 0

        # 规则检测
        if action == 'hold' and pnl_pct < -5:
            bias = 'loss_aversion'
            severity = 'medium' if pnl_pct < -8 else 'low'
            challenge = f"浮亏{pnl_pct:.1f}%仍建议hold，可能存在损失厌恶"
            counter = 'close' if pnl_pct < -8 else 'reduce'
            confidence = 60

        elif action == 'hold' and pnl_pct < 0 and hours_held > 24:
            bias = 'sunk_cost'
            severity = 'medium' if hours_held > 36 else 'low'
            challenge = f"持仓{hours_held:.0f}h+浮亏，可能存在沉没成本偏差"
            counter = 'close'
            confidence = 50

        elif action == 'add' and pnl_pct > 3:
            bias = 'fomo'
            severity = 'medium' if leverage >= 10 else 'low'
            challenge = f"浮盈{pnl_pct:.1f}%就想加仓，可能是FOMO"
            counter = 'hold'
            confidence = 55

        elif action == 'add' and leverage >= 10:
            bias = 'overconfidence'
            severity = 'medium'
            challenge = f"高杠杆{leverage}x还想加仓，可能过度自信"
            counter = 'hold'
            confidence = 60

        elif action == 'close' and 0 < pnl_pct < 3:
            bias = 'disposition'
            severity = 'low'
            challenge = f"小幅浮盈{pnl_pct:.1f}%就想平仓，可能是处置效应"
            counter = 'hold'
            confidence = 40

        return {
            "symbol": symbol,
            "bias_detected": bias,
            "severity": severity,
            "challenge": challenge,
            "counter_recommendation": counter,
            "confidence_in_challenge": confidence,
        }
