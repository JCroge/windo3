"""市场情绪研究 Agent - 恐贪指数、社交热度、Taker买卖比"""

import aiohttp
import asyncio
from agents.base import BaseAgent


class SentimentResearcher(BaseAgent):
    name = "sentiment_researcher"
    subscriptions = ["research_trigger"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._timeout = 15
        self._current_cycle_id = None

    async def setup(self):
        self.logger.info("市场情绪研究Agent就绪")

    async def on_message(self, msg: dict):
        if msg['type'] == 'research_trigger':
            self._current_cycle_id = msg.get('payload', {}).get('cycle_id')
            await self._research_sentiment()

    async def _research_sentiment(self):
        self.logger.info("[情绪] 开始采集市场情绪数据...")

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
            headers={"User-Agent": "Mozilla/5.0 CryptoResearchBot/1.0"}
        ) as session:
            tasks = [
                self._fetch_fear_greed(session),
                self._fetch_trending(session),
                self._fetch_taker_ratio(session),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        fear_greed = results[0] if not isinstance(results[0], Exception) else None
        trending = results[1] if not isinstance(results[1], Exception) else []
        taker_ratios = results[2] if not isinstance(results[2], Exception) else {}

        if isinstance(results[0], Exception):
            self.logger.warning(f"[情绪] 恐贪指数获取失败: {results[0]}")
        if isinstance(results[1], Exception):
            self.logger.warning(f"[情绪] CoinGecko热度获取失败: {results[1]}")
        if isinstance(results[2], Exception):
            self.logger.warning(f"[情绪] Taker比获取失败: {results[2]}")

        await self.publish("research_sentiment_data", {
            "fear_greed": fear_greed,
            "trending_coins": trending,
            "taker_ratios": taker_ratios,
            "cycle_id": self._current_cycle_id,
        })

        fg_val = fear_greed.get('value', '?') if fear_greed else '?'
        fg_label = fear_greed.get('classification', '?') if fear_greed else '?'
        self.logger.info(
            f"[情绪] 完成: 恐贪={fg_val}({fg_label}), "
            f"热门币={len(trending)}个, Taker比={len(taker_ratios)}个标的"
        )

    async def _fetch_fear_greed(self, session: aiohttp.ClientSession) -> dict:
        """Alternative.me 恐贪指数"""
        url = "https://api.alternative.me/fng/?limit=7"
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()

        records = data.get('data', [])
        if not records:
            return None

        current = records[0]
        history = [{"value": int(r['value']), "date": r.get('timestamp', '')} for r in records[:7]]

        return {
            "value": int(current['value']),
            "classification": current.get('value_classification', ''),
            "history_7d": history,
        }

    async def _fetch_trending(self, session: aiohttp.ClientSession) -> list:
        """CoinGecko 热门币种"""
        url = "https://api.coingecko.com/api/v3/search/trending"
        async with session.get(url) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()

        coins = data.get('coins', [])
        trending = []
        for item in coins[:15]:
            coin = item.get('item', {})
            trending.append({
                "symbol": coin.get('symbol', '').upper(),
                "name": coin.get('name', ''),
                "market_cap_rank": coin.get('market_cap_rank'),
                "score": coin.get('score', 0),
            })
        return trending

    async def _fetch_taker_ratio(self, session: aiohttp.ClientSession) -> dict:
        """Binance Taker买卖比（Top标的）"""
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT",
                   "BNBUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "ARBUSDT"]
        ratios = {}

        for symbol in symbols:
            try:
                url = "https://fapi.binance.com/futures/data/takerlongshortRatio"
                params = {"symbol": symbol, "period": "1h", "limit": 1}
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                if data:
                    record = data[0]
                    ratios[symbol.replace("USDT", "-USDT")] = {
                        "buy_sell_ratio": float(record.get('buySellRatio', 1.0)),
                        "buy_vol": float(record.get('buyVol', 0)),
                        "sell_vol": float(record.get('sellVol', 0)),
                    }
                await asyncio.sleep(0.2)
            except Exception:
                continue

        return ratios
