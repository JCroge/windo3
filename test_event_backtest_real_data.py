#!/usr/bin/env python3
"""P1-I: 用真实 BTCUSDT 1h 数据验证 event_backtest

对比项：
    1. 旧 backtest.py (long-only, 无 SL/TP) vs 新 event_backtest (含全套机制)
    2. 是否能跑通 + 数字合理
"""

import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from optimize_1h import RobustStrategy
from backtest import BacktestEngine
from event_backtest import EventBacktest, enrich_with_atr_htf


def load_klines(symbol='BTCUSDT', interval='1h', limit=1000):
    conn = sqlite3.connect('data/klines.db')
    df = pd.read_sql_query(
        '''SELECT open_time, open, high, low, close, volume
           FROM klines WHERE symbol = ? AND interval = ?
           ORDER BY open_time DESC LIMIT ?''',
        conn, params=(symbol, interval, limit))
    conn.close()
    return df.sort_values('open_time').reset_index(drop=True)


def main():
    print("=== P1-I 真实数据验证 (BTCUSDT 1h × 1000 K线) ===\n")

    # 1. 加载数据
    df = load_klines('BTCUSDT', '1h', 1000)
    print(f"数据量: {len(df)} 根 K线")
    print(f"时间范围: {df['open_time'].min()} → {df['open_time'].max()}\n")

    # 2. 策略产生信号
    strat = RobustStrategy(ma_fast=7, ma_slow=25, rsi_threshold=75, volume_factor=1.0)
    df_signals = strat.analyze(df.copy())
    n_long = int(df_signals['entry_long'].sum())
    n_short = int(df_signals['entry_short'].sum())
    print(f"策略信号: long={n_long}, short={n_short}\n")

    # 3. 旧 backtest（long-only, 无 SL/TP）
    print("=" * 60)
    print("[旧 backtest.py] long-only, fee_rate=0.001, 无 SL/TP")
    print("=" * 60)
    old_bt = BacktestEngine(initial_capital=1000, fee_rate=0.001, trade_amount=10)
    old_result = old_bt.run(df_signals)
    print(f"  trades={old_result['total_trades']}, "
          f"win_rate={old_result['win_rate']:.1f}%, "
          f"pf={old_result['profit_factor']:.2f}, "
          f"net={old_result['total_profit']:.2f} USDT, "
          f"max_dd={old_result['max_drawdown_pct']:.2f}%")

    # 4. 新 event_backtest (默认参数：含 SL/TP/leverage/funding/trailing)
    print()
    print("=" * 60)
    print("[新 event_backtest] long+short, leverage=auto, SL/TP/trailing/partial_tp")
    print("=" * 60)
    df_enriched = enrich_with_atr_htf(df_signals.copy()).fillna(0)

    # 4a. 默认（含全部机制）
    eb = EventBacktest(initial_capital=1000, entry_threshold=30,
                       margin_pct=0.1, max_margin=10, risk_per_trade_pct=0.05,
                       enable_partial_tp=True, enable_trailing=True, enable_sl_guard=True)
    result = eb.run(df_enriched, symbol='BTCUSDT')
    print(f"  trades={result['total_trades']}, "
          f"win_rate={result['win_rate']:.1f}%, "
          f"pf={result['profit_factor']:.2f}, "
          f"net={result['total_pnl']:.2f} USDT, "
          f"max_dd={result['max_drawdown_pct']:.2f}%")
    print(f"  退出原因分布: SL={result['sl_count']}, TP={result['tp_count']}, signal={result['signal_exit_count']}")
    print(f"  平均持仓: {result['avg_holding_bars']:.1f} bars, sharpe={result['sharpe']:.2f}")
    print(f"  final_equity={result['final_equity']:.2f}")

    # 4b. 对比：禁用 partial_tp + trailing（只 SL/TP）
    print()
    eb_simple = EventBacktest(initial_capital=1000, entry_threshold=30,
                              margin_pct=0.1, max_margin=10, risk_per_trade_pct=0.05,
                              enable_partial_tp=False, enable_trailing=False, enable_sl_guard=True)
    result_simple = eb_simple.run(df_enriched, symbol='BTCUSDT')
    print(f"[新 event_backtest] 不含 partial_tp/trailing:")
    print(f"  trades={result_simple['total_trades']}, "
          f"win_rate={result_simple['win_rate']:.1f}%, "
          f"pf={result_simple['profit_factor']:.2f}, "
          f"net={result_simple['total_pnl']:.2f} USDT")

    # 4c. 对比：低门槛 + 不区分方向（验证开仓率提升）
    print()
    eb_aggressive = EventBacktest(initial_capital=1000, entry_threshold=25,
                                  margin_pct=0.1, max_margin=10, risk_per_trade_pct=0.05,
                                  enable_partial_tp=True, enable_trailing=True, enable_sl_guard=True)
    result_aggr = eb_aggressive.run(df_enriched, symbol='BTCUSDT')
    print(f"[新 event_backtest] 入场门槛 25（激进）:")
    print(f"  trades={result_aggr['total_trades']}, "
          f"win_rate={result_aggr['win_rate']:.1f}%, "
          f"pf={result_aggr['profit_factor']:.2f}, "
          f"net={result_aggr['total_pnl']:.2f} USDT")

    print()
    print("=" * 60)
    print("结论：")
    print("=" * 60)
    print(f"  旧 backtest: {old_result['total_trades']} 笔, "
          f"胜率 {old_result['win_rate']:.1f}%, pnl {old_result['total_profit']:.2f}")
    print(f"  新 event_backtest (默认): {result['total_trades']} 笔, "
          f"胜率 {result['win_rate']:.1f}%, pnl {result['total_pnl']:.2f}, dd {result['max_drawdown_pct']:.1f}%")
    print(f"  新 vs 旧：新版含 SL/TP/leverage/funding 等实盘特性，与实盘流水线更对齐")


if __name__ == '__main__':
    main()
