import ccxt
from datetime import datetime
import yaml
import json
from utils.logger import setup_logger

logger = setup_logger('coin_selector_v2')

class CoinSelectorV2:
    """
    币种研判Agent V2
    融合了深度研判prompt的精华：
    - 资金费率分析（判断市场情绪）
    - 多维度评分（技术面+情绪面）
    - 风险预警机制
    """

    def __init__(self, config_path='config.yaml'):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.binance = ccxt.binance()
        self.okx = ccxt.okx()

        # 筛选参数
        self.min_volume = 10_000_000
        self.max_volume = 100_000_000
        self.min_price = 0.01

    def analyze(self):
        """分析并返回优质币种"""
        logger.info("=== 币种研判Agent V2 启动 ===")

        # 阶段1：市场环境扫描
        market_env = self._scan_market_environment()
        logger.info(f"市场环境: {market_env['rating']}")

        # 阶段2：获取交易对
        logger.info("阶段2：获取交易对列表")
        binance_tickers = self.binance.fetch_tickers()
        okx_tickers = self.okx.fetch_tickers()

        binance_symbols = set(s for s in binance_tickers.keys() if s.endswith('/USDT'))
        okx_symbols = set(s for s in okx_tickers.keys() if s.endswith('/USDT'))
        common_symbols = binance_symbols & okx_symbols

        logger.info(f"共同交易对: {len(common_symbols)}个")

        # 阶段3：一级筛选
        candidates = self._first_filter(common_symbols, binance_tickers, okx_tickers)
        logger.info(f"一级筛选后: {len(candidates)}个")

        # 阶段4：深度评分（含资金费率）
        scored = self._deep_scoring(candidates, market_env)
        logger.info(f"评分完成: {len(scored)}个")

        # 阶段5：风险过滤
        filtered = self._risk_filter(scored)
        logger.info(f"风险过滤后: {len(filtered)}个")

        return filtered

    def _scan_market_environment(self):
        """扫描市场环境（借鉴prompt的市场环境快速扫描）"""
        try:
            btc_ticker = self.binance.fetch_ticker('BTC/USDT')
            eth_ticker = self.binance.fetch_ticker('ETH/USDT')

            btc_change = btc_ticker.get('percentage', 0) or 0
            eth_change = eth_ticker.get('percentage', 0) or 0

            # 简化的市场环境评级
            avg_change = (btc_change + eth_change) / 2

            if avg_change > 5:
                rating = "极度看涨"
            elif avg_change > 2:
                rating = "看涨"
            elif avg_change > -2:
                rating = "中性"
            elif avg_change > -5:
                rating = "看跌"
            else:
                rating = "极度看跌"

            return {
                'rating': rating,
                'btc_change': btc_change,
                'eth_change': eth_change
            }
        except Exception as e:
            logger.error(f"市场环境扫描失败: {e}")
            return {'rating': '未知', 'btc_change': 0, 'eth_change': 0}

    def _first_filter(self, symbols, b_tickers, o_tickers):
        """一级筛选"""
        candidates = []

        for symbol in symbols:
            b_ticker = b_tickers.get(symbol)
            o_ticker = o_tickers.get(symbol)

            if not b_ticker or not o_ticker:
                continue

            b_volume = b_ticker.get('quoteVolume', 0) or 0
            o_volume = o_ticker.get('quoteVolume', 0) or 0
            avg_volume = (b_volume + o_volume) / 2

            price = b_ticker.get('last', 0) or 0

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

        return candidates

    def _deep_scoring(self, candidates, market_env):
        """深度评分（含资金费率分析）"""
        scored = []

        for candidate in candidates:
            try:
                score_data = self._calculate_score_v2(candidate, market_env)
                if score_data:
                    scored.append(score_data)
            except Exception as e:
                logger.error(f"评分失败 {candidate['symbol']}: {e}")

        scored.sort(key=lambda x: x['total_score'], reverse=True)
        return scored

    def _calculate_score_v2(self, candidate, market_env):
        """计算综合得分V2（加入资金费率）"""
        symbol = candidate['symbol']
        b_ticker = candidate['binance_ticker']
        o_ticker = candidate['okx_ticker']

        # 基础指标
        b_high = b_ticker.get('high', 0) or 0
        b_low = b_ticker.get('low', 0) or 0
        b_volatility = (b_high - b_low) / b_low if b_low > 0 else 0

        volatility_score = min(b_volatility * 500, 20)

        # 价差分析
        b_bid = b_ticker.get('bid', 0) or 0
        o_ask = o_ticker.get('ask', 0) or 0
        max_spread = abs(b_bid - o_ask) / o_ask if o_ask > 0 else 0
        spread_score = min(max_spread * 5000, 25)

        # 交易活跃度
        activity_score = min((candidate['avg_volume'] / 10_000_000) * 2, 10)

        # 价格稳定性
        b_change = abs(b_ticker.get('percentage', 0) or 0)
        if b_change > 20:
            stability_score = 0
        elif b_change > 10:
            stability_score = 5
        else:
            stability_score = 10

        # 流动性均衡度
        b_vol = b_ticker.get('quoteVolume', 0) or 1
        o_vol = o_ticker.get('quoteVolume', 0) or 1
        balance = min(b_vol, o_vol) / max(b_vol, o_vol)
        balance_score = balance * 5

        # 资金费率分析（新增）
        funding_score = self._analyze_funding_rate(symbol)

        total_score = (volatility_score + spread_score + activity_score +
                      stability_score + balance_score + funding_score)

        return {
            'symbol': symbol,
            'total_score': round(total_score, 2),
            'volatility': round(b_volatility * 100, 2),
            'spread': round(max_spread * 100, 4),
            'volume': round(candidate['avg_volume'] / 1_000_000, 2),
            'price': round(candidate['price'], 6),
            'change_24h': round(b_ticker.get('percentage', 0) or 0, 2),
            'funding_rate': funding_score,
            'scores': {
                'volatility': round(volatility_score, 1),
                'spread': round(spread_score, 1),
                'activity': round(activity_score, 1),
                'stability': round(stability_score, 1),
                'balance': round(balance_score, 1),
                'funding': round(funding_score, 1)
            }
        }

    def _analyze_funding_rate(self, symbol):
        """分析资金费率（借鉴prompt的衍生品市场分析）"""
        try:
            market = self.binance.market(symbol)
            if not market.get('swap'):
                return 3
            funding = self.binance.fetch_funding_rate(symbol)
            rate = funding.get('fundingRate', 0) or 0

            # 资金费率评分逻辑
            # 正费率（多头付费）：-5到0分（多头过热，不利套利）
            # 负费率（空头付费）：0到+5分（空头过热，可能反弹）
            # 接近0：+5分（市场均衡，适合套利）

            if abs(rate) < 0.0001:  # 接近0
                return 5
            elif rate > 0:  # 正费率
                return max(0, 5 - rate * 100000)
            else:  # 负费率
                return min(5, 5 + abs(rate) * 50000)

        except Exception:
            # 无永续合约数据，返回中性分
            return 3

    def _risk_filter(self, scored):
        """风险过滤（借鉴prompt的风险预警机制）"""
        filtered = []

        for coin in scored:
            # 风险检查
            risks = []

            # 检查1：极端波动
            if coin['change_24h'] > 30:
                risks.append("24h涨幅>30%，可能存在操纵")
            elif coin['change_24h'] < -30:
                risks.append("24h跌幅>30%，可能存在恐慌")

            # 检查2：价差异常
            if coin['spread'] > 1:
                risks.append("价差>1%，可能存在流动性问题")

            # 检查3：交易量异常
            if coin['volume'] < 10:
                risks.append("交易量<1000万，流动性不足")

            coin['risks'] = risks
            coin['risk_level'] = '高' if len(risks) >= 2 else ('中' if len(risks) == 1 else '低')

            filtered.append(coin)

        return filtered

    def get_recommendations(self, top_n=20):
        """获取推荐币种"""
        results = self.analyze()

        if not results:
            logger.warning("未找到符合条件的币种")
            return []

        top_coins = results[:top_n]

        # 分层
        tier1 = [c for c in top_coins[:5] if c['risk_level'] != '高']
        tier2 = [c for c in top_coins[5:15] if c['risk_level'] != '高']
        tier3 = top_coins[15:20]

        self._save_results(tier1, tier2, tier3)

        return [coin['symbol'] for coin in top_coins]

    def _save_results(self, tier1, tier2, tier3):
        """保存结果"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        data = {
            'timestamp': timestamp,
            'version': 'v2',
            'tier1': tier1,
            'tier2': tier2,
            'tier3': tier3
        }

        with open(f'data/coin_analysis_v2_{timestamp}.json', 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"结果已保存到 data/coin_analysis_v2_{timestamp}.json")
