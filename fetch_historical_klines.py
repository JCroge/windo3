#!/usr/bin/env python3
"""从Binance获取历史K线数据"""

import ccxt
import sqlite3
from datetime import datetime


def fetch_historical_klines(symbol='BTC/USDT', interval='1m', limit=100):
    """从Binance获取历史K线"""
    print(f"从Binance获取 {symbol} 历史K线...")

    exchange = ccxt.binance()

    # 获取OHLCV数据
    ohlcv = exchange.fetch_ohlcv(symbol, interval, limit=limit)

    print(f"✅ 获取了 {len(ohlcv)} 条K线数据")

    # 存储到数据库
    conn = sqlite3.connect('data/klines.db')
    cursor = conn.cursor()

    # 创建表（如果不存在）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS klines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            close_time INTEGER NOT NULL,
            quote_volume REAL,
            trades INTEGER,
            UNIQUE(symbol, interval, open_time)
        )
    ''')

    # 插入数据
    inserted = 0
    for candle in ohlcv:
        timestamp, open_price, high, low, close, volume = candle

        try:
            cursor.execute('''
                INSERT OR REPLACE INTO klines
                (symbol, interval, open_time, open, high, low, close, volume, close_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol.replace('/', ''),
                interval,
                timestamp,
                open_price,
                high,
                low,
                close,
                volume,
                timestamp + 60000  # 1分钟后
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    conn.close()

    print(f"✅ 插入了 {inserted} 条新数据到数据库")
    return inserted


if __name__ == '__main__':
    fetch_historical_klines('BTC/USDT', '1m', 100)
    fetch_historical_klines('ETH/USDT', '1m', 100)
