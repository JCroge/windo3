import asyncio

import pytest

from utils.decision_replay import (
    restore_state, replay_decision, compare_decision, production_base_config,
)


def test_production_base_config_has_phase2_true():
    cfg = production_base_config()
    assert cfg["phase2_signal_confidence_split_enabled"] is True
    assert cfg["phase2_momentum_probe_long_enabled"] is True
    assert cfg["phase2_trend_saturation_enabled"] is True
    assert cfg["phase2_bucketed_ev_enabled"] is True
    assert cfg["rr_floor_default"] == 1.50


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


def _accept_fixture_record():
    """A crafted record that legitimately produces an open_long through the REAL
    _make_decision (no harness/judge weakening). The tech is strong-bullish and
    clears every open gate: rule_signal entry_long (score ~96, locks direction),
    3/3 HTF votes bullish, RSI 56 (not overbought), high liquidity, 15m confirmed
    long, range position 0.5 (not overheated), and a winning rolling track record
    (8/14 ~57%) so the EV gate passes with effective R:R ~1.39. The accept is
    buffered into the real CandidateRanker and published by the deferred flush the
    harness drives synchronously — this is the proof the ranker fix works.
    """
    snap = {
        "_open_positions": [], "_pending_open_symbols": [],
        "_position_slots": {}, "_pending_open_slots": {},
        "_archetype_cooldown": {"_history": {}, "_cooldown_until": {}},
        # Rolling track record -> _get_p_win returns rolling 0.57 (>= 10 trades),
        # so the EV gate sees a realistic edge instead of the 0.40 cold-start prior.
        "_recent_wins": 8, "_total_completed_trades": 14, "_recent_win_rate": 0.57,
        "_probe_short_active": None, "_probe_short_sl_count": 0,
        "_probe_short_cooldown_until": 0.0,
        "_symbol_state": {}, "_available_balance": 1000.0,
        "_regime_manager": {"effective_regime": "bullish", "confidence": 70, "basis": {}},
    }
    price = 50000.0
    tech = {
        "indicators": {"price": price, "rsi": 56},
        "trend": {
            "direction": "bullish", "strength": 82,
            "higher_tf_bias": "bullish", "daily_bias": "bullish",
            "change_24h_pct": 2.0,
            "daily_near_resistance": False, "daily_near_support": False,
            "tf_4h_rsi": 58,
        },
        "momentum": {"rsi": 56, "rsi_divergence": None, "volume_ratio": 1.4,
                     "atr_pct": 0.02},
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
        "schema_version": "decision_replay_record.v1",
        "request_id": "rep-accept-1", "timestamp": 1700000000.0, "symbol": "BTC-USDT",
        "decision": "accept", "tech_analysis": tech,
        "price_at_decision": price, "regime_state": "bullish",
        "llm_output_inline": {"action": "open_long", "confidence": 75,
                              "reasoning": "strong bullish confluence",
                              "key_factors": [], "risk_warnings": []},
        "llm_audit_ref": None,
        "trade_decision_output": {},
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


def test_replay_accepted_open_does_not_crash_and_captures_open():
    """L2 proof: a real accepted open replays end-to-end through _make_decision
    (no crash on the ranked-accept deferred-flush path) and captures an open."""
    rec = _accept_fixture_record()
    captured = asyncio.run(replay_decision(rec, config={}))
    assert captured is not None, "ranked accept must publish (ranker fix)"
    assert captured["action"] in ("open_long", "open_short")
    assert captured.get("plan") is not None


def test_replay_accepted_open_is_deterministic():
    """Golden-master: replaying the same accept twice yields a matching decision
    (request_id/uuid noise is excluded from compare_decision)."""
    rec = _accept_fixture_record()
    d1 = asyncio.run(replay_decision(rec, config={}))
    d2 = asyncio.run(replay_decision(rec, config={}))
    assert d1 is not None and d2 is not None
    assert compare_decision(d1, d2)["match"] is True


# --- End-to-end fidelity on the real live tape (skip-if-absent) ---------------

import json
import os
from utils.sequential_perturbation import _gate_of_recorded, _gate_of_replayed

_TAPE = os.path.join(os.path.dirname(__file__), "..", "data", "decision_replay_tape.jsonl")


def _load_v2_v3():
    if not os.path.exists(_TAPE):
        pytest.skip("no live tape")
    out = []
    for line in open(_TAPE):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("schema_version") not in ("decision_replay_record.v2", "decision_replay_record.v3"):
            continue
        if not (r.get("tech_analysis") or {}):
            continue
        out.append(r)
    return out


def test_production_baseline_restores_fidelity():
    recs = _load_v2_v3()
    if len(recs) < 50:
        pytest.skip("insufficient tape")

    def _ar(g):
        return "accept" if g == "accept" else "reject"

    async def run():
        gate_agree = 0
        ar_agree = 0
        for r in recs:
            # 纪元解析：不传全局 pin，replay_decision 逐记录按录制纪元解析
            # （缺键用 _EPOCH_FALLBACK 录制纪元默认，snapshot 录值优先）。
            d = await replay_decision(r, None)
            gr = _gate_of_recorded(r)
            gd = _gate_of_replayed(d)
            if gr == gd:
                gate_agree += 1
            if _ar(gr) == _ar(gd):
                ar_agree += 1
        return gate_agree / len(recs), ar_agree / len(recs)

    gate_fid, ar_fid = asyncio.run(run())
    # gate 严格保真：诊断-only（门归因短路顺序敏感，不作硬门）
    print(f"[diag] L2 gate fidelity = {gate_fid:.3f} (诊断, 实测 ~0.89)")
    # accept/reject 二元保真：主可信度硬门（方向推荐真正依赖的维度）
    assert ar_fid >= 0.95, f"L2 accept/reject fidelity {ar_fid:.3f} < 0.95 (production baseline should be ~0.985)"


# --- 纪元解析单测 ---------------------------------------------------------

def test_epoch_fallback_for_missing_keys():
    """缺键记录用录制纪元默认（ladder→False, ev_winrate→True），非当前 production 默认"""
    from utils.decision_replay import _resolve_effective_config
    rec_old = {"config_snapshot": None}
    eff = _resolve_effective_config(rec_old, None)
    assert eff["ladder_rr_enabled"] is False, "旧记录 ladder 应回退纪元默认 False"
    assert eff["ev_winrate_gate_enabled"] is True, "旧记录 ev 门应回退纪元默认 True"
    print("  ✅ Case: 缺键回退录制纪元默认")


def test_snapshot_overrides_epoch_fallback():
    """v3 记录 snapshot 的 ladder=True 应盖回纪元兜底的 False"""
    from utils.decision_replay import _resolve_effective_config
    rec_v3 = {"config_snapshot": {"ladder_rr_enabled": True}}
    eff = _resolve_effective_config(rec_v3, None)
    assert eff["ladder_rr_enabled"] is True, "snapshot 录值应优先于纪元兜底"
    print("  ✅ Case: snapshot 优先于纪元兜底")


def test_perturbation_overrides_all():
    """扰动 override 在最顶层，盖过 snapshot"""
    from utils.decision_replay import _resolve_effective_config
    rec_v3 = {"config_snapshot": {"ladder_rr_enabled": True}}
    eff = _resolve_effective_config(rec_v3, {"ladder_rr_enabled": False})
    assert eff["ladder_rr_enabled"] is False, "扰动 override 应盖过 snapshot"
    print("  ✅ Case: 扰动 override 最顶层")


def test_epoch_fallback_keys_exist_in_defaults():
    """_EPOCH_FALLBACK 每个键都存在于当前 DEFAULTS（无 stale/typo）"""
    from utils.decision_replay import _EPOCH_FALLBACK, _PROD_DEFAULTS
    for k in _EPOCH_FALLBACK:
        assert k in _PROD_DEFAULTS, f"_EPOCH_FALLBACK 键 {k} 不在 DEFAULTS"
    print("  ✅ Case: 纪元兜底键不悬空")


def test_no_unclassified_missing_snapshot_keys():
    """磁带 v3 记录中缺于 snapshot 的 DEFAULTS 键，必须被显式分类（兜底或无关）"""
    from utils.decision_replay import _EPOCH_FALLBACK, _GATE_IRRELEVANT, _PROD_DEFAULTS
    recs = [r for r in _load_v2_v3()
            if r.get("schema_version") == "decision_replay_record.v3"
            and (r.get("config_snapshot") or {})]
    if len(recs) < 50:
        pytest.skip("insufficient v3 tape")
    classified = set(_EPOCH_FALLBACK) | set(_GATE_IRRELEVANT)
    missing = set()
    for r in recs:
        snap = r.get("config_snapshot") or {}
        for k in _PROD_DEFAULTS:
            if k not in snap:
                missing.add(k)
    unclassified = missing - classified
    assert not unclassified, (
        f"v3 snapshot 缺键未分类（新增翻转默认键？请登记进 _EPOCH_FALLBACK 或 _GATE_IRRELEVANT）: "
        f"{sorted(unclassified)}")
    print(f"  ✅ Case: 缺键全分类（missing={sorted(missing)}）")
