#!/usr/bin/env python3
"""Phase 2 EPIC E: 回放报表脚本

复放指定时间窗口的交易决策，输出结构化指标：
- 决策数 / hold 原因分布
- 质量门拦截数
- 影子 TP / SL (CounterfactualLedger)
- 最大 score
- 分桶 PF / 样本数
- 自然日与运行窗口分离

用法:
    python3 replay_report.py --date 2026-05-22
    python3 replay_report.py --start 2026-05-22T00:00 --end 2026-05-22T12:00
"""

import argparse
import json
import os
import datetime
from collections import defaultdict

from utils.cf_honesty_gate import summarize_bucket


def load_trade_history(path='data/trade_history.json'):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def load_counterfactual_ledger(path='data/counterfactual_ledger.json'):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def load_judge_decisions_log(log_dir='logs'):
    """Parse judge decision logs for hold reasons and scores."""
    decisions = []
    today = datetime.datetime.utcnow().strftime('%Y-%m-%d')
    log_file = os.path.join(log_dir, f'trading_{today}.log')
    if not os.path.exists(log_file):
        return decisions
    return decisions


def filter_by_window(records, start_ts, end_ts, ts_key='timestamp'):
    """Filter records by timestamp window."""
    return [r for r in records if start_ts <= r.get(ts_key, 0) <= end_ts]


def compute_bucket_metrics(trades):
    """Compute PF and win_rate per bucket (side x regime x entry_type x slot_type)."""
    buckets = defaultdict(list)
    for t in trades:
        side = t.get('side', 'unknown')
        regime = t.get('entry_regime', 'unknown')
        entry_type = t.get('entry_type', 'unknown')
        slot_type = t.get('slot_type', 'main')
        key = f"{side}_{regime}_{entry_type}_{slot_type}"
        buckets[key].append(t)

    result = {}
    for key, bucket_trades in buckets.items():
        wins = [t for t in bucket_trades if t.get('pnl', 0) > 0]
        losses = [t for t in bucket_trades if t.get('pnl', 0) < 0]
        gp = sum(t['pnl'] for t in wins)
        gl = abs(sum(t['pnl'] for t in losses)) if losses else 0
        pf = gp / gl if gl > 0 else (gp if gp > 0 else 0)
        wr = len(wins) / len(bucket_trades) if bucket_trades else 0
        result[key] = {
            'trade_count': len(bucket_trades),
            'win_rate': round(wr, 3),
            'profit_factor': round(pf, 2),
            'total_pnl': round(sum(t.get('pnl', 0) for t in bucket_trades), 2),
            'insufficient_sample': len(bucket_trades) < 5,
        }
    return result


def generate_report(start_ts, end_ts, window_label='custom'):
    """Generate replay report for the given time window."""
    trades = load_trade_history()
    ledger = load_counterfactual_ledger()

    window_trades = filter_by_window(trades, start_ts, end_ts)
    window_ledger = filter_by_window(ledger, start_ts, end_ts, ts_key='rejected_at')

    # Basic counts
    total_decisions = len(window_trades) + len(window_ledger)
    open_count = len(window_trades)
    rejected_count = len(window_ledger)

    # Hold reasons from ledger
    hold_reasons = defaultdict(int)
    for r in window_ledger:
        reason = r.get('reason', 'unknown')
        hold_reasons[reason] += 1

    # Quality gate rejections
    quality_gate_count = sum(1 for r in window_ledger if 'quality_gate' in r.get('reason', ''))
    ev_gate_count = sum(1 for r in window_ledger if 'ev_gate' in r.get('reason', ''))
    regime_gate_count = sum(1 for r in window_ledger if 'regime' in r.get('reason', ''))

    # Max score
    all_scores = [abs(r.get('score', 0)) for r in window_ledger]
    all_scores += [abs(t.get('score', t.get('signal_score', 0))) for t in window_trades]
    max_score = max(all_scores) if all_scores else 0

    # Shadow TP/SL from counterfactual ledger
    shadow_tp = sum(1 for r in window_ledger if r.get('outcome') == 'tp_hit')
    shadow_sl = sum(1 for r in window_ledger if r.get('outcome') == 'sl_hit')
    shadow_pending = sum(1 for r in window_ledger if r.get('outcome') in (None, 'pending', ''))

    # Bucket metrics
    bucket_metrics = compute_bucket_metrics(window_trades)

    report = {
        'window': window_label,
        'start': datetime.datetime.utcfromtimestamp(start_ts).isoformat() + 'Z',
        'end': datetime.datetime.utcfromtimestamp(end_ts).isoformat() + 'Z',
        'total_decisions': total_decisions,
        'opened': open_count,
        'rejected': rejected_count,
        'quality_gate_rejections': quality_gate_count,
        'ev_gate_rejections': ev_gate_count,
        'regime_gate_rejections': regime_gate_count,
        'hold_reasons': dict(hold_reasons),
        'max_score': round(max_score, 1),
        'shadow_tp': shadow_tp,
        'shadow_sl': shadow_sl,
        'shadow_pending': shadow_pending,
        'bucket_pf': bucket_metrics,
    }
    return report


def build_cf_report(resolved_rows, *, min_sample=30, lowconf_sample=100):
    """按 reject_reason|regime|side 分桶，每桶过诚实 gate + 偏差带。
    observability-only。"""
    groups = defaultdict(list)
    for r in resolved_rows:
        key = f"{r.get('reject_reason')}|{r.get('effective_regime')}|{r.get('side')}"
        groups[key].append(r)
    buckets = {}
    for key, rows in groups.items():
        wins = sum(1 for r in rows if r.get("outcome") == "tp")
        losses = sum(1 for r in rows if r.get("outcome") == "sl")
        samples = [r["net_usdt"] for r in rows if r.get("net_usdt") is not None]
        verdict = summarize_bucket(wins=wins, losses=losses, net_usdt_samples=samples,
                                   min_sample=min_sample, lowconf_sample=lowconf_sample)
        ambiguous = sum(1 for r in rows if r.get("price_ambiguous"))
        verdict["bias_band"] = {
            "ambiguous_count": ambiguous,
            "ambiguous_pct": (ambiguous / len(rows)) if rows else 0.0,
        }
        verdict["sources"] = sorted({r.get("source") for r in rows})
        buckets[key] = verdict
    return {"buckets": buckets, "total": len(resolved_rows)}


def main():
    parser = argparse.ArgumentParser(description='Phase 2 Replay Report')
    parser.add_argument('--date', help='Natural day (UTC), e.g. 2026-05-22')
    parser.add_argument('--start', help='Window start, e.g. 2026-05-22T00:00')
    parser.add_argument('--end', help='Window end, e.g. 2026-05-22T12:00')
    parser.add_argument('--output', default=None, help='Output JSON path')
    args = parser.parse_args()

    if args.date:
        day = datetime.datetime.strptime(args.date, '%Y-%m-%d')
        start_ts = day.timestamp()
        end_ts = (day + datetime.timedelta(days=1)).timestamp()
        label = f"day:{args.date}"
    elif args.start and args.end:
        start_dt = datetime.datetime.fromisoformat(args.start)
        end_dt = datetime.datetime.fromisoformat(args.end)
        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp()
        label = f"window:{args.start}~{args.end}"
    else:
        # Default: last 24h
        end_ts = datetime.datetime.utcnow().timestamp()
        start_ts = end_ts - 86400
        label = "last_24h"

    report = generate_report(start_ts, end_ts, label)

    output_json = json.dumps(report, indent=2, ensure_ascii=False)
    print(output_json)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(output_json)
        print(f"\nReport saved to {args.output}")


if __name__ == '__main__':
    main()