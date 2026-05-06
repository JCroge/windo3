#!/usr/bin/env python3
"""WebSocket 30分钟监控 - Top 30币种"""

import asyncio
import sys
sys.path.append('.')

from websocket_monitor import WebSocketMonitor

async def main():
    # Top 30高流动性币种
    symbols = [
        'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT', 'TON/USDT',
        'XRP/USDT', 'BNB/USDT', 'FIL/USDT', 'PEPE/USDT', 'TRX/USDT',
        'DASH/USDT', 'PENGU/USDT', 'ADA/USDT', 'SUI/USDT', 'LINK/USDT',
        'WIF/USDT', 'BCH/USDT', 'ICP/USDT', 'CHIP/USDT', 'LTC/USDT',
        'STX/USDT', 'AVAX/USDT', 'NEAR/USDT', 'MEGA/USDT', 'ENA/USDT',
        'PENDLE/USDT', 'EIGEN/USDT', 'APT/USDT', 'CFX/USDT', 'FET/USDT'
    ]

    monitor = WebSocketMonitor(symbols)
    await monitor.run(duration_minutes=30)

if __name__ == '__main__':
    asyncio.run(main())
