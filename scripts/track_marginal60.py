#!/usr/bin/env python3
"""60 分边缘多单 PnL 跟踪（observability-only，纯读日志）。

背景：衰减期关闭 EV 胜率门后，放行的开仓里 ~85% 是"rule_signal 触发、LLM 观望、
衰减 30% 后置信度恰落 60 门槛线"的边缘多单。本脚本关联这批单的成交与已实现 PnL，
回答"放开胜率门是放水还是合理入场"——看这批 60 分单的实盘 PnL，对比 70 分信念单。

用法：python3 scripts/track_marginal60.py [YYYYMMDD ...]
  不带参数 = 扫所有 agent_judge_*.log / agent_reviewer_*.log（跨日累计）。
  带日期 = 只扫指定日。
"""
import datetime
import glob
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from utils.symbol import to_internal

LOGS = os.path.join(ROOT, "logs")
LIFECYCLE = os.path.join(ROOT, "data", "live_position_lifecycle.json")

TS = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
# [决策] SYMBOL-USDT open_long slot=main req=... 置信度=60 plan=5x
RE_DECISION = re.compile(TS + r".*\[决策\] (\S+?) open_(long|short) .*置信度=([0-9.]+)")
# [Judge] SYMBOL-USDT 开仓成功 slot=main
RE_FILL = re.compile(TS + r".*\[Judge\] (\S+?) 开仓成功 slot=")


def _log_ts_to_epoch(ts_str):
    """日志时间戳(本地时区 naive 'YYYY-MM-DD HH:MM:SS') → Unix epoch。

    lifecycle 的 opened_at 是 `time.time()` 写下的本地 epoch，judge 日志时间戳是同机
    本地时间字符串；两者对齐须用本地解释(`.timestamp()`)，实测同一开仓 dt≈0-2s。
    """
    return datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()


def load_lifecycle(path=LIFECYCLE):
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path))
    except Exception:
        return {}


def settle_fill_from_lifecycle(sym_internal, side, fill_ts, lifecycle, used_keys, tol=300):
    """按 symbol(归一) + side + |opened_at-fill_ts|<=tol join lifecycle, 取 total_realized_pnl。

    返回 (pnl_or_None, matched_key_or_None)。已用 key 在 used_keys 中跳过, 避免重复消费。
    """
    best_key, best_dt = None, None
    for k, v in lifecycle.items():
        if not isinstance(v, dict) or k in used_keys:
            continue
        if to_internal(v.get("symbol", "")) != sym_internal:
            continue
        if v.get("side") != side:
            continue
        op = v.get("opened_at")
        if op is None:
            continue
        dt = abs(op - fill_ts)
        if dt <= tol and (best_dt is None or dt < best_dt):
            best_key, best_dt = k, dt
    if best_key is None:
        return None, None
    v = lifecycle[best_key]
    pnl = v.get("total_realized_pnl")
    if pnl is None or v.get("status") not in ("closed",):
        return None, None
    return float(pnl), best_key


def _logs(prefix, dates):
    if dates:
        return sorted(f for d in dates for f in glob.glob(os.path.join(LOGS, f"{prefix}_{d}.log")))
    return sorted(glob.glob(os.path.join(LOGS, f"{prefix}_*.log")))


def main():
    dates = sys.argv[1:]
    judge_logs = _logs("agent_judge", dates)
    if not judge_logs:
        print("无 judge 日志")
        return

    # 1) 逐 symbol 收集 long 开仓决策(ts→tier) 与 成交(ts)
    #    symbol 经 to_internal 归一(judge 日志已是内部格式, 防御性收口)
    decisions = defaultdict(list)   # symbol -> [(ts_str, tier)]
    fills = defaultdict(list)       # symbol -> [ts_str]
    for path in judge_logs:
        for line in open(path, errors="ignore"):
            m = RE_DECISION.search(line)
            if m and m.group(3) == "long":
                decisions[to_internal(m.group(2))].append((m.group(1), float(m.group(4))))
            m = RE_FILL.search(line)
            if m:
                fills[to_internal(m.group(2))].append(m.group(1))

    # 2) 结算源改读权威 live_position_lifecycle.json:
    #    每个 long fill 按 symbol(归一)+side=long+|opened_at-fill_ts|<=tol join,
    #    取 total_realized_pnl(reconcile 后的统一键值); used_keys 防重复消费。
    lifecycle = load_lifecycle()
    used_keys = set()

    # 3) 关联：每个成交 → 取其前最近的 long 决策 tier；从 lifecycle 结算 PnL
    entries = []  # (open_ts_str, symbol, tier, pnl_or_None)
    for sym, fill_ts_list in fills.items():
        decs = sorted(decisions.get(sym, []))
        for fts in sorted(fill_ts_list):
            # tier = 该成交前最近的 long 决策置信度
            tier = None
            for dts, conf in decs:
                if dts <= fts:
                    tier = conf
                else:
                    break
            # 从权威 lifecycle 结算(本脚本只跟踪 long 边缘单, side 固定 long)
            pnl, key = settle_fill_from_lifecycle(
                sym, "long", _log_ts_to_epoch(fts), lifecycle, used_keys, tol=300)
            if key is not None:
                used_keys.add(key)
            entries.append((fts, sym, tier, pnl))

    entries.sort()

    # 4) 报告
    def bucket(tier):
        if tier is None:
            return "未知"
        return "边缘60" if tier <= 60 else "信念≥70" if tier >= 70 else f"中间{tier:.0f}"

    print(f"=== 60分边缘多单 PnL 跟踪（judge={len(judge_logs)}日 / 结算源=lifecycle.json）===\n")
    print(f"{'开仓时间':<20} {'标的':<14} {'置信度':<7} {'类别':<9} {'已实现PnL'}")
    for ts, sym, tier, pnl in entries:
        ps = f"{pnl:+.2f} USDT" if pnl is not None else "持仓中/未结算"
        tstr = f"{tier:.0f}" if tier is not None else "?"
        print(f"{ts:<20} {sym:<14} {tstr:<7} {bucket(tier):<9} {ps}")

    print("\n=== 分桶汇总（仅已结算）===")
    for label, lo, hi in [("边缘60单", 0, 60), ("信念≥70单", 70, 999)]:
        settled = [pnl for ts, sym, tier, pnl in entries
                   if tier is not None and lo <= tier <= hi and pnl is not None]
        if settled:
            wins = sum(1 for p in settled if p > 0)
            print(f"  {label}: n={len(settled)}  胜率={wins/len(settled)*100:.0f}%  "
                  f"总PnL={sum(settled):+.2f}  均PnL={sum(settled)/len(settled):+.3f} USDT")
        else:
            print(f"  {label}: 暂无已结算样本")
    open_cnt = sum(1 for *_, pnl in entries if pnl is None)
    print(f"\n  持仓中/未结算: {open_cnt} 单")
    print("\n注：observability-only，纯读日志。样本薄时勿据此自动改 config；")
    print("    重点看『边缘60单』均PnL是否持续为负——若是，放开胜率门=放水，考虑收紧。")


if __name__ == "__main__":
    main()
