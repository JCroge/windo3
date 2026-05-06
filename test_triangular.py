#!/usr/bin/env python3
"""测试三角套利检测器"""

import sys
sys.path.append('.')

from triangular_arbitrage import TriangularArbitrage

def main():
    print("=" * 60)
    print("三角套利检测器测试")
    print("=" * 60)

    # 使用币安
    arb = TriangularArbitrage('binance')

    # 找出所有三角组合
    triangles = arb.find_triangles('USDT')

    print(f"\n前10个三角组合示例:")
    for i, t in enumerate(triangles[:10], 1):
        print(f"{i}. {t['pair_base_coin1']} - {t['pair_coin1_coin2']} - {t['pair_base_coin2']}")

    # 检测机会
    print(f"\n检测套利机会...")
    opportunities = arb.check_opportunities(triangles)

    print(f"\n{'='*60}")
    print(f"发现{len(opportunities)}个套利机会")
    print("=" * 60)

    if opportunities:
        for opp in opportunities[:10]:
            t = opp['triangle']
            print(f"\n利润率: {opp['profit']*100:.3f}%")
            print(f"路径: {t['pair_base_coin1']} → {t['pair_coin1_coin2']} → {t['pair_base_coin2']}")

if __name__ == '__main__':
    main()
