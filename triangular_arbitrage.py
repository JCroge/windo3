#!/usr/bin/env python3
"""三角套利检测器"""

import ccxt
from itertools import combinations

class TriangularArbitrage:
    """
    三角套利原理：
    例如 BTC/USDT, ETH/USDT, ETH/BTC
    路径：USDT → BTC → ETH → USDT
    如果最终回到USDT的金额 > 初始金额 * (1 + 手续费)，就有套利机会
    """

    def __init__(self, exchange_name='binance'):
        if exchange_name == 'binance':
            self.exchange = ccxt.binance()
        else:
            self.exchange = ccxt.okx()

        self.fee = 0.001  # 0.1% 手续费
        self.min_profit = 0.003  # 最小利润率0.3%

    def find_triangles(self, base_currency='USDT'):
        """找出所有可能的三角套利组合"""
        print(f"获取{self.exchange.id}交易对...")
        markets = self.exchange.load_markets()

        # 找出所有与base_currency相关的交易对
        base_pairs = {}  # {coin: symbol}
        for symbol, market in markets.items():
            if market['quote'] == base_currency and market['active']:
                base_pairs[market['base']] = symbol

        print(f"找到{len(base_pairs)}个{base_currency}交易对")

        # 找出所有可能的三角组合
        triangles = []
        coins = list(base_pairs.keys())

        for coin1, coin2 in combinations(coins, 2):
            # 检查是否存在 coin1/coin2 或 coin2/coin1
            pair1 = f"{coin1}/{coin2}"
            pair2 = f"{coin2}/{coin1}"

            if pair1 in markets and markets[pair1]['active']:
                triangles.append({
                    'base': base_currency,
                    'coin1': coin1,
                    'coin2': coin2,
                    'pair_base_coin1': base_pairs[coin1],  # USDT/coin1
                    'pair_base_coin2': base_pairs[coin2],  # USDT/coin2
                    'pair_coin1_coin2': pair1,  # coin1/coin2
                    'direction': 'forward'  # coin1是base
                })
            elif pair2 in markets and markets[pair2]['active']:
                triangles.append({
                    'base': base_currency,
                    'coin1': coin1,
                    'coin2': coin2,
                    'pair_base_coin1': base_pairs[coin1],
                    'pair_base_coin2': base_pairs[coin2],
                    'pair_coin1_coin2': pair2,  # coin2/coin1
                    'direction': 'reverse'  # coin2是base
                })

        print(f"找到{len(triangles)}个三角套利组合")
        return triangles

    def calculate_profit(self, triangle, tickers):
        """计算三角套利利润率"""
        try:
            # 获取三个交易对的价格
            p1 = tickers[triangle['pair_base_coin1']]  # USDT/coin1
            p2 = tickers[triangle['pair_base_coin2']]  # USDT/coin2
            p3 = tickers[triangle['pair_coin1_coin2']]  # coin1/coin2 或 coin2/coin1

            if not p1 or not p2 or not p3:
                return None

            # 路径1: USDT → coin1 → coin2 → USDT
            # 买coin1, 买coin2(用coin1), 卖coin2(换USDT)
            if triangle['direction'] == 'forward':
                # coin1/coin2 (coin1是base)
                # 1 USDT → 1/p1['ask'] coin1 → (1/p1['ask']) * p3['bid'] coin2 → (1/p1['ask']) * p3['bid'] * p2['bid'] USDT
                profit1 = (1 / p1['ask']) * p3['bid'] * p2['bid'] - 1 - 3 * self.fee
            else:
                # coin2/coin1 (coin2是base)
                # 1 USDT → 1/p1['ask'] coin1 → (1/p1['ask']) / p3['ask'] coin2 → (1/p1['ask']) / p3['ask'] * p2['bid'] USDT
                profit1 = (1 / p1['ask']) / p3['ask'] * p2['bid'] - 1 - 3 * self.fee

            # 路径2: USDT → coin2 → coin1 → USDT
            if triangle['direction'] == 'forward':
                profit2 = (1 / p2['ask']) / p3['ask'] * p1['bid'] - 1 - 3 * self.fee
            else:
                profit2 = (1 / p2['ask']) * p3['bid'] * p1['bid'] - 1 - 3 * self.fee

            return max(profit1, profit2) if max(profit1, profit2) > 0 else None

        except Exception as e:
            return None

    def check_opportunities(self, triangles):
        """检测套利机会"""
        print("获取实时价格...")
        tickers = self.exchange.fetch_tickers()

        opportunities = []
        for triangle in triangles:
            profit = self.calculate_profit(triangle, tickers)
            if profit and profit >= self.min_profit:
                opportunities.append({
                    'triangle': triangle,
                    'profit': profit
                })

        return opportunities

