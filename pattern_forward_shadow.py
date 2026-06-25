"""日线/4h 形态前向影子记录器(observability-only,严禁决策路径 import)。
--record: 在各 symbol 最新已闭合 bar 检测确认信号(Bearish Engulfing|low|down),write-only 追加。
--settle: 对未结算信号经 settle-when-determinable 结算(早退出立即/整窗满 expired/未满留 None),
          报滚动前向净 R + 诚实门。
验证已确认信号 Bearish Engulfing|低位跌势;绝不接入 live 决策。"""
import sqlite3, json, os, argparse, datetime as dt
from cf_pattern_edge_discovery import context, atr, set_interval_windows, SL_ATR, TP_ATR, MAX_HOLD_DAYS, DB
from utils.candlestick_patterns import detect_patterns
from utils.counterfactual_pnl import resolve_counterfactual
from utils.cf_honesty_gate import summarize_bucket

TARGET = ("Bearish Engulfing", -1)
TARGET_CTX = "low|down"
SIZE = 100.0

# import 同 cf_pattern_edge_discovery 的 BARS_PER_DAY 映射
_BARS_PER_DAY = {"1d": 1, "12h": 2, "6h": 4, "4h": 6, "2h": 12, "1h": 24, "30m": 48, "15m": 96}


def _log_path(interval):
    """Per-interval jsonl 路径: 1d → data/pattern_forward_shadow.jsonl; 4h → data/pattern_forward_shadow_4h.jsonl。"""
    if interval == "1d":
        return "data/pattern_forward_shadow.jsonl"
    return f"data/pattern_forward_shadow_{interval}.jsonl"


def _window_bars(interval):
    """返回 window_bars = MAX_HOLD_DAYS × bars_per_day(interval)。"""
    bpd = _BARS_PER_DAY.get(interval, 1)
    return MAX_HOLD_DAYS * bpd


def _load_bars(sym, interval):
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            "SELECT open_time,open,high,low,close FROM klines WHERE symbol=? AND interval=? ORDER BY open_time",
            (sym, interval)).fetchall()
    except sqlite3.Error:
        rows = []
    conn.close()
    return [{"open_time": t, "open": o, "high": h, "low": l, "close": c} for t, o, h, l, c in rows]


def _symbols(interval):
    conn = sqlite3.connect(DB)
    try:
        r = [x[0] for x in conn.execute("SELECT DISTINCT symbol FROM klines WHERE interval=?", (interval,))]
    except sqlite3.Error:
        r = []
    conn.close()
    return r


def _dedup_key(symbol, detect_bar_open_time, interval):
    """dedup 键: (symbol, detect_bar_open_time_ms, interval)。"""
    return (symbol, int(detect_bar_open_time), interval)


def _existing_keys(interval):
    """读取 per-interval jsonl 构建 dedup 集合。
    新记录: _dedup_key(symbol, detect_bar_open_time, interval)
    旧记录(无 detect_bar_open_time): fallback 用 detect_date_utc 字符串,不破已有记录。
    """
    log = _log_path(interval)
    keys = set()
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
    set_interval_windows(interval)
    log = _log_path(interval)
    keys = _existing_keys(interval)
    added = 0
    for sym in _symbols(interval):
        bars = _load_bars(sym, interval)
        if len(bars) < 60:
            continue
        i = len(bars) - 1  # 最新已闭合 bar(防前视)
        try:
            if context(bars, i) != TARGET_CTX:
                continue
            if TARGET not in detect_patterns(bars[max(0, i - 4):i + 1]):
                continue
            a = atr(bars, i)
            if not a:
                continue
        except Exception:
            continue
        bar_open_time = bars[i]["open_time"]
        key = _dedup_key(sym, bar_open_time, interval)
        if key in keys:
            # fallback: 也检查旧式 detect_date_utc key
            ddate = dt.datetime.utcfromtimestamp(bar_open_time / 1000).strftime("%Y-%m-%d")
            if (sym, ddate, interval) in keys:
                continue
            if key in keys:
                continue
        entry = bars[i]["close"]
        ddate = dt.datetime.utcfromtimestamp(bar_open_time / 1000).strftime("%Y-%m-%d")
        rec = {"detect_date_utc": ddate,
               "detect_bar_open_time": bar_open_time,
               "interval": interval,
               "symbol": sym, "pattern": TARGET[0], "direction": -1,
               "context": TARGET_CTX, "entry": entry, "atr": a,
               "stop_loss": entry + SL_ATR * a, "take_profit": entry - TP_ATR * a,
               "max_hold_days": MAX_HOLD_DAYS, "settled": False}
        with open(log, "a") as f:
            f.write(json.dumps(rec) + "\n")
        keys.add(key)
        added += 1
    print(f"[record][{interval}] 新增 {added} 条;日志累计 {len(keys)} 条。observability-only。")


def _resolve_signal(d, interval):
    """settle-when-determinable 纯函数:扫 future bars[:window_bars] SL-first。
    Returns (net_r, outcome) 或 None(窗口未满留待)。"""
    window_bars = _window_bars(interval)
    bars = _load_bars(d["symbol"], interval)
    start = d.get("detect_bar_open_time")
    if start is None:
        # 旧记录 fallback: detect_date_utc → ms
        start = int(dt.datetime.strptime(d["detect_date_utc"], "%Y-%m-%d").timestamp() * 1000)
    fut = [b for b in bars if b["open_time"] > start]
    entry, sl, tp = d["entry"], d["stop_loss"], d["take_profit"]
    sl_dist = abs(entry - sl) / entry if entry else 0

    for b in fut[:window_bars]:
        hit_sl = b["high"] >= sl
        hit_tp = b["low"] <= tp
        if hit_sl:  # SL-first 保守(含 hit_sl and hit_tp)
            net_usdt = SIZE * ((entry - sl) / entry) - SIZE * 0.002
            net_r = (net_usdt / (SIZE * sl_dist)) if sl_dist > 0 else 0.0
            return net_r, "sl"
        if hit_tp:
            net_usdt = SIZE * ((entry - tp) / entry) - SIZE * 0.002
            net_r = (net_usdt / (SIZE * sl_dist)) if sl_dist > 0 else 0.0
            return net_r, "tp"

    if len(fut) >= window_bars:  # 整窗满且无退出 → expired
        last_close = fut[window_bars - 1]["close"]
        net_usdt = SIZE * ((entry - last_close) / entry) - SIZE * 0.002
        net_r = (net_usdt / (SIZE * sl_dist)) if sl_dist > 0 else 0.0
        return net_r, "expired"

    return None  # 窗口未满 → 留未结算(NO premature expiry)


def settle(interval="1d"):
    log = _log_path(interval)
    if not os.path.exists(log):
        print(f"[settle][{interval}] 无日志。")
        return
    recs = [json.loads(l) for l in open(log) if l.strip()]
    rs = []
    updated = 0
    out = []
    for d in recs:
        if d.get("settled"):
            out.append(d)
            if d.get("net_r") is not None:
                rs.append(d["net_r"])
            continue
        # settle-when-determinable: 无日历门预检,直接尝试
        r = _resolve_signal(d, interval)
        if r is None:
            out.append(d)  # 窗口未满,留待
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
        summ = summarize_bucket(wins=wins, losses=len(rs) - wins, net_usdt_samples=rs)
        print(f"  滚动前向: 胜率{wins / len(rs) * 100:.1f}% 均净{sum(rs) / len(rs):+.3f}R "
              f"总{sum(rs):+.2f}R 诚实门={summ['verdict']}")
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
        record(a.interval)
