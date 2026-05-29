#!/usr/bin/env python3
"""测试技术指标计算"""

import sqlite3
import pandas as pd
from indicators import TechnicalIndicators

def load_klines(symbol='BTCUSDT', interval='1m', limit=100):
    """从数据库加载K线数据"""
    conn = sqlite3.connect('data/klines.db')

    query = '''
        SELECT open_time, open, high, low, close, volume
        FROM klines
        WHERE symbol = ? AND interval = ?
        ORDER BY open_time DESC
        LIMIT ?
    '''

    df = pd.read_sql_query(query, conn, params=(symbol, interval, limit))
    conn.close()

    # 按时间正序排列
    df = df.sort_values('open_time').reset_index(drop=True)

    return df

import pytest

@pytest.mark.network
def test_indicators(klines_db):
    """测试所有技术指标（依赖 data/klines.db；缺则 skip）"""
    print("加载K线数据...")
    df = load_klines('BTCUSDT', '1m', 100)

    if len(df) == 0:
        pytest.skip("klines.db 没有 BTCUSDT 1m 数据，请先运行 `python3 test_kline.py` 采集")

    print(f"✅ 加载了 {len(df)} 条K线数据")
    print(f"时间范围: {df['open_time'].min()} - {df['open_time'].max()}")
    print(f"价格范围: {df['close'].min():.2f} - {df['close'].max():.2f}")
    print()

    print("计算技术指标...")
    df = TechnicalIndicators.add_all_indicators(df)

    # 显示最新的指标值
    latest = df.iloc[-1]
    print("\n=== 最新技术指标 ===")
    print(f"收盘价: {latest['close']:.2f}")
    print(f"\nMA指标:")
    print(f"  MA5:  {latest['ma_5']:.2f}")
    print(f"  MA10: {latest['ma_10']:.2f}")
    print(f"  MA20: {latest['ma_20']:.2f}")
    print(f"\nMACD指标:")
    print(f"  MACD:      {latest['macd']:.2f}")
    print(f"  Signal:    {latest['macd_signal']:.2f}")
    print(f"  Histogram: {latest['macd_histogram']:.2f}")
    print(f"\nRSI: {latest['rsi']:.2f}")
    print(f"\n布林带:")
    print(f"  上轨: {latest['bb_upper']:.2f}")
    print(f"  中轨: {latest['bb_middle']:.2f}")
    print(f"  下轨: {latest['bb_lower']:.2f}")

    print("\n✅ 技术指标计算完成")

if __name__ == '__main__':
    test_indicators()
