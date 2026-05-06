import yaml
from utils.logger import setup_logger

logger = setup_logger('detector')

class ArbitrageDetector:
    def __init__(self, config_path='config.yaml'):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.min_profit_rate = self.config['arbitrage']['min_profit_rate']
        self.fees = self.config['fees']

    def detect(self, tickers):
        opportunities = []

        for symbol in self.config['symbols']:
            # 获取各交易所价格
            prices = {}
            for ticker in tickers:
                if ticker['symbol'] == symbol:
                    prices[ticker['exchange']] = {
                        'bid': ticker['bid'],
                        'ask': ticker['ask']
                    }

            if len(prices) < 2:
                continue

            # 计算所有交易所对之间的套利机会
            exchanges = list(prices.keys())
            for i in range(len(exchanges)):
                for j in range(i+1, len(exchanges)):
                    ex1, ex2 = exchanges[i], exchanges[j]

                    # 方向1: 在ex1买入，在ex2卖出
                    opp1 = self._calculate_opportunity(
                        symbol, ex1, ex2,
                        prices[ex1]['ask'], prices[ex2]['bid']
                    )
                    if opp1:
                        opportunities.append(opp1)

                    # 方向2: 在ex2买入，在ex1卖出
                    opp2 = self._calculate_opportunity(
                        symbol, ex2, ex1,
                        prices[ex2]['ask'], prices[ex1]['bid']
                    )
                    if opp2:
                        opportunities.append(opp2)

        return opportunities

    def _calculate_opportunity(self, symbol, buy_ex, sell_ex, buy_price, sell_price):
        # 计算扣除手续费后的净利润率
        buy_fee = self.fees[buy_ex]
        sell_fee = self.fees[sell_ex]

        net_profit_rate = (sell_price / buy_price - 1) - buy_fee - sell_fee

        if net_profit_rate >= self.min_profit_rate:
            logger.info(f"发现套利机会: {symbol} 在{buy_ex}买入@{buy_price}, 在{sell_ex}卖出@{sell_price}, 净利润率: {net_profit_rate:.4f}")
            return {
                'symbol': symbol,
                'buy_exchange': buy_ex,
                'sell_exchange': sell_ex,
                'buy_price': buy_price,
                'sell_price': sell_price,
                'profit_rate': net_profit_rate
            }
        return None
