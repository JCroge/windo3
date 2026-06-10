import pytest
from utils.data_provenance import derive_confidence, provenance_entry


def test_fresh_native_scores_high():
    c = derive_confidence("okx", 10, native_venue="okx", period_sec=60)
    assert c > 0.9


def test_stale_beyond_two_periods_is_zero():
    c = derive_confidence("okx", 7201, native_venue="okx", period_sec=3600)
    assert c == 0.0


def test_cross_exchange_penalty():
    native = derive_confidence("okx", 0, native_venue="okx", period_sec=3600)
    cross = derive_confidence("binance_fapi", 0, native_venue="okx", period_sec=3600)
    assert cross == pytest.approx(native * 0.7)


def test_degraded_floors_to_zero():
    assert derive_confidence("okx", 0, native_venue="okx", period_sec=60, degraded=True) == 0.0


def test_monotonic_decay():
    a = derive_confidence("okx", 100, native_venue="okx", period_sec=3600)
    b = derive_confidence("okx", 2000, native_venue="okx", period_sec=3600)
    assert a > b


def test_provenance_entry_shape():
    e = provenance_entry("binance_fapi", item_ts_ms=1000_000, now=1100.0,
                         period_sec=3600, native_venue="okx")
    assert set(e.keys()) == {"source", "freshness_sec", "confidence"}
    assert e["source"] == "binance_fapi"
    assert e["freshness_sec"] == pytest.approx(100.0)


def test_provenance_entry_missing_ts_falls_back_to_fetch_age():
    e = provenance_entry("okx", item_ts_ms=None, now=1100.0, fetch_ts=1090.0,
                         period_sec=60, native_venue="okx")
    assert e["freshness_sec"] == pytest.approx(10.0)
