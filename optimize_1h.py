#!/usr/bin/env python3
"""1小时周期参数优化 - 加入反欺骗机制"""

import sqlite3
import pandas as pd
from itertools import product
from strategy_base import StrategyBase
from backtest import BacktestEngine
from indicators import TechnicalIndicators


class RobustStrategy(StrategyBase):
    """稳健策略 - 加入反欺骗机制"""

    def __init__(self, ma_fast=5, ma_slow=20, rsi_period=14,
                 rsi_threshold=70, volume_factor=1.2):
        """
        Args:
            volume_factor: 成交量确认因子（当前成交量需要>平均成交量*factor）
        """
        super().__init__()
        self.ma_fast = ma_fast
        self.ma_slow = ma_slow
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold
        self.volume_factor = volume_factor

    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['ma_fast'] = self.indicators.calculate_ma(df, self.ma_fast)
        df['ma_slow'] = self.indicators.calculate_ma(df, self.ma_slow)
        df['rsi'] = self.indicators.calculate_rsi(df, self.rsi_period)

        # 成交量均线（用于识别放量）
        df['volume_ma'] = df['volume'].rolling(window=20).mean()

        return df

    def populate_entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """入场信号 - 多重确认机制"""
        df['entry_long'] = 0
        df['entry_short'] = 0

        # 做多：MA金叉 + RSI不超买 + 放量 + 价格上涨
        ma_cross_up = (df['ma_fast'] > df['ma_slow']) & (df['ma_fast'].shift(1) <= df['ma_slow'].shift(1))
        rsi_not_overbought = df['rsi'] < self.rsi_threshold
        volume_confirm = df['volume'] > (df['volume_ma'] * self.volume_factor)
        price_rising = df['close'] > df['close'].shift(1)
        df.loc[ma_cross_up & rsi_not_overbought & volume_confirm & price_rising, 'entry_long'] = 1

        # 做空：MA死叉 + RSI不超卖（避免超卖区做空，Phase 6d教训）+ 放量 + 价格下跌
        ma_cross_down = (df['ma_fast'] < df['ma_slow']) & (df['ma_fast'].shift(1) >= df['ma_slow'].shift(1))
        rsi_not_oversold = df['rsi'] > (100 - self.rsi_threshold)  # 对称：默认>25
        price_falling = df['close'] < df['close'].shift(1)
        df.loc[ma_cross_down & rsi_not_oversold & volume_confirm & price_falling, 'entry_short'] = 1

        return df

    def populate_exit_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """出场信号 - 及时止盈"""
        df['exit_long'] = 0
        df['exit_short'] = 0

        ma_cross_up = (df['ma_fast'] > df['ma_slow']) & (df['ma_fast'].shift(1) <= df['ma_slow'].shift(1))
        ma_cross_down = (df['ma_fast'] < df['ma_slow']) & (df['ma_fast'].shift(1) >= df['ma_slow'].shift(1))

        df.loc[ma_cross_down | (df['rsi'] > 80), 'exit_long'] = 1
        df.loc[ma_cross_up | (df['rsi'] < 20), 'exit_short'] = 1

        return df


def load_klines(symbol='BTCUSDT', interval='1h', limit=500):
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


def optimize_1h_parameters():
    """优化1小时周期参数"""
    print("=== 1小时周期参数优化（反欺骗机制）===\n")

    # 加载数据
    df = load_klines('BTCUSDT', '1h', 500)
    print(f"数据量: {len(df)} 根K线\n")

    # 参数搜索空间（聚焦优化）
    param_grid = {
        'ma_fast': [3, 5, 7],
        'ma_slow': [15, 20, 25],
        'rsi_period': [14],
        'rsi_threshold': [65, 70, 75],
        'volume_factor': [1.0, 1.2, 1.5]  # 成交量确认因子
    }

    print("参数搜索空间:")
    for key, values in param_grid.items():
        print(f"  {key}: {values}")

    total = 1
    for values in param_grid.values():
        total *= len(values)
    print(f"\n总组合数: {total}\n")

    # 遍历参数组合
    results = []
    backtest = BacktestEngine(initial_capital=1000, fee_rate=0.001, trade_amount=10)

    print("开始优化...")
    for i, params in enumerate(product(*param_grid.values()), 1):
        ma_fast, ma_slow, rsi_period, rsi_threshold, volume_factor = params

        if ma_fast >= ma_slow:
            continue

        strategy = RobustStrategy(ma_fast, ma_slow, rsi_period, rsi_threshold, volume_factor)
        df_test = strategy.analyze(df.copy())
        result = backtest.run(df_test)

        # 只保留有足够交易次数且盈利的结果
        if result['total_trades'] >= 5 and result['total_profit_pct'] > 0:
            results.append({
                'ma_fast': ma_fast,
                'ma_slow': ma_slow,
                'rsi_threshold': rsi_threshold,
                'volume_factor': volume_factor,
                'total_trades': result['total_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor'],
                'total_profit_pct': result['total_profit_pct'],
                'max_drawdown_pct': result['max_drawdown_pct']
            })

        if i % 10 == 0:
            print(f"  进度: {i}/{total}")

    print(f"\n✅ 优化完成，找到 {len(results)} 个盈利组合\n")

    if not results:
        print("❌ 没有找到盈利的参数组合")
        return

    # 排序并显示最佳结果
    df_results = pd.DataFrame(results)
    df_results = df_results.sort_values('total_profit_pct', ascending=False)

    print("=== Top 5 最佳参数组合 ===\n")
    for idx, row in df_results.head(5).iterrows():
        print(f"#{idx+1}:")
        print(f"  MA: {int(row['ma_fast'])}/{int(row['ma_slow'])}, RSI阈值: {int(row['rsi_threshold'])}, 成交量因子: {row['volume_factor']:.1f}")
        print(f"  交易: {int(row['total_trades'])}笔, 胜率: {row['win_rate']:.1f}%, 盈亏比: {row['profit_factor']:.2f}")
        print(f"  总收益: {row['total_profit_pct']:.2f}%, 最大回撤: {row['max_drawdown_pct']:.2f}%")
        print()


if __name__ == '__main__':
    optimize_1h_parameters()
