#!/usr/bin/env python3
"""深度验证器 - 验证套利机会的真实可执行性"""

import asyncio
import ccxt.async_support as ccxt
from datetime import datetime
import sys
sys.path.append('.')

class DepthValidator:
    def __init__(self, exchanges=['binance', 'okx'], trade_amount_usdt=10):
        self.exchanges = {
            'binance': ccxt.binance(),
            'okx': ccxt.okx()
        }
        self.trade_amount = trade_amount_usdt

    async def fetch_orderbook(self, exchange_name, symbol, limit=10):
        """获取订单簿"""
        try:
            exchange = self.exchanges[exchange_name]
            orderbook = await exchange.fetch_order_book(symbol, limit=limit)
            return orderbook
        except Exception as e:
            print(f"❌ {exchange_name} {symbol} 订单簿获取失败: {e}")
            return None

    def calculate_real_price(self, orders, amount_usdt, side='buy'):
        """计算实际成交价（考虑深度）

        Args:
            orders: [[price, amount], ...] 订单簿
            amount_usdt: 交易金额（USDT）
            side: 'buy' 或 'sell'

        Returns:
            (avg_price, filled_amount, depth_sufficient)
        """
        if not orders or len(orders) == 0:
            return None, 0, False

        total_cost = 0
        total_amount = 0
        remaining = amount_usdt

        for order in orders:
            if not isinstance(order, (list, tuple)) or len(order) < 2:
                continue
            price, amount = order[0], order[1]
            if remaining <= 0:
                break

            # 这一档能吃掉多少
            cost_at_this_level = min(remaining, price * amount)
            amount_at_this_level = cost_at_this_level / price

            total_cost += cost_at_this_level
            total_amount += amount_at_this_level
            remaining -= cost_at_this_level

        if remaining > 0.01:  # 深度不足
            return None, total_amount, False

        avg_price = total_cost / total_amount if total_amount > 0 else None
        return avg_price, total_amount, True

    async def validate_opportunity(self, symbol):
        """验证单个币种的套利机会"""
        print(f"\n{'='*60}")
        print(f"验证 {symbol}")
        print(f"{'='*60}")

        # 并行获取两个交易所的订单簿
        tasks = [
            self.fetch_orderbook('binance', symbol),
            self.fetch_orderbook('okx', symbol)
        ]
        results = await asyncio.gather(*tasks)

        binance_book, okx_book = results

        if not binance_book or not okx_book:
            print("❌ 订单簿获取失败")
            return None

        # 计算实际成交价
        # 场景1: 币安买，OKX卖
        binance_buy_price, binance_buy_amt, binance_depth_ok = \
            self.calculate_real_price(binance_book['asks'], self.trade_amount, 'buy')
        okx_sell_price, okx_sell_amt, okx_depth_ok = \
            self.calculate_real_price(okx_book['bids'], self.trade_amount, 'sell')

        # 场景2: OKX买，币安卖
        okx_buy_price, okx_buy_amt, okx_depth_ok2 = \
            self.calculate_real_price(okx_book['asks'], self.trade_amount, 'buy')
        binance_sell_price, binance_sell_amt, binance_depth_ok2 = \
            self.calculate_real_price(binance_book['bids'], self.trade_amount, 'sell')

        # 分析结果
        print(f"\n场景1: 币安买 → OKX卖")
        if binance_buy_price and okx_sell_price:
            profit_rate = (okx_sell_price / binance_buy_price - 1) - 0.002  # 扣除手续费
            print(f"  买入价: {binance_buy_price:.6f} (深度{'✅' if binance_depth_ok else '❌'})")
            print(f"  卖出价: {okx_sell_price:.6f} (深度{'✅' if okx_depth_ok else '❌'})")
            print(f"  利润率: {profit_rate*100:.3f}%")
            if profit_rate >= 0.003:
                print(f"  ✅ 可执行套利")
        else:
            print(f"  ❌ 深度不足")

        print(f"\n场景2: OKX买 → 币安卖")
        if okx_buy_price and binance_sell_price:
            profit_rate = (binance_sell_price / okx_buy_price - 1) - 0.002
            print(f"  买入价: {okx_buy_price:.6f} (深度{'✅' if okx_depth_ok2 else '❌'})")
            print(f"  卖出价: {binance_sell_price:.6f} (深度{'✅' if binance_depth_ok2 else '❌'})")
            print(f"  利润率: {profit_rate*100:.3f}%")
            if profit_rate >= 0.003:
                print(f"  ✅ 可执行套利")
        else:
            print(f"  ❌ 深度不足")

        return {
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            'scenario1': {
                'buy_price': binance_buy_price,
                'sell_price': okx_sell_price,
                'depth_ok': binance_depth_ok and okx_depth_ok,
                'profit_rate': (okx_sell_price / binance_buy_price - 1) - 0.002 if binance_buy_price and okx_sell_price else None
            },
            'scenario2': {
                'buy_price': okx_buy_price,
                'sell_price': binance_sell_price,
                'depth_ok': okx_depth_ok2 and binance_depth_ok2,
                'profit_rate': (binance_sell_price / okx_buy_price - 1) - 0.002 if okx_buy_price and binance_sell_price else None
            }
        }

    async def close(self):
        for exchange in self.exchanges.values():
            await exchange.close()

async def main():
    symbols = ['WIF/USDT', 'TON/USDT', 'DOGE/USDT', 'MEGA/USDT', 'DASH/USDT']

    validator = DepthValidator(trade_amount_usdt=10)

    print("=" * 60)
    print("深度验证测试 - 10 USDT交易量")
    print("=" * 60)

    for symbol in symbols:
        await validator.validate_opportunity(symbol)
        await asyncio.sleep(0.5)  # 避免API限流

    await validator.close()

    print(f"\n{'='*60}")
    print("验证完成")
    print(f"{'='*60}")

if __name__ == '__main__':
    asyncio.run(main())
