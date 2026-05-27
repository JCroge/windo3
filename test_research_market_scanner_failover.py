"""Research-cycle failover tests for MarketScanner and Orchestrator."""

import asyncio

import pytest

from agents.message_bus import MessageBus
from agents.research.market_scanner import MarketScanner


class FailingExchange:
    def fetch_tickers(self):
        raise RuntimeError("okx tickers unavailable")


class SequencedExchange:
    def __init__(self):
        self.calls = 0

    def fetch_tickers(self):
        self.calls += 1
        if self.calls == 1:
            return {
                "BTC/USDT:USDT": {
                    "quoteVolume": 200_000_000,
                    "high": 105_000,
                    "low": 100_000,
                    "last": 103_000,
                    "percentage": 3.0,
                }
            }
        raise RuntimeError("okx tickers unavailable")

    def market(self, symbol):
        return {"swap": True}


@pytest.mark.asyncio
async def test_market_scanner_failure_publishes_degraded_empty_payload():
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register("catcher", ["research_market_data"])

    scanner = MarketScanner({"market_scan_retries": 1, "market_scan_retry_delay": 0})
    scanner.exchange = FailingExchange()
    scanner._current_cycle_id = "cycle_fail"

    await scanner._scan_market()

    msg = await bus.receive("catcher", timeout=0.2)
    assert msg is not None
    assert msg["type"] == "research_market_data"
    payload = msg["payload"]
    assert payload["cycle_id"] == "cycle_fail"
    assert payload["candidates"] == []
    assert payload["degraded"] is True
    assert payload["stale"] is False
    assert payload["fallback_source"] == "empty"
    assert "okx tickers unavailable" in payload["error"]


@pytest.mark.asyncio
async def test_market_scanner_failure_reuses_last_good_candidates(monkeypatch):
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register("catcher", ["research_market_data"])

    scanner = MarketScanner({"market_scan_retries": 1, "market_scan_retry_delay": 0})
    scanner.exchange = SequencedExchange()
    scanner._current_cycle_id = "cycle_ok"
    monkeypatch.setattr(scanner, "_fetch_monthly_kline_count", lambda inst_id: asyncio.sleep(0, result=12))
    monkeypatch.setattr(scanner, "_fetch_funding", lambda symbol: asyncio.sleep(0, result=0.0001))
    monkeypatch.setattr(scanner, "_fetch_long_short_ratio", lambda inst_id: asyncio.sleep(0, result=1.05))
    monkeypatch.setattr(scanner, "_fetch_open_interest", lambda inst_id: asyncio.sleep(0, result=1_000_000))
    monkeypatch.setattr(scanner, "_fetch_sl_structure", lambda inst_id, price: asyncio.sleep(0, result={"sl_viable": True}))

    await scanner._scan_market()
    first = await bus.receive("catcher", timeout=0.2)
    assert first is not None
    assert first["payload"]["candidates"][0]["symbol"] == "BTC-USDT"

    scanner._current_cycle_id = "cycle_fail"
    await scanner._scan_market()

    second = await bus.receive("catcher", timeout=0.2)
    payload = second["payload"]
    assert payload["cycle_id"] == "cycle_fail"
    assert payload["degraded"] is True
    assert payload["stale"] is True
    assert payload["fallback_source"] == "last_good"
    assert payload["candidates"][0]["symbol"] == "BTC-USDT"


@pytest.mark.asyncio
async def test_orchestrator_watchdog_retries_missing_preliminary_once():
    from agents.orchestrator import Orchestrator

    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register("trigger_catcher", ["research_trigger"])

    orch = Orchestrator({
        "research_watchdog_timeout": 0.01,
        "research_watchdog_max_retries": 1,
    })
    orch.bus = bus
    orch._shutdown_event = asyncio.Event()

    await orch._publish_research_trigger(reason="test")
    first = await bus.receive("trigger_catcher", timeout=0.2)
    retry = await bus.receive("trigger_catcher", timeout=0.2)
    third = await bus.receive("trigger_catcher", timeout=0.05)

    for task in orch._research_watchdogs.values():
        task.cancel()

    assert first is not None
    assert retry is not None
    assert retry["payload"].get("retry_of") == first["payload"]["cycle_id"]
    assert third is None


@pytest.mark.asyncio
async def test_orchestrator_watchdog_completion_cancels_retry():
    from agents.orchestrator import Orchestrator

    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register("trigger_catcher", ["research_trigger"])

    orch = Orchestrator({
        "research_watchdog_timeout": 0.02,
        "research_watchdog_max_retries": 1,
    })
    orch.bus = bus
    orch._shutdown_event = asyncio.Event()

    cycle_id = await orch._publish_research_trigger(reason="test")
    first = await bus.receive("trigger_catcher", timeout=0.2)
    orch._mark_research_cycle_completed(cycle_id, "research_preliminary")

    retry = await bus.receive("trigger_catcher", timeout=0.05)

    for task in orch._research_watchdogs.values():
        task.cancel()

    assert first is not None
    assert retry is None


@pytest.mark.asyncio
async def test_degraded_last_good_market_data_still_reaches_preliminary():
    from agents.research.synthesizer import ResearchSynthesizer

    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register("prelim_catcher", ["research_preliminary"])

    synth = ResearchSynthesizer({})

    async def llm_fail(*args, **kwargs):
        raise RuntimeError("LLM unavailable")

    synth.ask_claude_json = llm_fail
    cycle_id = "cycle_last_good"

    await synth.on_message({
        "type": "research_news_data",
        "payload": {"cycle_id": cycle_id, "headlines": [], "symbol_mentions": {}},
    })
    await synth.on_message({
        "type": "research_sentiment_data",
        "payload": {"cycle_id": cycle_id, "fear_greed": {"value": 50, "classification": "neutral"}},
    })
    await synth.on_message({
        "type": "research_market_data",
        "payload": {
            "cycle_id": cycle_id,
            "degraded": True,
            "stale": True,
            "fallback_source": "last_good",
            "candidates": [
                {
                    "symbol": "BTC-USDT",
                    "price": 100_000,
                    "volume_24h": 200_000_000,
                    "volatility_pct": 5,
                    "change_24h_pct": 4,
                    "funding_rate": 0.0001,
                }
            ],
        },
    })

    msg = await bus.receive("prelim_catcher", timeout=0.5)
    assert msg is not None
    assert msg["payload"]["cycle_id"] == cycle_id
    assert msg["payload"]["selected"]


@pytest.mark.asyncio
async def test_empty_degraded_market_data_does_not_leave_barrier_task():
    from agents.research.synthesizer import ResearchSynthesizer

    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register("prelim_catcher", ["research_preliminary"])

    synth = ResearchSynthesizer({})
    cycle_id = "cycle_empty"

    await synth.on_message({
        "type": "research_news_data",
        "payload": {"cycle_id": cycle_id, "headlines": [], "symbol_mentions": {}},
    })
    await synth.on_message({
        "type": "research_sentiment_data",
        "payload": {"cycle_id": cycle_id, "fear_greed": None},
    })
    await synth.on_message({
        "type": "research_market_data",
        "payload": {
            "cycle_id": cycle_id,
            "degraded": True,
            "stale": False,
            "fallback_source": "empty",
            "candidates": [],
        },
    })

    msg = await bus.receive("prelim_catcher", timeout=0.05)
    assert msg is None
    assert synth._barrier_event is None
    assert synth._barrier_task is None
