"""5/27 当日两轮研判终选事后回顾.

研判时间点（北京时间，已知）:
- 01:33  终选 11 标的 (终选时刻 ~01:33, 之前 4h 节奏的窗口)
- 09:38  终选 9  标的

对每个标的拉取 1h K线, 看研判时刻起 +4h、+8h、和"最高点 / 最低点"价格变动,
量化"如果在研判时刻按 LLM 方向开多/开空, 4h/8h 内 R = (后续峰值 - 研判时刻收盘) / 研判时刻收盘 是多少".

只是定性观察, 不做交易决策.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

import ccxt


CYCLES = [
    {
        "label": "5/27 01:33 终选",
        "decision_ts": "2026-05-27 01:33:00",
        "symbols": [
            "TON-USDT", "WLD-USDT", "NEAR-USDT", "HYPE-USDT", "SUI-USDT",
            "BTC-USDT", "ONDO-USDT", "TRUMP-USDT", "DOGE-USDT", "ADA-USDT",
            "GRASS-USDT",
        ],
    },
    {
        "label": "5/27 09:38 终选",
        "decision_ts": "2026-05-27 09:38:00",
        "symbols": [
            "WLD-USDT", "HYPE-USDT", "TON-USDT", "RENDER-USDT", "DYDX-USDT",
            "FIL-USDT", "VIRTUAL-USDT", "TAO-USDT", "ONDO-USDT",
        ],
    },
]

# 用 OKX swap 永续作为价格源
def to_okx_swap(sym: str) -> str:
    base = sym.split("-")[0]
    return f"{base}/USDT:USDT"


def fetch_window(ex: ccxt.Exchange, sym: str, start_ts_ms: int, hours: int = 9):
    """拉 1h K 线, 从研判时刻往前 1h 起到往后 hours 小时."""
    since = start_ts_ms - 60 * 60 * 1000
    bars = ex.fetch_ohlcv(to_okx_swap(sym), timeframe="1h", since=since, limit=hours + 2)
    return bars  # [[ts, o, h, l, c, v], ...]


def analyze(bars, anchor_ms: int):
    """anchor_ms 处的 1h 收盘 (anchor 落在哪根 1h 的 close 之前) 作为基准价,
    返回 (anchor_close, max_up_ret, max_down_ret, ret_4h, ret_8h)."""
    if not bars:
        return None
    # 找到第一根 close_ts >= anchor_ms 的 K 线 (其 open_ts <= anchor_ms < open_ts+1h)
    anchor_idx = None
    for i, bar in enumerate(bars):
        bar_open_ts = bar[0]
        bar_close_ts = bar_open_ts + 60 * 60 * 1000
        if bar_open_ts <= anchor_ms < bar_close_ts:
            anchor_idx = i
            break
    if anchor_idx is None:
        return None
    anchor_close = bars[anchor_idx][4]  # 用 anchor 所在 1h 的 close 作研判时刻参考价
    after = bars[anchor_idx + 1:]
    if not after:
        return {
            "anchor_close": anchor_close, "n_bars_after": 0,
            "max_up_pct": 0.0, "max_down_pct": 0.0,
            "ret_4h_pct": 0.0, "ret_8h_pct": 0.0,
        }
    highs = [b[2] for b in after]
    lows = [b[3] for b in after]
    closes = [b[4] for b in after]
    max_up_pct = (max(highs) / anchor_close - 1) * 100
    max_down_pct = (min(lows) / anchor_close - 1) * 100
    ret_4h = (closes[3] / anchor_close - 1) * 100 if len(closes) >= 4 else None
    ret_8h = (closes[7] / anchor_close - 1) * 100 if len(closes) >= 8 else None
    return {
        "anchor_close": anchor_close,
        "n_bars_after": len(after),
        "max_up_pct": max_up_pct,
        "max_down_pct": max_down_pct,
        "ret_4h_pct": ret_4h,
        "ret_8h_pct": ret_8h,
    }


def run():
    ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    cn_tz = timezone(timedelta(hours=8))
    for cycle in CYCLES:
        print(f"\n=== {cycle['label']} ===")
        dec_ts = datetime.strptime(cycle["decision_ts"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=cn_tz)
        anchor_ms = int(dec_ts.timestamp() * 1000)
        rows = []
        for sym in cycle["symbols"]:
            try:
                bars = fetch_window(ex, sym, anchor_ms, hours=9)
                r = analyze(bars, anchor_ms)
                if r is None:
                    rows.append((sym, "no_data"))
                    continue
                rows.append((sym, r))
            except Exception as e:
                rows.append((sym, f"err:{e}"))
            time.sleep(0.15)
        # 打印
        print(f"{'symbol':<14} {'anchor':>10} {'max_up%':>8} {'max_dn%':>8} {'ret_4h%':>8} {'ret_8h%':>8} bars")
        for sym, r in rows:
            if isinstance(r, str):
                print(f"{sym:<14} {r}")
                continue
            print(f"{sym:<14} {r['anchor_close']:>10.4f} "
                  f"{r['max_up_pct']:>+8.2f} {r['max_down_pct']:>+8.2f} "
                  f"{(r['ret_4h_pct'] if r['ret_4h_pct'] is not None else 0):>+8.2f} "
                  f"{(r['ret_8h_pct'] if r['ret_8h_pct'] is not None else 0):>+8.2f} "
                  f"{r['n_bars_after']}")


if __name__ == "__main__":
    run()
