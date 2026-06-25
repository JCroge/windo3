#!/usr/bin/env python3
"""自包含日线/4h 形态前向影子 runner —— 验证已确认信号 Bearish Engulfing|低位跌势。

为什么自包含:macOS TCC 拒绝把 Full Disk Access 传给 launchd 派生的 CLI python,
导致放在 ~/Desktop 下的仓库脚本被后台调度访问时 EPERM。本 runner **零 Desktop import**
(只用 ccxt[site-packages] + stdlib),部署到非保护目录 ~/Library/Application Support/cryptoarb-fwdshadow/
由 launchd 运行,数据也落在那,彻底绕开 TCC。

逻辑冻结、与 cf_pattern_edge_discovery 同口径:日线/4h、ATR(14bars/84bars) 退出
SL=1.5×/TP=3.0×/最长10日(10×bpd bars)、range_pos(20日/<interval>-bars)<0.25 且
close<MA50(50日/<interval>-bars) = "low|down"、Bearish Engulfing(2K) 看跌。
净 R = net_usdt/(size×sl_dist),flat 往返成本 0.2%(透明假设)。
settle 诚实门:n<30 拒答 + Wilson 区间。observability-only,绝不下单/改 config。

用法:
  python3 fwdshadow_runner.py --record             # 默认 1d
  python3 fwdshadow_runner.py --record --interval 4h
  python3 fwdshadow_runner.py --settle             # 默认 1d
  python3 fwdshadow_runner.py --settle --interval 4h
数据目录:环境变量 FWDSHADOW_DIR,否则用本脚本所在目录。
"""
import os
import sys
import json
import math
import sqlite3
import argparse
import datetime as dt

# ── 与 fetch_historical_klines.DEFAULT_SYMBOLS 同一份冻结列表(100 个) ──
SYMBOLS = ["BTC","ETH","MEGA","SOL","USD1","XRP","ZEC","BNB","DOGE","WLD",
    "TRX","ADA","XPL","AAVE","SPCXB","XAUT","PAXG","SUI","NEAR","RLUSD","SYN",
    "TAO","RE","PEPE","AVAX","LINK","XLM","ENA","HEI","LTC","SAHARA","HYPER",
    "UNI","ATM","UTK","HBAR","PUMP","TRUMP","BCH","ONDO","ASTER","ID","ALICE","FET",
    "TON","FIL","DASH","CELO","RESOLV","ALLO","RENDER","SNDKB","WBTC","INJ",
    "JTO","WLFI","DOT","ICP","MUB","OPG","OP","SEI","POL","PENGU","AVNT","ARB",
    "ZRO","CHZ","APT","TIA","DYDX","AXS","DEXE","ETC","XUSD","BIO",
    "SHIB","MITO","ALGO","JST","LDO","BICO","MMT","EIGEN","ORDI","CRV","BEL",
    "ETHFI","ZAMA","ATOM","VIRTUAL","HOME","OPN","LUNC","STRAX","TNSR",
    "LRC","ROBO","CAKE","WIF"]

# ── interval 参数化:每日 bar 数 + 基准天数常量 ──
BARS_PER_DAY = {"1d": 1, "4h": 6}
ATR_DAYS, RANGE_DAYS, MA_DAYS, HOLD_DAYS = 14, 20, 50, 10

SL_ATR, TP_ATR = 1.5, 3.0
COST_RT = 0.002           # flat 往返成本假设(透明)
TARGET_CTX = "low|down"
SIZE, LEV = 100.0, 1.0
DAY_MS = 86_400_000


def _interval_windows(interval):
    """返回 (atr_n, range_n, ma_n, window_bars) 按 interval 缩放。
    1d → (14, 20, 50, 10); 4h → (84, 120, 300, 60)。"""
    bpd = BARS_PER_DAY.get(interval, 1)
    return ATR_DAYS * bpd, RANGE_DAYS * bpd, MA_DAYS * bpd, HOLD_DAYS * bpd


def _dedup_key(symbol, bar_open_time, interval):
    """dedup 键: (symbol, bar_open_time_ms, interval)。
    4h 同 symbol 同 UTC 日不同 bar 时间 → 不同键,不塌缩。"""
    return (symbol, int(bar_open_time), interval)


DATA_DIR = os.environ.get("FWDSHADOW_DIR") or os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DATA_DIR, "klines.db")


def _log_path(interval):
    """Per-interval jsonl 路径: 1d → pattern_forward_shadow.jsonl; 4h → pattern_forward_shadow_4h.jsonl。"""
    name = "pattern_forward_shadow.jsonl" if interval == "1d" else f"pattern_forward_shadow_{interval}.jsonl"
    return os.path.join(DATA_DIR, name)


# ── 历史 OHLC(ccxt,向历史纵深正向分页,幂等) ──
def _ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS klines(
        symbol TEXT, interval TEXT, open_time INTEGER,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        UNIQUE(symbol, interval, open_time))""")


def fetch_bars(interval="1d", max_bars=1200):
    """拉取指定 interval 的历史 K 线。"""
    import ccxt
    ex = ccxt.binance()
    conn = sqlite3.connect(DB)
    _ensure_table(conn)
    now_ms = int(dt.datetime.utcnow().timestamp() * 1000)
    bpd = BARS_PER_DAY.get(interval, 1)
    interval_ms = DAY_MS // bpd
    since0 = now_ms - max_bars * interval_ms
    n_ok = 0
    for sym in SYMBOLS:
        rows, since = {}, since0
        while True:
            try:
                o = ex.fetch_ohlcv(f"{sym}/USDT", interval, since=since, limit=1000)
            except Exception as e:
                print(f"  {sym} 抓取失败: {str(e)[:50]}")
                break
            if not o:
                break
            for r in o:
                rows[r[0]] = r
            last = o[-1][0]
            if len(o) < 1000 or last + interval_ms > now_ms:
                break
            since = last + interval_ms
        for t in sorted(rows):
            r = rows[t]
            conn.execute("INSERT OR IGNORE INTO klines VALUES(?,?,?,?,?,?,?,?)",
                         (sym, interval, r[0], r[1], r[2], r[3], r[4], r[5]))
        if rows:
            n_ok += 1
    conn.commit()
    conn.close()
    print(f"[fetch] {n_ok}/{len(SYMBOLS)} 币 {interval} 已刷新 → {DB}")


# 向前兼容别名
def fetch_daily(max_bars=1200):
    fetch_bars("1d", max_bars)


def load_bars(sym, interval="1d"):
    """从 klines.db 读取指定 symbol+interval 的历史 K 线。"""
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute("SELECT open_time,open,high,low,close FROM klines "
                            "WHERE symbol=? AND interval=? ORDER BY open_time",
                            (sym, interval)).fetchall()
    except sqlite3.Error:
        rows = []
    conn.close()
    return [{"open_time": t, "open": o, "high": h, "low": l, "close": c} for t, o, h, l, c in rows]


# ── 指标 / 上下文 / 形态(与 lab 同口径) ──
def atr(bars, i, n=None):
    if n is None:
        n = ATR_DAYS
    if i < n:
        return None
    trs = [max(bars[j]["high"] - bars[j]["low"],
               abs(bars[j]["high"] - bars[j - 1]["close"]),
               abs(bars[j]["low"] - bars[j - 1]["close"])) for j in range(i - n + 1, i + 1)]
    return sum(trs) / n


def context(bars, i, range_n=None, ma_n=None):
    if range_n is None:
        range_n = RANGE_DAYS
    if ma_n is None:
        ma_n = MA_DAYS
    if i < ma_n:
        return None
    win = bars[i - range_n + 1:i + 1]
    hi = max(b["high"] for b in win)
    lo = min(b["low"] for b in win)
    rp = (bars[i]["close"] - lo) / max(hi - lo, 1e-9)
    ma = sum(b["close"] for b in bars[i - ma_n + 1:i + 1]) / ma_n
    trend = "up" if bars[i]["close"] > ma else "down"
    rp_b = "low" if rp < 0.25 else ("high" if rp > 0.75 else "mid")
    return f"{rp_b}|{trend}"


def is_bearish_engulfing(prev, cur):
    bull0 = prev["close"] > prev["open"]
    bear1 = cur["close"] < cur["open"]
    return (bull0 and bear1 and cur["open"] >= prev["close"] and cur["close"] <= prev["open"]
            and abs(cur["close"] - cur["open"]) > abs(prev["close"] - prev["open"]))


# ── 结算核心:纯函数 ──
def _net_r(entry, exit_px, sl):
    """计算净 R(空单口径)。"""
    gross_pct = (entry - exit_px) / entry            # 空单:涨亏跌盈
    sl_dist_pct = abs(sl - entry) / entry
    net_usdt = SIZE * LEV * gross_pct - SIZE * LEV * COST_RT
    risk = SIZE * LEV * sl_dist_pct
    return (net_usdt / risk) if risk > 0 else 0.0


def resolve_signal(rec, fut_bars, window_bars):
    """纯函数:扫描 fut_bars[:window_bars](SL-first)决定退出。

    Returns:
        (net_r: float, outcome: str)  — 已确定退出
        None                          — 窗口未满,留待下次
    """
    fut = fut_bars[:window_bars]
    entry, sl, tp = rec["entry"], rec["stop_loss"], rec["take_profit"]  # short: sl>entry>tp
    for b in fut:
        hit_sl = b["high"] >= sl
        hit_tp = b["low"] <= tp
        if hit_sl:  # 同根 SL-first 保守(含 hit_sl and hit_tp)
            return _net_r(entry, sl, sl), "sl"
        if hit_tp:
            return _net_r(entry, tp, sl), "tp"
    if len(fut) >= window_bars:           # 整窗满且无退出 → expired
        return _net_r(entry, fut[-1]["close"], sl), "expired"
    return None                           # 窗口未满 → 留未结算


# ── 记录(防前视:仅最新已闭合 bar) ──
def _existing_keys(interval):
    """读取 per-interval jsonl 构建 dedup 集合。

    新记录: _dedup_key(symbol, detect_bar_open_time, interval)
    旧记录(无 detect_bar_open_time): fallback 用 detect_date_utc 字符串,不破旧 5 条日线记录。
    """
    keys = set()
    log = _log_path(interval)
    if os.path.exists(log):
        for line in open(log):
            try:
                d = json.loads(line)
                if "detect_bar_open_time" in d and "interval" in d:
                    keys.add(_dedup_key(d["symbol"], d["detect_bar_open_time"], d["interval"]))
                else:
                    # fallback 旧记录
                    keys.add((d["symbol"], d.get("detect_date_utc", ""), interval))
            except Exception:
                pass
    return keys


def record(interval="1d"):
    atr_n, range_n, ma_n, window_bars = _interval_windows(interval)
    min_bars_needed = ma_n + 2
    keys = _existing_keys(interval)
    log = _log_path(interval)
    added = 0
    for sym in SYMBOLS:
        bars = load_bars(sym, interval)
        if len(bars) < min_bars_needed:
            continue
        i = len(bars) - 1  # 最新已闭合 <interval> bar
        if context(bars, i, range_n=range_n, ma_n=ma_n) != TARGET_CTX:
            continue
        if not is_bearish_engulfing(bars[i - 1], bars[i]):
            continue
        a = atr(bars, i, n=atr_n)
        if not a:
            continue
        bar_open_time = bars[i]["open_time"]
        key = _dedup_key(sym, bar_open_time, interval)
        if key in keys:
            continue
        ddate = dt.datetime.utcfromtimestamp(bar_open_time / 1000).strftime("%Y-%m-%d")
        entry = bars[i]["close"]
        rec = {"detect_date_utc": ddate,
               "detect_bar_open_time": bar_open_time,
               "interval": interval,
               "symbol": sym,
               "pattern": "Bearish Engulfing",
               "direction": -1,
               "context": TARGET_CTX,
               "entry": entry,
               "atr": a,
               "stop_loss": entry + SL_ATR * a,
               "take_profit": entry - TP_ATR * a,
               "max_hold_days": HOLD_DAYS,
               "settled": False}
        with open(log, "a") as f:
            f.write(json.dumps(rec) + "\n")
        keys.add(key)
        added += 1
    print(f"[record][{interval}] 新增 {added};日志累计 {len(keys)}。observability-only。")


# ── 结算(settle-when-determinable: 删除日历门,窗口未满则留待) ──
def _settle_one(rec, interval):
    """对单条未结算记录尝试结算。返回 (net_r, outcome) 或 None(未满窗)。"""
    _, _, _, window_bars = _interval_windows(interval)
    bars = load_bars(rec["symbol"], interval)
    start = rec.get("detect_bar_open_time")
    if start is None:
        # 旧记录 fallback: 从 detect_date_utc 推算 ms
        start = int(dt.datetime.strptime(rec["detect_date_utc"], "%Y-%m-%d").timestamp() * 1000)
    fut = [b for b in bars if b["open_time"] > start]
    return resolve_signal(rec, fut, window_bars)


def _wilson(wins, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def settle(interval="1d"):
    """结算未结算记录: settle-when-determinable(无日历门)。

    对每条未结算记录调用 _settle_one; None → 保持未结算(窗口未满)。
    """
    log = _log_path(interval)
    if not os.path.exists(log):
        print(f"[settle][{interval}] 无日志。")
        return
    recs = [json.loads(l) for l in open(log) if l.strip()]
    rs, updated, out = [], 0, []
    for d in recs:
        if d.get("settled"):
            out.append(d)
            if d.get("net_r") is not None:
                rs.append(d["net_r"])
            continue
        # settle-when-determinable: 直接尝试,不做日历预检
        r = _settle_one(d, interval)
        if r is None:
            out.append(d)   # 窗口未满,留待
            continue
        net_r, outcome = r
        d.update(settled=True, net_r=net_r, outcome=outcome)
        rs.append(net_r)
        updated += 1
        out.append(d)
    with open(log, "w") as f:
        for d in out:
            f.write(json.dumps(d) + "\n")
    print(f"[settle][{interval}] 新结算 {updated};已结算样本 {len(rs)}")
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
    ap.add_argument("--interval", choices=["1d", "4h"], default="1d",
                    help="K 线周期: 1d(默认) 或 4h")
    a = ap.parse_args()
    if a.settle:
        settle(a.interval)
    else:
        fetch_bars(a.interval)
        record(a.interval)
