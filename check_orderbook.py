#!/usr/bin/env python3
"""检查订单簿数据结构"""

import asyncio
import ccxt.async_support as ccxt

async def check():
    binance = ccxt.binance()

    orderbook = await binance.fetch_order_book('TON/USDT', limit=5)

    print("订单簿结构:")
    print(f"Keys: {orderbook.keys()}")
    print(f"\nAsks (卖单) 前3档:")
    for i, order in enumerate(orderbook['asks'][:3]):
        print(f"  {i}: {order} (type: {type(order)})")

    print(f"\nBids (买单) 前3档:")
    for i, order in enumerate(orderbook['bids'][:3]):
        print(f"  {i}: {order} (type: {type(order)})")

    await binance.close()

asyncio.run(check())
