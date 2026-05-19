#!/usr/bin/env python3
"""测试趋势跟踪策略"""

import sqlite3
import pandas as pd
from strategy_trend import TrendFollowingStrategy


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

    df = df.sort_values('open_time').reset_index(drop=True)
    return df


import pytest

@pytest.mark.network
def test_strategy():
    """测试策略信号生成"""
    print("加载K线数据...")
    df = load_klines('BTCUSDT', '1m', 100)

    if len(df) < 30:
        print(f"❌ 数据不足：只有{len(df)}条K线，需要至少30条")
        print("请运行 test_kline.py 采集更多数据")
        return

    print(f"✅ 加载了 {len(df)} 条K线数据\n")

    print("运行策略分析...")
    strategy = TrendFollowingStrategy()
    df = strategy.analyze(df)

    # 统计信号
    entry_count = df['entry_long'].sum()
    exit_count = df['exit_long'].sum()

    print(f"\n=== 信号统计 ===")
    print(f"入场信号: {entry_count} 次")
    print(f"出场信号: {exit_count} 次")

    # 显示信号详情
    if entry_count > 0:
        print(f"\n=== 入场信号详情 ===")
        entries = df[df['entry_long'] == 1][['open_time', 'close', 'ma_5', 'ma_20', 'rsi']]
        print(entries.to_string(index=False))

    if exit_count > 0:
        print(f"\n=== 出场信号详情 ===")
        exits = df[df['exit_long'] == 1][['open_time', 'close', 'ma_5', 'ma_20']]
        print(exits.to_string(index=False))

    # 显示最新状态
    latest = df.iloc[-1]
    print(f"\n=== 当前状态 ===")
    print(f"价格: {latest['close']:.2f}")
    print(f"MA5: {latest['ma_5']:.2f}")
    print(f"MA20: {latest['ma_20']:.2f}")
    print(f"RSI: {latest['rsi']:.2f}")
    print(f"趋势: {'看涨' if latest['ma_5'] > latest['ma_20'] else '看跌'}")

    print("\n✅ 策略测试完成")


if __name__ == '__main__':
    test_strategy()
