from unittest.mock import MagicMock
from agents.research.synthesizer import ResearchSynthesizer


def _synth():
    s = ResearchSynthesizer.__new__(ResearchSynthesizer)
    s._max_symbols = 12
    s.logger = MagicMock()
    return s


_CANDS = [
    {"symbol": "BTC-USDT", "volume_24h": 300_000_000, "volatility_pct": 8,
     "funding_rate": 0.002, "change_24h_pct": 5},
    {"symbol": "ETH-USDT", "volume_24h": 200_000_000, "volatility_pct": 6,
     "funding_rate": -0.002, "change_24h_pct": -4},
]


def test_uses_llm_when_nonempty():
    s = _synth()
    result = {"selected_symbols": [{"symbol": "SOL-USDT"}], "market_regime": "bullish"}
    selected, regime = s._select_preliminary(result, _CANDS)
    assert [x["symbol"] for x in selected] == ["SOL-USDT"]   # 用 LLM 结果
    assert regime == "bullish"


def test_empty_selected_falls_back_to_rules():
    s = _synth()
    result = {"selected_symbols": [], "market_regime": "x"}  # 截断/空
    selected, regime = s._select_preliminary(result, _CANDS)
    assert len(selected) >= 1                                # 非空
    assert {x["symbol"] for x in selected} <= {"BTC-USDT", "ETH-USDT"}  # 来自规则降级
    assert regime == "unknown"


def test_none_result_falls_back_to_rules():
    s = _synth()
    selected, regime = s._select_preliminary(None, _CANDS)  # LLM 异常 → result=None
    assert len(selected) >= 1
    assert regime == "unknown"
