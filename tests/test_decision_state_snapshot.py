import json
from utils.decision_tape import build_bundle, _jsonable


def test_jsonable_set_to_sorted_list():
    assert _jsonable({"b", "a"}) == ["a", "b"]
    assert _jsonable({"x": {"c", "a"}}) == {"x": ["a", "c"]}
    assert _jsonable([1, {"a"}]) == [1, ["a"]]


def test_build_bundle_with_snapshot_marks_replayable():
    snap = {"_open_positions": ["BTC-USDT"], "_available_balance": 1000.0}
    # replayable 真实性守卫（schema v2）：需有快照【且】tech 非空，故此处给真实 tech。
    b = build_bundle(symbol="BTC-USDT", decision="accept", request_id="r1",
                     tech_analysis={"indicators": {"price": 1.0}}, price_at_decision=1.0,
                     regime_state="bullish",
                     llm_output=None, llm_audit_ref=None, trade_decision_output={},
                     state_snapshot=snap)
    assert b["state_snapshot_before_decision"] == snap
    assert b["replayable"] is True
    json.dumps(b)


def test_build_bundle_snapshot_but_empty_tech_not_replayable():
    # 新契约：有快照但 tech 空（如历史 v1 空记录）-> 不可回放。
    snap = {"_open_positions": ["BTC-USDT"], "_available_balance": 1000.0}
    b = build_bundle(symbol="BTC-USDT", decision="reject", request_id="r1b",
                     tech_analysis={}, price_at_decision=1.0, regime_state="bullish",
                     llm_output=None, llm_audit_ref=None, trade_decision_output={},
                     state_snapshot=snap)
    assert b["replayable"] is False


def test_build_bundle_without_snapshot_not_replayable():
    b = build_bundle(symbol="BTC-USDT", decision="reject", request_id="r2",
                     tech_analysis={}, price_at_decision=1.0, regime_state="x",
                     llm_output=None, llm_audit_ref=None, trade_decision_output={})
    assert b["state_snapshot_before_decision"] is None
    assert b["replayable"] is False


def test_capture_state_snapshot_whitelist():
    from agents.trading.judge import MultiJudge
    from utils.archetype_cooldown import ArchetypeCooldown
    j = MultiJudge.__new__(MultiJudge)
    j._open_positions = {"BTC-USDT"}
    j._pending_open_symbols = set()
    j._position_slots = {"BTC-USDT": "main"}
    j._pending_open_slots = {}
    ac = ArchetypeCooldown(enabled=True, logger=None)
    ac._history = {"standard": [{"pnl": -1.0, "timestamp": 100.0}]}
    ac._cooldown_until = {"standard": 200.0}
    j._archetype_cooldown = ac
    j._recent_wins = 3
    j._total_completed_trades = 10
    j._recent_win_rate = 0.3
    j._probe_short_active = None
    j._probe_short_sl_count = 1
    j._probe_short_cooldown_until = 0.0
    j._symbol_state = {"BTC-USDT": {"trend_streak": 2}}
    j._available_balance = 1234.5

    class _RM:
        def snapshot(self):
            return {"effective_regime": "bullish", "confidence": 70, "basis": {}}
    j._regime_manager = _RM()

    snap = j._capture_state_snapshot("BTC-USDT")
    assert snap["_open_positions"] == ["BTC-USDT"]
    assert snap["_position_slots"] == {"BTC-USDT": "main"}
    assert snap["_archetype_cooldown"]["_history"]["standard"][0]["pnl"] == -1.0
    assert snap["_archetype_cooldown"]["_cooldown_until"] == {"standard": 200.0}
    assert snap["_recent_wins"] == 3 and snap["_total_completed_trades"] == 10
    assert snap["_available_balance"] == 1234.5
    assert snap["_regime_manager"]["effective_regime"] == "bullish"
    assert snap["_symbol_state"] == {"trend_streak": 2}
    json.dumps(snap)
