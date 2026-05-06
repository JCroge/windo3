import ccxt
import asyncio
from datetime import datetime
import yaml
import json
from utils.logger import setup_logger

logger = setup_logger('coin_selector')

class CoinSelector:
    def __init__(self, config_path='config.yaml'):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.binance = ccxt.binance()
        self.okx = ccxt.okx()

        # 筛选参数
        self.min_volume = 10_000_000  # 1000万美元
        self.max_volume = 100_000_000  # 1亿美元
        self.min_price = 0.01
        self.min_volatility = 0.02  # 2%

    def analyze(self):
        """分析并返回优质币种"""
        logger.info("开始币种研判...")

        # 阶段1：获取交易对
        logger.info("阶段1：获取交易对列表")
        binance_tickers = self.binance.fetch_tickers()
        okx_tickers = self.okx.fetch_tickers()

        binance_symbols = set(s for s in binance_tickers.keys() if s.endswith('/USDT'))
        okx_symbols = set(s for s in okx_tickers.keys() if s.endswith('/USDT'))
        common_symbols = binance_symbols & okx_symbols

        logger.info(f"Binance: {len(binance_symbols)}个, OKX: {len(okx_symbols)}个, 共同: {len(common_symbols)}个")

        # 阶段2：一级筛选
        logger.info("阶段2：一级筛选")
        candidates = []

        for symbol in common_symbols:
            b_ticker = binance_tickers.get(symbol)
            o_ticker = okx_tickers.get(symbol)

            if not b_ticker or not o_ticker:
                continue

            # 交易量（取两个交易所的平均）
            b_volume = b_ticker.get('quoteVolume', 0) or 0
            o_volume = o_ticker.get('quoteVolume', 0) or 0
            avg_volume = (b_volume + o_volume) / 2

            # 价格
            price = b_ticker.get('last', 0) or 0

            # 一级过滤
            if not (self.min_volume <= avg_volume <= self.max_volume):
                continue
            if price < self.min_price:
                continue

            candidates.append({
                'symbol': symbol,
                'binance_ticker': b_ticker,
                'okx_ticker': o_ticker,
                'avg_volume': avg_volume,
                'price': price
            })

        logger.info(f"一级筛选后: {len(candidates)}个候选币种")

        # 阶段3：深度评分
        logger.info("阶段3：深度评分")
        scored = []

        for candidate in candidates:
            score_data = self._calculate_score(candidate)
            if score_data:
                scored.append(score_data)

        # 排序
        scored.sort(key=lambda x: x['total_score'], reverse=True)

        logger.info(f"评分完成，共{len(scored)}个币种")

        return scored

    def _calculate_score(self, candidate):
        """计算综合得分"""
        symbol = candidate['symbol']
        b_ticker = candidate['binance_ticker']
        o_ticker = candidate['okx_ticker']

        try:
            # 指标1：波动率（日内振幅）
            b_high = b_ticker.get('high', 0) or 0
            b_low = b_ticker.get('low', 0) or 0
            b_volatility = (b_high - b_low) / b_low if b_low > 0 else 0

            volatility_score = min(b_volatility * 500, 20)  # 最高20分

            # 指标2：当前价差
            b_bid = b_ticker.get('bid', 0) or 0
            b_ask = b_ticker.get('ask', 0) or 0
            o_bid = o_ticker.get('bid', 0) or 0
            o_ask = o_ticker.get('ask', 0) or 0

            if b_bid > 0 and o_ask > 0:
                spread_1 = (b_bid - o_ask) / o_ask
            else:
                spread_1 = 0

            if o_bid > 0 and b_ask > 0:
                spread_2 = (o_bid - b_ask) / b_ask
            else:
                spread_2 = 0

            max_spread = max(abs(spread_1), abs(spread_2))
            spread_score = min(max_spread * 5000, 25)  # 最高25分

            # 指标3：交易活跃度
            b_volume = candidate['avg_volume']
            activity_score = min((b_volume / 10_000_000) * 2, 10)  # 最高10分

            # 指标4：价格稳定性（涨跌幅不要太极端）
            b_change = abs(b_ticker.get('percentage', 0) or 0)
            if b_change > 20:  # 涨跌超过20%，扣分
                stability_score = 0
            elif b_change > 10:
                stability_score = 5
            else:
                stability_score = 10

            # 指标5：流动性均衡度
            b_vol = b_ticker.get('quoteVolume', 0) or 1
            o_vol = o_ticker.get('quoteVolume', 0) or 1
            balance = min(b_vol, o_vol) / max(b_vol, o_vol)
            balance_score = balance * 5  # 最高5分

            total_score = volatility_score + spread_score + activity_score + stability_score + balance_score

            return {
                'symbol': symbol,
                'total_score': round(total_score, 2),
                'volatility': round(b_volatility * 100, 2),
                'spread': round(max_spread * 100, 4),
                'volume': round(candidate['avg_volume'] / 1_000_000, 2),
                'price': round(candidate['price'], 6),
                'change_24h': round(b_ticker.get('percentage', 0) or 0, 2),
                'scores': {
                    'volatility': round(volatility_score, 1),
                    'spread': round(spread_score, 1),
                    'activity': round(activity_score, 1),
                    'stability': round(stability_score, 1),
                    'balance': round(balance_score, 1)
                }
            }
        except Exception as e:
            logger.error(f"计算{symbol}得分失败: {e}")
            return None

    def get_recommendations(self, top_n=20):
        """获取推荐币种列表"""
        results = self.analyze()

        if not results:
            logger.warning("未找到符合条件的币种")
            return []

        top_coins = results[:top_n]

        # 分层
        tier1 = top_coins[:5]
        tier2 = top_coins[5:15]
        tier3 = top_coins[15:20]

        # 保存结果
        self._save_results(tier1, tier2, tier3)

        # 返回所有推荐币种的symbol列表
        return [coin['symbol'] for coin in top_coins]

    def _save_results(self, tier1, tier2, tier3):
        """保存分析结果"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # JSON格式
        data = {
            'timestamp': timestamp,
            'tier1': tier1,
            'tier2': tier2,
            'tier3': tier3
        }

        with open(f'data/coin_analysis_{timestamp}.json', 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"结果已保存到 data/coin_analysis_{timestamp}.json")
