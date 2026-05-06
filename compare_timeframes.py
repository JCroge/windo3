#!/usr/bin/env python3
"""多时间周期策略对比测试"""

import sqlite3
import pandas as pd
from strategy_trend import TrendFollowingStrategy
from backtest import BacktestEngine


def load_klines(symbol='BTCUSDT', interval='1m', limit=500):
    """加载K线数据"""
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
    return df.sort_values('open_time').reset_index(drop=True)


def test_timeframe(interval, interval_name):
    """测试单个时间周期"""
    print(f"\n{'='*50}")
    print(f"测试时间周期: {interval_name}")
    print(f"{'='*50}")

    # 加载数据
    df = load_klines('BTCUSDT', interval, 500)
    print(f"数据量: {len(df)} 根K线")

    if len(df) < 50:
        print("❌ 数据不足")
        return None

    # 运行策略
    strategy = TrendFollowingStrategy()
    df = strategy.analyze(df)

    # 运行回测
    backtest = BacktestEngine(initial_capital=1000, fee_rate=0.001, trade_amount=10)
    results = backtest.run(df)

    return results


def compare_timeframes():
    """对比不同时间周期"""
    print("=== 多时间周期策略对比 ===\n")

    timeframes = [
        ('1m', '1分钟'),
        ('15m', '15分钟'),
        ('1h', '1小时')
    ]

    all_results = {}

    for interval, name in timeframes:
        result = test_timeframe(interval, name)
        if result:
            all_results[name] = result

    # 对比结果
    print(f"\n{'='*80}")
    print("对比结果汇总")
    print(f"{'='*80}\n")

    print(f"{'时间周期':<10} {'交易次数':<10} {'胜率':<10} {'盈亏比':<10} {'总收益%':<12} {'最大回撤%':<12}")
    print("-" * 80)

    for name, result in all_results.items():
        print(f"{name:<10} {result['total_trades']:<10} "
              f"{result['win_rate']:<10.2f} {result['profit_factor']:<10.2f} "
              f"{result['total_profit_pct']:<12.2f} {result['max_drawdown_pct']:<12.2f}")

    # 找出最佳时间周期
    if all_results:
        best_timeframe = max(all_results.items(), key=lambda x: x[1]['total_profit_pct'])
        print(f"\n✅ 最佳时间周期: {best_timeframe[0]}")
        print(f"   总收益: {best_timeframe[1]['total_profit_pct']:.2f}%")
        print(f"   胜率: {best_timeframe[1]['win_rate']:.2f}%")


if __name__ == '__main__':
    compare_timeframes()
