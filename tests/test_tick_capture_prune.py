"""tick-capture-retention-prune：OneSecBarStore retention 滚动清理测试。
observability-only —— 验证 klines_1s.db 有界、节流、fail-safe。"""
import sqlite3
import time

from utils.tick_capture import OneSecBarStore


def _count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    finally:
        conn.close()


def test_prune_deletes_expired_keeps_in_window(tmp_path):
    db = str(tmp_path / "klines_1s.db")
    # prune_every=1 → 每次写都触发，方便确定性断言
    store = OneSecBarStore(db, enabled=True, retention_days=1, prune_every=1)
    now_ms = int(time.time() * 1000)
    day_ms = 86400 * 1000
    # 一条 2 天前（超期），一条 1 小时前（界内）
    store.record_bar("BTC-USDT", now_ms - 2 * day_ms, 1, 1, 1, 1)
    store.record_bar("BTC-USDT", now_ms - 3600 * 1000, 2, 2, 2, 2)
    rows = sqlite3.connect(db).execute(
        "SELECT open_time FROM klines ORDER BY open_time").fetchall()
    # 超期行已被 prune 删除，仅保留界内行
    assert len(rows) == 1
    assert rows[0][0] == now_ms - 3600 * 1000


def test_prune_throttled_by_prune_every(tmp_path):
    db = str(tmp_path / "klines_1s.db")
    store = OneSecBarStore(db, enabled=True, retention_days=1, prune_every=5)
    now_ms = int(time.time() * 1000)
    old = now_ms - 2 * 86400 * 1000
    # 写 4 条超期 bar（< prune_every=5）→ 尚未触发 prune，旧行仍在
    for i in range(4):
        store.record_bar("ETH-USDT", old + i, 1, 1, 1, 1)
    assert _count(db) == 4
    # 第 5 条触发 prune → 全部超期行（含本条同为超期）被删
    store.record_bar("ETH-USDT", old + 4, 1, 1, 1, 1)
    assert _count(db) == 0


def test_prune_failure_does_not_break_record(tmp_path, monkeypatch):
    db = str(tmp_path / "klines_1s.db")
    store = OneSecBarStore(db, enabled=True, retention_days=1, prune_every=1)
    now_ms = int(time.time() * 1000)

    # 让 _maybe_prune 内部抛异常，断言 record_bar 不传播、采集继续
    def boom():
        raise RuntimeError("simulated prune failure")
    monkeypatch.setattr(store, "_maybe_prune", boom)
    before = store.drop_count
    # record_bar 不得抛出
    store.record_bar("BTC-USDT", now_ms, 1, 1, 1, 1)
    # 写入本身成功（drop 计数因 prune 失败 +1，但 bar 已落盘）
    assert _count(db) == 1
    assert store.drop_count == before + 1


def test_prune_disabled_store_noop(tmp_path):
    db = str(tmp_path / "klines_1s.db")
    store = OneSecBarStore(db, enabled=False, retention_days=1, prune_every=1)
    store.record_bar("BTC-USDT", 1, 1, 1, 1, 1)  # disabled → 不写不 prune，不抛
