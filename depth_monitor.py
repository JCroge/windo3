#!/usr/bin/env python3
"""深度监控 - 30分钟持续测试"""

import asyncio
import ccxt.async_support as ccxt
from datetime import datetime
import json

class DepthMonitor:
    def __init__(self, symbols, duration_minutes=30, interval_seconds=5):
        self.symbols = symbols
        self.duration = duration_minutes * 60
        self.interval = interval_seconds
        self.exchanges = {
            'binance': ccxt.binance(),
            'okx': ccxt.okx()
        }
        self.opportunities = []
        self.checks = 0

    async def fetch_orderbook(self, exchange_name, symbol):
        try:
            exchange = self.exchanges[exchange_name]
            return await exchange.fetch_order_book(symbol, limit=10)
        except Exception as e:
            return None

    def calc_real_price(self, orders, amount_usdt):
        if not orders:
            return None, False

        total_cost = 0
        total_amount = 0
        remaining = amount_usdt

        for order in orders:
            if len(order) < 2:
                continue
            price, amount = order[0], order[1]

            if remaining <= 0:
                break

            cost = min(remaining, price * amount)
            total_cost += cost
            total_amount += cost / price
            remaining -= cost

        if remaining > 0.01:
            return None, False

        return total_cost / total_amount if total_amount > 0 else None, True

    async def check_symbol(self, symbol):
        tasks = [
            self.fetch_orderbook('binance', symbol),
            self.fetch_orderbook('okx', symbol)
        ]
        binance_book, okx_book = await asyncio.gather(*tasks)

        if not binance_book or not okx_book:
            return None

        # 场景1: 币安买 OKX卖
        buy_price, buy_ok = self.calc_real_price(binance_book['asks'], 10)
        sell_price, sell_ok = self.calc_real_price(okx_book['bids'], 10)

        profit1 = None
        if buy_price and sell_price and buy_ok and sell_ok:
            profit1 = (sell_price / buy_price - 1) - 0.002

        # 场景2: OKX买 币安卖
        buy_price2, buy_ok2 = self.calc_real_price(okx_book['asks'], 10)
        sell_price2, sell_ok2 = self.calc_real_price(binance_book['bids'], 10)

        profit2 = None
        if buy_price2 and sell_price2 and buy_ok2 and sell_ok2:
            profit2 = (sell_price2 / buy_price2 - 1) - 0.002

        return {
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            'profit1': profit1,
            'profit2': profit2,
            'max_profit': max(profit1 or -1, profit2 or -1)
        }

    async def monitor(self):
        print("=" * 60)
        print(f"深度监控 - {self.duration//60}分钟测试")
        print(f"检查间隔: {self.interval}秒")
        print(f"币种: {', '.join(self.symbols)}")
        print("=" * 60)

        start_time = datetime.now()

        while (datetime.now() - start_time).total_seconds() < self.duration:
            self.checks += 1

            tasks = [self.check_symbol(s) for s in self.symbols]
            results = await asyncio.gather(*tasks)

            found = False
            for r in results:
                if r and r['max_profit'] >= 0.003:
                    found = True
                    self.opportunities.append(r)
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🎉 {r['symbol']}: {r['max_profit']*100:.3f}%")

            if not found:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 检查 #{self.checks}: 无机会", end='\r')

            await asyncio.sleep(self.interval)

        await self.close()
        self.report()

    async def close(self):
        for ex in self.exchanges.values():
            await ex.close()

    def report(self):
        print(f"\n\n{'='*60}")
        print("监控完成")
        print(f"{'='*60}")
        print(f"总检查: {self.checks}次")
        print(f"发现机会: {len(self.opportunities)}次")
        print(f"机会率: {len(self.opportunities)/self.checks*100:.2f}%")

        if self.opportunities:
            print(f"\n机会详情:")
            for opp in self.opportunities:
                print(f"  {opp['timestamp']} {opp['symbol']}: {opp['max_profit']*100:.3f}%")

            with open('data/depth_monitor_result.json', 'w') as f:
                json.dump({
                    'checks': self.checks,
                    'opportunities': self.opportunities,
                    'rate': len(self.opportunities)/self.checks
                }, f, indent=2)
            print(f"\n详细数据已保存到 data/depth_monitor_result.json")

async def main():
    symbols = ['WIF/USDT', 'TON/USDT', 'DOGE/USDT', 'MEGA/USDT', 'DASH/USDT']
    monitor = DepthMonitor(symbols, duration_minutes=30, interval_seconds=5)
    await monitor.monitor()

if __name__ == '__main__':
    asyncio.run(main())
