"""数据采集 Agent - 获取K线、资金费率等市场数据"""

import asyncio
import os
import ccxt
from dotenv import load_dotenv
from agents.base import BaseAgent

load_dotenv()


class DataCollectorAgent(BaseAgent):
    name = "data_collector"
    subscriptions = []

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.exchange = None
        self.symbol = config.get('symbol', 'BTC-USDT')
        self.interval = config.get('interval', '1h')
        self.check_interval = 60

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

        self.logger.info(f"交易所连接: {exchange_id} {self.symbol}")

    async def on_message(self, msg: dict):
        pass

    async def tick(self):
        await asyncio.sleep(self.check_interval)
        await self._collect_and_publish()

    async def _collect_and_publish(self):
        try:
            klines = self.exchange.fetch_ohlcv(self.symbol, self.interval, limit=100)
            funding = self._fetch_funding_rate()

            payload = {
                "symbol": self.symbol,
                "interval": self.interval,
                "klines": klines,
                "funding_rate": funding,
                "latest_price": klines[-1][4] if klines else None,
            }

            await self.publish("market_data", payload)
            self.logger.info(f"[采集] {self.symbol} 价格={payload['latest_price']:.2f} "
                           f"K线={len(klines)}根 资金费率={funding}")

        except Exception as e:
            self.logger.error(f"数据采集失败: {e}")

    def _fetch_funding_rate(self):
        try:
            market = self.exchange.market(self.symbol)
            if not market.get('swap'):
                return None
            funding = self.exchange.fetch_funding_rate(self.symbol)
            return funding.get('fundingRate', None)
        except Exception:
            return None
