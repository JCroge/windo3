#!/usr/bin/env python3
"""技术指标计算模块"""

import pandas as pd
import numpy as np


class TechnicalIndicators:
    """技术指标计算器"""

    @staticmethod
    def calculate_ma(df, period=20, column='close'):
        """计算移动平均线 (MA)"""
        return df[column].rolling(window=period).mean()

    @staticmethod
    def calculate_ema(df, period=12, column='close'):
        """计算指数移动平均线 (EMA)"""
        return df[column].ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_macd(df, fast=12, slow=26, signal=9, column='close'):
        """计算MACD指标

        Returns:
            dict: {'macd': MACD线, 'signal': 信号线, 'histogram': 柱状图}
        """
        ema_fast = df[column].ewm(span=fast, adjust=False).mean()
        ema_slow = df[column].ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=signal, adjust=False).mean()
        histogram = macd - signal_line

        return {
            'macd': macd,
            'signal': signal_line,
            'histogram': histogram
        }

    @staticmethod
    def calculate_rsi(df, period=14, column='close'):
        """计算相对强弱指标 (RSI)"""
        delta = df[column].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def calculate_bollinger_bands(df, period=20, std_dev=2, column='close'):
        """计算布林带

        Returns:
            dict: {'upper': 上轨, 'middle': 中轨, 'lower': 下轨}
        """
        middle = df[column].rolling(window=period).mean()
        std = df[column].rolling(window=period).std()
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)

        return {
            'upper': upper,
            'middle': middle,
            'lower': lower
        }

    @staticmethod
    def add_all_indicators(df):
        """为DataFrame添加所有技术指标"""
        df = df.copy()

        # MA
        df['ma_5'] = TechnicalIndicators.calculate_ma(df, 5)
        df['ma_10'] = TechnicalIndicators.calculate_ma(df, 10)
        df['ma_20'] = TechnicalIndicators.calculate_ma(df, 20)

        # MACD
        macd = TechnicalIndicators.calculate_macd(df)
        df['macd'] = macd['macd']
        df['macd_signal'] = macd['signal']
        df['macd_histogram'] = macd['histogram']

        # RSI
        df['rsi'] = TechnicalIndicators.calculate_rsi(df)

        # 布林带
        bb = TechnicalIndicators.calculate_bollinger_bands(df)
        df['bb_upper'] = bb['upper']
        df['bb_middle'] = bb['middle']
        df['bb_lower'] = bb['lower']

        return df
