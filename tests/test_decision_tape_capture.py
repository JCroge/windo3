"""End-to-end proof: a decision-replay record carrying REAL tech_analysis +
llm_output_inline (the payload the capture fix now stores) can be replayed by
utils.decision_replay.replay_decision so that it:
  (a) reaches the R:R floor gate and reproduces a rr_below_floor reject under
      baseline config (rr_floor_default=1.50), and
  (b) FLIPS to an open decision when rr_floor_default is lowered to 1.30.

This directly proves the root cause of the counterfactual lab's empty results
(records with empty tech/llm short-circuit to hold and never reach any gate)
is resolved: a record with populated tech + llm propagates all the way to the
R:R gate, where the knob is the binding decision.

Tuning notes (empirical):
- tech.trend.direction/higher_tf_bias/daily_bias all set to 'neutral' so that
  the mixed-regime long_aligned_low_rr branch in _select_rr_floor does NOT
  trigger (which would give floor 1.30 and pass the gate).  Keeping all three
  neutral while regime='mixed' routes to the DEFAULT policy (floor=1.50).
- The same rule_signal / levels / momentum / entry_timing geometry as
  _accept_fixture_record yields effective_rr ~= 1.39 (between 1.30 and 1.50),
  so floor 1.50 rejects and floor 1.30 accepts.
- Rolling track record (8/14 ~57%) ensures the EV gate passes and
  rr_below_floor is the first (and only) binding gate.
"""
import asyncio

import pytest

from utils.decision_replay import replay_decision


@pytest.fixture(autouse=True)
def _restore_event_loop():
    """`asyncio.run()` closes and clears the current event loop, polluting
    subsequent tests that call `asyncio.get_event_loop()` (e.g. on Py3.9).
    Restore a fresh event loop after each replay test so the module does not
    bleed into the rest of the suite.
    """
    yield
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _rr_reject_record():
    """Return a replayable schema-v2 record whose effective R:R lands ~1.39 —
    above the 1.30 lower bound but below the 1.50 default floor.

    Regime policy routing:
      effective_regime='mixed', trend.direction='neutral' →
      long_aligned_low_rr branch condition NOT satisfied (sym_dir != 'bullish')
      → falls through to DEFAULT policy → floor = rr_floor_default (1.50).

    When rr_floor_default is lowered to 1.30 via config the same DEFAULT policy
    now accepts (1.39 >= 1.30), proving the knob controls the gate outcome.

    EV gate:
      _recent_wins=8, _total_completed_trades=14, _recent_win_rate=0.57
      → rolling p_win 0.57 with 14 completed trades satisfies the EV gate
      (min_trades_for_ev_gate defaults to 10), so rr_below_floor is the sole
      binding rejection reason.
    """
    snap = {
        "_open_positions": [], "_pending_open_symbols": [],
        "_position_slots": {}, "_pending_open_slots": {},
        "_archetype_cooldown": {"_history": {}, "_cooldown_until": {}},
        # Rolling track record: 8/14 ~57% so EV gate passes (>=10 trades).
        "_recent_wins": 8, "_total_completed_trades": 14, "_recent_win_rate": 0.57,
        "_probe_short_active": None, "_probe_short_sl_count": 0,
        "_probe_short_cooldown_until": 0.0,
        "_symbol_state": {}, "_available_balance": 1000.0,
        # mixed regime.  trend signals below are neutral so the
        # long_aligned_low_rr branch does NOT fire -> default floor applies.
        "_regime_manager": {"effective_regime": "mixed", "confidence": 70, "basis": {}},
    }
    price = 50000.0
    tech = {
        "indicators": {"price": price, "rsi": 56},
        "trend": {
            # Neutral direction/htf/daily: breaks the alignment condition in
            # _select_rr_floor so the default floor (1.50) is used instead of
            # long_aligned_low_rr (1.30).
            "direction": "neutral", "strength": 60,
            "higher_tf_bias": "neutral", "daily_bias": "neutral",
            "change_24h_pct": 1.0,
            "daily_near_resistance": False, "daily_near_support": False,
            "tf_4h_rsi": 55,
        },
        "momentum": {"rsi": 56, "rsi_divergence": None, "volume_ratio": 1.4,
                     "atr_pct": 0.02},
        # rule_signal.entry_long=True locks direction to open_long.
        "rule_signal": {"entry_long": True, "entry_short": False,
                        "ma_aligned_long": True, "ma_aligned_short": False},
        "levels": {"support": [49000.0, 48000.0], "resistance": [52000.0, 54000.0]},
        "risk": {"liquidity_score": 80},
        "microstructure": {"whale_direction": "accumulating"},
        "money_flow": {"oi_price_divergence": "bullish", "taker_pressure": "buy"},
        "crowd": {"contrarian_signal": "neutral"},
        "entry_timing": {
            "tf_15m_available": True,
            "tf_15m_confirm_long": True, "tf_15m_block_long": False,
            "tf_15m_bias": "bullish", "tf_15m_rsi": 55,
            "tf_15m_recent_closes": "up",
        },
        "entry_context": {
            "position_in_24h_range": 0.5,
            "pre_12h_return_pct": 0.01,
            "prev_daily_return_pct": 0.02,
        },
    }
    return {
        "schema_version": "decision_replay_record.v2",
        "request_id": "rep-rr-reject-1",
        "timestamp": 1700000000.0,
        "symbol": "BTC-USDT",
        "decision": "reject",
        "tech_analysis": tech,
        "price_at_decision": price,
        "regime_state": "mixed",
        "llm_output_inline": {
            "action": "open_long",
            "confidence": 70,
            "reasoning": "bull",
            "key_factors": [],
            "risk_warnings": [],
        },
        "llm_audit_ref": None,
        "trade_decision_output": {},
        "state_snapshot_before_decision": snap,
        "replayable": True,
    }


def test_capture_record_replays_to_gate_reject():
    """A record with real tech + llm reaches the R:R gate and is rejected with
    rr_below_floor under the baseline floor of 1.50.

    This proves the capture fix works: previously empty tech/llm caused an
    immediate short-circuit to hold (reject_reason=None, no gate reached).
    Now the full gate sequence executes and the R:R gate is the binding stop.
    """
    rec = _rr_reject_record()
    # ladder_rr_enabled=False: 该 fixture 验 TP1 口径下 R:R 地板 gate（trend-entry-levers-default-on
    # 后 lever2 默认开会用阶梯口径抬高 effective_rr 越过 1.50，掩盖本测试要验的地板效应）。
    out = asyncio.run(replay_decision(rec, {"rr_floor_default": 1.50, "ladder_rr_enabled": False}))
    assert (out or {}).get("action") in (None, "hold")
    rr = (out or {}).get("reject_reason") or (
        (out or {}).get("attribution") or {}
    ).get("blocked_by")
    assert rr and "rr_below_floor" in rr


def test_capture_record_flips_to_accept_when_floor_lowered():
    """Lowering rr_floor_default from 1.50 to 1.30 flips the same record from
    reject to open_long, proving the R:R knob controls the gate outcome and
    that the fix enables real counterfactual perturbation experiments.
    """
    rec = _rr_reject_record()
    # 同上 pin TP1 口径，纯验地板 1.50→1.30 翻转 reject→open（与 lever2 阶梯口径解耦）。
    out = asyncio.run(replay_decision(rec, {"rr_floor_default": 1.30, "ladder_rr_enabled": False}))
    assert (out or {}).get("action") in ("open_long", "open_short")
