#!/usr/bin/env python3
"""K线数据采集器 - WebSocket实时订阅"""

import asyncio
import websockets
import json
from datetime import datetime
import sqlite3

class KlineCollector:
    """
    K线数据采集器
    - 实时订阅K线数据
    - 存储到SQLite数据库
    - 支持多个币种和时间周期
    """

    def __init__(self, symbols, interval='1m', db_path='data/klines.db'):
        """
        Args:
            symbols: 币种列表，如 ['BTCUSDT', 'ETHUSDT']
            interval: K线周期，如 '1m', '5m', '15m', '1h'
            db_path: 数据库路径
        """
        self.symbols = symbols
        self.interval = interval
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

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

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_symbol_interval_time
            ON klines(symbol, interval, open_time)
        ''')

        conn.commit()
        conn.close()
        print(f"✅ 数据库初始化完成: {self.db_path}")

    def _save_kline(self, symbol, kline_data):
        """保存K线数据到数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT OR REPLACE INTO klines
                (symbol, interval, open_time, open, high, low, close, volume,
                 close_time, quote_volume, trades)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol,
                self.interval,
                kline_data['t'],  # open_time
                float(kline_data['o']),  # open
                float(kline_data['h']),  # high
                float(kline_data['l']),  # low
                float(kline_data['c']),  # close
                float(kline_data['v']),  # volume
                kline_data['T'],  # close_time
                float(kline_data['q']),  # quote_volume
                kline_data['n']  # trades
            ))
            conn.commit()
        except Exception as e:
            print(f"❌ 保存K线失败 {symbol}: {e}")
        finally:
            conn.close()

    async def stream(self):
        """订阅K线数据流"""
        # 构建WebSocket URL
        streams = [f"{s.lower()}@kline_{self.interval}" for s in self.symbols]
        url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

        print(f"🔗 连接币安K线WebSocket...")
        print(f"   币种: {', '.join(self.symbols)}")
        print(f"   周期: {self.interval}")

        async with websockets.connect(url) as ws:
            print("✅ WebSocket已连接")

            async for msg in ws:
                data = json.loads(msg)
                if 'data' in data:
                    stream_data = data['data']
                    symbol = stream_data['s']
                    kline = stream_data['k']

                    # 只在K线关闭时保存
                    if kline['x']:
                        self._save_kline(symbol, kline)
                        print(f"📊 {symbol} {self.interval} | "
                              f"O:{kline['o']} H:{kline['h']} "
                              f"L:{kline['l']} C:{kline['c']} V:{kline['v']}")

    def get_latest_klines(self, symbol, limit=100):
        """获取最新的K线数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT open_time, open, high, low, close, volume
            FROM klines
            WHERE symbol = ? AND interval = ?
            ORDER BY open_time DESC
            LIMIT ?
        ''', (symbol, self.interval, limit))

        rows = cursor.fetchall()
        conn.close()

        return [{
            'time': row[0],
            'open': row[1],
            'high': row[2],
            'low': row[3],
            'close': row[4],
            'volume': row[5]
        } for row in reversed(rows)]

