#!/usr/bin/env python3
# scripts/derive_universe.py —— 一次性派生 binance USDT-spot 成交量 top~100 流动 universe
# (构建期跑一次, 结果固化进代码)
import re
import ccxt

STABLES = {"USDC","USDT","FDUSD","TUSD","DAI","USDP","PYUSD","BUSD","EUR","GBP","USTC"}
SANE_BASE = re.compile(r"[A-Z0-9]{2,15}")  # 大写字母数字, 长度 2-15

def is_leveraged(base): return any(base.endswith(s) for s in ("UP","DOWN","BULL","BEAR"))

def derive(top=100):
    ex = ccxt.binance()
    ts = ex.fetch_tickers()
    rows = []
    for sym, t in ts.items():
        if not sym.endswith("/USDT"): continue
        base = sym[:-5]
        if base in STABLES or is_leveraged(base): continue
        if not SANE_BASE.fullmatch(base): continue  # 排除非标准/promo base(非ASCII+单字符+畸形)
        qv = t.get("quoteVolume") or 0
        rows.append((base, qv))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [b for b, _ in rows[:top]]

if __name__ == "__main__":
    syms = derive()
    print(f"# {len(syms)} symbols")
    print(", ".join(f'"{s}"' for s in syms))
