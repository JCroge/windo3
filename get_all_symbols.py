#!/usr/bin/env python3
"""获取所有可监控的币种"""

import ccxt

binance = ccxt.binance()
okx = ccxt.okx()

print("获取交易对列表...")
b_tickers = binance.fetch_tickers()
o_tickers = okx.fetch_tickers()

b_symbols = set(s for s in b_tickers.keys() if s.endswith('/USDT'))
o_symbols = set(s for s in o_tickers.keys() if s.endswith('/USDT'))
common = b_symbols & o_symbols

print(f"\n币安USDT交易对: {len(b_symbols)}")
print(f"OKX USDT交易对: {len(o_symbols)}")
print(f"共同交易对: {len(common)}")

# 过滤：日交易量>100万
filtered = []
for symbol in common:
    b_vol = b_tickers[symbol].get('quoteVolume', 0) or 0
    o_vol = o_tickers[symbol].get('quoteVolume', 0) or 0
    avg_vol = (b_vol + o_vol) / 2

    if avg_vol > 1_000_000:
        filtered.append((symbol, avg_vol))

filtered.sort(key=lambda x: x[1], reverse=True)

print(f"\n过滤后（日交易量>100万）: {len(filtered)}")
print(f"\nTop 50:")
for i, (symbol, vol) in enumerate(filtered[:50], 1):
    print(f"{i:2d}. {symbol:15s} ${vol/1e6:.1f}M")
