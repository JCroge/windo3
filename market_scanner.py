#!/usr/bin/env python3
"""全市场扫描 - 数据驱动选币"""

import asyncio
import ccxt.async_support as ccxt
from datetime import datetime
import json

class MarketScanner:
    def __init__(self, min_volume=1_000_000, duration_minutes=30, interval_seconds=5):
        self.min_volume = min_volume
        self.duration = duration_minutes * 60
        self.interval = interval_seconds
        self.exchanges = {
            'binance': ccxt.binance(),
            'okx': ccxt.okx()
        }
        self.symbols = []
        self.opportunities = []
        self.checks = 0

    async def init_symbols(self):
        """获取所有可监控币种"""
        print("获取币种列表...")
        b_tickers = await self.exchanges['binance'].fetch_tickers()
        o_tickers = await self.exchanges['okx'].fetch_tickers()

        b_symbols = set(s for s in b_tickers.keys() if s.endswith('/USDT'))
        o_symbols = set(s for s in o_tickers.keys() if s.endswith('/USDT'))
        common = b_symbols & o_symbols

        for symbol in common:
            b_vol = b_tickers[symbol].get('quoteVolume', 0) or 0
            o_vol = o_tickers[symbol].get('quoteVolume', 0) or 0
            avg_vol = (b_vol + o_vol) / 2

            if avg_vol > self.min_volume:
                self.symbols.append(symbol)

        print(f"监控币种: {len(self.symbols)}个")

    async def quick_scan(self):
        """快速扫描（ticker）"""
        tasks = [
            self.exchanges['binance'].fetch_tickers(),
            self.exchanges['okx'].fetch_tickers()
        ]
        b_tickers, o_tickers = await asyncio.gather(*tasks)

        suspects = []
        for symbol in self.symbols:
            b_ticker = b_tickers.get(symbol)
            o_ticker = o_tickers.get(symbol)

            if not b_ticker or not o_ticker:
                continue

            # 场景1: 币安买 OKX卖
            b_ask = b_ticker.get('ask', 0) or 0
            o_bid = o_ticker.get('bid', 0) or 0
            if b_ask > 0 and o_bid > 0:
                profit1 = (o_bid / b_ask - 1) - 0.002
                if profit1 >= 0.003:
                    suspects.append((symbol, profit1, 'binance_buy'))

            # 场景2: OKX买 币安卖
            o_ask = o_ticker.get('ask', 0) or 0
            b_bid = b_ticker.get('bid', 0) or 0
            if o_ask > 0 and b_bid > 0:
                profit2 = (b_bid / o_ask - 1) - 0.002
                if profit2 >= 0.003:
                    suspects.append((symbol, profit2, 'okx_buy'))

        return suspects

    async def verify_depth(self, symbol, direction):
        """验证orderbook深度"""
        try:
            tasks = [
                self.exchanges['binance'].fetch_order_book(symbol, limit=10),
                self.exchanges['okx'].fetch_order_book(symbol, limit=10)
            ]
            b_book, o_book = await asyncio.gather(*tasks)

            if direction == 'binance_buy':
                buy_price = self._calc_avg_price(b_book['asks'], 10)
                sell_price = self._calc_avg_price(o_book['bids'], 10)
            else:
                buy_price = self._calc_avg_price(o_book['asks'], 10)
                sell_price = self._calc_avg_price(b_book['bids'], 10)

            if buy_price and sell_price:
                real_profit = (sell_price / buy_price - 1) - 0.002
                return real_profit if real_profit >= 0.003 else None
            return None
        except:
            return None

    def _calc_avg_price(self, orders, amount_usdt):
        """计算加权平均成交价"""
        if not orders:
            return None

        total_cost = 0
        remaining = amount_usdt

        for order in orders:
            if len(order) < 2 or remaining <= 0:
                break
            price, amount = order[0], order[1]
            cost = min(remaining, price * amount)
            total_cost += cost
            remaining -= cost

        if remaining > 0.01:
            return None
        return total_cost / amount_usdt

    async def scan(self):
        """主扫描循环"""
        await self.init_symbols()

        print("=" * 60)
        print(f"全市场扫描 - {self.duration//60}分钟")
        print(f"检查间隔: {self.interval}秒")
        print("=" * 60)

        start_time = datetime.now()

        while (datetime.now() - start_time).total_seconds() < self.duration:
            self.checks += 1

            # 阶段1: 快速扫描
            suspects = await self.quick_scan()

            # 阶段2: 深度验证
            if suspects:
                for symbol, ticker_profit, direction in suspects:
                    real_profit = await self.verify_depth(symbol, direction)
                    if real_profit:
                        opp = {
                            'symbol': symbol,
                            'timestamp': datetime.now().isoformat(),
                            'profit': real_profit,
                            'direction': direction
                        }
                        self.opportunities.append(opp)
                        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🎉 {symbol}: {real_profit*100:.3f}% ({direction})")

            if not suspects or not any(self.opportunities):
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 扫描 #{self.checks}: 无机会", end='\r')

            await asyncio.sleep(self.interval)

        await self.close()
        self.report()

    async def close(self):
        for ex in self.exchanges.values():
            await ex.close()

    def report(self):
        print(f"\n\n{'='*60}")
        print("扫描完成")
        print(f"{'='*60}")
        print(f"总扫描: {self.checks}次")
        print(f"发现机会: {len(self.opportunities)}次")
        if self.checks > 0:
            print(f"机会率: {len(self.opportunities)/self.checks*100:.2f}%")

        if self.opportunities:
            # 统计币种分布
            coin_stats = {}
            for opp in self.opportunities:
                symbol = opp['symbol']
                coin_stats[symbol] = coin_stats.get(symbol, 0) + 1

            print(f"\n机会分布（按币种）:")
            for symbol, count in sorted(coin_stats.items(), key=lambda x: x[1], reverse=True):
                print(f"  {symbol}: {count}次")

            # 保存详细数据
            with open('data/market_scan_result.json', 'w') as f:
                json.dump({
                    'checks': self.checks,
                    'opportunities': self.opportunities,
                    'coin_stats': coin_stats,
                    'rate': len(self.opportunities)/self.checks
                }, f, indent=2)
            print(f"\n详细数据已保存到 data/market_scan_result.json")

async def main():
    scanner = MarketScanner(min_volume=1_000_000, duration_minutes=30, interval_seconds=5)
    await scanner.scan()

if __name__ == '__main__':
    asyncio.run(main())

