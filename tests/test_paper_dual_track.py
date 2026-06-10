import pytest
from agents.trading.paper_executor import PaperExecutor


def _mk(config=None):
    return PaperExecutor(config or {})


def test_books_exist_with_realistic_and_idealized():
    pe = _mk()
    assert set(pe._books.keys()) == {"realistic", "idealized"}
    assert pe._books["realistic"]["positions"] == {}
    assert pe._books["idealized"]["positions"] == {}


def test_positions_property_proxies_realistic_book():
    pe = _mk()
    pe._positions["BTC-USDT"] = {"side": "long"}
    assert pe._books["realistic"]["positions"]["BTC-USDT"] == {"side": "long"}


def test_equity_property_proxies_realistic_book():
    pe = _mk()
    start = pe._equity
    pe._equity -= 5.0
    assert pe._books["realistic"]["equity"] == pytest.approx(start - 5.0)


def test_idealized_book_starts_at_same_initial_equity():
    pe = _mk({"effective_balance_cap": 500})
    assert pe._books["idealized"]["equity"] == pytest.approx(500.0)
    assert pe._books["realistic"]["equity"] == pytest.approx(500.0)
