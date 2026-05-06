#!/usr/bin/env python3
"""WebSocket测试 - 1分钟"""

import asyncio
import sys
sys.path.append('.')

from websocket_monitor import WebSocketMonitor

async def test():
    # 测试5个币种
    symbols = ['BTC/USDT', 'ETH/USDT', 'DOGE/USDT', 'TON/USDT', 'SOL/USDT']

    monitor = WebSocketMonitor(symbols)
    await monitor.run(duration_minutes=1)

if __name__ == '__main__':
    asyncio.run(test())
