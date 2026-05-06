#!/usr/bin/env python3
"""样本外验证 - 测试策略稳健性"""

import sqlite3
import pandas as pd
from optimize_1h import RobustStrategy
from backtest import BacktestEngine


def load_klines(symbol='BTCUSDT', interval='1h', limit=1000):
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


def out_of_sample_validation():
    """样本外验证"""
    print("=== 样本外验证 ===\n")

    # 加载全部数据
    df_all = load_klines('BTCUSDT', '1h', 1000)
    print(f"总数据量: {len(df_all)} 根K线")

    # 分割数据
    train_size = 800
    df_train = df_all.iloc[:train_size].copy()
    df_test = df_all.iloc[train_size:].copy()

    print(f"训练集: {len(df_train)} 根K线（前80%）")
    print(f"测试集: {len(df_test)} 根K线（后20%）\n")

    # 最佳参数（从优化中得到）
    best_params = {
        'ma_fast': 7,
        'ma_slow': 25,
        'rsi_period': 14,
        'rsi_threshold': 75,
        'volume_factor': 1.0
    }

    print("使用最佳参数:")
    for key, value in best_params.items():
        print(f"  {key}: {value}")
    print()

    # 初始化策略和回测引擎
    strategy = RobustStrategy(**best_params)
    backtest = BacktestEngine(initial_capital=1000, fee_rate=0.001, trade_amount=10)

    # 训练集测试
    print("="*60)
    print("训练集表现（参数优化所用数据）")
    print("="*60)
    df_train_analyzed = strategy.analyze(df_train)
    train_results = backtest.run(df_train_analyzed)

    print(f"交易次数: {train_results['total_trades']}")
    print(f"胜率: {train_results['win_rate']:.2f}%")
    print(f"盈亏比: {train_results['profit_factor']:.2f}")
    print(f"总收益: {train_results['total_profit_pct']:.2f}%")
    print(f"最大回撤: {train_results['max_drawdown_pct']:.2f}%\n")

    # 测试集验证
    print("="*60)
    print("测试集表现（策略未见过的数据）")
    print("="*60)
    df_test_analyzed = strategy.analyze(df_test)
    test_results = backtest.run(df_test_analyzed)

    print(f"交易次数: {test_results['total_trades']}")
    print(f"胜率: {test_results['win_rate']:.2f}%")
    print(f"盈亏比: {test_results['profit_factor']:.2f}")
    print(f"总收益: {test_results['total_profit_pct']:.2f}%")
    print(f"最大回撤: {test_results['max_drawdown_pct']:.2f}%\n")

    # 对比分析
    print("="*60)
    print("样本外验证结果")
    print("="*60)

    if test_results['total_trades'] < 3:
        print("⚠️  测试集交易次数过少，样本不足")
        return

    # 计算性能衰减
    win_rate_decay = test_results['win_rate'] - train_results['win_rate']
    profit_decay = test_results['total_profit_pct'] - train_results['total_profit_pct']

    print(f"\n胜率变化: {train_results['win_rate']:.1f}% → {test_results['win_rate']:.1f}% ({win_rate_decay:+.1f}%)")
    print(f"收益变化: {train_results['total_profit_pct']:.2f}% → {test_results['total_profit_pct']:.2f}% ({profit_decay:+.2f}%)")

    # 判断稳健性
    print("\n稳健性评估:")
    if test_results['win_rate'] >= 50 and test_results['total_profit_pct'] > 0:
        print("✅ 策略在样本外数据上表现良好，具有稳健性")
    elif test_results['total_profit_pct'] > 0:
        print("⚠️  策略在样本外仍盈利，但胜率下降，需谨慎")
    else:
        print("❌ 策略在样本外表现不佳，可能存在过拟合")


if __name__ == '__main__':
    out_of_sample_validation()
