"""研判综合 Agent - Claude综合分析，识别预期差和时间窗口

两阶段决策流程：
1. 初选：综合市场/链上/新闻数据，选出候选标的 → 发给言官
2. 终选：收到言官谏言后，综合考虑风险，做出最终决策 → 发给SymbolRouter
"""

import time
from agents.base import BaseAgent

SYNTHESIS_PROMPT = """你是一个加密货币研究分析师。你的任务是综合市场数据、链上信号和新闻舆情，筛选出未来4小时最有交易价值的12个永续合约标的。

分析维度：
1. 预期差识别：市场定价是否偏离基本面？资金费率是否暗示过度拥挤的方向？
2. 时间窗口：波动率是否在扩张初期？成交量是否在放大？
3. 利好出尽检测：涨幅已经很大+资金费率极高+新闻密集报道 → 可能反转
4. 市场操纵过滤：成交量突然暴增但价格不动 → 可能是刷量
5. 相关性考虑：不要选高度相关的标的（如同一板块多个meme币）
6. 链上信号：鲸鱼大额转入交易所=抛压信号，转出=囤币信号
7. 新闻催化：重大利好/利空新闻是否已被价格消化？新闻时间vs价格变动时间差

筛选标准：
- OKX上所有USDT永续合约均可选，包括XAU/XAG/CL/股票合约等，只要有波动和成交量
- 波动率范围：3%-50%均可考虑，高波动（>15%）配合方向明确的信号是高质量机会，不要因为波动率高就回避
- 【最重要】必须选有明确趋势方向的标的！趋势中性/横盘的标的没有交易价值。优先选：
  * 24h涨跌幅>3%且方向一致的标的（说明有趋势）
  * MA5/MA20已形成金叉或死叉的标的
  * 成交量持续放大+价格单边运动的标的
- 不要总是选BTC/ETH/SOL——它们流动性好但机会平庸。优先寻找有明确催化剂、动量强劲的标的
- 高动量信号（24h涨跌>10%+成交量放大）是重要的入场机会，尤其是趋势初期
- 资金费率极端值（>0.1%或<-0.1%）是重要的反向信号
- 成交量>$30M即可考虑，不必局限于大市值标的
- 【止损结构】优先选止损结构=[✓可交易]的标的（支撑/阻力距当前价1.5%~15%，止损放得下且不过宽）；止损结构=[✗止损结构差]的标的即使趋势好也难以开仓，应降低优先级

以JSON格式回复：
{
    "selected_symbols": [
        {
            "symbol": "SOL-USDT",
            "direction_bias": "long/short/neutral",
            "confidence": 0-100,
            "reasoning": "选择理由",
            "key_signal": "最关键的信号",
            "risk_factor": "主要风险"
        }
    ],
    "market_regime": "trending/ranging/volatile/quiet",
    "overall_assessment": "整体市场判断（1-2句）"
}"""

FINAL_DECISION_PROMPT = """你是一个加密货币研究分析师的主管。你之前选出了一批候选标的，现在"言官"（Devil's Advocate）对你的选择提出了质疑和风险警告。

你需要认真考虑言官的每一条谏言，然后做出最终决策：
- 如果言官的质疑有道理 → 移除该标的或降低置信度
- 如果言官的质疑站不住脚 → 保留该标的并说明为什么
- 如果言官发现了你忽略的系统性风险 → 可能需要全部重新评估

原则：宁可错过机会，不可忽视风险。言官说"reject"的标的，除非你有非常充分的理由，否则应该移除。

以JSON格式回复：
{
    "final_symbols": [
        {
            "symbol": "SOL-USDT",
            "direction_bias": "long/short/neutral",
            "confidence": 0-100,
            "reasoning": "最终决策理由（含对言官质疑的回应）",
            "key_signal": "最关键的信号",
            "risk_factor": "综合风险评估"
        }
    ],
    "market_regime": "trending/ranging/volatile/quiet",
    "overall_assessment": "最终判断",
    "censor_response": "对言官整体意见的回应（1-2句）"
}"""


class ResearchSynthesizer(BaseAgent):
    name = "research_synthesizer"
    subscriptions = ["research_market_data", "research_sentiment_data", "research_news_data",
                     "research_challenge"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._pending_data = {}
        self._max_symbols = 12
        self._preliminary_result = None
        self._market_context = ""

    async def setup(self):
        self.init_llm()
        self.logger.info("研判综合Agent就绪（两阶段决策）")

    async def on_message(self, msg: dict):
        if msg['type'] == 'research_challenge':
            await self._final_decision(msg['payload'])
            return

        self._pending_data[msg['type']] = msg['payload']

        if 'research_market_data' in self._pending_data:
            await self._preliminary_synthesis()

    async def _preliminary_synthesis(self):
        """第一阶段：初选"""
        market_data = self._pending_data.get('research_market_data', {})
        sentiment_data = self._pending_data.get('research_sentiment_data', {})
        news_data = self._pending_data.get('research_news_data', {})
        self._pending_data.clear()

        candidates = market_data.get('candidates', [])
        if not candidates:
            self.logger.warning("[研判] 无候选标的数据")
            return

        self._market_context = self._build_research_summary(candidates, sentiment_data, news_data)

        try:
            result = await self.ask_claude_json(SYNTHESIS_PROMPT, self._market_context)
            selected = result.get('selected_symbols', [])[:self._max_symbols]
            market_regime = result.get('market_regime', 'unknown')
        except Exception as e:
            self.logger.warning(f"LLM研判失败，规则降级: {e}")
            selected = self._rule_fallback(candidates)
            market_regime = 'unknown'

        # 负面催化剂否决：有近4h负面新闻的标的直接降confidence至0
        # 设计原则：新闻做veto层而非加分层（参考Freqtrade news-filter）
        if news_data:
            for s in selected:
                if self._check_negative_catalyst(s['symbol'], news_data):
                    s['confidence'] = 0
                    s['risk_factor'] = '近4h检测到负面催化剂新闻'

        self._preliminary_result = {
            "selected": selected,
            "market_regime": market_regime,
            "total_candidates": len(candidates),
        }

        symbols = [s['symbol'] for s in selected]
        self.logger.info(f"[研判·初选] 候选标的: {symbols} → 提交言官审查")

        await self.publish("research_preliminary", {
            "selected": selected,
            "market_regime": market_regime,
            "market_context": self._market_context,
        })

    async def _final_decision(self, challenge: dict):
        """第二阶段：收到言官谏言后做最终决策"""
        if not self._preliminary_result:
            self.logger.warning("[研判] 收到谏言但无初选结果，忽略")
            return

        preliminary = self._preliminary_result
        self._preliminary_result = None

        challenges = challenge.get('challenges', [])
        systemic_risks = challenge.get('systemic_risks', [])
        overall_verdict = challenge.get('overall_verdict', '')

        user_msg = self._build_final_decision_request(
            preliminary['selected'], challenges, systemic_risks, overall_verdict
        )

        try:
            result = await self.ask_claude_json(FINAL_DECISION_PROMPT, user_msg)
            final_selected = result.get('final_symbols', [])[:self._max_symbols]
            market_regime = result.get('market_regime', preliminary['market_regime'])
            censor_response = result.get('censor_response', '')
        except Exception as e:
            self.logger.warning(f"LLM终选失败，采用保守策略: {e}")
            final_selected = self._apply_censor_rules(preliminary['selected'], challenges)
            market_regime = preliminary['market_regime']
            censor_response = "规则降级处理"

        if not final_selected:
            final_selected = self._salvage_from_preliminary(preliminary['selected'], challenges)
            if final_selected:
                self.logger.info(f"[研判·终选] 言官全部驳回，保底保留: {[s['symbol'] for s in final_selected]}")

        symbols = [s['symbol'] for s in final_selected]
        self.logger.info(f"[研判·终选] 最终标的: {symbols} (言官回应: {censor_response[:50]})")

        await self.publish("research_result", {
            "selected": final_selected,
            "market_regime": market_regime,
            "overall_assessment": result.get('overall_assessment', '') if 'result' in dir() else '',
            "total_candidates": preliminary['total_candidates'],
            "censor_incorporated": True,
        })

    def _build_final_decision_request(self, selected: list, challenges: list,
                                       systemic_risks: list, verdict: str) -> str:
        parts = ["你之前的初选结果：\n"]
        for s in selected:
            parts.append(f"- {s['symbol']}: {s.get('direction_bias','?')} "
                        f"置信度={s.get('confidence',0)} 理由={s.get('reasoning','')}")

        parts.append("\n\n言官的谏言：\n")
        for c in challenges:
            parts.append(f"【{c['symbol']}】 风险等级: {c.get('risk_level','?')}")
            parts.append(f"  反对理由: {c.get('objections', [])}")
            parts.append(f"  盲点: {c.get('blind_spots', [])}")
            parts.append(f"  最坏情况: {c.get('worst_case', '?')}")
            parts.append(f"  建议: {c.get('recommendation', '?')}")

        if systemic_risks:
            parts.append(f"\n系统性风险警告: {systemic_risks}")

        if verdict:
            parts.append(f"\n言官总评: {verdict}")

        parts.append("\n\n请综合言官的谏言，做出最终决策。")
        return "\n".join(parts)

    def _apply_censor_rules(self, selected: list, challenges: list) -> list:
        """规则降级：根据言官建议过滤"""
        reject_symbols = set()
        for c in challenges:
            if c.get('recommendation') == 'reject':
                reject_symbols.add(c['symbol'])

        final = []
        for s in selected:
            if s['symbol'] in reject_symbols:
                self.logger.info(f"[研判·规则降级] 移除被言官驳回的: {s['symbol']}")
                continue
            caution = next((c for c in challenges if c['symbol'] == s['symbol']
                          and c.get('recommendation') == 'proceed_with_caution'), None)
            if caution:
                s['confidence'] = max(s.get('confidence', 0) - 15, 30)
            final.append(s)

        return final

    def _salvage_from_preliminary(self, selected: list, challenges: list) -> list:
        """保底机制：如果终选全空，保留言官评为reduce_size或accept的标的（降低置信度）"""
        salvaged = []
        for s in selected:
            challenge = next((c for c in challenges if c['symbol'] == s['symbol']), None)
            if challenge and challenge.get('recommendation') in ('reduce_size', 'accept', 'proceed_with_caution'):
                s['confidence'] = max(s.get('confidence', 0) - 20, 30)
                s['risk_factor'] = f"言官警告: {challenge.get('objections', [''])[0][:40]}"
                salvaged.append(s)
        return salvaged[:1]

    def _build_research_summary(self, candidates: list, sentiment: dict, news: dict) -> str:
        parts = []

        parts.append("【市场数据 - Top候选标的】")
        for i, c in enumerate(candidates[:20], 1):
            funding = c.get('funding_rate')
            funding_str = f"{funding*100:.4f}%" if funding else "N/A"
            ls_ratio = c.get('long_short_ratio')
            ls_str = f"{ls_ratio:.2f}" if ls_ratio else "N/A"
            oi = c.get('open_interest_usd')
            oi_str = f"${oi/1e6:.0f}M" if oi else "N/A"
            sl = c.get('sl_structure', {})
            sl_str = f"支撑距{sl.get('support_dist_pct','?')}%/阻力距{sl.get('resist_dist_pct','?')}% ATR={sl.get('atr_pct','?')}% {'✓可交易' if sl.get('sl_viable', True) else '✗止损结构差'}" if sl else "N/A"
            parts.append(
                f"{i}. {c['symbol']}: 价格=${c['price']:.4f}, "
                f"24h量=${c['volume_24h']/1e6:.1f}M, "
                f"波动率={c['volatility_pct']}%, "
                f"涨跌={c['change_24h_pct']}%, "
                f"资金费率={funding_str}, "
                f"多空比={ls_str}, 持仓量={oi_str}, 止损结构=[{sl_str}]"
            )

        if sentiment:
            fg = sentiment.get('fear_greed')
            if fg:
                parts.append(f"\n【市场情绪】")
                parts.append(f"恐贪指数: {fg['value']} ({fg['classification']})")
                history = fg.get('history_7d', [])
                if history:
                    vals = [h['value'] for h in history]
                    parts.append(f"  7日趋势: {vals}")

            trending = sentiment.get('trending_coins', [])
            if trending:
                parts.append(f"社交热度Top币种: {', '.join(t['symbol'] for t in trending[:8])}")

            taker = sentiment.get('taker_ratios', {})
            if taker:
                parts.append("Taker买卖比(>1多头主导, <1空头主导):")
                for sym, data in list(taker.items())[:8]:
                    ratio = data['buy_sell_ratio']
                    signal = "多头强势" if ratio > 1.1 else "空头强势" if ratio < 0.9 else "均衡"
                    parts.append(f"  {sym}: {ratio:.3f} ({signal})")

        if news:
            headlines = news.get('headlines', [])
            symbol_mentions = news.get('symbol_mentions', {})
            if headlines:
                parts.append("\n【新闻舆情 - 最新头条】")
                for h in headlines[:8]:
                    parts.append(f"- [{h.get('source','')}] {h.get('title','')}")
            if symbol_mentions:
                parts.append("\n币种提及频率:")
                for sym, data in list(symbol_mentions.items())[:8]:
                    parts.append(f"  {sym}: {data['count']}次提及")

        parts.append(f"\n总扫描合约数: {len(candidates)}")
        return "\n".join(parts)

    # 负面催化剂关键词（参考Freqtrade news-filter veto层设计）
    _NEGATIVE_KEYWORDS = [
        'hack', 'exploit', 'breach', 'stolen', 'rug', 'scam', 'fraud',
        'sec', 'lawsuit', 'ban', 'crackdown', 'arrest', 'seized',
        'insolvent', 'bankrupt', 'shutdown', 'suspend',
        '黑客', '漏洞', '被盗', '跑路', '诈骗', '监管', '起诉', '查封', '暂停',
    ]

    def _check_negative_catalyst(self, symbol: str, news_data: dict) -> bool:
        """检查近4h内是否有负面催化剂新闻，返回True表示有风险应否决"""
        headlines = news_data.get('headlines', [])
        symbol_mentions = news_data.get('symbol_mentions', {})
        now = time.time()
        window = 4 * 3600  # 4小时窗口（参考QuantConnect Alpha Streams研究）

        # 获取该标的相关的新闻标题
        base = symbol.split('-')[0].upper()
        relevant_titles = []

        # 从symbol_mentions获取该标的的新闻标题
        for key, data in symbol_mentions.items():
            if key.upper() == base:
                relevant_titles.extend(data.get('headlines', []))

        # 从全局headlines中找近4h内提及该标的的新闻
        for h in headlines:
            if not h.get('published_ts') or now - h['published_ts'] > window:
                continue
            title_lower = (h.get('title', '') + ' ' + h.get('summary', '')).lower()
            if base.lower() in title_lower:
                relevant_titles.append(h.get('title', ''))

        # 关键词匹配
        for title in relevant_titles:
            title_lower = title.lower()
            for kw in self._NEGATIVE_KEYWORDS:
                if kw in title_lower:
                    self.logger.warning(f"[研判] {symbol} 检测到负面催化剂: '{kw}' in '{title[:60]}'")
                    return True
        return False

    def _rule_fallback(self, candidates: list) -> list:
        """LLM不可用时的规则降级选币"""
        scored = []
        for c in candidates[:20]:
            score = 0
            vol = c.get('volatility_pct', 0)
            if 3 <= vol <= 15:
                score += 30
            elif vol > 15:
                score -= 20

            funding = c.get('funding_rate')
            if funding is not None:
                if abs(funding) > 0.001:
                    score += 20
                if abs(funding) > 0.003:
                    score += 10

            if c.get('volume_24h', 0) > 50_000_000:
                score += 20
            if c.get('volume_24h', 0) > 200_000_000:
                score += 10

            change = abs(c.get('change_24h_pct', 0))
            if 2 <= change <= 10:
                score += 15

            direction = "neutral"
            if funding is not None and funding > 0.001:
                direction = "short"
            elif funding is not None and funding < -0.001:
                direction = "long"
            elif c.get('change_24h_pct', 0) > 3:
                direction = "long"
            elif c.get('change_24h_pct', 0) < -3:
                direction = "short"

            scored.append({
                "symbol": c['symbol'],
                "direction_bias": direction,
                "confidence": min(score, 80),
                "reasoning": f"规则降级: 波动率{vol}%, 资金费率{funding}, 24h量${c['volume_24h']/1e6:.0f}M",
                "key_signal": "规则引擎筛选",
                "risk_factor": "无LLM深度分析"
            })

        scored.sort(key=lambda x: x['confidence'], reverse=True)
        return scored[:self._max_symbols]
