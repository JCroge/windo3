import pytest
from agents.trading.multi_data_collector import MultiDataCollector


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._p


class _FakeSession:
    def __init__(self, payload):
        self._p = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, *a, **k):
        return _FakeResp(self._p)


def _mk():
    return MultiDataCollector({})


# ---------------------------------------------------------------------------
# _fetch_taker_ratio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_taker_ratio_returns_value_and_meta(monkeypatch):
    import agents.trading.multi_data_collector as m
    payload = [{"buySellRatio": "1.2", "buyVol": "100", "sellVol": "80", "timestamp": 1700000000000}]
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(payload))
    value, meta = await _mk()._fetch_taker_ratio("BTC-USDT")
    assert value["buy_sell_ratio"] == pytest.approx(1.2, abs=1e-4)
    assert meta["source"] == "binance_fapi"
    assert meta["item_ts"] == 1700000000000


@pytest.mark.asyncio
async def test_taker_ratio_value_fields(monkeypatch):
    import agents.trading.multi_data_collector as m
    payload = [{"buySellRatio": "0.8", "buyVol": "50", "sellVol": "62.5", "timestamp": 1700000001000}]
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(payload))
    value, meta = await _mk()._fetch_taker_ratio("ETH-USDT")
    assert "buy_sell_ratio" in value
    assert "buy_vol" in value
    assert "sell_vol" in value
    assert meta["item_ts"] == 1700000001000


@pytest.mark.asyncio
async def test_taker_ratio_empty_response(monkeypatch):
    import agents.trading.multi_data_collector as m
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession([]))
    value, meta = await _mk()._fetch_taker_ratio("BTC-USDT")
    assert value == {}
    assert meta["source"] == "binance_fapi"
    assert meta["item_ts"] is None


@pytest.mark.asyncio
async def test_taker_ratio_exception_returns_empty(monkeypatch):
    import agents.trading.multi_data_collector as m

    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            raise RuntimeError("network error")

    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _BrokenSession())
    value, meta = await _mk()._fetch_taker_ratio("BTC-USDT")
    assert value == {}
    assert meta["source"] == "binance_fapi"
    assert meta["item_ts"] is None


# ---------------------------------------------------------------------------
# _fetch_oi_delta
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oi_delta_returns_value_and_meta(monkeypatch):
    import agents.trading.multi_data_collector as m
    # 48 items; item[-1] has timestamp
    base_ts = 1700000000000
    payload = [
        {"sumOpenInterestValue": str(1000 + i * 10), "timestamp": base_ts + i * 300_000}
        for i in range(48)
    ]
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(payload))
    value, meta = await _mk()._fetch_oi_delta("BTC-USDT")
    assert "current_usd" in value
    assert "delta_1h_pct" in value
    assert "delta_4h_pct" in value
    assert meta["source"] == "binance_fapi"
    # item_ts should be from data[-1]
    assert meta["item_ts"] == base_ts + 47 * 300_000


@pytest.mark.asyncio
async def test_oi_delta_empty_response(monkeypatch):
    import agents.trading.multi_data_collector as m
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession([]))
    value, meta = await _mk()._fetch_oi_delta("BTC-USDT")
    assert value == {}
    assert meta["source"] == "binance_fapi"
    assert meta["item_ts"] is None


@pytest.mark.asyncio
async def test_oi_delta_missing_timestamp_gives_none(monkeypatch):
    import agents.trading.multi_data_collector as m
    payload = [
        {"sumOpenInterestValue": str(1000 + i * 10)}  # no timestamp field
        for i in range(48)
    ]
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(payload))
    value, meta = await _mk()._fetch_oi_delta("BTC-USDT")
    assert "current_usd" in value
    assert meta["source"] == "binance_fapi"
    assert meta["item_ts"] is None


@pytest.mark.asyncio
async def test_oi_delta_exception_returns_empty(monkeypatch):
    import agents.trading.multi_data_collector as m

    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            raise RuntimeError("network error")

    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _BrokenSession())
    value, meta = await _mk()._fetch_oi_delta("BTC-USDT")
    assert value == {}
    assert meta["source"] == "binance_fapi"
    assert meta["item_ts"] is None


# ---------------------------------------------------------------------------
# _fetch_big_trades
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_big_trades_returns_value_and_meta(monkeypatch):
    import agents.trading.multi_data_collector as m
    # OKX response shape; ts is ms string; trades[0] is the "newest"
    trades = [
        {"sz": "5.0", "px": "30000", "side": "buy", "ts": "1700000009000"},
        {"sz": "3.0", "px": "29990", "side": "sell", "ts": "1700000008000"},
        {"sz": "1.0", "px": "29980", "side": "buy", "ts": "1700000007000"},
    ]
    payload = {"code": "0", "data": trades}
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(payload))
    value, meta = await _mk()._fetch_big_trades("BTC-USDT")
    assert "big_buy_vol" in value
    assert "big_sell_vol" in value
    assert "big_ratio" in value
    assert "whale_direction" in value
    assert meta["source"] == "okx"
    # item_ts = newest trade ts = trades[0]["ts"]
    assert meta["item_ts"] == 1700000009000


@pytest.mark.asyncio
async def test_big_trades_non_zero_response(monkeypatch):
    import agents.trading.multi_data_collector as m
    payload = {"code": "0", "data": []}
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(payload))
    value, meta = await _mk()._fetch_big_trades("BTC-USDT")
    assert value == {}
    assert meta["source"] == "okx"
    assert meta["item_ts"] is None


@pytest.mark.asyncio
async def test_big_trades_error_code(monkeypatch):
    import agents.trading.multi_data_collector as m
    payload = {"code": "1", "data": [{"sz": "1.0", "px": "100", "side": "buy", "ts": "1234"}]}
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(payload))
    value, meta = await _mk()._fetch_big_trades("BTC-USDT")
    assert value == {}
    assert meta["source"] == "okx"
    assert meta["item_ts"] is None


@pytest.mark.asyncio
async def test_big_trades_exception_returns_empty(monkeypatch):
    import agents.trading.multi_data_collector as m

    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            raise RuntimeError("network error")

    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _BrokenSession())
    value, meta = await _mk()._fetch_big_trades("BTC-USDT")
    assert value == {}
    assert meta["source"] == "okx"
    assert meta["item_ts"] is None


# ---------------------------------------------------------------------------
# _fetch_long_short_ratio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_long_short_ratio_returns_value_and_meta(monkeypatch):
    import agents.trading.multi_data_collector as m
    payload = [{"longAccount": "0.55", "shortAccount": "0.45", "timestamp": 1700000005000}]
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(payload))
    value, meta = await _mk()._fetch_long_short_ratio("BTC-USDT")
    assert "long_ratio" in value
    assert "short_ratio" in value
    assert "crowd_sentiment" in value
    assert meta["source"] == "binance_fapi"
    assert meta["item_ts"] == 1700000005000


@pytest.mark.asyncio
async def test_long_short_ratio_empty_response(monkeypatch):
    import agents.trading.multi_data_collector as m
    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _FakeSession([]))
    value, meta = await _mk()._fetch_long_short_ratio("BTC-USDT")
    assert value == {}
    assert meta["source"] == "binance_fapi"
    assert meta["item_ts"] is None


@pytest.mark.asyncio
async def test_long_short_ratio_exception_returns_empty(monkeypatch):
    import agents.trading.multi_data_collector as m

    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            raise RuntimeError("network error")

    monkeypatch.setattr(m.aiohttp, "ClientSession", lambda *a, **k: _BrokenSession())
    value, meta = await _mk()._fetch_long_short_ratio("BTC-USDT")
    assert value == {}
    assert meta["source"] == "binance_fapi"
    assert meta["item_ts"] is None


# ---------------------------------------------------------------------------
# _full_collect emits provenance block
# ---------------------------------------------------------------------------

import asyncio as _asyncio


def _aw(val):
    """Return a coroutine that resolves to val."""
    async def _coro(*_a, **_k):
        return val
    return _coro()


@pytest.mark.asyncio
async def test_full_collect_emits_provenance_block(monkeypatch):
    import time as _t
    c = _mk()

    now = _t.time()
    fresh_ts = int((now - 3000) * 1000)  # 50 min old — matches hourly feed

    # --- stub the four _fetch_* helpers ---
    monkeypatch.setattr(c, "_fetch_taker_ratio",
        lambda s: _aw(({"buy_sell_ratio": 1.2}, {"source": "binance_fapi", "item_ts": fresh_ts})))
    monkeypatch.setattr(c, "_fetch_oi_delta",
        lambda s: _aw(({"current_usd": 1e9, "delta_1h_pct": 0.5, "delta_4h_pct": 1.0},
                       {"source": "binance_fapi", "item_ts": fresh_ts})))
    monkeypatch.setattr(c, "_fetch_big_trades",
        lambda s: _aw(({}, {"source": "okx", "item_ts": None})))  # FAILED dim → confidence 0
    monkeypatch.setattr(c, "_fetch_long_short_ratio",
        lambda s: _aw(({"long_ratio": 0.55, "short_ratio": 0.45, "crowd_sentiment": "long"},
                       {"source": "binance_fapi", "item_ts": fresh_ts})))

    # --- stub _fetch_funding_history (returns plain list) ---
    async def _fake_funding_history(s):
        return [{"rate": 0.0001, "time": int(now * 1000)}]
    monkeypatch.setattr(c, "_fetch_funding_history", _fake_funding_history)

    # --- stub _fetch_orderbook / _fetch_liquidations (populate caches) ---
    async def _fake_orderbook(s):
        c._orderbook_cache[s] = {"asks": [[50000, 1]], "bids": [[49999, 1]],
                                  "spread_pct": 0.002, "bid_depth_usd": 50000,
                                  "ask_depth_usd": 50000}
    async def _fake_liquidations(s):
        c._liquidation_cache[s] = {"long_vol_usd": 0, "short_vol_usd": 0,
                                    "net_direction": "short_squeezed", "recent_big": []}
    monkeypatch.setattr(c, "_fetch_orderbook", _fake_orderbook)
    monkeypatch.setattr(c, "_fetch_liquidations", _fake_liquidations)

    # --- stub exchange (fetch_ohlcv, market, fetch_funding_rate) ---
    now_ms = int(now * 1000)
    fake_klines = [[now_ms - 3600000 * i, 49000, 51000, 48000, 50000 + i, 100]
                   for i in range(5, -1, -1)]  # 6 rows newest-last

    class _FakeExchange:
        def fetch_ohlcv(self, *a, **k):
            return fake_klines
        def market(self, sym):
            return {"swap": True}
        def fetch_funding_rate(self, sym):
            return {"fundingRate": 0.0001, "timestamp": now_ms}
        def load_markets(self):
            pass

    c.exchange = _FakeExchange()

    # --- stub _check_gaps / _fill_gaps to avoid side-effects ---
    monkeypatch.setattr(c, "_check_gaps", lambda s, k: 0)

    # --- capture publish ---
    captured = {}
    async def _cap(topic, payload, **kwargs):
        captured[topic] = payload
    monkeypatch.setattr(c, "publish", _cap)

    await c._full_collect("BTC-USDT")

    assert "market_data" in captured, "publish was not called with market_data"
    md = captured["market_data"]

    assert "provenance" in md, "provenance key missing from market_data payload"
    prov = md["provenance"]

    # taker_ratio: 50-min-old hourly feed → freshness ≈ 3000s
    assert prov["taker_ratio"]["source"] == "binance_fapi"
    assert prov["taker_ratio"]["freshness_sec"] == pytest.approx(3000, abs=10)
    assert 0.0 <= prov["taker_ratio"]["confidence"] <= 1.0

    # big_trades: failed dim (empty value) → confidence must be 0.0
    assert prov["big_trades"]["confidence"] == 0.0

    # oi_data and long_short_account populated → confidence > 0
    assert prov["oi_data"]["source"] == "binance_fapi"
    assert prov["long_short_account"]["confidence"] > 0.0

    # funding_rate provenance present
    assert "funding_rate" in prov
    assert 0.0 <= prov["funding_rate"]["confidence"] <= 1.0

    # flat values untouched
    assert md["taker_ratio"] == {"buy_sell_ratio": 1.2}
    assert md["big_trades"] == {}
