#!/usr/bin/env python3
"""回测引擎 - 参考Freqtrade架构"""

import pandas as pd
from typing import Dict, List


class BacktestEngine:
    """回测引擎"""

    def __init__(self, initial_capital=1000, fee_rate=0.001, trade_amount=10):
        """
        Args:
            initial_capital: 初始资金 (USDT)
            fee_rate: 手续费率 (0.1%)
            trade_amount: 每次交易金额 (USDT)
        """
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.trade_amount = trade_amount

    def run(self, df: pd.DataFrame) -> Dict:
        """运行回测

        Args:
            df: 包含信号的dataframe (必须有entry_long和exit_long列)

        Returns:
            回测结果字典
        """
        df = df.copy()
        trades = []
        position = None  # 当前持仓 {entry_price, entry_time, amount}

        # 遍历每根K线
        for i in range(len(df)):
            row = df.iloc[i]

            # 如果有持仓，检查出场信号
            if position is not None:
                if row['exit_long'] == 1:
                    # 在下一根K线开盘价出场（避免前视偏差）
                    if i + 1 < len(df):
                        exit_price = df.iloc[i + 1]['open']
                        exit_time = df.iloc[i + 1]['open_time']

                        # 计算收益
                        entry_fee = position['amount'] * self.fee_rate
                        exit_fee = position['amount'] * self.fee_rate
                        profit = (exit_price - position['entry_price']) * (position['amount'] / position['entry_price'])
                        net_profit = profit - entry_fee - exit_fee

                        trades.append({
                            'entry_time': position['entry_time'],
                            'entry_price': position['entry_price'],
                            'exit_time': exit_time,
                            'exit_price': exit_price,
                            'amount': position['amount'],
                            'profit': net_profit,
                            'profit_pct': (net_profit / position['amount']) * 100
                        })

                        position = None

            # 如果无持仓，检查入场信号
            elif row['entry_long'] == 1:
                # 在下一根K线开盘价入场
                if i + 1 < len(df):
                    entry_price = df.iloc[i + 1]['open']
                    entry_time = df.iloc[i + 1]['open_time']

                    position = {
                        'entry_price': entry_price,
                        'entry_time': entry_time,
                        'amount': self.trade_amount
                    }

        # 计算回测指标
        return self._calculate_metrics(trades)

    def _calculate_metrics(self, trades: List[Dict]) -> Dict:
        """计算回测指标"""
        if not trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'profit_factor': 0,
                'total_profit': 0,
                'total_profit_pct': 0,
                'max_drawdown': 0,
                'avg_profit': 0,
                'trades': []
            }

        df_trades = pd.DataFrame(trades)

        # 基础指标
        total_trades = len(trades)
        winning_trades = df_trades[df_trades['profit'] > 0]
        losing_trades = df_trades[df_trades['profit'] <= 0]

        win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0

        # 盈亏比
        total_profit = winning_trades['profit'].sum() if len(winning_trades) > 0 else 0
        total_loss = abs(losing_trades['profit'].sum()) if len(losing_trades) > 0 else 0
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')

        # 总收益
        net_profit = df_trades['profit'].sum()
        net_profit_pct = (net_profit / self.initial_capital) * 100

        # 最大回撤
        cumulative = df_trades['profit'].cumsum()
        running_max = cumulative.cummax()
        drawdown = running_max - cumulative
        max_drawdown = drawdown.max()
        max_drawdown_pct = (max_drawdown / self.initial_capital) * 100 if self.initial_capital > 0 else 0

        # 平均收益
        avg_profit = df_trades['profit'].mean()

        return {
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'total_profit': net_profit,
            'total_profit_pct': net_profit_pct,
            'max_drawdown': max_drawdown,
            'max_drawdown_pct': max_drawdown_pct,
            'avg_profit': avg_profit,
            'avg_profit_pct': df_trades['profit_pct'].mean(),
            'trades': trades
        }
