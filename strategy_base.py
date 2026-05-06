#!/usr/bin/env python3
"""策略基类 - 参考Freqtrade架构"""

from abc import ABC, abstractmethod
import pandas as pd
from indicators import TechnicalIndicators


class StrategyBase(ABC):
    """策略基类"""

    # 必须设置的参数
    timeframe = '1m'
    startup_candle_count = 30  # 指标预热需要的K线数量

    def __init__(self):
        self.indicators = TechnicalIndicators()

    @abstractmethod
    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加技术指标到dataframe

        Args:
            df: 包含OHLCV数据的dataframe

        Returns:
            添加了指标列的dataframe
        """
        pass

    @abstractmethod
    def populate_entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成入场信号

        Args:
            df: 包含指标的dataframe

        Returns:
            添加了entry_long列的dataframe (1=买入, 0=不操作)
        """
        pass

    @abstractmethod
    def populate_exit_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成出场信号

        Args:
            df: 包含指标的dataframe

        Returns:
            添加了exit_long列的dataframe (1=卖出, 0=不操作)
        """
        pass

    def analyze(self, df: pd.DataFrame) -> pd.DataFrame:
        """完整分析流程：指标 → 入场信号 → 出场信号"""
        df = df.copy()
        df = self.populate_indicators(df)
        df = self.populate_entry_signals(df)
        df = self.populate_exit_signals(df)
        return df
