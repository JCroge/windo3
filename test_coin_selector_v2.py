#!/usr/bin/env python3
"""测试币种研判Agent V2"""

import sys
sys.path.append('.')

from agents.coin_selector_v2 import CoinSelectorV2

def main():
    print("=" * 60)
    print("币种研判Agent V2 - 融合深度研判精华")
    print("=" * 60)

    selector = CoinSelectorV2()

    print("\n开始分析...")
    recommendations = selector.get_recommendations(top_n=20)

    print(f"\n推荐币种列表（Top 20）:")
    for i, symbol in enumerate(recommendations, 1):
        print(f"{i}. {symbol}")

    print("\n详细报告已保存到 data/ 目录")
    print("=" * 60)

if __name__ == '__main__':
    main()
