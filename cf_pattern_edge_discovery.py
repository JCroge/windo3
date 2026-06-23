"""日线蜡烛形态 edge 发现(observability-only,严禁决策路径读取)。

动机:判定日线蜡烛形态在样本外有无可交易 edge。镜像 cf_oi_divergence_ab.py 的研究驱动风格:
  load → fire → settle → aggregate → gate → report。

方法:
  - 形态识别复用 utils/candlestick_patterns.py::detect_patterns
  - 真实 ATR(14) 设 SL/TP(1.5/3.0 ATR),resolve_counterfactual 走真实路径退出 + CostModel
  - 上下文 6 桶: trailing 20 日 range_pos(low/mid/high) × close vs MA50(up/down)
  - OOS 三分: train(≤2024) / val(2025) / test(≥2026),edge 须三段同号才算稳健
  - cf_honesty_gate 诚实门(test 段 Wilson + bootstrap CI + 薄样本拒答)
  - Benjamini-Hochberg FDR 跨桶多重比较校正
  - 加权: 仅当(三段同号 AND 诚实门非薄样本 AND PnL_CI 下界>0 AND FDR 拒 H0)才给正权重

仅读 data/klines.db(interval=1d),不下单、不改 config、不进决策。
用法: python3 cf_pattern_edge_discovery.py
"""
import os
import sqlite3
import math
import statistics
import datetime
from collections import defaultdict

from utils.counterfactual_pnl import resolve_counterfactual
from utils.cf_honesty_gate import summarize_bucket
from utils.candlestick_patterns import detect_patterns

DB = "data/klines.db"
ATR_N = 14
RANGE_N = 20
MA_N = 50
SL_ATR = 1.5
TP_ATR = 3.0
MAX_HOLD_DAYS = 10


# ── load: 读 klines.db 按 symbol 分组升序 ──
def load(interval="1d"):
    bysym = defaultdict(list)
    if not os.path.exists(DB):
        return bysym
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            "SELECT symbol,open_time,open,high,low,close FROM klines "
            "WHERE interval=? ORDER BY symbol,open_time",
            (interval,),
        ).fetchall()
    except sqlite3.Error:
        conn.close()
        return bysym
    conn.close()
    for s, t, o, h, l, c in rows:
        bysym[s].append({"open_time": t, "open": o, "high": h, "low": l, "close": c})
    return bysym


# ── ATR(14): TR 均值,不足返回 None ──
def atr(bars, i, n=ATR_N):
    if i < n:
        return None
    trs = [
        max(
            bars[j]["high"] - bars[j]["low"],
            abs(bars[j]["high"] - bars[j - 1]["close"]),
            abs(bars[j]["low"] - bars[j - 1]["close"]),
        )
        for j in range(i - n + 1, i + 1)
    ]
    return sum(trs) / n


# ── context: trailing 20 日 range_pos × close vs MA50 → "rp|trend",不足 MA_N 返回 None ──
def context(bars, i):
    if i < MA_N:
        return None
    win = bars[i - RANGE_N + 1:i + 1]
    hi = max(b["high"] for b in win)
    lo = min(b["low"] for b in win)
    rp = (bars[i]["close"] - lo) / max(hi - lo, 1e-9)
    ma = sum(b["close"] for b in bars[i - MA_N + 1:i + 1]) / MA_N
    trend = "up" if bars[i]["close"] > ma else "down"
    rp_b = "low" if rp < 0.25 else ("high" if rp > 0.75 else "mid")
    return f"{rp_b}|{trend}"


# ── fire: 逐 bar 形态识别 + 簇去重(同 sym+name+dir 在 5 根内只取一次) ──
def fire(bysym):
    """返回 [(sym, i, pattern_name, direction, ctx)]。"""
    sig = []
    for sym, bars in bysym.items():
        last = {}
        for i in range(MA_N, len(bars) - 1):
            ctx = context(bars, i)
            if ctx is None:
                continue
            for name, d in detect_patterns(bars[max(0, i - 4):i + 1]):
                if d == 0:
                    continue
                key = (name, d)
                if key in last and i - last[key] < 5:
                    continue
                last[key] = i
                sig.append((sym, i, name, d, ctx))
    return sig


# ── settle: ATR 退出 + resolve_counterfactual,返回净 R(float|None) ──
def settle(bars, i, direction, atr_val, size=100.0):
    entry = bars[i]["close"]
    side = "long" if direction == 1 else "short"
    if side == "long":
        sl = entry - SL_ATR * atr_val
        tp = entry + TP_ATR * atr_val
    else:
        sl = entry + SL_ATR * atr_val
        tp = entry - TP_ATR * atr_val
    fut = bars[i + 1:i + 1 + MAX_HOLD_DAYS]
    if len(fut) < 2:
        return None
    cf_bars = [
        {"open_time": b["open_time"], "high": b["high"], "low": b["low"], "close": b["close"]}
        for b in fut
    ]
    rec = {
        "symbol": "x", "side": side, "entry_price": entry,
        "stop_loss": sl, "take_profit": [tp], "leverage": 1,
        "size_usdt": size, "funding_rate": 0.0,
        "created_at": bars[i]["open_time"] / 1000.0,
    }
    res = resolve_counterfactual(rec, cf_bars, max_hold_sec=MAX_HOLD_DAYS * 86400)
    if res.net_usdt is None:
        return None
    if entry <= 0:
        return None
    sl_dist = abs(entry - sl) / entry
    risk = size * sl_dist
    return (res.net_usdt / risk) if risk > 0 else None


# ── OOS 三分: train(≤2024) / val(2025) / test(≥2026) ──
def _seg(open_time_ms):
    y = datetime.datetime.utcfromtimestamp(open_time_ms / 1000).year
    return "train" if y <= 2024 else ("val" if y == 2025 else "test")


# ── Benjamini-Hochberg FDR: 返回每个 p 是否拒绝 H0 的布尔 ──
def bh_fdr(pvals, q=0.10):
    m = len(pvals)
    if m == 0:
        return []
    idx = sorted(range(m), key=lambda k: pvals[k])
    rej = [False] * m
    kmax = -1
    for rank, k in enumerate(idx, 1):
        if pvals[k] <= rank / m * q:
            kmax = rank
    for rank, k in enumerate(idx, 1):
        if rank <= kmax:
            rej[k] = True
    return rej


def main(interval="1d"):
    print("=" * 92)
    print(f"蜡烛形态 edge 反事实回测 [{interval}] (ATR退出 + OOS三分 + FDR + 诚实门; observability-only)")
    print("=" * 92)
    bysym = load(interval)
    n_sym = len([s for s in bysym if bysym[s]])
    print(f"klines({interval}) 符号: {n_sym}")
    if n_sym == 0:
        print(f"→ 无 {interval} 数据 / 无可结算数据,拒答(待抓取后重跑)。")
        print("\nobservability-only —— 仅量化,不据此自动改 config/上 live。")
        return

    sig = fire(bysym)
    print(f"形态信号(簇去重后): {len(sig)}")

    # 结算并按 (pattern,dir,ctx) × seg 累积净 R
    buckets = defaultdict(lambda: defaultdict(list))  # key -> seg -> [R]
    for sym, i, name, d, ctx in sig:
        a = atr(bysym[sym], i)
        if not a:
            continue
        R = settle(bysym[sym], i, d, a)
        if R is None:
            continue
        buckets[(name, d, ctx)][_seg(bysym[sym][i]["open_time"])].append(R)

    if not buckets:
        print("→ 无可结算样本(信号未能产生有效退出路径),拒答。")
        print("\nobservability-only —— 仅量化,不据此自动改 config/上 live。")
        return

    # gate: 三段同号 + 诚实门(test段) + FDR
    rows = []
    for key, segs in buckets.items():
        tr, va, te = segs.get("train", []), segs.get("val", []), segs.get("test", [])
        if min(len(tr), len(va), len(te)) < 5:
            continue
        m = lambda x: sum(x) / len(x)
        same_sign = (m(tr) > 0) == (m(va) > 0) == (m(te) > 0)
        summ = summarize_bucket(
            wins=sum(1 for r in te if r > 0),
            losses=sum(1 for r in te if r <= 0),
            net_usdt_samples=te,
        )
        # 单样本 t 近似双尾 p(test 段),用 math.erf
        sd = statistics.pstdev(te) or 1e-9
        t = m(te) / (sd / math.sqrt(len(te)))
        p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
        rows.append({
            "key": key, "n_test": len(te), "mean_te": m(te),
            "same_sign": same_sign, "honest": summ["verdict"],
            "ci": summ["net_pnl_ci"], "p": p,
        })

    rej = bh_fdr([r["p"] for r in rows]) if rows else []
    for r, fdr_ok in zip(rows, rej):
        r["fdr_ok"] = fdr_ok

    print()
    if not rows:
        print("→ 无桶满足三段各≥5 的最小样本要求 → 样本不足,诚实拒答(edge 未确认)。")
        print("\nobservability-only —— 仅量化,不据此自动改 config/上 live。")
        return

    print(f"{'形态|方向|上下文':<40}{'n_test':>7}{'净R_test':>9}{'三段同号':>8}"
          f"{'诚实门':>16}{'FDR':>6}{'权重':>9}")
    for r in sorted(rows, key=lambda x: -x["mean_te"]):
        passed = (
            r["same_sign"]
            and r["honest"] != "INSUFFICIENT_SAMPLE"
            and r["ci"][0] > 0
            and r["fdr_ok"]
        )
        w = max(0.0, r["mean_te"]) if passed else 0.0
        k = f"{r['key'][0]}|{r['key'][1]:+d}|{r['key'][2]}"
        print(f"{k:<40}{r['n_test']:>7}{r['mean_te']:>+8.3f}{str(r['same_sign']):>8}"
              f"{r['honest']:>16}{str(r['fdr_ok']):>6}{w:>+9.3f}")

    passed_rows = [
        r for r in rows
        if r["same_sign"]
        and r["honest"] != "INSUFFICIENT_SAMPLE"
        and r["ci"][0] > 0
        and r["fdr_ok"]
    ]
    print(f"\n过三关(三段同号 + 诚实门非薄样本 + PnL_CI下界>0 + FDR): {len(passed_rows)}")
    if not passed_rows:
        print("→ 无形态过关 → 日线尺度形态无可信 edge(干净证伪)。")
    else:
        print("→ 候选形态(待 4h 确认集解封验证):")
        for r in passed_rows:
            print(f"   {r['key']} 净R_test={r['mean_te']:+.3f}")

    print("\nobservability-only —— 仅量化,不据此自动改 config/上 live。")


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--interval", default="1d", help="1d(主测) 或 4h(确认集)")
    main(_ap.parse_args().interval)
