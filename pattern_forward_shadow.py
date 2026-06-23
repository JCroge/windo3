"""日线形态前向影子记录器(observability-only,严禁决策路径 import)。
--record: 每日在各 symbol 最新已闭合日线检测确认信号(Bearish Engulfing|low|down),write-only 追加。
--settle: 对 ≥10 日前未结算信号经 resolve_counterfactual 结算,报滚动前向净 R + 诚实门。
验证已确认信号 Bearish Engulfing|低位跌势;绝不接入 live 决策。"""
import sqlite3, json, os, argparse, datetime as dt
from cf_pattern_edge_discovery import context, atr, set_interval_windows, SL_ATR, TP_ATR, MAX_HOLD_DAYS, DB
from utils.candlestick_patterns import detect_patterns
from utils.counterfactual_pnl import resolve_counterfactual
from utils.cf_honesty_gate import summarize_bucket

LOG = "data/pattern_forward_shadow.jsonl"
TARGET = ("Bearish Engulfing", -1)
TARGET_CTX = "low|down"


def _load_bars(sym):
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            "SELECT open_time,open,high,low,close FROM klines WHERE symbol=? AND interval='1d' ORDER BY open_time",
            (sym,)).fetchall()
    except sqlite3.Error:
        rows = []
    conn.close()
    return [{"open_time": t, "open": o, "high": h, "low": l, "close": c} for t, o, h, l, c in rows]


def _symbols():
    conn = sqlite3.connect(DB)
    try:
        r = [x[0] for x in conn.execute("SELECT DISTINCT symbol FROM klines WHERE interval='1d'")]
    except sqlite3.Error:
        r = []
    conn.close()
    return r


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
    set_interval_windows("1d")
    keys = _existing_keys()
    added = 0
    for sym in _symbols():
        bars = _load_bars(sym)
        if len(bars) < 60:
            continue
        i = len(bars) - 1  # 最新已闭合 bar(klines.db 只存已收盘日线;防前视)
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
        ddate = dt.datetime.utcfromtimestamp(bars[i]["open_time"] / 1000).strftime("%Y-%m-%d")
        if (sym, ddate) in keys:
            continue
        entry = bars[i]["close"]
        rec = {"detect_date_utc": ddate, "symbol": sym, "pattern": TARGET[0], "direction": -1,
               "context": TARGET_CTX, "entry": entry, "atr": a,
               "stop_loss": entry + SL_ATR * a, "take_profit": entry - TP_ATR * a,
               "max_hold_days": MAX_HOLD_DAYS, "settled": False}
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        keys.add((sym, ddate))
        added += 1
    print(f"[record] 新增 {added} 条;日志累计 {len(keys)} 条。observability-only。")


def settle():
    if not os.path.exists(LOG):
        print("[settle] 无日志。")
        return
    recs = [json.loads(l) for l in open(LOG) if l.strip()]
    now = dt.datetime.utcnow()
    rs = []
    updated = 0
    out = []
    for d in recs:
        if d.get("settled"):
            out.append(d)
            if d.get("net_r") is not None:
                rs.append(d["net_r"])
            continue
        ddt = dt.datetime.strptime(d["detect_date_utc"], "%Y-%m-%d")
        if (now - ddt).days < d["max_hold_days"]:
            out.append(d)
            continue  # 未成熟
        bars = _load_bars(d["symbol"])
        start = int(ddt.timestamp() * 1000)
        fut = [b for b in bars if b["open_time"] > start][:d["max_hold_days"]]
        if len(fut) < 2:
            out.append(d)
            continue
        rec = {"symbol": d["symbol"], "side": "short", "entry_price": d["entry"],
               "stop_loss": d["stop_loss"], "take_profit": [d["take_profit"]], "leverage": 1,
               "size_usdt": 100.0, "funding_rate": 0.0, "created_at": start / 1000.0}
        r = resolve_counterfactual(rec, fut, max_hold_sec=d["max_hold_days"] * 86400)
        if r.net_usdt is None:
            out.append(d)
            continue
        sl_dist = abs(d["entry"] - d["stop_loss"]) / d["entry"]
        net_r = r.net_usdt / (100.0 * sl_dist) if sl_dist > 0 else 0
        d.update(settled=True, net_r=net_r, outcome=r.outcome)
        rs.append(net_r)
        updated += 1
        out.append(d)
    with open(LOG, "w") as f:
        for d in out:
            f.write(json.dumps(d) + "\n")
    print(f"[settle] 新结算 {updated};已结算样本 {len(rs)}")
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
    a = ap.parse_args()
    if a.settle:
        settle()
    else:
        record()
