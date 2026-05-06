import sqlite3
from datetime import datetime

class Database:
    def __init__(self, db_path='data/market.db'):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.init_tables()

    def init_tables(self):
        cursor = self.conn.cursor()

        # 行情表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT,
                symbol TEXT,
                bid REAL,
                ask REAL,
                timestamp INTEGER
            )
        ''')

        # 交易记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                buy_exchange TEXT,
                sell_exchange TEXT,
                buy_price REAL,
                sell_price REAL,
                amount REAL,
                profit REAL,
                timestamp INTEGER
            )
        ''')

        self.conn.commit()

    def insert_ticker(self, exchange, symbol, bid, ask):
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT INTO tickers (exchange, symbol, bid, ask, timestamp) VALUES (?, ?, ?, ?, ?)',
            (exchange, symbol, bid, ask, int(datetime.now().timestamp()))
        )
        self.conn.commit()

    def insert_trade(self, symbol, buy_ex, sell_ex, buy_price, sell_price, amount, profit):
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT INTO trades (symbol, buy_exchange, sell_exchange, buy_price, sell_price, amount, profit, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (symbol, buy_ex, sell_ex, buy_price, sell_price, amount, profit, int(datetime.now().timestamp()))
        )
        self.conn.commit()
