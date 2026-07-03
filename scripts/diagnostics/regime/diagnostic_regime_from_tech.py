#!/usr/bin/env python3
"""
从当前技术分析缓存重建体制计算过程
模拟 market_regime.py::_compute_raw_regime 的逻辑
"""
import json
from collections import defaultdict

def load_judge_state():
    with open('data/judge_state.json') as f:
        return json.load(f)

def simulate_regime_computation(techs):
    """模拟体制计算逻辑"""
    if not techs:
        print("错误: 技术分析缓存为空")
        return

    BTC_WEIGHT = 2.0
    ETH_WEIGHT = 1.5

    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    total = 0

    btc_bias = None
    eth_bias = None

    symbols_by_direction = defaultdict(list)

    for sym, tech in techs.items():
        trend = tech.get('trend', {})
        direction = trend.get('direction', 'neutral')
        total += 1

        symbols_by_direction[direction].append(sym)

        if direction == 'bullish':
            bullish_count += 1
        elif direction == 'bearish':
            bearish_count += 1
        else:
            neutral_count += 1

        base = sym.split('-')[0].upper()
        if base == 'BTC':
            btc_bias = trend.get('higher_tf_bias') or trend.get('daily_bias')
        elif base == 'ETH':
            eth_bias = trend.get('higher_tf_bias') or trend.get('daily_bias')

    if total == 0:
        print("候选池为空")
        return

    print("=== 体制分类实时诊断（从技术分析缓存重建）===\n")
    print(f"候选池: {total} 个币种\n")

    # 原始统计
    print("步骤 1: 原始投票（无权重）")
    for direction in ['bullish', 'bearish', 'neutral']:
        count = len(symbols_by_direction[direction])
        pct = count / total * 100
        symbols = sorted(symbols_by_direction[direction])

        print(f"  {direction:8s}: {count:2d} 币 ({pct:5.1f}%)")
        if symbols:
            for i in range(0, len(symbols), 5):
                batch = symbols[i:i+5]
                display = []
                for s in batch:
                    if s in ['BTC-USDT', 'ETH-USDT']:
                        display.append(f"★{s}★")
                    else:
                        display.append(s)
                print(f"             {', '.join(display)}")

    print()

    # Anchor bias
    print("步骤 2: BTC/ETH Anchor Bias")
    print(f"  BTC higher_tf_bias: {btc_bias or 'N/A'} (权重 {BTC_WEIGHT}x)")
    print(f"  ETH higher_tf_bias: {eth_bias or 'N/A'} (权重 {ETH_WEIGHT}x)")

    anchor_bullish_weight = 0
    anchor_bearish_weight = 0

    if btc_bias == 'bullish':
        anchor_bullish_weight += BTC_WEIGHT
    elif btc_bias == 'bearish':
        anchor_bearish_weight += BTC_WEIGHT

    if eth_bias == 'bullish':
        anchor_bullish_weight += ETH_WEIGHT
    elif eth_bias == 'bearish':
        anchor_bearish_weight += ETH_WEIGHT

    print(f"  → anchor_bullish_weight: {anchor_bullish_weight}")
    print(f"  → anchor_bearish_weight: {anchor_bearish_weight}")
    print()

    # 加权计算
    print("步骤 3: 加权计算")
    weighted_bullish = bullish_count + anchor_bullish_weight
    weighted_bearish = bearish_count + anchor_bearish_weight
    weighted_total = total + (BTC_WEIGHT if btc_bias else 0) + (ETH_WEIGHT if eth_bias else 0)

    bullish_pct = weighted_bullish / weighted_total * 100
    bearish_pct = weighted_bearish / weighted_total * 100
    neutral_pct = neutral_count / total * 100  # neutral 不加权

    print(f"  weighted_bullish = {bullish_count} + {anchor_bullish_weight} = {weighted_bullish}")
    print(f"  weighted_bearish = {bearish_count} + {anchor_bearish_weight} = {weighted_bearish}")
    print(f"  weighted_total   = {total} + {BTC_WEIGHT if btc_bias else 0} + {ETH_WEIGHT if eth_bias else 0} = {weighted_total}")
    print()
    print(f"  bullish_pct = {weighted_bullish}/{weighted_total} = {bullish_pct:.1f}%")
    print(f"  bearish_pct = {weighted_bearish}/{weighted_total} = {bearish_pct:.1f}%")
    print(f"  neutral_pct = {neutral_count}/{total} = {neutral_pct:.1f}% (不加权)")
    print()

    # 阈值判定
    print("步骤 4: 阈值判定")
    print(f"  bullish {bullish_pct:.1f}% >= 50%? ", end='')
    if bullish_pct >= 50.0:
        print(f"✓ → regime = bullish")
        regime = 'bullish'
    else:
        print(f"✗ (差 {50.0 - bullish_pct:.1f}%)")
        regime = None

    print(f"  bearish {bearish_pct:.1f}% >= 50%? ", end='')
    if bearish_pct >= 50.0:
        print(f"✓ → regime = bearish")
        if not regime:
            regime = 'bearish'
    else:
        print(f"✗ (差 {50.0 - bearish_pct:.1f}%)")

    print(f"  neutral {neutral_pct:.1f}% >= 60%? ", end='')
    if neutral_pct >= 60.0:
        print(f"✓ → regime = choppy")
        if not regime:
            regime = 'choppy'
    else:
        print(f"✗ (差 {60.0 - neutral_pct:.1f}%)")

    if not regime:
        regime = 'mixed'
        print(f"  → 无阈值满足，默认 regime = mixed")

    print()
    print("=" * 70)
    print(f"\n最终判定: {regime}\n")

    # 问题诊断
    print("=" * 70)
    print("\n问题诊断:")

    if bullish_pct < 50.0 and (btc_bias == 'bullish' or eth_bias == 'bullish'):
        print(f"\n⚠️ 关键矛盾:")
        print(f"  - BTC/ETH anchor 有 bullish bias")
        print(f"  - 但加权后 bullish 仍只有 {bullish_pct:.1f}%，未达 50% 阈值")
        print(f"  - 说明候选池中 {neutral_count}/{total} 个 neutral 币压倒了 anchor 权重")
        print(f"\n根因:")
        print(f"  - BTC/ETH 权重 ({BTC_WEIGHT}/{ETH_WEIGHT}) 只影响 bias 的加权")
        print(f"  - 但候选池中大部分币的 direction='neutral' 本身不参与 bullish/bearish 计数")
        print(f"  - neutral 占比 {neutral_pct:.1f}% 触发 choppy 判定，直接覆盖 bullish")

    if neutral_pct >= 60.0:
        print(f"\n⚠️ Choppy 触发:")
        print(f"  - Neutral 占比 {neutral_pct:.1f}% >= 60%")
        print(f"  - {neutral_count}/{total} 个币被标记为 neutral（无明确趋势）")
        print(f"  - 这些币包括:")
        for sym in sorted(symbols_by_direction['neutral'])[:10]:
            print(f"    - {sym}")
        if len(symbols_by_direction['neutral']) > 10:
            print(f"    ... 还有 {len(symbols_by_direction['neutral']) - 10} 个")

def main():
    judge_state = load_judge_state()
    techs = judge_state.get('_symbol_tech_cache', {})

    if not techs:
        print("错误: _symbol_tech_cache 为空，无法诊断")
        print("\n提示: live 系统可能刚启动，技术分析缓存尚未填充")
        return

    simulate_regime_computation(techs)

if __name__ == '__main__':
    main()
