#!/usr/bin/env python3
"""分页历史 OHLC 抓取器 → data/klines.db。observability-only 研究数据。"""
import ccxt, sqlite3, time, argparse
from datetime import datetime, timezone

DB = "data/klines.db"
DEFAULT_SYMBOLS = ["BTC","ETH","MEGA","SOL","USD1","XRP","ZEC","BNB","DOGE","WLD",
    "TRX","ADA","XPL","AAVE","SPCXB","XAUT","PAXG","SUI","NEAR","RLUSD","SYN",
    "TAO","RE","PEPE","AVAX","LINK","U","XLM","ENA","HEI","LTC","SAHARA","HYPER",
    "UNI","ATM","UTK","HBAR","PUMP","TRUMP","BCH","ONDO","ASTER","ID","ALICE","FET",
    "TON","FIL","DASH","CELO","RESOLV","ALLO","SNDKB","RENDER","WBTC","INJ","G",
    "JTO","WLFI","DOT","ICP","MUB","OPG","OP","SEI","POL","PENGU","AVNT","ARB",
    "ZRO","CHZ","APT","TIA","币安人生","DYDX","AXS","DEXE","S","ETC","XUSD","BIO",
    "SHIB","MITO","ALGO","JST","LDO","BICO","MMT","EIGEN","ORDI","CRV","BEL",
    "ETHFI","ZAMA","ATOM","HOME","VIRTUAL","OPN","LUNC","STRAX","TNSR"]

def _ensure_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS klines(
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, interval TEXT,
        open_time INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL,
        close_time INTEGER, quote_volume REAL, trades INTEGER,
        UNIQUE(symbol, interval, open_time))''')

_INTERVAL_MS = {"1m":60_000,"3m":180_000,"5m":300_000,"15m":900_000,"30m":1_800_000,
    "1h":3_600_000,"2h":7_200_000,"4h":14_400_000,"6h":21_600_000,"12h":43_200_000,
    "1d":86_400_000,"1w":604_800_000}

def fetch_symbol(ex, conn, symbol, interval, max_bars=4000):
    """从历史纵深起点(now - max_bars×interval)正向翻页至最新。open_time 去重。
    日线:start 远早于上市日 → 交易所自上市返回,行为不变。子日线:取回 ~max_bars 根历史深度。"""
    pair = f"{symbol}/USDT"
    step = _INTERVAL_MS.get(interval, 86_400_000)
    now_ms = int(time.time() * 1000)
    since = now_ms - max_bars * step
    by_time = {}
    while True:
        try:
            o = ex.fetch_ohlcv(pair, interval, since=since, limit=1000)
        except Exception as e:
            print(f"  {symbol} {interval} 抓取失败: {str(e)[:60]}"); break
        if not o: break
        for row in o:
            by_time[row[0]] = row
        last = o[-1][0]
        if len(o) < 1000 or last + step > now_ms or len(by_time) >= max_bars + 1000:
            break
        nxt = last + step
        if nxt <= since: break          # 防卡死
        since = nxt
        time.sleep(ex.rateLimit/1000)
    all_rows = [by_time[t] for t in sorted(by_time)][-max_bars:]
    for t,op,hi,lo,cl,vol in all_rows:
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
