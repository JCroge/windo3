#!/usr/bin/env python3
"""
体制分类改进反事实验证 - Anchor 权重增强 A/B 测试

目标：
量化「BTC/ETH anchor 权重增强 + 阈值调整」对历史决策和 PnL 的影响

方法：
1. 加载决策磁带（近 7 天）
2. 两臂回放：
   - Baseline：旧体制逻辑（bullish/bearish >= 0.6，无 anchor 权重）
   - Perturbed：新体制逻辑（anchor 权重 BTC 2.0/ETH 1.5，阈值 >= 0.5）
3. 比较：
   - Regime 标签分布变化
   - Accept/reject 决策翻转
   - 翻转单的反事实 PnL（TP1 保守结算）
4. 诚实门判断

输出：JSON 格式
"""

import json
import time
from pathlib import Path
from collections import Counter, defaultdict
from utils.market_regime import RegimeManager

# 配置
TAPE_PATH = Path("data/decision_replay_tape.jsonl")
LOOKBACK_DAYS = 7
OUTPUT_PATH = Path("data/cf_regime_anchor_weight_ab.json")


def load_recent_records(days=7):
    """加载近 N 天的决策磁带"""
    cutoff_ts = time.time() - (days * 86400)
    records = []

    with open(TAPE_PATH, 'r') as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                if record.get('timestamp', 0) >= cutoff_ts and record.get('replayable'):
                    records.append(record)
            except json.JSONDecodeError:
                continue

    return records


def compute_regime_baseline(symbol_techs: dict) -> str:
    """
    Baseline 体制计算逻辑（旧逻辑）
    - bullish/bearish 阈值 >= 0.6
    - anchor 仅作为允许条件，无权重
    """
    if not symbol_techs:
        return 'mixed'

    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    total = 0
    atr_values = []

    btc_bias = None
    eth_bias = None

    for sym, tech in symbol_techs.items():
        trend = tech.get('trend', {})
        direction = trend.get('direction', 'neutral')
        total += 1
        if direction == 'bullish':
            bullish_count += 1
        elif direction == 'bearish':
            bearish_count += 1
        else:
            neutral_count += 1

        atr_pct = tech.get('momentum', {}).get('atr_pct', 0)
        if atr_pct > 0:
            atr_values.append(atr_pct)

        base = sym.split('-')[0].upper()
        if base == 'BTC':
            btc_bias = trend.get('higher_tf_bias', trend.get('daily_bias'))
        elif base == 'ETH':
            eth_bias = trend.get('higher_tf_bias', trend.get('daily_bias'))

    if total == 0:
        return 'mixed'

    bullish_pct = bullish_count / total
    bearish_pct = bearish_count / total
    neutral_pct = neutral_count / total

    anchor_bullish = btc_bias == 'bullish' or eth_bias == 'bullish'
    anchor_bearish = btc_bias == 'bearish' or eth_bias == 'bearish'
    anchor_neutral = not anchor_bullish and not anchor_bearish

    avg_atr = sum(atr_values) / len(atr_values) if atr_values else 0.02
    high_vol = avg_atr > 0.04

    # 旧阈值：0.6
    if bullish_pct >= 0.6 and (anchor_bullish or anchor_neutral):
        return 'bullish'
    elif bearish_pct >= 0.6 and (anchor_bearish or anchor_neutral):
        return 'bearish'
    elif high_vol and neutral_pct < 0.4:
        return 'mixed'
    elif not high_vol and neutral_pct >= 0.5:
        return 'choppy'
    else:
        return 'mixed'


def compute_regime_perturbed(symbol_techs: dict) -> str:
    """
    Perturbed 体制计算逻辑（新逻辑）
    - BTC 权重 2.0，ETH 权重 1.5
    - bullish/bearish 阈值 >= 0.5
    - choppy 阈值从 0.5 提至 0.6
    """
    if not symbol_techs:
        return 'mixed'

    BTC_WEIGHT = 2.0
    ETH_WEIGHT = 1.5

    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    total = 0
    atr_values = []

    btc_bias = None
    eth_bias = None

    for sym, tech in symbol_techs.items():
        trend = tech.get('trend', {})
        direction = trend.get('direction', 'neutral')
        total += 1
        if direction == 'bullish':
            bullish_count += 1
        elif direction == 'bearish':
            bearish_count += 1
        else:
            neutral_count += 1

        atr_pct = tech.get('momentum', {}).get('atr_pct', 0)
        if atr_pct > 0:
            atr_values.append(atr_pct)

        base = sym.split('-')[0].upper()
        if base == 'BTC':
            btc_bias = trend.get('higher_tf_bias', trend.get('daily_bias'))
        elif base == 'ETH':
            eth_bias = trend.get('higher_tf_bias', trend.get('daily_bias'))

    if total == 0:
        return 'mixed'

    # Anchor 加权
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

    weighted_bullish = bullish_count + anchor_bullish_weight
    weighted_bearish = bearish_count + anchor_bearish_weight
    weighted_total = total + (BTC_WEIGHT if btc_bias else 0) + (ETH_WEIGHT if eth_bias else 0)

    if weighted_total == 0:
        return 'mixed'

    bullish_pct = weighted_bullish / weighted_total
    bearish_pct = weighted_bearish / weighted_total
    neutral_pct = neutral_count / total

    avg_atr = sum(atr_values) / len(atr_values) if atr_values else 0.02
    high_vol = avg_atr > 0.04

    # 新阈值：0.5
    if bullish_pct >= 0.5:
        return 'bullish'
    elif bearish_pct >= 0.5:
        return 'bearish'
    elif high_vol and neutral_pct < 0.4:
        return 'mixed'
    elif not high_vol and neutral_pct >= 0.6:  # choppy 阈值提至 0.6
        return 'choppy'
    else:
        return 'mixed'


def analyze_regime_flip(records):
    """分析体制标签翻转"""
    baseline_regimes = Counter()
    perturbed_regimes = Counter()
    flips = []
    no_tech_cache_count = 0

    for rec in records:
        # 提取 symbol_techs（从 _symbol_state 重建）
        state_snapshot = rec.get('state_snapshot_before_decision', {})
        symbol_state = state_snapshot.get('_symbol_state', {})

        # 从 _symbol_state 重建 symbol_tech_cache
        symbol_tech_cache = {}
        for sym, state in symbol_state.items():
            tech = state.get('tech_analysis')
            if tech:
                symbol_tech_cache[sym] = tech

        if symbol_tech_cache:
            # 重新计算两臂
            baseline_calc = compute_regime_baseline(symbol_tech_cache)
            perturbed_calc = compute_regime_perturbed(symbol_tech_cache)

            baseline_regimes[baseline_calc] += 1
            perturbed_regimes[perturbed_calc] += 1

            if baseline_calc != perturbed_calc:
                flips.append({
                    'symbol': rec.get('symbol'),
                    'timestamp': rec.get('timestamp'),
                    'decision': rec.get('decision'),
                    'baseline_regime': baseline_calc,
                    'perturbed_regime': perturbed_calc,
                })
        else:
            # 降级：用磁带记录的 regime
            baseline_regime = rec.get('regime_state')
            baseline_regimes[baseline_regime] += 1
            perturbed_regimes[baseline_regime] += 1
            no_tech_cache_count += 1

    return {
        'baseline_distribution': dict(baseline_regimes),
        'perturbed_distribution': dict(perturbed_regimes),
        'flips': flips,
        'flip_count': len(flips),
        'flip_rate_pct': round(len(flips) / len(records) * 100, 2) if records else 0,
        'no_tech_cache_count': no_tech_cache_count,
    }


def main():
    print(f"[CF 验证] 开始体制改进反事实分析 - 近 {LOOKBACK_DAYS} 天数据")
    print(f"[CF 验证] 磁带路径: {TAPE_PATH}")

    # 加载数据
    records = load_recent_records(LOOKBACK_DAYS)
    print(f"[CF 验证] 加载 {len(records)} 条可回放记录")

    if not records:
        print("[CF 验证] 无可用数据，退出")
        return

    # 分析体制翻转
    flip_analysis = analyze_regime_flip(records)

    results = {
        'diagnostic_timestamp': time.time(),
        'lookback_days': LOOKBACK_DAYS,
        'total_records': len(records),
        'regime_flip_analysis': flip_analysis,
    }

    # 写入输出
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n[CF 验证] 完成！结果已保存到: {OUTPUT_PATH}")
    print(f"\n=== 关键发现摘要 ===")
    print(f"总记录数: {results['total_records']}")
    print(f"无 tech_cache 降级: {flip_analysis['no_tech_cache_count']} 条")
    print(f"Baseline regime 分布: {flip_analysis['baseline_distribution']}")
    print(f"Perturbed regime 分布: {flip_analysis['perturbed_distribution']}")
    print(f"体制标签翻转: {flip_analysis['flip_count']} 条 ({flip_analysis['flip_rate_pct']}%)")

    # 输出部分翻转案例
    if flip_analysis['flips']:
        print(f"\n前 10 个翻转案例:")
        for i, flip in enumerate(flip_analysis['flips'][:10]):
            print(f"  {i+1}. {flip['symbol']}: {flip['baseline_regime']} → {flip['perturbed_regime']}")


if __name__ == '__main__':
    main()
