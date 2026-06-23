#!/usr/bin/env python3
"""自包含日线形态前向影子 runner —— 验证已确认信号 Bearish Engulfing|低位跌势。

为什么自包含:macOS TCC 拒绝把 Full Disk Access 传给 launchd 派生的 CLI python,
导致放在 ~/Desktop 下的仓库脚本被后台调度访问时 EPERM。本 runner **零 Desktop import**
(只用 ccxt[site-packages] + stdlib),部署到非保护目录 ~/Library/Application Support/cryptoarb-fwdshadow/
由 launchd 运行,数据也落在那,彻底绕开 TCC。

逻辑冻结、与 cf_pattern_edge_discovery 同口径:日线、ATR(14) 退出 SL=1.5×/TP=3.0×/最长10日、
range_pos(20日)<0.25 且 close<MA50 = "low|down"、Bearish Engulfing(2K) 看跌。净 R = net_usdt/(size×sl_dist),
flat 往返成本 0.2%(透明假设)。settle 诚实门:n<30 拒答 + Wilson 区间。observability-only,绝不下单/改 config。

用法:
  python3 fwdshadow_runner.py --record   # 默认:拉日线 + 检测当日确认信号 → 追加 jsonl(幂等)
  python3 fwdshadow_runner.py --settle    # 结算 ≥10 日成熟信号 + 滚动前向报告
数据目录:环境变量 FWDSHADOW_DIR,否则用本脚本所在目录。
"""
import os
import sys
import json
import math
import sqlite3
import argparse
import datetime as dt

DATA_DIR = os.environ.get("FWDSHADOW_DIR") or os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DATA_DIR, "klines.db")
LOG = os.path.join(DATA_DIR, "pattern_forward_shadow.jsonl")

SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BCH", "UNI", "NEAR", "XLM", "SUI",
           "WLD", "TRUMP", "AVAX", "LINK", "LTC", "ADA", "TON", "APT", "ARB", "FIL",
           "PEPE", "ONDO", "TAO", "INJ", "SEI", "TIA", "RUNE", "AAVE", "MKR", "ENA"]

ATR_N, RANGE_N, MA_N = 14, 20, 50
SL_ATR, TP_ATR, MAX_HOLD_DAYS = 1.5, 3.0, 10
COST_RT = 0.002           # flat 往返成本假设(透明)
TARGET_CTX = "low|down"
SIZE, LEV = 100.0, 1.0
DAY_MS = 86_400_000


# ── 历史 OHLC(ccxt,向历史纵深正向分页,幂等) ──
def _ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS klines(
        symbol TEXT, interval TEXT, open_time INTEGER,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        UNIQUE(symbol, interval, open_time))""")


def fetch_daily(max_bars=1200):
    import ccxt
    ex = ccxt.binance()
    conn = sqlite3.connect(DB)
    _ensure_table(conn)
    now_ms = int(dt.datetime.utcnow().timestamp() * 1000)
    since0 = now_ms - max_bars * DAY_MS
    n_ok = 0
    for sym in SYMBOLS:
        rows, since = {}, since0
        while True:
            try:
                o = ex.fetch_ohlcv(f"{sym}/USDT", "1d", since=since, limit=1000)
            except Exception as e:
                print(f"  {sym} 抓取失败: {str(e)[:50]}")
                break
            if not o:
                break
            for r in o:
                rows[r[0]] = r
            last = o[-1][0]
            if len(o) < 1000 or last + DAY_MS > now_ms:
                break
            since = last + DAY_MS
        for t in sorted(rows):
            r = rows[t]
            conn.execute("INSERT OR IGNORE INTO klines VALUES(?,?,?,?,?,?,?,?)",
                         (sym, "1d", r[0], r[1], r[2], r[3], r[4], r[5]))
        if rows:
            n_ok += 1
    conn.commit()
    conn.close()
    print(f"[fetch] {n_ok}/{len(SYMBOLS)} 币日线已刷新 → {DB}")


def load_bars(sym):
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute("SELECT open_time,open,high,low,close FROM klines "
                            "WHERE symbol=? AND interval='1d' ORDER BY open_time", (sym,)).fetchall()
    except sqlite3.Error:
        rows = []
    conn.close()
    return [{"open_time": t, "open": o, "high": h, "low": l, "close": c} for t, o, h, l, c in rows]


# ── 指标 / 上下文 / 形态(与 lab 同口径) ──
def atr(bars, i, n=ATR_N):
    if i < n:
        return None
    trs = [max(bars[j]["high"] - bars[j]["low"],
               abs(bars[j]["high"] - bars[j - 1]["close"]),
               abs(bars[j]["low"] - bars[j - 1]["close"])) for j in range(i - n + 1, i + 1)]
    return sum(trs) / n


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


def is_bearish_engulfing(prev, cur):
    bull0 = prev["close"] > prev["open"]
    bear1 = cur["close"] < cur["open"]
    return (bull0 and bear1 and cur["open"] >= prev["close"] and cur["close"] <= prev["open"]
            and abs(cur["close"] - cur["open"]) > abs(prev["close"] - prev["open"]))


# ── 记录(防前视:仅最新已闭合 bar) ──
def _existing_keys():
    keys = set()
    if os.path.exists(LOG):
        for line in open(LOG):
            try:
                d = json.loads(line)
                keys.add((d["symbol"], d["detect_date_utc"]))
            except Exception:
                pass
    return keys


def record():
    keys = _existing_keys()
    added = 0
    for sym in SYMBOLS:
        bars = load_bars(sym)
        if len(bars) < MA_N + 2:
            continue
        i = len(bars) - 1  # 最新已闭合日线
        if context(bars, i) != TARGET_CTX:
            continue
        if not is_bearish_engulfing(bars[i - 1], bars[i]):
            continue
        a = atr(bars, i)
        if not a:
            continue
        ddate = dt.datetime.utcfromtimestamp(bars[i]["open_time"] / 1000).strftime("%Y-%m-%d")
        if (sym, ddate) in keys:
            continue
        entry = bars[i]["close"]
        rec = {"detect_date_utc": ddate, "symbol": sym, "pattern": "Bearish Engulfing",
               "direction": -1, "context": TARGET_CTX, "entry": entry, "atr": a,
               "stop_loss": entry + SL_ATR * a, "take_profit": entry - TP_ATR * a,
               "max_hold_days": MAX_HOLD_DAYS, "settled": False}
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        keys.add((sym, ddate))
        added += 1
    print(f"[record] 新增 {added};日志累计 {len(keys)}。observability-only。")


# ── 结算(SL-first 保守路径 + flat 成本 + Wilson 诚实门) ──
def _settle_one(rec):
    bars = load_bars(rec["symbol"])
    start = int(dt.datetime.strptime(rec["detect_date_utc"], "%Y-%m-%d").timestamp() * 1000)
    fut = [b for b in bars if b["open_time"] > start][:rec["max_hold_days"]]
    if len(fut) < 2:
        return None
    entry, sl, tp = rec["entry"], rec["stop_loss"], rec["take_profit"]  # short: sl>entry>tp
    exit_px, outcome = fut[-1]["close"], "expired"
    for b in fut:
        hit_sl = b["high"] >= sl
        hit_tp = b["low"] <= tp
        if hit_sl and hit_tp:
            exit_px, outcome = sl, "sl"; break          # 同根 SL-first 保守
        if hit_sl:
            exit_px, outcome = sl, "sl"; break
        if hit_tp:
            exit_px, outcome = tp, "tp"; break
    gross_pct = (entry - exit_px) / entry               # 空单
    sl_dist_pct = abs(sl - entry) / entry
    net_usdt = SIZE * LEV * gross_pct - SIZE * LEV * COST_RT
    risk = SIZE * LEV * sl_dist_pct
    return (net_usdt / risk) if risk > 0 else 0.0, outcome


def _wilson(wins, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def settle():
    if not os.path.exists(LOG):
        print("[settle] 无日志。")
        return
    recs = [json.loads(l) for l in open(LOG) if l.strip()]
    now = dt.datetime.utcnow()
    rs, updated, out = [], 0, []
    for d in recs:
        if d.get("settled"):
            out.append(d)
            if d.get("net_r") is not None:
                rs.append(d["net_r"])
            continue
        ddt = dt.datetime.strptime(d["detect_date_utc"], "%Y-%m-%d")
        if (now - ddt).days < d["max_hold_days"]:
            out.append(d)
            continue
        r = _settle_one(d)
        if r is None:
            out.append(d)
            continue
        net_r, outcome = r
        d.update(settled=True, net_r=net_r, outcome=outcome)
        rs.append(net_r)
        updated += 1
        out.append(d)
    with open(LOG, "w") as f:
        for d in out:
            f.write(json.dumps(d) + "\n")
    print(f"[settle] 新结算 {updated};已结算样本 {len(rs)}")
    if rs:
        wins = sum(1 for x in rs if x > 0)
        lo, hi = _wilson(wins, len(rs))
        verdict = "INSUFFICIENT_SAMPLE" if len(rs) < 30 else ("low_confidence" if len(rs) < 100 else "actionable")
        print(f"  滚动前向: n={len(rs)} 胜率{wins / len(rs) * 100:.1f}%(Wilson[{lo*100:.0f},{hi*100:.0f}]) "
              f"均净{sum(rs) / len(rs):+.3f}R 总{sum(rs):+.2f}R 诚实门={verdict}")
    print("observability-only —— 仅量化,不据此自动改 config/上 live。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--settle", action="store_true")
    a = ap.parse_args()
    if a.settle:
        settle()
    else:
        fetch_daily()
        record()
