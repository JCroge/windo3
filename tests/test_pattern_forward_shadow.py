"""pattern_forward_shadow 记录器/结算器单测:命中/上下文/幂等/防前视/结算回写。
合成日线,不依赖网络/真实 klines.db。"""
import json
import pattern_forward_shadow as pfs


def _downtrend_with_bearish_engulfing(base_day=20000):
    """60 根跌势日线,末两根构成低位看跌吞没(context 应为 low|down)。"""
    bars = []
    for k in range(60):
        c = 100.0 - 0.7 * k
        o = c + 0.3
        bars.append({"open_time": (base_day + k) * 86400000,
                     "open": o, "high": max(o, c) + 0.2, "low": min(o, c) - 0.2, "close": c})
    # bar[-2] 小阳
    t2 = bars[-2]["open_time"]
    bars[-2] = {"open_time": t2, "open": 57.0, "high": 57.8, "low": 56.7, "close": 57.6}
    # bar[-1] 看跌吞没(开>昨收、收<昨开、实体更大)
    t1 = bars[-1]["open_time"]
    bars[-1] = {"open_time": t1, "open": 57.9, "high": 58.0, "low": 54.3, "close": 54.5}
    return bars


def _uptrend_with_bearish_engulfing(base_day=20000):
    """涨势版:同样末根看跌吞没,但 context 非 low|down(应不记录)。"""
    bars = []
    for k in range(60):
        c = 50.0 + 0.7 * k
        o = c - 0.3
        bars.append({"open_time": (base_day + k) * 86400000,
                     "open": o, "high": max(o, c) + 0.2, "low": min(o, c) - 0.2, "close": c})
    t2 = bars[-2]["open_time"]
    bars[-2] = {"open_time": t2, "open": 91.0, "high": 91.8, "low": 90.7, "close": 91.6}
    t1 = bars[-1]["open_time"]
    bars[-1] = {"open_time": t1, "open": 91.9, "high": 92.0, "low": 88.3, "close": 88.5}
    return bars


def _setup(monkeypatch, tmp_path, bars_map, interval="1d"):
    log = str(tmp_path / "fwd.jsonl")
    monkeypatch.setattr(pfs, "_log_path", lambda iv: log)
    monkeypatch.setattr(pfs, "_symbols", lambda iv: list(bars_map.keys()))
    monkeypatch.setattr(pfs, "_load_bars", lambda sym, iv: bars_map.get(sym, []))


def _read(path):
    try:
        return [json.loads(l) for l in open(path) if l.strip()]
    except FileNotFoundError:
        return []


def test_record_hits_confirmed_signal(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, {"TEST": _downtrend_with_bearish_engulfing()})
    pfs.record()
    recs = _read(pfs._log_path("1d"))
    assert len(recs) == 1
    r = recs[0]
    assert r["symbol"] == "TEST" and r["pattern"] == "Bearish Engulfing" and r["context"] == "low|down"
    assert r["direction"] == -1 and r["stop_loss"] > r["entry"] > r["take_profit"]  # 空单 SL 上 TP 下
    # 新字段验证
    assert "detect_bar_open_time" in r and "interval" in r
    assert r["interval"] == "1d"


def test_record_skips_non_low_down_context(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, {"TEST": _uptrend_with_bearish_engulfing()})
    pfs.record()
    assert _read(pfs._log_path("1d")) == []  # 形态命中但 context 非 low|down → 不记


def test_record_idempotent(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, {"TEST": _downtrend_with_bearish_engulfing()})
    pfs.record()
    pfs.record()
    assert len(_read(pfs._log_path("1d"))) == 1  # 同 symbol+bar_open_time+interval 不重复


def test_record_no_lookahead_uses_closed_bar(monkeypatch, tmp_path):
    """entry 必须等于最新已闭合 bar(bars[-1])的收盘,不取更早或虚构。"""
    bars = _downtrend_with_bearish_engulfing()
    _setup(monkeypatch, tmp_path, {"TEST": bars})
    pfs.record()
    assert _read(pfs._log_path("1d"))[0]["entry"] == bars[-1]["close"]


def test_settle_writes_back_matured(monkeypatch, tmp_path):
    import datetime as dt
    log = str(tmp_path / "fwd.jsonl")
    monkeypatch.setattr(pfs, "_log_path", lambda iv: log)
    old = (dt.datetime.utcnow() - dt.timedelta(days=30)).strftime("%Y-%m-%d")
    start_ms = int(dt.datetime.strptime(old, "%Y-%m-%d").timestamp() * 1000)
    rec = {"detect_date_utc": old, "symbol": "TEST", "pattern": "Bearish Engulfing", "direction": -1,
           "context": "low|down", "entry": 100.0, "atr": 2.0, "stop_loss": 103.0, "take_profit": 94.0,
           "max_hold_days": 10, "settled": False}
    open(log, "w").write(json.dumps(rec) + "\n")
    # 未来日线:价格下跌触 TP(空单盈利)
    fut = [{"open_time": start_ms + (k + 1) * 86400000, "open": 99.0 - k, "high": 99.5 - k,
            "low": 93.0 - k, "close": 95.0 - k} for k in range(10)]
    monkeypatch.setattr(pfs, "_load_bars", lambda sym, iv: fut)
    pfs.settle()
    out = _read(log)[0]
    assert out["settled"] is True and "net_r" in out and "outcome" in out
