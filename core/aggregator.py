import ccxt
import asyncio
from utils.logger import setup_logger
from utils.database import Database

logger = setup_logger('aggregator')

class TickerAggregator:
    def __init__(self, exchanges, symbols):
        self.exchanges = {
            'binance': ccxt.binance(),
            'okx': ccxt.okx()
        }
        self.symbols = symbols
        self.db = Database()
        self.latest_tickers = {}

    async def fetch_ticker(self, exchange_name, symbol):
        try:
            exchange = self.exchanges[exchange_name]
            ticker = exchange.fetch_ticker(symbol)

            bid = ticker['bid']
            ask = ticker['ask']

            self.latest_tickers[f"{exchange_name}_{symbol}"] = {
                'bid': bid,
                'ask': ask,
                'timestamp': ticker['timestamp']
            }

            self.db.insert_ticker(exchange_name, symbol, bid, ask)
            logger.info(f"{exchange_name} {symbol} - Bid: {bid}, Ask: {ask}")

            return {'exchange': exchange_name, 'symbol': symbol, 'bid': bid, 'ask': ask}
        except Exception as e:
            logger.error(f"Error fetching {exchange_name} {symbol}: {e}")
            return None

    async def fetch_all(self):
        tasks = []
        for exchange_name in self.exchanges.keys():
            for symbol in self.symbols:
                tasks.append(self.fetch_ticker(exchange_name, symbol))

        results = await asyncio.gather(*tasks)
        return [r for r in results if r]

    def get_latest(self, exchange, symbol):
        key = f"{exchange}_{symbol}"
        return self.latest_tickers.get(key)
