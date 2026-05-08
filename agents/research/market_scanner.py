"""市场扫描 Agent - 采集Top永续合约的多维度指标"""

import os
import ccxt
import aiohttp
from dotenv import load_dotenv
from agents.base import BaseAgent

load_dotenv()


class MarketScanner(BaseAgent):
    name = "market_scanner"
    subscriptions = ["research_trigger"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.exchange = None
        self.top_n = 50
        self.min_volume_24h = 5_000_000

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

        self.logger.info(f"市场扫描Agent就绪: {self.config.get('exchange', 'okx')}")

    async def on_message(self, msg: dict):
        if msg['type'] == 'research_trigger':
            await self._scan_market()

    async def _scan_market(self):
        try:
            self.logger.info("[扫描] 开始采集市场数据...")
            import asyncio
            loop = asyncio.get_event_loop()
            tickers = await loop.run_in_executor(None, self.exchange.fetch_tickers)

            candidates = []
            for symbol, ticker in tickers.items():
                if not symbol.endswith('/USDT:USDT') and not symbol.endswith('-USDT-SWAP'):
                    continue
                if ':' in symbol:
                    clean_symbol = symbol.split(':')[0].replace('/', '-')
                else:
                    clean_symbol = symbol

                volume_24h = float(ticker.get('quoteVolume', 0) or 0)
                if volume_24h == 0:
                    info = ticker.get('info', {})
                    vol_ccy = float(info.get('volCcy24h', 0) or 0)
                    last_price = float(ticker.get('last', 0) or 0)
                    if vol_ccy > 0 and last_price > 0:
                        volume_24h = vol_ccy * last_price
                    else:
                        base_vol = float(ticker.get('baseVolume', 0) or 0)
                        volume_24h = base_vol * last_price
                if volume_24h < self.min_volume_24h:
                    continue

                high = float(ticker.get('high', 0) or 0)
                low = float(ticker.get('low', 0) or 0)
                last = float(ticker.get('last', 0) or 0)
                change_pct = float(ticker.get('percentage', 0) or 0)

                volatility = ((high - low) / low * 100) if low > 0 else 0

                candidates.append({
                    "symbol": clean_symbol,
                    "raw_symbol": symbol,
                    "price": last,
                    "volume_24h": volume_24h,
                    "volatility_pct": round(volatility, 2),
                    "change_24h_pct": round(change_pct, 2),
                    "high_24h": high,
                    "low_24h": low,
                })

            candidates.sort(key=lambda x: x['volume_24h'], reverse=True)
            top_candidates = candidates[:self.top_n]

            # 简化：只获取前10个的详细数据，避免API超时
            for i, c in enumerate(top_candidates):
                if i < 10:
                    c['funding_rate'] = await self._fetch_funding(c['raw_symbol'])
                    inst_id = c['raw_symbol'].replace('/USDT:USDT', '-USDT-SWAP').replace('/', '-')
                    c['long_short_ratio'] = await self._fetch_long_short_ratio(inst_id)
                    c['open_interest_usd'] = await self._fetch_open_interest(inst_id)
                else:
                    c['funding_rate'] = None
                    c['long_short_ratio'] = None
                    c['open_interest_usd'] = None
                del c['raw_symbol']

            await self.publish("research_market_data", {
                "candidates": top_candidates,
                "total_scanned": len(tickers),
                "filtered": len(candidates),
            })

            self.logger.info(f"[扫描] 完成: {len(tickers)}个合约 → {len(candidates)}个符合条件 → Top{len(top_candidates)}")

        except Exception as e:
            self.logger.error(f"市场扫描失败: {e}")

    async def _fetch_funding(self, symbol: str):
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            funding = await loop.run_in_executor(None, self.exchange.fetch_funding_rate, symbol)
            return funding.get('fundingRate', None)
        except Exception:
            return None

    async def _fetch_long_short_ratio(self, inst_id: str):
        """Binance公开接口：Top Trader多空比（OKX rubik接口已不可用）"""
        try:
            symbol = inst_id.replace('-USDT-SWAP', 'USDT').replace('-', '')
            url = "https://fapi.binance.com/futures/data/topLongShortAccountRatio"
            params = {"symbol": symbol, "period": "1h", "limit": 1}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            if data:
                return float(data[0].get('longShortRatio', 1.0))
            return None
        except Exception:
            return None

    async def _fetch_open_interest(self, inst_id: str):
        """OKX公开接口：持仓量（USD）"""
        try:
            url = "https://www.okx.com/api/v5/public/open-interest"
            params = {"instType": "SWAP", "instId": inst_id}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            records = data.get('data', [])
            if records:
                return float(records[0].get('oiUsd', 0))
            return None
        except Exception:
            return None
