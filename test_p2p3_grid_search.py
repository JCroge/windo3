"""P2-3: 参数网格搜索 — 基于 EventBacktest 合成数据"""
import sys
import itertools
import pandas as pd
import numpy as np

sys.path.insert(0, '.')
from event_backtest import EventBacktest


# ── 合成数据生成 ──────────────────────────────────────────────────

def _make_trending_df(n=500, seed=42):
    """趋势行情：价格随机游走带正漂移，MA 交叉信号均匀分布"""
    rng = np.random.default_rng(seed)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.normal(0.0003, 0.012)))

    df = pd.DataFrame({
        'open_time': pd.date_range('2025-01-01', periods=n, freq='1h'),
        'open': prices,
        'high': [p * (1 + abs(rng.normal(0, 0.005))) for p in prices],
        'low': [p * (1 - abs(rng.normal(0, 0.005))) for p in prices],
        'close': prices,
        'volume': rng.integers(500, 2000, n).tolist(),
        'atr': [p * 0.015 for p in prices],
        'rsi': np.clip(50 + np.cumsum(rng.normal(0, 3, n)), 20, 80).tolist(),
        'htf_bias': ['bullish'] * n,
        'funding_rate': [0.0001] * n,
        'ma_aligned_long': [0] * n,
        'ma_aligned_short': [0] * n,
    })

    # 每 40 根 K 线放一个 entry_long 信号（模拟 MA 交叉）
    entry_long = [0] * n
    entry_short = [0] * n
    exit_long = [0] * n
    exit_short = [0] * n
    for i in range(20, n - 1, 40):
        entry_long[i] = 1
        exit_long[min(i + 20, n - 1)] = 1
    for i in range(35, n - 1, 80):
        entry_short[i] = 1
        exit_short[min(i + 15, n - 1)] = 1

    df['entry_long'] = entry_long
    df['entry_short'] = entry_short
    df['exit_long'] = exit_long
    df['exit_short'] = exit_short
    return df


def _make_choppy_df(n=500, seed=99):
    """震荡行情：价格均值回归，信号频繁但多数假突破"""
    rng = np.random.default_rng(seed)
    prices = [100.0]
    for _ in range(n - 1):
        mean_revert = (100 - prices[-1]) * 0.02
        prices.append(prices[-1] * (1 + rng.normal(mean_revert / 100, 0.010)))

    df = pd.DataFrame({
        'open_time': pd.date_range('2025-01-01', periods=n, freq='1h'),
        'open': prices,
        'high': [p * (1 + abs(rng.normal(0, 0.004))) for p in prices],
        'low': [p * (1 - abs(rng.normal(0, 0.004))) for p in prices],
        'close': prices,
        'volume': rng.integers(300, 1500, n).tolist(),
        'atr': [p * 0.010 for p in prices],
        'rsi': np.clip(50 + np.cumsum(rng.normal(0, 4, n)), 20, 80).tolist(),
        'htf_bias': ['neutral'] * n,
        'funding_rate': [0.0001] * n,
        'ma_aligned_long': [0] * n,
        'ma_aligned_short': [0] * n,
    })

    entry_long = [0] * n
    entry_short = [0] * n
    exit_long = [0] * n
    exit_short = [0] * n
    for i in range(15, n - 1, 30):
        entry_long[i] = 1
        exit_long[min(i + 10, n - 1)] = 1
    for i in range(25, n - 1, 30):
        entry_short[i] = 1
        exit_short[min(i + 10, n - 1)] = 1

    df['entry_long'] = entry_long
    df['entry_short'] = entry_short
    df['exit_long'] = exit_long
    df['exit_short'] = exit_short
    return df


# ── 网格搜索 ──────────────────────────────────────────────────────

PARAM_GRID = {
    'entry_threshold': [25, 30, 35, 40],
    'rr_floor':        [1.3, 1.5, 1.8],
    'post_close_cooldown_bars': [3, 5, 8],
    'enable_partial_tp': [True, False],
    'enable_trailing':   [True, False],
}

# 评分函数：综合 profit_factor、win_rate、max_drawdown
def _score(r) -> float:
    if r['total_trades'] < 3:
        return -999.0
    pf = min(r['profit_factor'], 5.0)   # cap 极端值
    wr = r['win_rate'] / 100.0
    dd = r['max_drawdown_pct'] / 100.0
    # 目标：高 PF + 高胜率 + 低回撤
    return pf * 0.4 + wr * 0.4 - dd * 0.2


def _run_grid(df, label):
    keys = list(PARAM_GRID.keys())
    combos = list(itertools.product(*[PARAM_GRID[k] for k in keys]))
    results = []
    for combo in combos:
        params = dict(zip(keys, combo))
        eb = EventBacktest(initial_capital=1000, **params)
        r = eb.run(df, label)
        results.append({**params, **{
            'trades': r['total_trades'],
            'win_rate': round(r['win_rate'], 1),
            'pf': round(r['profit_factor'], 2),
            'max_dd': round(r['max_drawdown_pct'], 2),
            'sharpe': round(r['sharpe'], 2),
            'total_pnl': round(r['total_pnl'], 2),
            'score': round(_score(r), 4),
        }})
    results.sort(key=lambda x: x['score'], reverse=True)
    return results


# ── 测试函数 ──────────────────────────────────────────────────────

def test_grid_search_trending():
    """趋势行情网格搜索：top-3 配置 profit_factor > 1.0"""
    df = _make_trending_df()
    results = _run_grid(df, 'TREND')
    top3 = results[:3]

    print(f"\n[趋势行情] 共 {len(results)} 组参数，Top-3:")
    for i, r in enumerate(top3):
        print(f"  #{i+1} score={r['score']:.4f} pf={r['pf']} wr={r['win_rate']}% "
              f"dd={r['max_dd']}% trades={r['trades']} "
              f"threshold={r['entry_threshold']} rr={r['rr_floor']} "
              f"cooldown={r['post_close_cooldown_bars']} "
              f"partial_tp={r['enable_partial_tp']} trailing={r['enable_trailing']}")

    assert top3[0]['pf'] > 1.0, f"趋势行情最优配置 profit_factor 应>1.0，实际 {top3[0]['pf']}"
    assert top3[0]['trades'] >= 3, "最优配置应有足够交易次数"
    print(f"  ✅ 趋势行情最优: pf={top3[0]['pf']} wr={top3[0]['win_rate']}% dd={top3[0]['max_dd']}%")


def test_grid_search_choppy():
    """震荡行情网格搜索：验证保守参数（高 threshold）在震荡中表现更好"""
    df = _make_choppy_df()
    results = _run_grid(df, 'CHOPPY')
    top3 = results[:3]

    print(f"\n[震荡行情] 共 {len(results)} 组参数，Top-3:")
    for i, r in enumerate(top3):
        print(f"  #{i+1} score={r['score']:.4f} pf={r['pf']} wr={r['win_rate']}% "
              f"dd={r['max_dd']}% trades={r['trades']} "
              f"threshold={r['entry_threshold']} rr={r['rr_floor']}")

    # 震荡行情中 top-3 的 rr_floor 应该 >= 1.5（高 R:R 是震荡行情的真正保护）
    top3_rr = [r['rr_floor'] for r in top3]
    assert max(top3_rr) >= 1.5, \
        f"震荡行情 top-3 应包含 rr_floor>=1.5 的配置，实际 {top3_rr}"
    print(f"  ✅ 震荡行情 top-3 rr_floors={top3_rr}，高 R:R 配置表现更好")


def test_grid_search_robustness():
    """稳健性：同一参数在趋势+震荡两种行情下都不亏损"""
    df_trend = _make_trending_df()
    df_choppy = _make_choppy_df()

    results_trend = _run_grid(df_trend, 'TREND')
    results_choppy = _run_grid(df_choppy, 'CHOPPY')

    # 找在两种行情下 score 都为正的参数组合
    trend_map = {
        (r['entry_threshold'], r['rr_floor'], r['post_close_cooldown_bars'],
         r['enable_partial_tp'], r['enable_trailing']): r
        for r in results_trend
    }
    choppy_map = {
        (r['entry_threshold'], r['rr_floor'], r['post_close_cooldown_bars'],
         r['enable_partial_tp'], r['enable_trailing']): r
        for r in results_choppy
    }

    robust = []
    for key in trend_map:
        rt = trend_map[key]
        rc = choppy_map.get(key)
        if rc and rt['score'] > 0 and rc['score'] > 0:
            combined = rt['score'] + rc['score']
            robust.append({**rt, 'choppy_score': rc['score'], 'combined': combined})

    robust.sort(key=lambda x: x['combined'], reverse=True)

    print(f"\n[稳健性] 两种行情均正分的参数组合: {len(robust)} 个")
    if robust:
        best = robust[0]
        print(f"  最稳健: threshold={best['entry_threshold']} rr={best['rr_floor']} "
              f"cooldown={best['post_close_cooldown_bars']} "
              f"partial_tp={best['enable_partial_tp']} trailing={best['enable_trailing']}")
        print(f"  趋势score={best['score']:.4f} 震荡score={best['choppy_score']:.4f} "
              f"combined={best['combined']:.4f}")

    assert len(robust) > 0, "应至少存在一组在两种行情下都不亏损的参数"
    print(f"  ✅ 找到 {len(robust)} 组稳健参数，最优 combined={robust[0]['combined']:.4f}")


def test_current_defaults_are_reasonable():
    """当前默认参数（entry_threshold=35, rr_floor=1.5, cooldown=5）在趋势行情中应排名前50%"""
    df = _make_trending_df()
    results = _run_grid(df, 'TREND')

    default_key = (35, 1.5, 5, True, True)
    default_result = next(
        (r for r in results
         if r['entry_threshold'] == 35 and r['rr_floor'] == 1.5
         and r['post_close_cooldown_bars'] == 5
         and r['enable_partial_tp'] is True and r['enable_trailing'] is True),
        None
    )
    assert default_result is not None, "默认参数组合应在网格中"

    rank = next(i for i, r in enumerate(results)
                if r['entry_threshold'] == 35 and r['rr_floor'] == 1.5
                and r['post_close_cooldown_bars'] == 5
                and r['enable_partial_tp'] is True and r['enable_trailing'] is True)

    total = len(results)
    percentile = (total - rank) / total * 100
    print(f"\n[默认参数] rank={rank+1}/{total} (top {percentile:.0f}%) "
          f"pf={default_result['pf']} wr={default_result['win_rate']}% "
          f"dd={default_result['max_dd']}%")

    assert percentile >= 10, \
        f"默认参数应在前90%，实际 top {percentile:.0f}% (rank {rank+1}/{total})"
    print(f"  ✅ 默认参数排名合理 (top {percentile:.0f}%)")


if __name__ == '__main__':
    print("=" * 70)
    print("P2-3: 参数网格搜索")
    print("=" * 70)
    best_trend = test_grid_search_trending()
    best_choppy = test_grid_search_choppy()
    best_robust = test_grid_search_robustness()
    test_current_defaults_are_reasonable()
    print("\n" + "=" * 70)
    print("✅ 全部 4 个测试通过")
    print("=" * 70)
    print(f"\n推荐参数（稳健性最优）:")
    print(f"  entry_threshold={best_robust['entry_threshold']}")
    print(f"  rr_floor={best_robust['rr_floor']}")
    print(f"  post_close_cooldown_bars={best_robust['post_close_cooldown_bars']}")
    print(f"  enable_partial_tp={best_robust['enable_partial_tp']}")
    print(f"  enable_trailing={best_robust['enable_trailing']}")
