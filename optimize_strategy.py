#!/usr/bin/env python3
"""策略参数优化工具"""

import sqlite3
import pandas as pd
from itertools import product
from strategy_base import StrategyBase
from backtest import BacktestEngine
from indicators import TechnicalIndicators


class OptimizableStrategy(StrategyBase):
    """可优化参数的策略"""

    def __init__(self, ma_fast=5, ma_slow=20, rsi_period=14, rsi_threshold=70):
        super().__init__()
        self.ma_fast = ma_fast
        self.ma_slow = ma_slow
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold

    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['ma_fast'] = self.indicators.calculate_ma(df, self.ma_fast)
        df['ma_slow'] = self.indicators.calculate_ma(df, self.ma_slow)
        df['rsi'] = self.indicators.calculate_rsi(df, self.rsi_period)
        return df

    def populate_entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['entry_long'] = 0
        df.loc[
            (df['ma_fast'] > df['ma_slow']) &
            (df['ma_fast'].shift(1) <= df['ma_slow'].shift(1)) &
            (df['rsi'] < self.rsi_threshold),
            'entry_long'
        ] = 1
        return df

    def populate_exit_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['exit_long'] = 0
        df.loc[
            (df['ma_fast'] < df['ma_slow']) &
            (df['ma_fast'].shift(1) >= df['ma_slow'].shift(1)),
            'exit_long'
        ] = 1
        return df


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


def optimize_parameters():
    """优化策略参数"""
    print("=== 策略参数优化 ===\n")

    # 加载数据
    print("加载历史数据...")
    df = load_klines('BTCUSDT', '1m', 500)
    print(f"✅ 加载了 {len(df)} 条K线\n")

    if len(df) < 100:
        print("❌ 数据不足，请先获取更多历史数据")
        return

    # 定义参数搜索空间
    param_grid = {
        'ma_fast': [5, 10, 15],
        'ma_slow': [20, 30, 40],
        'rsi_period': [14],
        'rsi_threshold': [60, 70, 80]
    }

    print("参数搜索空间:")
    for key, values in param_grid.items():
        print(f"  {key}: {values}")

    total_combinations = 1
    for values in param_grid.values():
        total_combinations *= len(values)
    print(f"\n总组合数: {total_combinations}\n")

    # 遍历所有参数组合
    results = []
    backtest = BacktestEngine(initial_capital=1000, fee_rate=0.001, trade_amount=10)

    print("开始优化...")
    for i, params in enumerate(product(*param_grid.values()), 1):
        ma_fast, ma_slow, rsi_period, rsi_threshold = params

        # 跳过无效组合
        if ma_fast >= ma_slow:
            continue

        # 运行策略
        strategy = OptimizableStrategy(ma_fast, ma_slow, rsi_period, rsi_threshold)
        df_test = strategy.analyze(df.copy())

        # 运行回测
        result = backtest.run(df_test)

        # 只保留有足够交易次数的结果
        if result['total_trades'] >= 3:
            results.append({
                'ma_fast': ma_fast,
                'ma_slow': ma_slow,
                'rsi_period': rsi_period,
                'rsi_threshold': rsi_threshold,
                'total_trades': result['total_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor'],
                'total_profit_pct': result['total_profit_pct'],
                'max_drawdown_pct': result['max_drawdown_pct']
            })

        if i % 5 == 0:
            print(f"  进度: {i}/{total_combinations}")

    print(f"\n✅ 优化完成，找到 {len(results)} 个有效组合\n")

    if not results:
        print("❌ 没有找到满足条件的参数组合（至少3笔交易）")
        return

    # 排序并显示最佳结果
    df_results = pd.DataFrame(results)
    df_results = df_results.sort_values('total_profit_pct', ascending=False)

    print("=== Top 5 最佳参数组合 ===\n")
    for i, row in df_results.head(5).iterrows():
        print(f"#{i+1}:")
        print(f"  MA快线: {row['ma_fast']}, MA慢线: {row['ma_slow']}")
        print(f"  RSI周期: {row['rsi_period']}, RSI阈值: {row['rsi_threshold']}")
        print(f"  交易次数: {row['total_trades']}")
        print(f"  胜率: {row['win_rate']:.2f}%")
        print(f"  盈亏比: {row['profit_factor']:.2f}")
        print(f"  总收益: {row['total_profit_pct']:.2f}%")
        print(f"  最大回撤: {row['max_drawdown_pct']:.2f}%")
        print()


if __name__ == '__main__':
    optimize_parameters()
