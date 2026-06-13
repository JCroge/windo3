"""独立 1 秒聚合 bar 采集 → klines_1s.db（interval='1s'）。
复用 kline schema，写独立 db 不污染主 klines.db。
observability-only：仅供反事实回放价格精度，严禁交易决策读取。"""
import os
import sqlite3
import logging

logger = logging.getLogger(__name__)


class OneSecBarStore:
    def __init__(self, db_path, enabled=True):
        self.db_path = db_path
        self.enabled = enabled
        self.drop_count = 0
        if self.enabled:
            try:
                self._init_db()
            except Exception as e:
                logger.warning(f"[TickCapture] init failed: {e}")

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS klines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL, interval TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL NOT NULL,
                    UNIQUE(symbol, interval, open_time)
                )''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_sit ON klines(symbol, interval, open_time)')
            conn.commit()
        finally:
            conn.close()

    def record_bar(self, symbol, open_time_ms, o, h, l, c, v=0.0):
        if not self.enabled:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute('''INSERT OR REPLACE INTO klines
                    (symbol, interval, open_time, open, high, low, close, volume)
                    VALUES (?, '1s', ?, ?, ?, ?, ?, ?)''',
                    (symbol, open_time_ms, float(o), float(h), float(l), float(c), float(v)))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.drop_count += 1
            logger.warning(f"[TickCapture] drop (#{self.drop_count}): {e}")
