#!/usr/bin/env python3
"""分页历史 OHLC 抓取器 → data/klines.db。observability-only 研究数据。"""
import ccxt, sqlite3, time, argparse
from datetime import datetime, timezone

DB = "data/klines.db"
DEFAULT_SYMBOLS = ["BTC","ETH","SOL","XRP","DOGE","BCH","UNI","NEAR","XLM","SUI",
    "WLD","TRUMP","AVAX","LINK","LTC","ADA","TON","APT","ARB","FIL","PEPE","ONDO",
    "TAO","INJ","SEI","TIA","RUNE","AAVE","MKR","ENA"]

def _ensure_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS klines(
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, interval TEXT,
        open_time INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL,
        close_time INTEGER, quote_volume REAL, trades INTEGER,
        UNIQUE(symbol, interval, open_time))''')

def fetch_symbol(ex, conn, symbol, interval, max_bars=4000):
    pair = f"{symbol}/USDT"
    all_rows, since = [], None
    while len(all_rows) < max_bars:
        try:
            o = ex.fetch_ohlcv(pair, interval, since=since, limit=1000)
        except Exception as e:
            print(f"  {symbol} {interval} 抓取失败: {str(e)[:60]}"); break
        if not o: break
        all_rows += o
        if len(o) < 1000: break
        since = o[-1][0] + 1
        time.sleep(ex.rateLimit/1000)
    for t,op,hi,lo,cl,vol in all_rows[:max_bars]:
        try:
            conn.execute('INSERT OR IGNORE INTO klines(symbol,interval,open_time,open,high,low,close,volume,close_time) VALUES(?,?,?,?,?,?,?,?,?)',
                (symbol, interval, t, op, hi, lo, cl, vol, t))
        except sqlite3.Error: pass
    conn.commit()
    return all_rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", default="1d,4h")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--exchange", default="binance")
    args = ap.parse_args()
    ex = getattr(ccxt, args.exchange)()
    conn = sqlite3.connect(DB); _ensure_table(conn)
    syms = args.symbols.split(","); intervals = args.intervals.split(",")
    for interval in intervals:
        print(f"=== interval={interval} ===")
        for s in syms:
            rows = fetch_symbol(ex, conn, s, interval)
            if rows:
                first = datetime.fromtimestamp(rows[0][0]/1000, tz=timezone.utc).date()
                flag = " ←短史" if len(rows) < 200 else ""
                print(f"  {s:<8} {len(rows):>5}根 起{first}{flag}")
            else:
                print(f"  {s:<8} 无数据")
    cur = conn.execute('SELECT interval,COUNT(*),COUNT(DISTINCT symbol) FROM klines GROUP BY interval')
    print("入库汇总:", cur.fetchall())
    conn.close()

if __name__ == "__main__":
    main()
