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


import json as _json_t


def test_legacy_flat_positions_loads_as_realistic(tmp_path, monkeypatch):
    from agents.trading import paper_executor as pe_mod
    legacy = {"BTC-USDT": {"side": "long", "margin": 10, "entry_price": 100}}
    pos_file = tmp_path / "paper_positions.json"
    pos_file.write_text(_json_t.dumps(legacy))
    monkeypatch.setattr(pe_mod, "PAPER_POSITIONS_FILE", str(pos_file))
    monkeypatch.setattr(pe_mod, "PAPER_EQUITY_FILE", str(tmp_path / "paper_equity.json"))
    monkeypatch.setattr(pe_mod, "PAPER_POSITIONS_IDEAL_FILE", str(tmp_path / "paper_positions_idealized.json"))
    monkeypatch.setattr(pe_mod, "PAPER_EQUITY_IDEAL_FILE", str(tmp_path / "paper_equity_idealized.json"))
    pe = pe_mod.PaperExecutor({})
    pe._load_state()
    assert pe._books["realistic"]["positions"]["BTC-USDT"]["side"] == "long"
    assert pe._books["idealized"]["positions"] == {}


def test_round_trip_preserves_book_separation(tmp_path, monkeypatch):
    from agents.trading import paper_executor as pe_mod
    monkeypatch.setattr(pe_mod, "PAPER_POSITIONS_FILE", str(tmp_path / "paper_positions.json"))
    monkeypatch.setattr(pe_mod, "PAPER_EQUITY_FILE", str(tmp_path / "paper_equity.json"))
    monkeypatch.setattr(pe_mod, "PAPER_POSITIONS_IDEAL_FILE", str(tmp_path / "paper_positions_idealized.json"))
    monkeypatch.setattr(pe_mod, "PAPER_EQUITY_IDEAL_FILE", str(tmp_path / "paper_equity_idealized.json"))
    pe = pe_mod.PaperExecutor({})
    pe._books["realistic"]["positions"]["BTC-USDT"] = {"side": "long", "margin": 5}
    pe._books["idealized"]["positions"]["BTC-USDT"] = {"side": "long", "margin": 5, "book": "idealized"}
    pe._persist_state()
    pe2 = pe_mod.PaperExecutor({})
    pe2._load_state()
    assert "BTC-USDT" in pe2._books["realistic"]["positions"]
    assert "BTC-USDT" in pe2._books["idealized"]["positions"]
    flat = _json_t.loads((tmp_path / "paper_positions.json").read_text())
    assert "BTC-USDT" in flat and "positions" not in flat


def test_dual_track_flag_defaults_and_override():
    assert _mk({}).dual_track_enabled is True  # paper default on
    assert _mk({"paper_dual_track_enabled": False}).dual_track_enabled is False


import time


@pytest.mark.asyncio
async def test_limit_decision_still_fills_idealized_at_market():
    pe = _mk({})
    pe._latest_price["BTC-USDT"] = 102.0
    pe._latest_tick_ts["BTC-USDT"] = time.time()
    plan = {"order_type": "limit", "entry_zone": [100, 101],
            "size_usdt": 30, "leverage": 5, "stop_loss": 95, "tp_levels": [120]}
    await pe._execute_decision({"action": "open_long", "symbol": "BTC-USDT",
                                "confidence": 99, "plan": plan, "request_id": "r1"})
    assert "BTC-USDT" in pe._pending_limits
    assert "BTC-USDT" not in pe._books["realistic"]["positions"]
    ideal = pe._books["idealized"]["positions"]["BTC-USDT"]
    assert ideal["entry_price"] == pytest.approx(102.0)
    assert ideal["entry_method"] == "market" and ideal["book"] == "idealized"


@pytest.mark.asyncio
async def test_idealized_skipped_when_tick_missing():
    pe = _mk({})
    plan = {"order_type": "limit", "entry_zone": [100, 101],
            "size_usdt": 30, "leverage": 5, "stop_loss": 95, "tp_levels": [120]}
    await pe._execute_decision({"action": "open_long", "symbol": "X-USDT",
                                "confidence": 99, "plan": plan, "request_id": "r2"})
    assert "X-USDT" not in pe._books["idealized"]["positions"]


@pytest.mark.asyncio
async def test_idealized_not_opened_when_disabled():
    pe = _mk({"paper_dual_track_enabled": False})
    pe._latest_price["BTC-USDT"] = 102.0
    pe._latest_tick_ts["BTC-USDT"] = time.time()
    plan = {"size_usdt": 30, "leverage": 5, "stop_loss": 95, "tp_levels": [120]}
    await pe._execute_decision({"action": "open_long", "symbol": "BTC-USDT",
                                "confidence": 99, "plan": plan, "request_id": "r3"})
    assert pe._books["idealized"]["positions"] == {}


@pytest.mark.asyncio
async def test_idealized_skipped_when_tick_stale():
    pe = _mk({})
    pe._latest_price["BTC-USDT"] = 102.0
    pe._latest_tick_ts["BTC-USDT"] = time.time() - 9999  # stale beyond staleness window
    plan = {"order_type": "limit", "entry_zone": [100, 101],
            "size_usdt": 30, "leverage": 5, "stop_loss": 95, "tp_levels": [120]}
    await pe._execute_decision({"action": "open_long", "symbol": "BTC-USDT",
                                "confidence": 99, "plan": plan, "request_id": "rs"})
    assert "BTC-USDT" not in pe._books["idealized"]["positions"]


@pytest.mark.asyncio
async def test_price_tick_checks_both_books_independently():
    pe = _mk({})
    # realistic SL 95 (will hit at 94), idealized SL 90 (will NOT hit at 94)
    for book, entry, sl in (("realistic", 100.0, 95.0), ("idealized", 102.0, 90.0)):
        pe._books[book]["positions"]["BTC-USDT"] = {
            "symbol": "BTC-USDT", "side": "long", "entry_price": entry,
            "sl": sl, "tp": 130.0, "margin": 10, "leverage": 5,
            "notional": 50, "opened_at": time.time(), "entry_fee": 0.05,
            "book": book,
        }
    await pe.on_message({"type": "price_tick", "symbol": "BTC-USDT",
                         "payload": {"symbol": "BTC-USDT", "price": 94.0}})
    # realistic SL hit and closed; idealized SL not hit, stays open
    assert "BTC-USDT" not in pe._books["realistic"]["positions"]
    assert "BTC-USDT" in pe._books["idealized"]["positions"]


@pytest.mark.asyncio
async def test_strategy_close_applies_to_both_books():
    pe = _mk({})
    for book in ("realistic", "idealized"):
        pe._books[book]["positions"]["ETH-USDT"] = {
            "symbol": "ETH-USDT", "side": "long", "entry_price": 50.0,
            "sl": 45.0, "tp": 60.0, "margin": 10, "leverage": 3,
            "notional": 30, "opened_at": time.time(), "entry_fee": 0.03,
            "book": book,
        }
    pe._latest_price["ETH-USDT"] = 55.0
    await pe._execute_decision({"action": "close", "symbol": "ETH-USDT"})
    assert "ETH-USDT" not in pe._books["realistic"]["positions"]
    assert "ETH-USDT" not in pe._books["idealized"]["positions"]


@pytest.mark.asyncio
async def test_close_noop_for_idealized_when_not_held():
    pe = _mk({})
    pe._books["realistic"]["positions"]["ETH-USDT"] = {
        "symbol": "ETH-USDT", "side": "long", "entry_price": 50.0,
        "sl": 45.0, "tp": 60.0, "margin": 10, "leverage": 3,
        "notional": 30, "opened_at": time.time(), "entry_fee": 0.03,
        "book": "realistic",
    }
    pe._latest_price["ETH-USDT"] = 55.0
    await pe._execute_decision({"action": "close", "symbol": "ETH-USDT"})
    assert "ETH-USDT" not in pe._books["realistic"]["positions"]
    assert "ETH-USDT" not in pe._books["idealized"]["positions"]


@pytest.mark.asyncio
async def test_unfilled_realistic_leaves_idealized_to_self_sl():
    pe = _mk({})
    pe._latest_price["NEAR-USDT"] = 2.40
    pe._latest_tick_ts["NEAR-USDT"] = time.time()
    plan = {"order_type": "limit", "entry_zone": [2.35, 2.36], "limit_no_fallback": True,
            "limit_timeout_sec": 1800, "size_usdt": 30, "leverage": 5,
            "stop_loss": 2.52, "tp_levels": [2.10]}
    await pe._execute_decision({"action": "open_short", "symbol": "NEAR-USDT",
                                "confidence": 99, "plan": plan, "request_id": "r9"})
    assert pe._books["idealized"]["positions"]["NEAR-USDT"]["side"] == "short"
    await pe.on_message({"type": "price_tick", "symbol": "NEAR-USDT",
                         "payload": {"symbol": "NEAR-USDT", "price": 2.55}})
    assert "NEAR-USDT" not in pe._books["idealized"]["positions"]


def test_reviewer_does_not_consume_idealized_or_paper():
    # Reviewer must not subscribe to paper streams nor read idealized files.
    import inspect
    from agents.trading import reviewer as rv
    src = inspect.getsource(rv)
    assert "paper_execution_result" not in src
    assert "paper_positions_idealized" not in src
    assert "book='idealized'" not in src and 'book="idealized"' not in src
