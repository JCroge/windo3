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


@pytest.mark.asyncio
async def test_open_on_idealized_book_isolated_from_realistic():
    pe = _mk()
    pe._latest_price["BTC-USDT"] = 100.0
    plan = {"size_usdt": 30, "leverage": 5, "stop_loss": 90, "tp_levels": [120]}
    await pe._open_paper_at_price(
        symbol="BTC-USDT", side="long", action="open_long",
        plan=plan, decision={"request_id": "r1"},
        fill_price=100.0, entry_method="market", book="idealized",
    )
    assert "BTC-USDT" in pe._books["idealized"]["positions"]
    assert pe._books["idealized"]["positions"]["BTC-USDT"]["book"] == "idealized"
    assert "BTC-USDT" not in pe._books["realistic"]["positions"]
    assert pe._books["idealized"]["equity"] < pe._initial_equity   # paid entry fee
    assert pe._books["realistic"]["equity"] == pytest.approx(pe._initial_equity)  # untouched


@pytest.mark.asyncio
async def test_realistic_record_tagged_realistic_by_default():
    pe = _mk()
    pe._latest_price["ETH-USDT"] = 50.0
    plan = {"size_usdt": 20, "leverage": 3, "stop_loss": 45, "tp_levels": [60]}
    await pe._open_paper_at_price(
        symbol="ETH-USDT", side="long", action="open_long",
        plan=plan, decision={"request_id": "r2"},
        fill_price=50.0, entry_method="market",
    )
    assert pe._books["realistic"]["positions"]["ETH-USDT"]["book"] == "realistic"
    assert pe._books["idealized"]["equity"] == pytest.approx(pe._initial_equity)  # untouched by realistic open
