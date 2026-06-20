"""影子决策对比驱动（observability-only）：lever1 增量 = 影子(both-levers) − 实盘(lever2-only)。

读 `data/shadow_decision_log.jsonl`，筛 flip_kind=shadow_opens（lever1 解锁、实盘没开的单），
用 klines + resolve_counterfactual 结算前向结局，报 lever1 增量：多开数 / 含亏单净 R / 簇胜率，
经诚实门薄样本拒答。observability-only —— 仅读影子日志 + klines，绝不改任何 config / 不进决策。

跑前需先让带影子记录器的 live 跑一段累积日志（前向）。日志为空时优雅拒答。
"""
import json
import os
import sqlite3
from collections import Counter

from utils.counterfactual_pnl import resolve_counterfactual
from utils.cf_honesty_gate import summarize_bucket

SHADOW_LOG = "data/shadow_decision_log.jsonl"
KLINES_1S = "data/klines_1s.db"


def load_shadow_opens():
    """读影子日志, 返回 flip_kind=shadow_opens 且 baseline 复现可信的记录。

    排除 baseline_mismatch=True（复盘失真）及缺 baseline_mismatch 字段的旧记录
    （fail-safe 当不可信），返回 (records, excluded_count)。
    """
    if not os.path.exists(SHADOW_LOG):
        return [], 0
    out = []
    excluded = 0
    for line in open(SHADOW_LOG):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("flip_kind") == "shadow_opens" and r.get("shadow_plan"):
            if r.get("baseline_mismatch") is False:
                out.append(r)
            else:                       # True 或缺字段(旧记录) → 不可信排除
                excluded += 1
    return out, excluded


def load_bars(db, sym, created, window=86400):
    if not os.path.exists(db):
        return []
    lo, hi = int(created * 1000), int((created + window) * 1000)
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT open_time,high,low,close FROM klines WHERE symbol=? "
            "AND open_time>=? AND open_time<=? ORDER BY open_time", (sym, lo, hi)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{"open_time": t, "high": h, "low": l, "close": c} for t, h, l, c in rows]


def settle(rec):
    """用 shadow_plan 构造 record → resolve_counterfactual 结算前向结局（保守 TP1 口径）。"""
    plan = rec.get("shadow_plan") or {}
    sym = rec.get("symbol")
    created = rec.get("timestamp")
    side = "long" if rec.get("shadow_action") == "open_long" else "short"
    cf_rec = {
        "symbol": sym, "side": side,
        "entry_price": plan.get("entry_ref") or plan.get("entry_price"),
        "stop_loss": plan.get("stop_loss"),
        "take_profit": plan.get("take_profit") or [],
        "leverage": plan.get("leverage", 1), "size_usdt": plan.get("size_usdt"),
        "funding_rate": 0.0,
    }
    if cf_rec["entry_price"] is None or cf_rec["stop_loss"] is None or not cf_rec["take_profit"]:
        return None
    bars = load_bars(KLINES_1S, sym, created)
    if len(bars) < 5:
        return None
    r = resolve_counterfactual(cf_rec, bars, max_hold_sec=86400)
    if r.net_usdt is None or cf_rec["size_usdt"] in (None, 0):
        return None
    # 净 R = net_usdt / 单笔最大风险(保证金×sl_dist×leverage 的名义风险≈ size×lev×sl_dist)
    entry, sl = cf_rec["entry_price"], cf_rec["stop_loss"]
    sl_dist = abs(entry - sl) / entry if entry else 0
    risk = float(cf_rec["size_usdt"]) * float(cf_rec["leverage"]) * sl_dist
    return (r.net_usdt / risk) if risk > 0 else None


def main():
    opens, excluded = load_shadow_opens()
    print(f"=== 影子对比：lever1 增量（影子 shadow_opens = lever1 解锁、实盘没开的单）===")
    print(f"shadow_opens 候选: {len(opens)}（已排除 baseline_mismatch/旧记录 {excluded} 条）")
    print(f"影子日志: {SHADOW_LOG}  {'存在' if os.path.exists(SHADOW_LOG) else '不存在(需先跑带影子记录器的 live 累积)'}")
    if not opens:
        print("→ 无数据, 优雅拒答。先让带 shadow_decision_logger 的 live 前向跑一段。")
        return

    by_sym = Counter(r.get("symbol") for r in opens)
    print(f"涉及 symbol: {dict(by_sym)}")
    rs = []
    skipped = 0
    for rec in opens:
        r = settle(rec)
        if r is None:
            skipped += 1
            continue
        rs.append(r)
    print(f"可结算: {len(rs)}  (无 klines/数据不全跳过 {skipped})")
    if not rs:
        print("→ 无可结算(klines 覆盖不足), 拒答。")
        return

    wins = [x for x in rs if x > 0]
    net = sum(rs)
    print(f"  成交胜率: {len(wins)/len(rs):.3f}  含亏单净: {net:+.2f} R  人均: {net/len(rs):+.3f} R/单")
    # 诚实门(Wilson + bootstrap + 薄样本拒答)；net_usdt_samples 这里传净 R 样本(口径一致)
    try:
        summ = summarize_bucket(wins=len(wins), losses=len(rs) - len(wins), net_usdt_samples=rs)
        print(f"  诚实门: {json.dumps(summ, ensure_ascii=False, default=str)[:300]}")
    except Exception as e:
        print(f"  诚实门跳过: {e}")
    print("\n注: lever1 增量 = 影子(both-levers) 比实盘(lever2-only) 多开的单的前向期望(保守 TP1 口径)。")
    print("    样本薄时以诚实门为准; observability-only, 不据此自动改 config。")


if __name__ == "__main__":
    main()
