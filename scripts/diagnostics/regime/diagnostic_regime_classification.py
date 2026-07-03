#!/usr/bin/env python3
"""
体制分类诊断脚本 - Phase 1: 数据取证

目标：
1. 统计 regime 标签分布（bullish/bearish/choppy/mixed/neutral）
2. 验证被判为 choppy 的标的是否真 choppy（事后 48h 涨跌幅）
3. 统计 HTF/daily bias 缺失率
4. 分析被拒单中体制误判的占比

输出：JSON 格式
"""

import json
import time
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import ccxt
from pathlib import Path

# 配置
TAPE_PATH = Path("data/decision_replay_tape.jsonl")
LOOKBACK_DAYS = 7
OUTPUT_PATH = Path("data/diagnostic_regime_classification.json")
DRY_RUN = False  # 完整模式：处理全部数据
DRY_RUN_LIMIT = 100


def load_recent_records(days=7):
    """加载近 N 天的决策磁带记录"""
    cutoff_ts = time.time() - (days * 86400)
    records = []

    with open(TAPE_PATH, 'r') as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                if record.get('timestamp', 0) >= cutoff_ts:
                    records.append(record)
                    # 干跑模式限制
                    if DRY_RUN and len(records) >= DRY_RUN_LIMIT:
                        break
            except json.JSONDecodeError:
                continue

    return records


def extract_regime_info(record):
    """提取体制分类相关信息"""
    regime_state = record.get('regime_state')
    tech = record.get('tech_analysis', {})
    trend = tech.get('trend', {})

    return {
        'symbol': record.get('symbol'),
        'timestamp': record.get('timestamp'),
        'decision': record.get('decision'),
        'regime': regime_state if isinstance(regime_state, str) else None,
        'direction': trend.get('direction'),
        'strength': trend.get('strength'),
        'htf_bias': trend.get('higher_tf_bias'),
        'daily_bias': trend.get('daily_bias'),
        'ma_alignment': trend.get('ma_alignment'),
        'price_at_decision': record.get('price_at_decision'),
    }


def fetch_price_change_48h(exchange, symbol, decision_ts):
    """
    获取决策后 48h 的涨跌幅
    返回: (涨跌幅%, 是否成功获取)
    """
    try:
        # OKX symbol 格式转换
        okx_symbol = symbol.replace('-USDT', '/USDT:USDT')

        # 决策时间点向下取整到小时
        decision_dt = datetime.fromtimestamp(decision_ts)
        start_dt = decision_dt.replace(minute=0, second=0, microsecond=0)

        # 48h 后
        end_dt = start_dt + timedelta(hours=48)

        # 如果 end_dt 超过当前时间，返回 None（数据不全）
        if end_dt > datetime.now():
            return None, False

        # 拉取 1h K线
        since = int(start_dt.timestamp() * 1000)
        klines = exchange.fetch_ohlcv(okx_symbol, '1h', since=since, limit=50)

        if len(klines) < 48:
            return None, False

        start_price = klines[0][4]  # 决策时点收盘价
        end_price = klines[47][4]   # 48h后收盘价

        change_pct = ((end_price - start_price) / start_price) * 100
        return round(change_pct, 2), True

    except Exception as e:
        return None, False


def analyze_regime_distribution(records):
    """统计 regime 标签分布"""
    regime_counter = Counter()
    direction_counter = Counter()
    bias_missing = {'htf': 0, 'daily': 0, 'both': 0}

    for rec in records:
        info = extract_regime_info(rec)

        regime = info['regime']
        if regime:
            regime_counter[regime] += 1

        direction = info['direction']
        if direction:
            direction_counter[direction] += 1

        # 统计 bias 缺失
        htf_missing = info['htf_bias'] is None
        daily_missing = info['daily_bias'] is None

        if htf_missing and daily_missing:
            bias_missing['both'] += 1
        elif htf_missing:
            bias_missing['htf'] += 1
        elif daily_missing:
            bias_missing['daily'] += 1

    total = len(records)

    return {
        'total_records': total,
        'regime_distribution': dict(regime_counter),
        'regime_distribution_pct': {k: round(v/total*100, 2) for k, v in regime_counter.items()},
        'direction_distribution': dict(direction_counter),
        'direction_distribution_pct': {k: round(v/total*100, 2) for k, v in direction_counter.items()},
        'bias_missing': bias_missing,
        'bias_missing_rate_pct': {
            'htf': round(bias_missing['htf']/total*100, 2),
            'daily': round(bias_missing['daily']/total*100, 2),
            'both': round(bias_missing['both']/total*100, 2),
        }
    }


def analyze_choppy_validation(records, exchange):
    """
    验证被判为 choppy 的标的是否真 choppy
    通过事后 48h 涨跌幅分布判断
    """
    choppy_records = [r for r in records if extract_regime_info(r)['regime'] == 'choppy']

    results = {
        'total_choppy': len(choppy_records),
        'price_changes': [],
        'verified_count': 0,
        'mean_abs_change': None,
        'strong_trend_count': 0,  # |涨跌幅| > 10% 视为强趋势
    }

    print(f"[诊断] 开始验证 {len(choppy_records)} 条 choppy 记录...")

    for i, rec in enumerate(choppy_records):
        info = extract_regime_info(rec)
        change_pct, success = fetch_price_change_48h(exchange, info['symbol'], info['timestamp'])

        if success:
            results['price_changes'].append({
                'symbol': info['symbol'],
                'timestamp': info['timestamp'],
                'decision': info['decision'],
                'change_48h_pct': change_pct,
            })
            results['verified_count'] += 1

            if abs(change_pct) > 10:
                results['strong_trend_count'] += 1

        if (i + 1) % 20 == 0:
            print(f"[诊断] 进度: {i+1}/{len(choppy_records)}")

        time.sleep(0.2)  # 避免 API 限流

    # 统计
    if results['price_changes']:
        abs_changes = [abs(x['change_48h_pct']) for x in results['price_changes']]
        results['mean_abs_change'] = round(sum(abs_changes) / len(abs_changes), 2)
        results['strong_trend_rate_pct'] = round(results['strong_trend_count'] / results['verified_count'] * 100, 2)

    return results


def analyze_rejected_regime_breakdown(records):
    """分析被拒单中的体制分布"""
    rejected = [r for r in records if extract_regime_info(r)['decision'] == 'reject']

    regime_counter = Counter()
    direction_counter = Counter()

    for rec in rejected:
        info = extract_regime_info(rec)
        if info['regime']:
            regime_counter[info['regime']] += 1
        if info['direction']:
            direction_counter[info['direction']] += 1

    total = len(rejected)

    return {
        'total_rejected': total,
        'regime_distribution': dict(regime_counter),
        'regime_distribution_pct': {k: round(v/total*100, 2) for k, v in regime_counter.items()},
        'direction_distribution': dict(direction_counter),
        'direction_distribution_pct': {k: round(v/total*100, 2) for k, v in direction_counter.items()},
    }


def main():
    mode = "干跑模式（前100条）" if DRY_RUN else f"近 {LOOKBACK_DAYS} 天数据"
    print(f"[诊断] 开始体制分类诊断 - {mode}")
    print(f"[诊断] 磁带路径: {TAPE_PATH}")

    # 加载数据
    records = load_recent_records(LOOKBACK_DAYS)
    print(f"[诊断] 加载 {len(records)} 条记录")

    if not records:
        print("[诊断] 无可用数据，退出")
        return

    # 初始化交易所（用于价格验证）
    from dotenv import load_dotenv
    import os
    load_dotenv()

    exchange = ccxt.okx({
        'apiKey': os.getenv('OKX_API_KEY'),
        'secret': os.getenv('OKX_SECRET'),
        'password': os.getenv('OKX_PASSWORD'),
        'enableRateLimit': True,
    })

    if os.getenv('USE_TESTNET', 'false').lower() == 'true':
        exchange.set_sandbox_mode(True)

    # 执行分析
    results = {
        'diagnostic_timestamp': time.time(),
        'lookback_days': LOOKBACK_DAYS,
        'total_records': len(records),
        'regime_distribution': analyze_regime_distribution(records),
        'choppy_validation': analyze_choppy_validation(records, exchange),
        'rejected_regime_breakdown': analyze_rejected_regime_breakdown(records),
    }

    # 写入输出
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n[诊断] 完成！结果已保存到: {OUTPUT_PATH}")
    print(f"\n=== 关键发现摘要 ===")
    print(f"总记录数: {results['total_records']}")
    print(f"Regime 分布: {results['regime_distribution']['regime_distribution_pct']}")
    print(f"Bias 缺失率: HTF {results['regime_distribution']['bias_missing_rate_pct']['htf']}%, "
          f"Daily {results['regime_distribution']['bias_missing_rate_pct']['daily']}%")
    print(f"Choppy 验证: {results['choppy_validation']['verified_count']}/{results['choppy_validation']['total_choppy']} 条可验证")
    if results['choppy_validation']['mean_abs_change']:
        print(f"Choppy 48h 平均绝对涨跌: {results['choppy_validation']['mean_abs_change']}%")
        print(f"Choppy 中强趋势(>10%)占比: {results['choppy_validation']['strong_trend_rate_pct']}%")


if __name__ == '__main__':
    main()
