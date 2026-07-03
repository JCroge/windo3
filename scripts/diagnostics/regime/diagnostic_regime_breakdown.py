#!/usr/bin/env python3
"""
体制分类详细分解诊断工具
输出候选池构成、加权前后占比、阈值判定过程
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime

def load_recent_decisions(hours=6):
    """加载最近 N 小时的决策记录"""
    with open('data/decision_replay_tape.jsonl') as f:
        decisions = [json.loads(line) for line in f]

    now = datetime.now().timestamp()
    cutoff = now - hours * 3600
    recent = [d for d in decisions if d.get('timestamp', 0) > cutoff]
    return recent

def extract_regime_snapshot(decision):
    """从决策快照中提取体制管理器状态"""
    snapshot = decision.get('state_snapshot_before_decision', {})
    regime_mgr = snapshot.get('_regime_manager', {})

    return {
        'current_regime': regime_mgr.get('_current_regime', 'unknown'),
        'symbol_directions': regime_mgr.get('_symbol_directions', {}),
        'last_update': regime_mgr.get('_last_update_time', 0),
    }

def analyze_regime_composition(symbol_directions):
    """分析候选池构成"""
    if not symbol_directions:
        return None

    direction_count = Counter(symbol_directions.values())
    total = sum(direction_count.values())

    if total == 0:
        return None

    return {
        'total': total,
        'bullish': direction_count.get('bullish', 0),
        'bearish': direction_count.get('bearish', 0),
        'neutral': direction_count.get('neutral', 0),
        'bullish_pct': direction_count.get('bullish', 0) / total * 100,
        'bearish_pct': direction_count.get('bearish', 0) / total * 100,
        'neutral_pct': direction_count.get('neutral', 0) / total * 100,
        'symbols_by_direction': defaultdict(list),
    }

def compute_weighted_regime(symbol_directions):
    """模拟加权计算（BTC 2.0x, ETH 1.5x）"""
    if not symbol_directions:
        return None

    weights = {
        'BTC-USDT': 2.0,
        'ETH-USDT': 1.5,
    }

    weighted_counts = {'bullish': 0.0, 'bearish': 0.0, 'neutral': 0.0}

    for symbol, direction in symbol_directions.items():
        weight = weights.get(symbol, 1.0)
        weighted_counts[direction] = weighted_counts.get(direction, 0.0) + weight

    total = sum(weighted_counts.values())
    if total == 0:
        return None

    return {
        'bullish_weighted_pct': weighted_counts['bullish'] / total * 100,
        'bearish_weighted_pct': weighted_counts['bearish'] / total * 100,
        'neutral_weighted_pct': weighted_counts['neutral'] / total * 100,
    }

def main():
    print("=== 体制分类详细分解诊断 ===\n")

    # 加载最近 6 小时的决策
    decisions = load_recent_decisions(hours=6)
    print(f"加载最近 6h 决策记录: {len(decisions)} 条\n")

    if not decisions:
        print("错误: 没有找到最近的决策记录")
        return

    # 找到最近的有完整 regime 快照的决策
    valid_decisions = []
    for d in reversed(decisions):
        regime = extract_regime_snapshot(d)
        if regime['symbol_directions']:
            valid_decisions.append((d, regime))
            if len(valid_decisions) >= 5:  # 取最近 5 个样本
                break

    if not valid_decisions:
        print("错误: 没有找到包含体制快照的决策记录")
        return

    print(f"找到 {len(valid_decisions)} 个有效体制快照样本\n")
    print("=" * 70)

    # 分析每个样本
    for idx, (decision, regime) in enumerate(valid_decisions, 1):
        symbol = decision.get('symbol', 'N/A')
        ts = datetime.fromtimestamp(decision.get('timestamp', 0))
        current_regime = regime['current_regime']
        symbol_directions = regime['symbol_directions']

        print(f"\n样本 #{idx}")
        print(f"时间: {ts.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"标的: {symbol}")
        print(f"判定体制: {current_regime}")
        print()

        # 候选池构成
        comp = analyze_regime_composition(symbol_directions)
        if not comp:
            print("  候选池为空，跳过")
            continue

        print(f"候选池: {comp['total']} 个币种")
        print()

        # 按方向分组显示币种
        symbols_by_dir = defaultdict(list)
        for sym, direction in symbol_directions.items():
            symbols_by_dir[direction].append(sym)

        print("原始投票（无权重）:")
        for direction in ['bullish', 'bearish', 'neutral']:
            count = comp.get(direction, 0)
            pct = comp.get(f'{direction}_pct', 0)
            symbols = symbols_by_dir.get(direction, [])
            print(f"  {direction:8s}: {count:2d} 币 ({pct:5.1f}%)", end='')
            if symbols:
                # 高亮 BTC/ETH
                display_symbols = []
                for s in symbols[:5]:  # 最多显示 5 个
                    if s in ['BTC-USDT', 'ETH-USDT']:
                        display_symbols.append(f"**{s}**")
                    else:
                        display_symbols.append(s)
                print(f" - {', '.join(display_symbols)}", end='')
                if len(symbols) > 5:
                    print(f" ... +{len(symbols)-5} 个", end='')
            print()

        print()

        # 加权后
        weighted = compute_weighted_regime(symbol_directions)
        if weighted:
            print("加权后（BTC 2.0x, ETH 1.5x）:")
            print(f"  bullish: {weighted['bullish_weighted_pct']:.1f}%")
            print(f"  bearish: {weighted['bearish_weighted_pct']:.1f}%")
            print(f"  neutral: {weighted['neutral_weighted_pct']:.1f}%")
            print()

            # 阈值判定（当前代码：bullish/bearish 0.5, choppy 0.6）
            bullish_pct = weighted['bullish_weighted_pct']
            bearish_pct = weighted['bearish_weighted_pct']
            choppy_pct = weighted['neutral_weighted_pct']

            print("阈值判定（bullish/bearish ≥50%, choppy ≥60%）:")

            if bullish_pct >= 50.0:
                print(f"  ✓ bullish {bullish_pct:.1f}% ≥ 50.0% → regime = bullish")
            else:
                print(f"  ✗ bullish {bullish_pct:.1f}% < 50.0%（差 {50.0 - bullish_pct:.1f}%）")

            if bearish_pct >= 50.0:
                print(f"  ✓ bearish {bearish_pct:.1f}% ≥ 50.0% → regime = bearish")
            else:
                print(f"  ✗ bearish {bearish_pct:.1f}% < 50.0%（差 {50.0 - bearish_pct:.1f}%）")

            if choppy_pct >= 60.0:
                print(f"  ✓ choppy {choppy_pct:.1f}% ≥ 60.0% → regime = choppy")
            else:
                print(f"  ✗ choppy {choppy_pct:.1f}% < 60.0%")

            # 判定逻辑（当前代码优先级：bullish > bearish > choppy > mixed）
            if bullish_pct >= 50.0:
                predicted = 'bullish'
            elif bearish_pct >= 50.0:
                predicted = 'bearish'
            elif choppy_pct >= 60.0:
                predicted = 'choppy'
            else:
                predicted = 'mixed'

            print()
            print(f"预测体制: {predicted}")
            if predicted == current_regime:
                print(f"✓ 与实际判定一致")
            else:
                print(f"✗ 与实际判定不一致（实际: {current_regime}）")

        print("=" * 70)

if __name__ == '__main__':
    main()
