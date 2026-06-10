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
