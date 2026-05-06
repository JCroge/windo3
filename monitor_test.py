#!/usr/bin/env python3
"""持续监控套利机会（5分钟测试）"""

import asyncio
import sys
from datetime import datetime
sys.path.append('.')

from core.aggregator import TickerAggregator
from core.detector import ArbitrageDetector

async def monitor():
    agg = TickerAggregator(
        ['binance', 'okx'],
        ['WIF/USDT', 'TON/USDT', 'DOGE/USDT', 'MEGA/USDT', 'DASH/USDT']
    )
    det = ArbitrageDetector()

    print("=" * 60)
    print("持续监控模式 - 5分钟测试")
    print("=" * 60)

    opportunity_count = 0
    check_count = 0

    start_time = datetime.now()

    while (datetime.now() - start_time).seconds < 300:  # 5分钟
        check_count += 1

        tickers = await agg.fetch_all()
        opps = det.detect(tickers)

        if opps:
            opportunity_count += len(opps)
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🎉 发现套利机会:")
            for o in opps:
                print(f"  {o['symbol']}: {o['profit_rate']*100:.3f}% "
                      f"({o['buy_exchange']}→{o['sell_exchange']})")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 检查 #{check_count}: 无机会", end='\r')

        await asyncio.sleep(1)

    print(f"\n\n{'='*60}")
    print(f"监控完成:")
    print(f"  总检查次数: {check_count}")
    print(f"  发现机会: {opportunity_count}次")
    print(f"  机会出现率: {opportunity_count/check_count*100:.1f}%")
    print("=" * 60)

if __name__ == '__main__':
    asyncio.run(monitor())
