import os, sqlite3
from utils.tick_capture import OneSecBarStore


def test_writes_1s_bar(tmp_path):
    db = str(tmp_path / "klines_1s.db")
    store = OneSecBarStore(db_path=db, enabled=True)
    store.record_bar("BTC-USDT", open_time_ms=1_700_000_000_000,
                     o=100, h=101, l=99, c=100.5, v=12.3)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT symbol, interval, open, high, low, close FROM klines").fetchall()
    conn.close()
    assert rows == [("BTC-USDT", "1s", 100.0, 101.0, 99.0, 100.5)]


def test_upsert_dedup(tmp_path):
    db = str(tmp_path / "klines_1s.db")
    store = OneSecBarStore(db_path=db, enabled=True)
    store.record_bar("BTC-USDT", 1_700_000_000_000, 100, 101, 99, 100.5, 1)
    store.record_bar("BTC-USDT", 1_700_000_000_000, 100, 102, 98, 100.7, 2)  # same open_time
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    last = conn.execute("SELECT high FROM klines").fetchone()[0]
    conn.close()
    assert n == 1 and last == 102.0  # INSERT OR REPLACE


def test_flag_off_no_db(tmp_path):
    db = str(tmp_path / "klines_1s.db")
    store = OneSecBarStore(db_path=db, enabled=False)
    store.record_bar("BTC-USDT", 1_700_000_000_000, 100, 101, 99, 100.5, 1)
    assert not os.path.exists(db)


def test_failure_isolated(tmp_path):
    bad = str(tmp_path / "afile")
    open(bad, "w").close()  # regular file as bogus parent dir
    store = OneSecBarStore(db_path=bad + "/k.db", enabled=True)
    store.record_bar("BTC-USDT", 1, 1, 1, 1, 1, 1)  # must not raise
    assert store.drop_count == 1
