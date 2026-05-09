"""多标的数据采集 Agent - 9维度全量版

采集维度（按频率分档）：
- 高频(10s): ticker价格流
- 中频(30s): orderbook深度、爆仓订单
- 低频(60s): K线+资金费率+OI+Taker比+大单+多空比
- 慢频(5min): 4h K线
"""

import asyncio
import time
import os
import calendar
import ccxt
import aiohttp
import feedparser
from dotenv import load_dotenv
from agents.base import BaseAgent

load_dotenv()

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


class MultiDataCollector(BaseAgent):
    name = "multi_data_collector"
    subscriptions = ["symbol_update"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.exchange = None
        self.interval = config.get('interval', '1h')
        self._active_symbols = []
        self._symbol_health = {}
        self._last_kline_time = {}
        self._counters = {"price": 0, "mid": 0, "low": 0, "slow": 0, "news": 0}
        self._max_consecutive_failures = 3
        self._orderbook_cache = {}
        self._liquidation_cache = {}
        self._news_cache = {}  # {symbol: {"headlines": [...], "fetched_at": ts}}

    async def setup(self):
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

        await asyncio.to_thread(self.exchange.load_markets)
        self.logger.info(f"9维度数据采集就绪: {exchange_id}")

    async def on_message(self, msg: dict):
        if msg['type'] == 'symbol_update':
            new_symbols = msg['payload'].get('active_symbols', [])
            added = msg['payload'].get('added', [])
            removed = msg['payload'].get('removed', [])

            for s in removed:
                self._symbol_health.pop(s, None)
                self._last_kline_time.pop(s, None)
                self._orderbook_cache.pop(s, None)
                self._liquidation_cache.pop(s, None)

            # 过滤非加密货币标的（商品期货等）
            CRYPTO_BLACKLIST = {'CL', 'XAU', 'XAG', 'OIL', 'GAS', 'SPX', 'NDX'}
            valid_symbols = []
            for s in new_symbols:
                base = s.split('-')[0].upper()
                if base in CRYPTO_BLACKLIST:
                    self.logger.warning(f"[采集] 标的 {s} 为非加密货币，跳过")
                    continue
                swap_sym = s.replace('-USDT', '/USDT:USDT')
                if swap_sym in self.exchange.markets or s in self.exchange.markets:
                    valid_symbols.append(s)
                else:
                    self.logger.warning(f"[采集] 标的 {s} 在交易所不存在，跳过")

            self._active_symbols = valid_symbols
            self.logger.info(f"[采集] 标的更新: {valid_symbols} +{added} -{removed}")

            for s in added:
                if s not in valid_symbols:
                    continue
                self._init_health(s)
                await self._full_collect(s)
                await asyncio.sleep(0.3)

    async def tick(self):
        await asyncio.sleep(1)
        if not self._active_symbols:
            return

        self._counters["price"] += 1
        self._counters["mid"] += 1
        self._counters["low"] += 1
        self._counters["slow"] += 1
        self._counters["news"] += 1

        if self._counters["price"] >= 10:
            self._counters["price"] = 0
            await self._tick_price()

        if self._counters["mid"] >= 30:
            self._counters["mid"] = 0
            await self._tick_mid()

        if self._counters["low"] >= 60:
            self._counters["low"] = 0
            await self._tick_low()

        if self._counters["slow"] >= 300:
            self._counters["slow"] = 0
            await self._tick_slow()

        # 新闻轮询：每30分钟一次（参考QuantConnect Alpha Streams 4h衰减窗口，30min足够捕获新事件）
        if self._counters["news"] >= 1800:
            self._counters["news"] = 0
            await self._tick_news()

    # ═══ 频率分档 ═══

    async def _tick_price(self):
        for symbol in self._active_symbols:
            await self._fetch_price_tick(symbol)
            await asyncio.sleep(0.2)

    async def _tick_mid(self):
        for symbol in self._active_symbols:
            await self._fetch_orderbook(symbol)
            await asyncio.sleep(0.2)
            await self._fetch_liquidations(symbol)
            await asyncio.sleep(0.2)

    async def _tick_low(self):
        for symbol in self._active_symbols:
            await self._full_collect(symbol)
            await asyncio.sleep(0.5)

    async def _tick_slow(self):
        for symbol in self._active_symbols:
            await self._collect_4h(symbol)
            await self._collect_1d(symbol)
            await asyncio.sleep(0.3)

    async def _tick_news(self):
        """30min新闻轮询：为活跃标的抓取近期新闻，发布news_snapshot供Judge做price-in检测

        设计参考：Freqtrade news-filter的交易层注入模式
        只抓取与活跃标的相关的新闻，不做全量采集，控制延迟
        """
        if not self._active_symbols:
            return

        RSS_FEEDS = [
            ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
            ("Cointelegraph", "https://cointelegraph.com/rss"),
            ("The Block", "https://www.theblock.co/rss.xml"),
        ]
        active_bases = {s.split('-')[0].upper() for s in self._active_symbols}
        all_headlines = []

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": "Mozilla/5.0 CryptoResearchBot/1.0"}
            ) as session:
                for name, url in RSS_FEEDS:
                    try:
                        async with session.get(url) as resp:
                            if resp.status != 200:
                                continue
                            parsed = feedparser.parse(await resp.text())
                            for entry in parsed.entries[:8]:
                                ts = 0
                                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                                    ts = calendar.timegm(entry.published_parsed)
                                all_headlines.append({
                                    "title": entry.get('title', ''),
                                    "source": name,
                                    "published_ts": ts,
                                    "summary": entry.get('summary', '')[:200],
                                })
                    except Exception:
                        continue
        except Exception as e:
            self.logger.warning(f"[新闻轮询] 采集失败: {e}")
            return

        if not all_headlines:
            return

        # 按标的分组，只保留相关新闻
        now = time.time()
        symbol_news = {}
        for base in active_bases:
            relevant = []
            for h in all_headlines:
                text = (h['title'] + ' ' + h.get('summary', '')).upper()
                if base in text or f"${base}" in text:
                    relevant.append(h)
            if relevant:
                symbol_news[base] = relevant

        if not symbol_news:
            return

        self._news_cache = {"symbol_news": symbol_news, "fetched_at": now}

        await self.publish("news_snapshot", {
            "symbol_news": symbol_news,
            "fetched_at": now,
            "active_symbols": self._active_symbols,
        })
        self.logger.info(f"[新闻轮询] 完成: 涉及标的 {list(symbol_news.keys())}")

    # ═══ 完整采集（低频，发布market_data） ═══

    async def _full_collect(self, symbol: str):
        dimensions_ok = 0
        dimensions_total = 9

        # 1. K线
        klines = []
        try:
            klines = self.exchange.fetch_ohlcv(symbol, self.interval, limit=100)
            gaps = self._check_gaps(symbol, klines)
            if gaps > 0:
                klines = self._fill_gaps(symbol, klines)
            if klines:
                self._last_kline_time[symbol] = klines[-1][0]
            dimensions_ok += 1
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} K线失败: {e}")

        # 2. 资金费率(当前)
        funding_rate = None
        try:
            base = symbol.split('-')[0]
            ccxt_sym = f"{base}/USDT:USDT"
            funding = self.exchange.fetch_funding_rate(ccxt_sym)
            funding_rate = funding.get('fundingRate')
            dimensions_ok += 1
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} 资金费率失败: {e}")

        # 3. 资金费率历史
        funding_history = await self._fetch_funding_history(symbol)
        if funding_history:
            dimensions_ok += 1

        # 4. OI delta
        oi_data = await self._fetch_oi_delta(symbol)
        if oi_data:
            dimensions_ok += 1

        # 5. Taker买卖比
        taker_ratio = await self._fetch_taker_ratio(symbol)
        if taker_ratio:
            dimensions_ok += 1

        # 6. 大单成交
        big_trades = await self._fetch_big_trades(symbol)
        if big_trades:
            dimensions_ok += 1

        # 7. 多空账户比
        long_short = await self._fetch_long_short_ratio(symbol)
        if long_short:
            dimensions_ok += 1

        # 8. Orderbook (用缓存，中频已采)
        orderbook = self._orderbook_cache.get(symbol)
        if not orderbook:
            await self._fetch_orderbook(symbol)
            orderbook = self._orderbook_cache.get(symbol)
        if orderbook:
            dimensions_ok += 1

        # 9. 爆仓 (用缓存，中频已采)
        liquidations = self._liquidation_cache.get(symbol)
        if not liquidations:
            await self._fetch_liquidations(symbol)
            liquidations = self._liquidation_cache.get(symbol)
        if liquidations:
            dimensions_ok += 1

        health = self._symbol_health.get(symbol, {})
        klines_4h = health.get('klines_4h', [])
        klines_1d = health.get('klines_1d', [])

        payload = {
            "symbol": symbol,
            "interval": self.interval,
            "klines": klines,
            "klines_4h": klines_4h,
            "klines_1d": klines_1d,
            "funding_rate": funding_rate,
            "funding_history": funding_history or [],
            "latest_price": klines[-1][4] if klines else None,
            "orderbook": orderbook or {},
            "oi_data": oi_data or {},
            "liquidations": liquidations or {},
            "taker_ratio": taker_ratio or {},
            "big_trades": big_trades or {},
            "long_short_account": long_short or {},
            "data_quality": {
                "kline_gaps": self._check_gaps(symbol, klines) if klines else 0,
                "last_success": time.time(),
                "consecutive_failures": 0,
                "dimensions_ok": dimensions_ok,
                "dimensions_total": dimensions_total,
            }
        }

        await self.publish("market_data", payload, symbol=symbol)
        self._record_success(symbol)
        price_str = f"{payload['latest_price']:.2f}" if payload['latest_price'] else "N/A"
        self.logger.info(
            f"[采集] {symbol} 价格={price_str} "
            f"维度={dimensions_ok}/{dimensions_total} K线={len(klines)}根"
        )

    # ═══ 各维度采集方法 ═══

    async def _fetch_price_tick(self, symbol: str):
        try:
            ticker = self.exchange.fetch_ticker(symbol.replace('-USDT', '/USDT:USDT'))
            payload = {
                "symbol": symbol,
                "price": float(ticker.get('last', 0)),
                "timestamp": time.time(),
                "bid": float(ticker.get('bid', 0) or 0),
                "ask": float(ticker.get('ask', 0) or 0),
                "volume_24h": float(ticker.get('quoteVolume', 0) or 0),
            }
            await self.publish("price_tick", payload, symbol=symbol)
        except Exception as e:
            self.logger.warning(f"[价格] {symbol} ticker失败: {e}")

    async def _fetch_orderbook(self, symbol: str):
        try:
            okx_inst = self._to_okx_inst(symbol)
            async with aiohttp.ClientSession() as session:
                url = f"https://www.okx.com/api/v5/market/books?instId={okx_inst}&sz=20"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()

            if data.get('code') == '0' and data.get('data'):
                book = data['data'][0]
                asks = [[float(a[0]), float(a[1])] for a in book.get('asks', [])[:20]]
                bids = [[float(b[0]), float(b[1])] for b in book.get('bids', [])[:20]]

                mid_price = (bids[0][0] + asks[0][0]) / 2 if bids and asks else 0
                spread_pct = ((asks[0][0] - bids[0][0]) / mid_price * 100) if mid_price else 0
                bid_depth = sum(b[0] * b[1] for b in bids)
                ask_depth = sum(a[0] * a[1] for a in asks)

                self._orderbook_cache[symbol] = {
                    "asks": asks,
                    "bids": bids,
                    "spread_pct": round(spread_pct, 4),
                    "bid_depth_usd": round(bid_depth, 2),
                    "ask_depth_usd": round(ask_depth, 2),
                }
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} orderbook失败: {e}")

    async def _fetch_liquidations(self, symbol: str):
        try:
            uly = self._to_okx_uly(symbol)
            async with aiohttp.ClientSession() as session:
                url = (f"https://www.okx.com/api/v5/public/liquidation-orders"
                       f"?instType=SWAP&uly={uly}&state=filled")
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()

            long_vol = 0.0
            short_vol = 0.0
            recent_big = []

            if data.get('code') == '0' and data.get('data'):
                for item in data['data']:
                    details = item.get('details', [])
                    for d in details:
                        side = d.get('side', '')
                        sz = float(d.get('sz', 0))
                        px = float(d.get('bkPx', 0))
                        vol_usd = sz * px

                        if side == 'buy':
                            short_vol += vol_usd
                        else:
                            long_vol += vol_usd

                        if vol_usd > 100_000:
                            recent_big.append({
                                "side": side,
                                "vol_usd": round(vol_usd, 2),
                                "price": px,
                            })

            net_dir = "long_squeezed" if long_vol > short_vol else "short_squeezed"
            self._liquidation_cache[symbol] = {
                "long_vol_usd": round(long_vol, 2),
                "short_vol_usd": round(short_vol, 2),
                "net_direction": net_dir,
                "recent_big": recent_big[-5:],
            }
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} 爆仓数据失败: {e}")

    async def _fetch_funding_history(self, symbol: str) -> list:
        try:
            okx_inst = self._to_okx_inst(symbol)
            async with aiohttp.ClientSession() as session:
                url = (f"https://www.okx.com/api/v5/public/funding-rate-history"
                       f"?instId={okx_inst}&limit=8")
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()

            if data.get('code') == '0' and data.get('data'):
                return [
                    {"rate": float(r['fundingRate']), "time": int(r['fundingTime'])}
                    for r in data['data']
                ]
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} 资金费率历史失败: {e}")
        return []

    async def _fetch_oi_delta(self, symbol: str) -> dict:
        try:
            bn_symbol = self._to_binance_symbol(symbol)
            async with aiohttp.ClientSession() as session:
                url = (f"https://fapi.binance.com/futures/data/openInterestHist"
                       f"?symbol={bn_symbol}&period=5m&limit=48")
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()

            if not data or not isinstance(data, list):
                return {}

            current = float(data[-1]['sumOpenInterestValue'])
            oi_1h_ago = float(data[-12]['sumOpenInterestValue']) if len(data) >= 12 else current
            oi_4h_ago = float(data[0]['sumOpenInterestValue'])

            delta_1h = ((current - oi_1h_ago) / oi_1h_ago * 100) if oi_1h_ago else 0
            delta_4h = ((current - oi_4h_ago) / oi_4h_ago * 100) if oi_4h_ago else 0

            return {
                "current_usd": round(current, 2),
                "delta_1h_pct": round(delta_1h, 2),
                "delta_4h_pct": round(delta_4h, 2),
            }
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} OI数据失败: {e}")
        return {}

    async def _fetch_taker_ratio(self, symbol: str) -> dict:
        try:
            bn_symbol = self._to_binance_symbol(symbol)
            async with aiohttp.ClientSession() as session:
                url = (f"https://fapi.binance.com/futures/data/takerlongshortRatio"
                       f"?symbol={bn_symbol}&period=1h&limit=1")
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()

            if data and isinstance(data, list) and len(data) > 0:
                item = data[0]
                return {
                    "buy_sell_ratio": round(float(item.get('buySellRatio', 1)), 4),
                    "buy_vol": round(float(item.get('buyVol', 0)), 2),
                    "sell_vol": round(float(item.get('sellVol', 0)), 2),
                }
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} Taker比失败: {e}")
        return {}

    async def _fetch_big_trades(self, symbol: str) -> dict:
        try:
            okx_inst = self._to_okx_inst(symbol)
            async with aiohttp.ClientSession() as session:
                url = f"https://www.okx.com/api/v5/market/trades?instId={okx_inst}&limit=100"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()

            if data.get('code') != '0' or not data.get('data'):
                return {}

            trades = data['data']
            sizes = sorted([float(t['sz']) for t in trades])
            threshold = sizes[int(len(sizes) * 0.9)] if sizes else 0

            big_buy_vol = 0.0
            big_sell_vol = 0.0
            for t in trades:
                sz = float(t['sz'])
                if sz >= threshold:
                    px = float(t['px'])
                    if t['side'] == 'buy':
                        big_buy_vol += sz
                    else:
                        big_sell_vol += sz

            big_ratio = (big_buy_vol / big_sell_vol) if big_sell_vol > 0 else 999
            if big_ratio > 1.5:
                whale_dir = "accumulating"
            elif big_ratio < 0.67:
                whale_dir = "distributing"
            else:
                whale_dir = "neutral"

            return {
                "big_buy_vol": round(big_buy_vol, 4),
                "big_sell_vol": round(big_sell_vol, 4),
                "big_ratio": round(big_ratio, 2),
                "whale_direction": whale_dir,
            }
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} 大单数据失败: {e}")
        return {}

    async def _fetch_long_short_ratio(self, symbol: str) -> dict:
        try:
            bn_symbol = self._to_binance_symbol(symbol)
            async with aiohttp.ClientSession() as session:
                url = (f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
                       f"?symbol={bn_symbol}&period=1h&limit=1")
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()

            if data and isinstance(data, list) and len(data) > 0:
                item = data[0]
                long_r = float(item.get('longAccount', 0.5))
                short_r = float(item.get('shortAccount', 0.5))

                if long_r > 0.6:
                    sentiment = "very_bullish"
                elif long_r > 0.5:
                    sentiment = "bullish"
                elif short_r > 0.6:
                    sentiment = "very_bearish"
                elif short_r > 0.5:
                    sentiment = "bearish"
                else:
                    sentiment = "neutral"

                return {
                    "long_ratio": round(long_r, 4),
                    "short_ratio": round(short_r, 4),
                    "crowd_sentiment": sentiment,
                }
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} 多空比失败: {e}")
        return {}

    async def _collect_4h(self, symbol: str):
        try:
            klines_4h = self.exchange.fetch_ohlcv(symbol, '4h', limit=50)
            health = self._symbol_health.get(symbol, {})
            health['klines_4h'] = klines_4h
            self._symbol_health[symbol] = health
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} 4h K线失败: {e}")

    async def _collect_1d(self, symbol: str):
        try:
            klines_1d = self.exchange.fetch_ohlcv(symbol, '1d', limit=30)
            health = self._symbol_health.get(symbol, {})
            health['klines_1d'] = klines_1d
            self._symbol_health[symbol] = health
        except Exception as e:
            self.logger.warning(f"[采集] {symbol} 1d K线失败: {e}")

    # ═══ K线连续性 ═══

    def _check_gaps(self, symbol: str, klines: list) -> int:
        if len(klines) < 2:
            return 0
        interval_ms = INTERVAL_MS.get(self.interval, 3_600_000)
        gaps = 0
        for i in range(1, len(klines)):
            if klines[i][0] - klines[i-1][0] > interval_ms * 1.5:
                gaps += 1
        return gaps

    def _fill_gaps(self, symbol: str, klines: list) -> list:
        last_time = self._last_kline_time.get(symbol)
        if not last_time:
            return klines
        try:
            filled = self.exchange.fetch_ohlcv(
                symbol, self.interval, since=last_time, limit=200
            )
            return filled if filled else klines
        except Exception:
            return klines

    # ═══ 健康监控 ═══

    def _init_health(self, symbol: str):
        self._symbol_health[symbol] = {
            "successes": 0, "failures": 0,
            "consecutive_failures": 0,
            "last_success": None, "last_error": None,
            "klines_4h": [],
            "klines_1d": [],
        }

    def _record_success(self, symbol: str):
        health = self._symbol_health.setdefault(symbol, {})
        health['successes'] = health.get('successes', 0) + 1
        health['consecutive_failures'] = 0
        health['last_success'] = time.time()

    def _record_failure(self, symbol: str, error: Exception):
        health = self._symbol_health.setdefault(symbol, {})
        health['failures'] = health.get('failures', 0) + 1
        health['consecutive_failures'] = health.get('consecutive_failures', 0) + 1
        health['last_error'] = str(error)
        cf = health['consecutive_failures']
        self.logger.error(f"[采集] {symbol} 失败({cf}次): {error}")
        if cf >= self._max_consecutive_failures:
            asyncio.create_task(self._publish_data_alert(symbol, cf, error))

    async def _publish_data_alert(self, symbol: str, count: int, error: Exception):
        await self.publish("data_alert", {
            "symbol": symbol,
            "consecutive_failures": count,
            "last_error": str(error),
            "action": "check_connectivity",
        })

    # ═══ Symbol格式转换 ═══

    def _to_okx_inst(self, symbol: str) -> str:
        """SOL/USDT:USDT → SOL-USDT-SWAP"""
        if '-SWAP' in symbol:
            return symbol
        base = symbol.replace('/USDT:USDT', '').replace('/USDT', '').replace('-USDT', '')
        return f"{base}-USDT-SWAP"

    def _to_okx_uly(self, symbol: str) -> str:
        """SOL/USDT:USDT → SOL-USDT"""
        base = symbol.replace('/USDT:USDT', '').replace('/USDT', '').replace('-USDT', '')
        return f"{base}-USDT"

    def _to_binance_symbol(self, symbol: str) -> str:
        """SOL/USDT:USDT → SOLUSDT"""
        base = symbol.replace('/USDT:USDT', '').replace('/USDT', '').replace('-USDT', '').replace('-', '')
        return f"{base}USDT"
