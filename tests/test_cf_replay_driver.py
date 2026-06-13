import json, sqlite3
from cf_replay_driver import load_klines_window, build_report_from_rejected


def _mk_klines_db(path, symbol, bars):
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE klines (symbol TEXT, interval TEXT, open_time INTEGER,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        UNIQUE(symbol, interval, open_time))''')
    for t, hi, lo in bars:
        conn.execute("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?)",
                     (symbol, "1m", t, (hi+lo)/2, hi, lo, (hi+lo)/2, 0))
    conn.commit(); conn.close()


def test_load_klines_window_filters_24h(tmp_path):
    db = str(tmp_path / "k.db")
    base = 1_700_000_000_000  # ms
    _mk_klines_db(db, "BTC-USDT", [(base, 101, 99), (base + 25*3600*1000, 200, 1)])
    bars = load_klines_window(db, "BTC-USDT", created_at=base/1000, window_sec=86400)
    assert len(bars) == 1
    assert bars[0]["high"] == 101


def test_build_report_from_rejected_end_to_end(tmp_path):
    db = str(tmp_path / "k.db")
    base = 1_700_000_000_000
    _mk_klines_db(db, "BTC-USDT", [(base + 60_000, 111, 109)])  # high 触 TP 110
    events = str(tmp_path / "rejected.jsonl")
    rec = {"event_type": "rejected_plan_created", "record": {
        "symbol": "BTC-USDT", "side": "long", "entry_price": 100.0, "stop_loss": 95.0,
        "take_profit": [110.0], "leverage": 5, "size_usdt": 30.0,
        "created_at": base/1000, "funding_rate": 0.0,
        "reject_reason": "rr_below_floor", "effective_regime": "choppy"}}
    with open(events, "w") as f:
        f.write(json.dumps(rec) + "\n")
    rep = build_report_from_rejected(events, klines_1s_db="/nonexistent", klines_db=db,
                                     min_sample=1, lowconf_sample=2)
    bucket = rep["buckets"]["rr_below_floor|choppy|long"]
    assert bucket["n"] >= 1
    assert rep["skipped_no_data"] == 0
