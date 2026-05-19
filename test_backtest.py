#!/usr/bin/env python3
"""测试回测引擎"""

import sqlite3
import pandas as pd
from strategy_trend import TrendFollowingStrategy
from backtest import BacktestEngine


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
def test_backtest():
    """测试回测引擎"""
    print("=== 回测测试 ===\n")

    # 1. 加载数据
    print("1. 加载K线数据...")
    df = load_klines('BTCUSDT', '1m', 100)
    print(f"   ✅ 加载了 {len(df)} 条K线\n")

    # 2. 运行策略
    print("2. 运行策略分析...")
    strategy = TrendFollowingStrategy()
    df = strategy.analyze(df)
    print(f"   ✅ 生成了 {df['entry_long'].sum()} 个入场信号")
    print(f"   ✅ 生成了 {df['exit_long'].sum()} 个出场信号\n")

    # 3. 运行回测
    print("3. 运行回测...")
    backtest = BacktestEngine(
        initial_capital=1000,
        fee_rate=0.001,  # 0.1%
        trade_amount=10
    )
    results = backtest.run(df)

    # 4. 显示结果
    print("\n=== 回测结果 ===")
    print(f"总交易次数: {results['total_trades']}")
    print(f"盈利交易: {results['winning_trades']}")
    print(f"亏损交易: {results['losing_trades']}")
    print(f"胜率: {results['win_rate']:.2f}%")
    print(f"盈亏比: {results['profit_factor']:.2f}")
    print(f"\n总收益: {results['total_profit']:.2f} USDT ({results['total_profit_pct']:.2f}%)")
    print(f"平均收益: {results['avg_profit']:.4f} USDT ({results['avg_profit_pct']:.2f}%)")
    print(f"最大回撤: {results['max_drawdown']:.2f} USDT ({results['max_drawdown_pct']:.2f}%)")

    # 5. 显示交易详情
    if results['total_trades'] > 0:
        print(f"\n=== 交易详情 ===")
        for i, trade in enumerate(results['trades'], 1):
            print(f"\n交易 #{i}:")
            print(f"  入场: {trade['entry_price']:.2f} USDT")
            print(f"  出场: {trade['exit_price']:.2f} USDT")
            print(f"  收益: {trade['profit']:.4f} USDT ({trade['profit_pct']:.2f}%)")

    print("\n✅ 回测完成")


if __name__ == '__main__':
    test_backtest()
