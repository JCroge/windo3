#!/usr/bin/env python3
"""
实时抓取 live 系统的体制状态并详细分解
"""
import json
import sys
from collections import Counter, defaultdict

def load_judge_state():
    """加载 Judge 状态（包含体制管理器）"""
    try:
        with open('data/judge_state.json') as f:
            return json.load(f)
    except FileNotFoundError:
        print("错误: judge_state.json 不存在")
        return None

def analyze_regime_breakdown(judge_state):
    """分析体制分类的详细分解"""
    regime_mgr = judge_state.get('_regime_manager', {})

    current_regime = regime_mgr.get('_current_regime', 'unknown')
    symbol_directions = regime_mgr.get('_symbol_directions', {})

    if not symbol_directions:
        print("错误: 候选池为空")
        return

    print("=== 体制分类实时状态详细分解 ===\n")
    print(f"当前判定体制: {current_regime}\n")

    # 候选池统计
    total = len(symbol_directions)
    direction_count = Counter(symbol_directions.values())

    print(f"候选池: {total} 个币种\n")

    # 按方向分组
    symbols_by_dir = defaultdict(list)
    for sym, direction in symbol_directions.items():
        symbols_by_dir[direction].append(sym)

    print("原始投票（无权重）:")
    for direction in ['bullish', 'bearish', 'neutral']:
        count = direction_count.get(direction, 0)
        pct = count / total * 100 if total > 0 else 0
        symbols = sorted(symbols_by_dir.get(direction, []))

        print(f"  {direction:8s}: {count:2d} 币 ({pct:5.1f}%)")
        if symbols:
            # 分行显示，高亮 BTC/ETH
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

    # 加权计算
    weights = {
        'BTC-USDT': 2.0,
        'ETH-USDT': 1.5,
    }

    weighted_counts = {'bullish': 0.0, 'bearish': 0.0, 'neutral': 0.0}

    for symbol, direction in symbol_directions.items():
        weight = weights.get(symbol, 1.0)
        weighted_counts[direction] += weight

    total_weighted = sum(weighted_counts.values())

    print("加权后（BTC 2.0x, ETH 1.5x）:")
    print(f"  bullish: {weighted_counts['bullish']:.1f} 权重 ({weighted_counts['bullish']/total_weighted*100:.1f}%)")
    print(f"  bearish: {weighted_counts['bearish']:.1f} 权重 ({weighted_counts['bearish']/total_weighted*100:.1f}%)")
    print(f"  neutral: {weighted_counts['neutral']:.1f} 权重 ({weighted_counts['neutral']/total_weighted*100:.1f}%)")
    print(f"  总权重: {total_weighted:.1f}")
    print()

    # 阈值判定
    bullish_pct = weighted_counts['bullish'] / total_weighted * 100
    bearish_pct = weighted_counts['bearish'] / total_weighted * 100
    neutral_pct = weighted_counts['neutral'] / total_weighted * 100

    print("阈值判定（当前代码：bullish/bearish ≥50%, choppy ≥60%）:")

    if bullish_pct >= 50.0:
        print(f"  ✓ bullish {bullish_pct:.1f}% ≥ 50.0% → 满足 bullish 条件")
    else:
        gap = 50.0 - bullish_pct
        print(f"  ✗ bullish {bullish_pct:.1f}% < 50.0%（差 {gap:.1f}% / 需 +{gap/100*total_weighted:.1f} 权重）")

    if bearish_pct >= 50.0:
        print(f"  ✓ bearish {bearish_pct:.1f}% ≥ 50.0% → 满足 bearish 条件")
    else:
        gap = 50.0 - bearish_pct
        print(f"  ✗ bearish {bearish_pct:.1f}% < 50.0%（差 {gap:.1f}% / 需 +{gap/100*total_weighted:.1f} 权重）")

    if neutral_pct >= 60.0:
        print(f"  ✓ choppy {neutral_pct:.1f}% ≥ 60.0% → 满足 choppy 条件")
    else:
        print(f"  ✗ choppy {neutral_pct:.1f}% < 60.0%")

    print()

    # 判定逻辑（优先级：bullish > bearish > choppy > mixed）
    if bullish_pct >= 50.0:
        predicted = 'bullish'
    elif bearish_pct >= 50.0:
        predicted = 'bearish'
    elif neutral_pct >= 60.0:
        predicted = 'choppy'
    else:
        predicted = 'mixed'

    print(f"根据当前逻辑预测: {predicted}")
    if predicted == current_regime:
        print(f"✓ 与实际判定一致\n")
    else:
        print(f"✗ 与实际判定不一致（实际: {current_regime}）\n")

    # 关键币种分析
    print("=" * 70)
    print("\n关键币种状态:")
    for key_symbol in ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'DOGE-USDT']:
        direction = symbol_directions.get(key_symbol, 'N/A')
        weight = weights.get(key_symbol, 1.0)
        print(f"  {key_symbol:12s}: {direction:8s} (权重 {weight}x)")

    print("\n" + "=" * 70)

    # 问题总结
    print("\n诊断结论:")
    if bullish_pct < 50.0:
        print(f"1. bullish 占比 {bullish_pct:.1f}% 未达 50% 阈值")
        print(f"   - 差距: {50.0 - bullish_pct:.1f}%")
        print(f"   - 即使 BTC/ETH 都是 bullish，其他币的 neutral/bearish 压倒了它们")

    if neutral_pct >= 60.0:
        print(f"2. neutral 占比 {neutral_pct:.1f}% 超过 60% 阈值")
        print(f"   - 候选池中 {direction_count.get('neutral', 0)}/{total} 币是 neutral")
        print(f"   - 说明大部分币没有明确趋势方向")

    # 如果 BTC/ETH 是 bullish 但整体判 choppy
    btc_dir = symbol_directions.get('BTC-USDT', '')
    eth_dir = symbol_directions.get('ETH-USDT', '')
    if (btc_dir == 'bullish' or eth_dir == 'bullish') and current_regime == 'choppy':
        print(f"3. ⚠️ 关键矛盾: BTC/ETH 有 bullish 但整体判 choppy")
        print(f"   - 说明候选池中其他币的 neutral 权重过大")
        print(f"   - BTC/ETH 权重 (2.0x/1.5x) 不足以主导判定")

def main():
    judge_state = load_judge_state()
    if judge_state:
        analyze_regime_breakdown(judge_state)

if __name__ == '__main__':
    main()
