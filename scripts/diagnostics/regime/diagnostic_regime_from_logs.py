#!/usr/bin/env python3
"""
从日志解析最近的技术分析数据，重建体制计算
"""
import re
from collections import defaultdict

def parse_tech_analysis_from_logs(log_file, last_n_minutes=10):
    """从日志中解析最近 N 分钟的技术分析"""
    techs = {}

    # 正则匹配技术分析日志
    # 例: [技术分析] BTC-USDT 趋势=neutral 强度=28 杠杆风险=low LLM=neutral/58
    pattern = re.compile(r'\[技术分析\] (\S+) 趋势=(\w+) 强度=(\d+)')

    with open(log_file) as f:
        lines = f.readlines()

    # 只看最后 1000 行（大约最近 10-15 分钟）
    recent_lines = lines[-1000:]

    for line in recent_lines:
        match = pattern.search(line)
        if match:
            symbol = match.group(1)
            direction = match.group(2)
            strength = int(match.group(3))

            # 最新的覆盖旧的
            techs[symbol] = {
                'trend': {
                    'direction': direction,
                    'strength': strength,
                }
            }

    return techs

def simulate_regime_computation(techs):
    """模拟体制计算逻辑"""
    if not techs:
        print("错误: 没有解析到技术分析数据")
        return

    BTC_WEIGHT = 2.0
    ETH_WEIGHT = 1.5

    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    total = 0

    # 注意: 日志里没有 higher_tf_bias/daily_bias，我们只能看 direction
    # 假设 BTC/ETH 的 direction 可以作为 bias 的代理
    btc_direction = None
    eth_direction = None

    symbols_by_direction = defaultdict(list)

    for sym, tech in techs.items():
        direction = tech['trend']['direction']
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
            btc_direction = direction
        elif base == 'ETH':
            eth_direction = direction

    if total == 0:
        print("候选池为空")
        return

    print("=== 体制分类诊断（从日志重建）===\n")
    print(f"数据源: 最近 ~1000 行日志")
    print(f"候选池: {total} 个币种\n")

    # 原始统计
    print("原始投票（无权重）:")
    for direction in ['bullish', 'bearish', 'neutral']:
        count = len(symbols_by_direction[direction])
        pct = count / total * 100
        symbols = sorted(symbols_by_direction[direction])

        print(f"  {direction:8s}: {count:2d} 币 ({pct:5.1f}%)")
        if symbols:
            for i in range(0, len(symbols), 6):
                batch = symbols[i:i+6]
                display = []
                for s in batch:
                    if s in ['BTC-USDT', 'ETH-USDT']:
                        display.append(f"★{s}★")
                    else:
                        display.append(s)
                print(f"             {', '.join(display)}")

    print()

    # BTC/ETH 状态
    print("BTC/ETH 状态:")
    print(f"  BTC-USDT direction: {btc_direction or 'N/A'}")
    print(f"  ETH-USDT direction: {eth_direction or 'N/A'}")
    print()

    # 简化权重计算（用 direction 代替 bias）
    print("⚠️ 注意: 日志里没有 higher_tf_bias 数据")
    print("   这里用 direction 作为粗略代理进行诊断\n")

    anchor_bullish_weight = 0
    anchor_bearish_weight = 0

    if btc_direction == 'bullish':
        anchor_bullish_weight += BTC_WEIGHT
    elif btc_direction == 'bearish':
        anchor_bearish_weight += BTC_WEIGHT

    if eth_direction == 'bullish':
        anchor_bullish_weight += ETH_WEIGHT
    elif eth_direction == 'bearish':
        anchor_bearish_weight += ETH_WEIGHT

    # 加权计算
    weighted_bullish = bullish_count + anchor_bullish_weight
    weighted_bearish = bearish_count + anchor_bearish_weight
    weighted_total = total + (BTC_WEIGHT if btc_direction else 0) + (ETH_WEIGHT if eth_direction else 0)

    bullish_pct = weighted_bullish / weighted_total * 100
    bearish_pct = weighted_bearish / weighted_total * 100
    neutral_pct = neutral_count / total * 100

    print("加权后:")
    print(f"  bullish: {weighted_bullish:.1f}/{weighted_total:.1f} = {bullish_pct:.1f}%")
    print(f"  bearish: {weighted_bearish:.1f}/{weighted_total:.1f} = {bearish_pct:.1f}%")
    print(f"  neutral: {neutral_count}/{total} = {neutral_pct:.1f}% (不加权)")
    print()

    # 阈值判定
    print("阈值判定:")
    if bullish_pct >= 50.0:
        print(f"  ✓ bullish {bullish_pct:.1f}% >= 50% → regime = bullish")
        regime = 'bullish'
    else:
        print(f"  ✗ bullish {bullish_pct:.1f}% < 50% (差 {50.0 - bullish_pct:.1f}%)")
        regime = None

    if bearish_pct >= 50.0:
        print(f"  ✓ bearish {bearish_pct:.1f}% >= 50% → regime = bearish")
        if not regime:
            regime = 'bearish'
    else:
        print(f"  ✗ bearish {bearish_pct:.1f}% < 50% (差 {50.0 - bearish_pct:.1f}%)")

    if neutral_pct >= 60.0:
        print(f"  ✓ neutral {neutral_pct:.1f}% >= 60% → regime = choppy")
        if not regime:
            regime = 'choppy'
    else:
        print(f"  ✗ neutral {neutral_pct:.1f}% < 60%")

    if not regime:
        regime = 'mixed'

    print(f"\n最终判定: {regime}")
    print("\n" + "=" * 70)

    # 诊断
    print("\n问题诊断:\n")

    if neutral_pct >= 60.0:
        print(f"✗ Neutral 占比 {neutral_pct:.1f}% 超过 60% 阈值")
        print(f"  → 候选池中 {neutral_count}/{total} 个币被标记为 neutral")
        print(f"  → 即使 BTC/ETH 加权，也无法改变 neutral 主导的事实")
        print(f"  → 这就是为什么体制改进失败的根本原因")
        print()

    if btc_direction == 'neutral' and eth_direction == 'neutral':
        print(f"✗ BTC 和 ETH 自己也是 neutral")
        print(f"  → Anchor 权重完全没起作用（neutral 不加权到 bullish/bearish）")
        print(f"  → 当前市场可能真的是震荡，不是误判")
        print()

    if (btc_direction == 'bullish' or eth_direction == 'bullish') and regime == 'choppy':
        print(f"✗ BTC/ETH 有 bullish 但整体判 choppy")
        print(f"  → BTC/ETH 权重被其他 {neutral_count} 个 neutral 币压倒")
        print(f"  → 需要提高 BTC/ETH 权重或降低 choppy 阈值")

def main():
    log_file = 'logs/live_20260701_202916.log'
    techs = parse_tech_analysis_from_logs(log_file)

    if techs:
        simulate_regime_computation(techs)
    else:
        print("无法从日志解析技术分析数据")

if __name__ == '__main__':
    main()
