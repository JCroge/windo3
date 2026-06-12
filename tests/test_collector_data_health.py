import time
import pytest
from agents.message_bus import MessageBus
from agents.trading.multi_data_collector import MultiDataCollector


@pytest.fixture(autouse=True)
def _reset_bus():
    MessageBus._instance = None
    yield
    MessageBus._instance = None


def test_init_data_health_default():
    c = MultiDataCollector({})
    h = c._latest_data_health
    assert h["any_degraded"] is False
    assert h["degraded_symbols"] == []
    assert h["last_collect_ts"] is None


def test_update_data_health_marks_degraded_symbol():
    c = MultiDataCollector({})
    c._update_data_health("BTC-USDT", degraded=False)
    c._update_data_health("ETH-USDT", degraded=True)
    h = c._latest_data_health
    assert h["any_degraded"] is True
    assert "ETH-USDT" in h["degraded_symbols"]
    assert "BTC-USDT" not in h["degraded_symbols"]
    assert h["last_collect_ts"] is not None


def test_update_data_health_clears_recovered_symbol():
    c = MultiDataCollector({})
    c._update_data_health("ETH-USDT", degraded=True)
    assert c._latest_data_health["any_degraded"] is True
    c._update_data_health("ETH-USDT", degraded=False)
    h = c._latest_data_health
    assert h["any_degraded"] is False
    assert h["degraded_symbols"] == []
