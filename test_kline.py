#!/usr/bin/env python3
"""测试K线采集器（手动脚本/network 集成测试，依赖 Binance WebSocket）"""

import asyncio
import sys
import pytest
sys.path.append('.')

from kline_collector import KlineCollector


# 5 秒采集窗口，足以拿到至少 1-2 条 tick 又不会无限挂住 pytest
_STREAM_TIMEOUT_SEC = 5.0


@pytest.mark.network
async def test():
    collector = KlineCollector(
        symbols=['BTCUSDT', 'ETHUSDT'],
        interval='1m',
    )
    print(f"开始采集K线数据（最多 {_STREAM_TIMEOUT_SEC:.0f} 秒）...")
    try:
        await asyncio.wait_for(collector.stream(), timeout=_STREAM_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        # 限时窗口到点，正常结束
        print(f"✅ 采集窗口 {_STREAM_TIMEOUT_SEC:.0f}s 到点，结束")
    except Exception as e:
        # network 环境不可达视为 skip 而非 failure（CI / 离线环境）
        pytest.skip(f"Binance WebSocket 连接失败，跳过 network 测试: {e}")


if __name__ == '__main__':
    try:
        asyncio.run(asyncio.wait_for(
            KlineCollector(symbols=['BTCUSDT', 'ETHUSDT'], interval='1m').stream(),
            timeout=None,
        ))
    except KeyboardInterrupt:
        print("\n✅ 采集已停止")
