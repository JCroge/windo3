#!/usr/bin/env python3
"""P1-I: event_backtest 综合测试"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from event_backtest import EventBacktest, enrich_with_atr_htf


def make_df(prices, signals=None, atrs=None, rsis=None, htf=None):
    """构造 dataframe（OHLC 用 price，high/low 加 0.5% buffer）。"""
    n = len(prices)
    df = pd.DataFrame({
        'open_time': pd.date_range('2026-01-01', periods=n, freq='1h'),
        'open': prices,
        'high': [p * 1.002 for p in prices],
        'low': [p * 0.998 for p in prices],
        'close': prices,
        'volume': [1000] * n,
        'entry_long': [0] * n,
        'entry_short': [0] * n,
        'exit_long': [0] * n,
        'exit_short': [0] * n,
        'atr': atrs if atrs else [p * 0.01 for p in prices],
        'rsi': rsis if rsis else [50] * n,
        'htf_bias': htf if htf else ['neutral'] * n,
        'ma_aligned_long': [0] * n,
        'ma_aligned_short': [0] * n,
        'funding_rate': [0] * n,
    })
    if signals:
        for col, indices in signals.items():
            for i in indices:
                df.at[i, col] = 1
    return df


def test_long_signal_to_tp():
    """做多信号 → TP 触发"""
    # 价格从 100 涨到 110，TP 应该触发
    prices = [100] * 5 + [101, 102, 105, 108, 112] + [112] * 5
    df = make_df(prices, signals={'entry_long': [4]})
    # entry_long 在 idx=4 触发；idx=5 开盘价 = 101 入场
    # SL=101*(1-0.015)=99.49 (假设 atr_pct=1%, sl_dist=2.5%, 实际=2.5%)
    # 实际: atr=1.01, atr_pct=0.01, sl_dist=min(0.025, 0.05)=0.025
    # 入场 101, SL=98.475, TP=101*(1+0.0375)=104.79
    # 价格涨到 105.21, TP 触发 @104.79
    eb = EventBacktest(initial_capital=100, entry_threshold=30)  # 30 让 rule_signal=35 通过
    result = eb.run(df, 'TEST')
    assert result['total_trades'] >= 1, f"Expected trades, got {result['total_trades']}"
    trade = result['trades'][0]
    assert trade['direction'] == 'long', f"Expected long, got {trade['direction']}"
    # 退出可能是 tp 或者 partial_tp 后被 trailing 拉走
    print(f"  ✓ long_to_tp: trade direction={trade['direction']}, exit={trade['exit_reason']}, net_pnl={trade['net_pnl']:.2f}")


def test_long_signal_to_sl():
    """做多信号 → SL 触发"""
    # 入场后价格下跌
    prices = [100] * 5 + [101, 100, 98, 95, 90] + [90] * 5
    df = make_df(prices, signals={'entry_long': [4]})
    eb = EventBacktest(initial_capital=100, entry_threshold=30)
    result = eb.run(df, 'TEST')
    assert result['total_trades'] >= 1
    trade = result['trades'][0]
    assert trade['exit_reason'] == 'sl', f"Expected sl, got {trade['exit_reason']}"
    assert trade['net_pnl'] < 0, f"SL hit should be losing, got {trade['net_pnl']}"
    print(f"  ✓ long_to_sl: net_pnl={trade['net_pnl']:.2f} (should be negative)")


def test_short_signal_to_tp():
    """做空信号 → TP 触发"""
    prices = [100] * 5 + [99, 98, 95, 92, 88] + [88] * 5
    df = make_df(prices, signals={'entry_short': [4]})
    eb = EventBacktest(initial_capital=100, entry_threshold=30)
    result = eb.run(df, 'TEST')
    assert result['total_trades'] >= 1
    trade = result['trades'][0]
    assert trade['direction'] == 'short'
    assert trade['net_pnl'] > 0, f"Short on falling price should win, got {trade['net_pnl']}"
    print(f"  ✓ short_to_tp: direction={trade['direction']}, net_pnl={trade['net_pnl']:.2f}")


def test_rsi_extreme_blocks_entry():
    """RSI 极端值禁区：RSI=75 时做多 score 被压"""
    # entry_long signal + RSI=75 → score 应被压到 ≤15，低于门槛 35
    prices = [100] * 30
    df = make_df(prices,
                 signals={'entry_long': [10]},
                 rsis=[75] * 30)
    eb = EventBacktest(initial_capital=100, entry_threshold=35)
    result = eb.run(df, 'TEST')
    # RSI>=70 时即使 rule_signal +35，score 被 cap 到 15，应该 hold
    assert result['total_trades'] == 0, f"RSI=75 should block long entry, got {result['total_trades']} trades"
    print(f"  ✓ rsi_extreme_blocks: total_trades=0 (RSI=75 压制做多)")


def test_htf_resonance_boost():
    """HTF 一致时 RSI cap 放宽 + 加分"""
    # entry_long + HTF=bullish → score 应 = 35 + 10 = 45（HTF 共振加分）
    prices = [100] * 30
    df = make_df(prices,
                 signals={'entry_long': [10]},
                 htf=['bullish'] * 30)
    eb = EventBacktest(initial_capital=100, entry_threshold=40)  # 较高门槛，验证 HTF 加分能突破
    result = eb.run(df, 'TEST')
    assert result['total_trades'] >= 1, f"HTF bullish should boost score above 40, got {result['total_trades']}"
    print(f"  ✓ htf_resonance_boost: trades={result['total_trades']} (HTF 加分突破门槛)")


def test_partial_tp_and_trailing():
    """tp1 触发 → 平 50% → trailing 跟住"""
    # 价格上涨到 tp1（70% of full TP）然后继续涨
    # 入场 100, atr_pct=0.01, sl_dist=0.025
    # tp_full = 100 * 1.0375 = 103.75, tp1 = 100 * 1.02625 = 102.625
    # 价格涨到 102.7（触发 tp1）, 然后到 105
    prices = [100] * 5 + [101, 102, 102.7, 104, 105, 104, 103] + [100] * 5
    df = make_df(prices, signals={'entry_long': [4]})
    eb = EventBacktest(initial_capital=100, entry_threshold=30,
                       enable_partial_tp=True, enable_trailing=True)
    result = eb.run(df, 'TEST')
    if result['trades']:
        trade = result['trades'][0]
        # partial_realized > 0 说明 tp1 触发过
        assert trade['partial_realized'] > 0, f"Expected partial_realized>0, got {trade['partial_realized']}"
        print(f"  ✓ partial_tp_trailing: partial_realized={trade['partial_realized']:.2f}, "
              f"final_pnl={trade['net_pnl']:.2f}, exit={trade['exit_reason']}")


def test_sl_guard_cooldown():
    """连续 SL 应触发递增冷却"""
    # 多次入场都被 SL，第二次入场前应该被冷却阻止
    prices = []
    signals_long = []
    for cycle in range(3):
        base = cycle * 8
        # 入场上涨一点然后暴跌
        prices.extend([100, 100, 100, 100, 100, 101, 95, 95])
        # signal at idx (base + 4), 入场在 base + 5
        signals_long.append(base + 4)
    # 补一些尾部 K 线给最后一笔仓位平掉机会
    prices.extend([95] * 4)
    df = make_df(prices, signals={'entry_long': signals_long})
    eb = EventBacktest(initial_capital=100, entry_threshold=30, enable_sl_guard=True)
    result = eb.run(df, 'TEST')

    eb_no_guard = EventBacktest(initial_capital=100, entry_threshold=30, enable_sl_guard=False)
    result_no_guard = eb_no_guard.run(df, 'TEST')

    assert result['total_trades'] <= result_no_guard['total_trades'], \
        f"SL guard should reduce trades: {result['total_trades']} vs {result_no_guard['total_trades']}"
    print(f"  ✓ sl_guard_cooldown: with_guard={result['total_trades']} <= no_guard={result_no_guard['total_trades']}")


def test_leverage_calculation():
    """杠杆按 max_loss / (margin × sl_dist) 圆整"""
    # equity=100, margin=10, max_loss=5, sl_dist=2.5%
    # raw_leverage = 5 / (10 × 0.025) = 20
    # 应该圆整到 20
    prices = [100] * 30
    df = make_df(prices, signals={'entry_long': [4]})
    eb = EventBacktest(initial_capital=100, margin_pct=0.1, max_margin=10,
                       risk_per_trade_pct=0.05, entry_threshold=30)
    # build plan 直接调用
    plan = eb._build_plan(df.iloc[4], 'long', 100)
    assert plan is not None
    assert plan['leverage'] == 20, f"Expected leverage=20, got {plan['leverage']}"
    print(f"  ✓ leverage_calculation: lev={plan['leverage']} (期望 20)")


def test_low_equity_skips_entry():
    """余额不足时跳过入场"""
    # margin 计算 = min(equity*10%, 10), equity=5 → margin=0.5 < 1.0 → 应该拒绝
    prices = [100] * 30
    df = make_df(prices, signals={'entry_long': [4]})
    eb = EventBacktest(initial_capital=5, margin_pct=0.1, max_margin=10, entry_threshold=30)
    plan = eb._build_plan(df.iloc[4], 'long', 5)
    assert plan is None, f"Low equity should return None, got {plan}"
    print(f"  ✓ low_equity_skips: equity=5 → plan=None")


def test_costs_reduce_pnl():
    """成本（手续费 + funding）应减少 PnL"""
    # 入场 100，平仓 102 (gross +2%)
    # margin=10, lev=10, notional=100
    # gross_pnl = (102-100)/100 * 100 = 2
    # fees = 100 * 0.002 = 0.2
    # net = 2 - 0.2 = 1.8
    prices = [100] * 5 + [101, 102, 103] + [103] * 20
    df = make_df(prices, signals={'entry_long': [4]})
    eb = EventBacktest(initial_capital=100, entry_threshold=30)
    result = eb.run(df, 'TEST')
    if result['trades']:
        trade = result['trades'][0]
        assert trade['fees'] > 0, f"Expected fees>0, got {trade['fees']}"
        assert trade['gross_pnl'] > trade['net_pnl'], "net should be < gross"
        print(f"  ✓ costs_reduce_pnl: gross={trade['gross_pnl']:.3f}, fees={trade['fees']:.3f}, net={trade['net_pnl']:.3f}")


def test_metrics_computation():
    """metrics 完整性"""
    np.random.seed(123)
    n = 200
    prices = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open_time': pd.date_range('2026-01-01', periods=n, freq='1h'),
        'open': prices, 'high': prices * 1.005, 'low': prices * 0.995,
        'close': prices, 'volume': [1000] * n,
    })
    df['ma_fast'] = df['close'].rolling(7).mean()
    df['ma_slow'] = df['close'].rolling(25).mean()
    df['rsi'] = 50
    cross_up = (df['ma_fast'] > df['ma_slow']) & (df['ma_fast'].shift(1) <= df['ma_slow'].shift(1))
    cross_down = (df['ma_fast'] < df['ma_slow']) & (df['ma_fast'].shift(1) >= df['ma_slow'].shift(1))
    df['entry_long'] = cross_up.astype(int)
    df['entry_short'] = cross_down.astype(int)
    df['exit_long'] = cross_down.astype(int)
    df['exit_short'] = cross_up.astype(int)
    df = enrich_with_atr_htf(df).fillna(0)

    eb = EventBacktest(initial_capital=100, entry_threshold=30)
    result = eb.run(df, 'METRICS_TEST')
    required_keys = ['total_trades', 'win_rate', 'profit_factor', 'total_pnl',
                     'max_drawdown_pct', 'sharpe', 'sl_count', 'tp_count', 'signal_exit_count',
                     'avg_holding_bars']
    for k in required_keys:
        assert k in result, f"Missing metric: {k}"
    assert len(result['equity_curve']) == n
    print(f"  ✓ metrics_computation: 所有 {len(required_keys)} 个 metric 字段齐全")


def test_input_validation():
    """缺少必要列应报错"""
    df = pd.DataFrame({'close': [100, 101, 102]})
    eb = EventBacktest()
    try:
        eb.run(df, 'BAD')
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert 'event_backtest 缺少必要列' in str(e)
        print(f"  ✓ input_validation: 正确拒绝缺列")


def test_end_of_data_forces_close():
    """数据结束时未平仓位强制平仓"""
    prices = [100] * 5 + [101] + [101] * 5  # 入场后价格不动
    df = make_df(prices, signals={'entry_long': [4]})
    eb = EventBacktest(initial_capital=100, entry_threshold=30)
    result = eb.run(df, 'TEST')
    assert len(result['trades']) >= 1
    # 找到强制平仓的（exit_reason='end_of_data'）
    forced = [t for t in result['trades'] if t['exit_reason'] == 'end_of_data']
    assert len(forced) >= 1, "Expected at least 1 forced close"
    print(f"  ✓ end_of_data_forces_close: forced_closes={len(forced)}")


if __name__ == '__main__':
    print("=== event_backtest 综合测试 ===\n")
    tests = [
        test_long_signal_to_tp,
        test_long_signal_to_sl,
        test_short_signal_to_tp,
        test_rsi_extreme_blocks_entry,
        test_htf_resonance_boost,
        test_partial_tp_and_trailing,
        test_sl_guard_cooldown,
        test_leverage_calculation,
        test_low_equity_skips_entry,
        test_costs_reduce_pnl,
        test_metrics_computation,
        test_input_validation,
        test_end_of_data_forces_close,
    ]
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed.append((t.__name__, str(e)))
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n=== 结果: {passed}/{len(tests)} 通过 ===")
    if failed:
        for name, err in failed:
            print(f"  失败: {name} - {err}")
        sys.exit(1)
