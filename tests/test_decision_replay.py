import asyncio

import pytest

from utils.decision_replay import restore_state, replay_decision


@pytest.fixture(autouse=True)
def _restore_event_loop():
    """`asyncio.run()` 关闭并清空当前事件循环，污染后续依赖
    `asyncio.get_event_loop()` 的测试（如 test_paper_limit_fill.py 的 Py3.9
    fixture）。回放测试结束后补一个新的当前事件循环，避免跨文件污染。"""
    yield
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _fixture_record():
    snap = {
        "_open_positions": [], "_pending_open_symbols": [],
        "_position_slots": {}, "_pending_open_slots": {},
        "_archetype_cooldown": {"_history": {}, "_cooldown_until": {}},
        "_recent_wins": 0, "_total_completed_trades": 0, "_recent_win_rate": None,
        "_probe_short_active": None, "_probe_short_sl_count": 0,
        "_probe_short_cooldown_until": 0.0,
        "_symbol_state": {}, "_available_balance": 1000.0,
        "_regime_manager": {"effective_regime": "bullish", "confidence": 70, "basis": {}},
    }
    return {
        "schema_version": "decision_replay_record.v1",
        "request_id": "rep-1", "timestamp": 1700000000.0, "symbol": "BTC-USDT",
        "decision": "reject", "tech_analysis": {"indicators": {"price": 50000.0}},
        "price_at_decision": 50000.0, "regime_state": "bullish",
        "llm_output_inline": {"action": "hold", "confidence": 0, "reasoning": "x",
                              "key_factors": [], "risk_warnings": []},
        "llm_audit_ref": None,
        "trade_decision_output": {"reject_reason": "synthetic", "attribution": {}},
        "state_snapshot_before_decision": snap, "replayable": True,
    }


def test_restore_state_sets_fields():
    from agents.trading.judge import MultiJudge
    j = MultiJudge.__new__(MultiJudge)
    restore_state(j, _fixture_record()["state_snapshot_before_decision"])
    assert j._open_positions == set()
    assert j._available_balance == 1000.0
    assert j._archetype_cooldown._cooldown_until == {}
    assert j._regime_manager.snapshot()["effective_regime"] == "bullish"


def test_replay_captures_published_decision():
    rec = _fixture_record()
    captured = asyncio.run(replay_decision(rec, config={}))
    assert captured is not None
    assert captured["symbol"] == "BTC-USDT"
    assert captured["action"] in ("open_long", "open_short", "hold", "close")
