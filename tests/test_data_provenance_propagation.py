"""Test: provenance block is forwarded from market_data payload through tech_analysis.

TDD implementation for Task 4 (data-source-provenance branch).
"""

import time
import pytest


def _make_klines(n=35):
    """Return n minimal kline rows [open_time, open, high, low, close, volume]."""
    base_ts = int(time.time() * 1000) - n * 3600_000
    rows = []
    price = 50_000.0
    for i in range(n):
        rows.append([base_ts + i * 3600_000, price, price + 200, price - 200, price + 50, 100.0])
        price += 10.0
    return rows


def _make_msg(symbol="BTC-USDT", provenance=None, include_provenance=True):
    """Build a minimal market_data message that satisfies on_message."""
    payload = {
        "symbol": symbol,
        "klines": _make_klines(35),
        "klines_4h": [],
        "klines_1d": [],
        "klines_15m": [],
        "orderbook": {},
        "long_short_account": {},
        "data_quality": {"tier": "ok"},
    }
    if include_provenance:
        payload["provenance"] = provenance or {}
    return {"type": "market_data", "symbol": symbol, "payload": payload}


@pytest.mark.asyncio
async def test_tech_analysis_forwards_provenance(monkeypatch):
    """Published tech_analysis carries the provenance block from the input payload."""
    from agents.trading.tech_analyst import MultiTechAnalyst

    ta = MultiTechAnalyst({})

    captured = {}

    async def _cap(topic, payload, **k):
        captured[topic] = payload

    monkeypatch.setattr(ta, "publish", _cap)

    # Stub out LLM call to avoid network dependency.
    async def _stub_llm(symbol, trend, levels, momentum, money_flow, micro, crowd, risk, price):
        return {"direction": "neutral", "confidence": 50, "key_insight": "stub"}

    monkeypatch.setattr(ta, "_llm_synthesize", _stub_llm)

    prov = {
        "taker_ratio": {
            "source": "binance_fapi",
            "freshness_sec": 3000,
            "confidence": 0.35,
        }
    }

    msg = _make_msg(provenance=prov)
    await ta.on_message(msg)

    assert "tech_analysis" in captured, "on_message did not publish tech_analysis"
    assert "provenance" in captured["tech_analysis"], "provenance key missing from tech_analysis"
    assert captured["tech_analysis"]["provenance"] == prov


@pytest.mark.asyncio
async def test_legacy_market_data_without_provenance_yields_empty(monkeypatch):
    """A market_data payload without a 'provenance' key results in result['provenance'] == {}."""
    from agents.trading.tech_analyst import MultiTechAnalyst

    ta = MultiTechAnalyst({})

    captured = {}

    async def _cap(topic, payload, **k):
        captured[topic] = payload

    monkeypatch.setattr(ta, "publish", _cap)

    async def _stub_llm(symbol, trend, levels, momentum, money_flow, micro, crowd, risk, price):
        return {"direction": "neutral", "confidence": 50, "key_insight": "stub"}

    monkeypatch.setattr(ta, "_llm_synthesize", _stub_llm)

    # Build message WITHOUT provenance key at all.
    msg = _make_msg(include_provenance=False)
    await ta.on_message(msg)

    assert "tech_analysis" in captured, "on_message did not publish tech_analysis"
    assert captured["tech_analysis"].get("provenance") == {}, (
        "Expected empty dict for missing provenance, got: "
        f"{captured['tech_analysis'].get('provenance')!r}"
    )
