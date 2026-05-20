"""新闻研究 Agent - RSS抓取加密货币新闻，提取币种提及"""

import asyncio
import time
import aiohttp
import feedparser
from agents.base import BaseAgent

RSS_FEEDS = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "The Block", "url": "https://www.theblock.co/rss.xml"},
    {"name": "CryptoSlate", "url": "https://cryptoslate.com/feed/"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/feed"},
]

KNOWN_SYMBOLS = [
    "BTC", "ETH", "SOL", "DOGE", "WIF", "PEPE", "ARB", "OP",
    "AVAX", "MATIC", "LINK", "UNI", "AAVE", "SUI", "APT",
    "TIA", "SEI", "INJ", "FET", "RNDR", "WLD", "JUP",
    "TON", "XRP", "ADA", "DOT", "ATOM", "NEAR", "FIL",
    "STX", "ORDI", "BONK", "FLOKI", "SHIB", "LTC",
]


class NewsResearcher(BaseAgent):
    name = "news_researcher"
    subscriptions = ["research_trigger"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._fetch_timeout = 15
        self._max_articles_per_feed = 10
        self._current_cycle_id = None

    async def setup(self):
        self.logger.info("新闻研究Agent就绪")

    async def on_message(self, msg: dict):
        if msg['type'] == 'research_trigger':
            self._current_cycle_id = msg.get('payload', {}).get('cycle_id')
            await self._fetch_news()

    async def _fetch_news(self):
        self.logger.info("[新闻] 开始采集...")
        all_headlines = []

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._fetch_timeout),
            headers={"User-Agent": "Mozilla/5.0 CryptoResearchBot/1.0"}
        ) as session:
            tasks = [self._fetch_feed(session, feed) for feed in RSS_FEEDS]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.warning(f"[新闻] {RSS_FEEDS[i]['name']} 采集失败: {result}")
                continue
            all_headlines.extend(result)

        all_headlines.sort(key=lambda x: x.get('published_ts', 0), reverse=True)

        symbol_mentions = self._extract_symbol_mentions(all_headlines)

        await self.publish("research_news_data", {
            "headlines": all_headlines[:30],
            "symbol_mentions": symbol_mentions,
            "sources_ok": sum(1 for r in results if not isinstance(r, Exception)),
            "sources_total": len(RSS_FEEDS),
            "cycle_id": self._current_cycle_id,
        })

        self.logger.info(
            f"[新闻] 完成: {len(all_headlines)}条头条, "
            f"提及币种: {list(symbol_mentions.keys())[:10]}"
        )

    async def _fetch_feed(self, session: aiohttp.ClientSession, feed: dict) -> list:
        url = feed['url']
        name = feed['name']

        async with session.get(url) as resp:
            if resp.status != 200:
                self.logger.warning(f"[新闻] {name} HTTP {resp.status}")
                return []
            content = await resp.text()

        parsed = feedparser.parse(content)
        headlines = []

        for entry in parsed.entries[:self._max_articles_per_feed]:
            published_ts = 0
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                import calendar
                published_ts = calendar.timegm(entry.published_parsed)

            headlines.append({
                "title": entry.get('title', ''),
                "source": name,
                "link": entry.get('link', ''),
                "published_ts": published_ts,
                "summary": entry.get('summary', '')[:200],
            })

        return headlines

    def _extract_symbol_mentions(self, headlines: list) -> dict:
        """统计各币种在新闻中被提及的次数和上下文"""
        mentions = {}

        for article in headlines:
            text = f"{article['title']} {article.get('summary', '')}".upper()

            for symbol in KNOWN_SYMBOLS:
                if symbol in text or f"${symbol}" in text:
                    if symbol not in mentions:
                        mentions[symbol] = {"count": 0, "headlines": []}
                    mentions[symbol]["count"] += 1
                    if len(mentions[symbol]["headlines"]) < 3:
                        mentions[symbol]["headlines"].append(article['title'])

        mentions = dict(sorted(mentions.items(), key=lambda x: x[1]['count'], reverse=True))
        return mentions
