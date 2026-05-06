#!/usr/bin/env python3
"""生成币种研判报告"""

import json
import sys
from datetime import datetime

def generate_report(json_file):
    with open(json_file) as f:
        data = json.load(f)

    report = []
    report.append("# 币种研判报告\n")
    report.append(f"**生成时间**: {data['timestamp']}\n")
    report.append(f"**分析范围**: Binance + OKX 共同交易对\n")
    report.append(f"**筛选条件**: 交易量1000万-1亿美元, 波动率>2%\n\n")

    # Tier 1
    report.append("## 🔥 Tier 1 - 立即监控（Top 5）\n\n")
    report.append("| 币种 | 总分 | 波动率 | 价差 | 交易量 | 24h涨跌 |\n")
    report.append("|------|------|--------|------|--------|----------|\n")

    for coin in data['tier1']:
        report.append(f"| {coin['symbol']} | {coin['total_score']:.1f} | "
                     f"{coin['volatility']:.1f}% | {coin['spread']:.3f}% | "
                     f"${coin['volume']:.1f}M | {coin['change_24h']:+.1f}% |\n")

    # Tier 2
    report.append("\n## ⭐ Tier 2 - 潜力币种（6-15名）\n\n")
    report.append("| 币种 | 总分 | 波动率 | 价差 | 交易量 |\n")
    report.append("|------|------|--------|------|--------|\n")

    for coin in data['tier2']:
        report.append(f"| {coin['symbol']} | {coin['total_score']:.1f} | "
                     f"{coin['volatility']:.1f}% | {coin['spread']:.3f}% | "
                     f"${coin['volume']:.1f}M |\n")

    # Tier 3
    report.append("\n## 📊 Tier 3 - 观察名单（16-20名）\n\n")
    for coin in data['tier3']:
        report.append(f"- {coin['symbol']}: 得分{coin['total_score']:.1f}\n")

    return ''.join(report)

if __name__ == '__main__':
    json_file = sys.argv[1] if len(sys.argv) > 1 else 'data/coin_analysis_20260506_161654.json'
    report = generate_report(json_file)

    output_file = json_file.replace('.json', '.md')
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)

    print(report)
    print(f"\n报告已保存到: {output_file}")
