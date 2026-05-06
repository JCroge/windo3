#!/usr/bin/env python3
"""币种研判Agent测试脚本"""

import sys
sys.path.append('.')

from agents.coin_selector import CoinSelector

def main():
    print("=" * 60)
    print("币种研判Agent - 测试运行")
    print("=" * 60)

    selector = CoinSelector()

    print("\n开始分析...")
    recommendations = selector.get_recommendations(top_n=20)

    print(f"\n推荐币种列表（Top 20）:")
    for i, symbol in enumerate(recommendations, 1):
        print(f"{i}. {symbol}")

    print("\n详细报告已保存到 data/ 目录")
    print("=" * 60)

if __name__ == '__main__':
    main()
