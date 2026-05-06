#!/usr/bin/env python3
"""趋势跟踪策略 - MA交叉"""

import pandas as pd
from strategy_base import StrategyBase


class TrendFollowingStrategy(StrategyBase):
    """趋势跟踪策略：基于MA交叉"""

    timeframe = '1m'
    startup_candle_count = 30

    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加技术指标"""
        # MA
        df['ma_5'] = self.indicators.calculate_ma(df, 5)
        df['ma_20'] = self.indicators.calculate_ma(df, 20)

        # RSI (用于过滤)
        df['rsi'] = self.indicators.calculate_rsi(df, 14)

        return df

    def populate_entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成入场信号：MA5上穿MA20 且 RSI < 70"""
        df['entry_long'] = 0

        # 条件：MA5上穿MA20 (金叉)
        df.loc[
            (df['ma_5'] > df['ma_20']) &
            (df['ma_5'].shift(1) <= df['ma_20'].shift(1)) &
            (df['rsi'] < 70),  # 避免超买
            'entry_long'
        ] = 1

        return df

    def populate_exit_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成出场信号：MA5下穿MA20"""
        df['exit_long'] = 0

        # 条件：MA5下穿MA20 (死叉)
        df.loc[
            (df['ma_5'] < df['ma_20']) &
            (df['ma_5'].shift(1) >= df['ma_20'].shift(1)),
            'exit_long'
        ] = 1

        return df
