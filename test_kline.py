#!/usr/bin/env python3
"""测试K线采集器"""

import asyncio
import sys
sys.path.append('.')

from kline_collector import KlineCollector

async def test():
    # 测试采集BTC和ETH的1分钟K线
    collector = KlineCollector(
        symbols=['BTCUSDT', 'ETHUSDT'],
        interval='1m'
    )

    print("开始采集K线数据（按Ctrl+C停止）...")
    await collector.stream()

if __name__ == '__main__':
    try:
        asyncio.run(test())
    except KeyboardInterrupt:
        print("\n✅ 采集已停止")
