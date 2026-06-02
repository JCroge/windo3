"""Entry Drift Hybrid Policy — Gate classification, recompute, invariants.

Coverage matrix lives in docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md
"""
import pytest
from executor import (
    ENTRY_DRIFT_ACCEPT_PCT,
    ENTRY_DRIFT_SMALL_PCT,
    ENTRY_DRIFT_LARGE_PCT,
    ENTRY_DRIFT_MEDIUM_FLOOR_BUMP,
    DriftDecision,
)


def test_thresholds_constants():
    assert ENTRY_DRIFT_ACCEPT_PCT == 0.005
    assert ENTRY_DRIFT_SMALL_PCT == 0.02
    assert ENTRY_DRIFT_LARGE_PCT == 0.05
    assert ENTRY_DRIFT_MEDIUM_FLOOR_BUMP == 0.20


def test_drift_decision_is_frozen_dataclass():
    d = DriftDecision(
        band='accept', drift_pct=0.0, decision='accept',
        reason=None, new_plan=None, rr_actual=None, rr_floor_used=None,
    )
    with pytest.raises((AttributeError, Exception)):
        d.band = 'small'  # frozen


import copy
from unittest.mock import MagicMock


def _exec_stub():
    """Build a minimal ContractExecutor stub for unit tests of pure helpers."""
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = MagicMock()
    return ex


def _plan_long(entry=100.0, sl_pct=0.025, tp_pcts=(0.05, 0.10, 0.15)):
    return {
        'side': 'long',
        'entry_ref': entry,
        'sl_pct': sl_pct,
        'tp_pct': list(tp_pcts),
        'entry_zone': [entry * 0.999, entry * 1.001],
        'stop_loss': entry * (1 - sl_pct),
        'take_profit': [entry * (1 + p) for p in tp_pcts],
        'leverage': 10,
        'size_usdt': 100,
        'order_type': 'limit',
        'attribution': {'rr_floor': 2.00},
    }


def test_recompute_long_small_band_pass():
    ex = _exec_stub()
    plan = _plan_long()  # original R:R = 0.05/0.025 = 2.0, floor=2.0
    new_plan = ex._recompute_plan_for_drift(plan, new_entry=101.0, drift_band='small')
    assert new_plan is not None
    assert new_plan['stop_loss'] == pytest.approx(101.0 * (1 - 0.025), rel=1e-4)
    assert new_plan['take_profit'][0] == pytest.approx(101.0 * 1.05, rel=1e-4)
    assert new_plan['recompute_reason'] == 'drift_small'
    assert new_plan['original_entry_ref'] == 100.0
    assert new_plan['recomputed_entry'] == 101.0
    assert new_plan['rr_floor_used'] == pytest.approx(2.0, rel=1e-4)
    assert new_plan['rr_actual_after_recompute'] == pytest.approx(2.0, rel=1e-4)


def test_recompute_medium_band_floor_bump():
    ex = _exec_stub()
    plan = _plan_long()  # R:R = 2.0
    # medium band requires floor 2.20 → 2.0 fails
    new_plan = ex._recompute_plan_for_drift(plan, new_entry=103.0, drift_band='medium')
    assert new_plan is None  # rr_actual=2.0 < floor 2.2


def test_recompute_medium_band_pass_when_rr_clears_bump():
    ex = _exec_stub()
    plan = _plan_long(sl_pct=0.025, tp_pcts=(0.06, 0.12, 0.18))  # R:R = 2.4
    new_plan = ex._recompute_plan_for_drift(plan, new_entry=103.0, drift_band='medium')
    assert new_plan is not None
    assert new_plan['rr_floor_used'] == pytest.approx(2.2, rel=1e-4)
    assert new_plan['rr_actual_after_recompute'] == pytest.approx(2.4, rel=1e-4)
    assert new_plan['recompute_reason'] == 'drift_medium'


def test_recompute_short_side():
    ex = _exec_stub()
    plan = _plan_long()
    plan['side'] = 'short'
    plan['stop_loss'] = 100.0 * (1 + 0.025)
    plan['take_profit'] = [100.0 * (1 - p) for p in plan['tp_pct']]
    new_plan = ex._recompute_plan_for_drift(plan, new_entry=99.0, drift_band='small')
    assert new_plan is not None
    assert new_plan['stop_loss'] == pytest.approx(99.0 * 1.025, rel=1e-4)
    assert new_plan['take_profit'][0] == pytest.approx(99.0 * 0.95, rel=1e-4)


def test_recompute_does_not_mutate_original():
    ex = _exec_stub()
    plan = _plan_long()
    plan_snapshot = copy.deepcopy(plan)
    ex._recompute_plan_for_drift(plan, new_entry=101.0, drift_band='small')
    assert plan == plan_snapshot


def test_classify_drift_accept_band():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long(entry=100.0)
    d = ex._classify_entry_drift(plan, live_price=100.4)  # drift=0.4%
    assert d.band == 'accept'
    assert d.decision == 'accept'
    assert d.drift_pct == pytest.approx(0.004, rel=1e-3)


def test_classify_drift_boundary_005_still_accept():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    d = ex._classify_entry_drift(plan, live_price=100.5)  # drift=0.5%
    assert d.band == 'accept'


def test_classify_drift_small_band_recalc_pass():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    d = ex._classify_entry_drift(plan, live_price=101.0)  # drift=1%
    assert d.band == 'small'
    assert d.decision == 'recalc_pass'
    assert d.new_plan is not None


def test_classify_drift_boundary_002_still_small():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    d = ex._classify_entry_drift(plan, live_price=102.0)  # drift=2%
    assert d.band == 'small'


def test_classify_drift_medium_band_recalc_fail():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()  # R:R=2.0; medium floor=2.2 → fail
    d = ex._classify_entry_drift(plan, live_price=103.0)  # drift=3%
    assert d.band == 'medium'
    assert d.decision == 'recalc_fail'
    assert d.reason == 'drift_rr_floor_fail'


def test_classify_drift_medium_band_recalc_pass_with_higher_rr():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long(sl_pct=0.025, tp_pcts=(0.06, 0.12, 0.18))  # R:R=2.4
    d = ex._classify_entry_drift(plan, live_price=103.0)
    assert d.decision == 'recalc_pass'
    assert d.rr_floor_used == pytest.approx(2.2, rel=1e-3)


def test_classify_drift_boundary_005_still_medium():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    d = ex._classify_entry_drift(plan, live_price=105.0)  # drift=5%
    assert d.band == 'medium'


def test_classify_drift_abandon_above_5pct():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    d = ex._classify_entry_drift(plan, live_price=105.5)  # drift=5.5%
    assert d.band == 'abandon'
    assert d.decision == 'abandon'
    assert d.reason == 'drift_too_large'
    assert d.new_plan is None


def test_classify_drift_xlm_replay_72pct_abandon():
    """5/30 XLM real replay: entry_ref=0.2179, live=0.2336."""
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long(entry=0.2179)
    d = ex._classify_entry_drift(plan, live_price=0.2336)
    assert d.band == 'abandon'
    assert d.reason == 'drift_too_large'
    assert d.drift_pct == pytest.approx(0.072, abs=0.001)


def test_classify_drift_missing_entry_ref_failsafe_accept():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    plan.pop('entry_ref')
    d = ex._classify_entry_drift(plan, live_price=999.0)
    assert d.band == 'accept'
    assert d.decision == 'accept'
    assert d.drift_pct == 0.0
    assert any(a['type'] == 'plan_missing_entry_ref'
               for a in ex._pending_drift_alerts)


def test_classify_drift_missing_sl_pct_failsafe_accept():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    plan.pop('sl_pct')
    d = ex._classify_entry_drift(plan, live_price=120.0)
    assert d.decision == 'accept'
    assert any(a['type'] == 'plan_missing_entry_ref'
               for a in ex._pending_drift_alerts)


def test_classify_drift_missing_tp_pct_failsafe_accept():
    ex = _exec_stub()
    ex._pending_drift_alerts = []
    plan = _plan_long()
    plan.pop('tp_pct')
    d = ex._classify_entry_drift(plan, live_price=120.0)
    assert d.decision == 'accept'
    assert any(a['type'] == 'plan_missing_entry_ref'
               for a in ex._pending_drift_alerts)


# ---------------------------------------------------------------------------
# Task 5: _set_position_tp single sink + partial_tp invariant
# ---------------------------------------------------------------------------

def test_set_position_tp_writes_both_fields():
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    pos = {}
    ex._set_position_tp(pos, 105.0, [105.0, 110.0, 115.0])
    assert pos['take_profit'] == 105.0
    assert pos['take_profit_levels'] == [105.0, 110.0, 115.0]


def test_set_position_tp_rejects_mismatch():
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    pos = {}
    with pytest.raises(AssertionError):
        ex._set_position_tp(pos, 99.0, [100.0, 110.0])


def test_set_position_tp_rejects_empty_levels():
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    with pytest.raises(AssertionError):
        ex._set_position_tp({}, 100.0, [])


def test_update_trailing_invariant_breach_halts_symbol():
    """Direct mutation breaks invariant → partial_tp_1 must halt symbol."""
    ex = _exec_stub()
    ex.exchange_id = 'okx'
    ex.testnet = True
    ex.logger = MagicMock()
    ex._halted_symbols = {}
    ex._pending_drift_alerts = []
    ex._halt_symbol = MagicMock(side_effect=lambda s, reason: ex._halted_symbols.update({s: reason}))
    pos = {
        'side': 'long', 'entry_price': 100.0, 'stop_loss': 97.5,
        'take_profit': 999.0,                # bypass — broken!
        'take_profit_levels': [102.0, 110.0],
        'tp_filled': 0,
        'original_sl': 97.5,
        'atr_pct': 0.02,
    }
    sig = ex._update_trailing('XLM-USDT', pos, price=103.0)
    # Should NOT return 'partial_tp_1'; should halt instead
    assert ex._halted_symbols.get('XLM-USDT') == 'tp_invariant_breach'
    assert any(a['type'] == 'tp_invariant_breach'
               for a in ex._pending_drift_alerts)
    assert sig is None


def test_gate1_abandons_xlm_replay():
    """End-to-end Gate 1: 5/30 XLM scenario must NOT submit any order."""
    from unittest.mock import MagicMock
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = MagicMock()
    ex.exchange = MagicMock()
    ex.exchange.fetch_ticker.return_value = {'last': 0.2336}
    ex.exchange.create_order = MagicMock()  # spy
    ex.exchange.set_leverage = MagicMock()
    ex.exchange_id = 'okx'
    ex._okx_pos_mode = 'long_short_mode'
    ex.testnet = True
    ex.balance_adapter = MagicMock()
    ex.balance_adapter.get_free.return_value = 5000.0
    ex.risk_manager = MagicMock()
    ex.risk_manager.max_trade_amount = 100
    ex.risk_manager.check_can_trade.return_value = (True, 'ok')
    ex.leverage = 10
    ex.idempotency = None
    ex.caps = None
    ex.ledger = None
    ex._pending_drift_alerts = []
    ex._halted_symbols = {}

    plan = {
        'side': 'long',
        'entry_ref': 0.2179, 'sl_pct': 0.025,
        'tp_pct': [0.061, 0.122, 0.180],
        'entry_zone': [0.2177, 0.2181],
        'stop_loss': 0.2125,
        'take_profit': [0.2312, 0.2444, 0.2571],
        'leverage': 10, 'size_usdt': 100,
        'order_type': 'limit',
        'attribution': {'rr_floor': 2.0},
    }

    result = ex.open_position_with_plan('XLM-USDT', 'long', plan)
    assert result is None
    ex.exchange.create_order.assert_not_called()


def test_gate2_basis_is_original_entry_ref_not_segmented():
    """Gate 1 small drift (1%) + 30s later additional 5% drift = 6% total → abandon
    even though each segment alone would be small/medium."""
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    ex._pending_drift_alerts = []

    plan = _plan_long(entry=100.0)  # entry_ref=100.0
    # Simulate: Gate 1 saw live=101 → recalc_pass
    gate1 = ex._classify_entry_drift(plan, live_price=101.0)
    assert gate1.decision == 'recalc_pass'

    # Gate 2 must use ORIGINAL plan, NOT gate1.new_plan
    # If Gate 2 wrongly used gate1.new_plan (entry=101), drift to 106 = 4.95% (medium pass possibly)
    # Correct: use original plan, drift = (106-100)/100 = 6% → abandon
    gate2 = ex._classify_entry_drift(plan, live_price=106.0)
    assert gate2.band == 'abandon'
    assert gate2.decision == 'abandon'


def test_ledger_records_entry_drift_decision(tmp_path):
    from utils.live_ledger import LiveLedger
    events_path = str(tmp_path / "live_order_events.jsonl")
    ledger = LiveLedger.__new__(LiveLedger)
    ledger.events_path = events_path
    ledger.logger = MagicMock()
    ledger._lock = __import__('threading').Lock()
    ledger.exchange = None
    ledger.record_entry_drift_decision(
        symbol='XLM-USDT', side='long', gate='gate_1',
        band='abandon', drift_pct=0.072, decision='abandon',
        reason='drift_too_large',
        rr_actual=None, rr_floor_used=None,
    )
    import json
    with open(events_path) as f:
        events = [json.loads(line) for line in f if line.strip()]
    assert len(events) == 1
    assert events[0]['event'] == 'entry_drift_decision'
    assert events[0]['symbol'] == 'XLM-USDT'
    assert events[0]['gate'] == 'gate_1'
    assert events[0]['band'] == 'abandon'
    assert events[0]['drift_pct'] == pytest.approx(0.072)


@pytest.mark.asyncio
async def test_agent_publishes_drift_alerts_after_open():
    """When root executor enqueues drift alerts, agent must drain & publish them."""
    from agents.trading.executor import MultiExecutor
    agent = MultiExecutor.__new__(MultiExecutor)
    agent.logger = MagicMock()
    agent.executor = MagicMock()
    agent.executor._pending_drift_alerts = [
        {'type': 'entry_drift_abandoned', 'symbol': 'XLM-USDT',
         'drift_pct': 0.072, 'gate': 'gate_1', 'timestamp': 1.0},
    ]
    agent.executor.open_position_with_plan = MagicMock(return_value=None)
    published = []

    async def mock_publish(topic, payload, **kw):
        published.append((topic, payload))

    agent.publish = mock_publish
    await agent._drain_drift_alerts()
    assert any(t == 'risk_alert' and p['type'] == 'entry_drift_abandoned'
               for t, p in published)


@pytest.mark.asyncio
async def test_execution_result_carries_attribution_entry_drift():
    """Reject path should put drift_decision into attribution.entry_drift."""
    from agents.trading.executor import MultiExecutor
    agent = MultiExecutor.__new__(MultiExecutor)
    agent.logger = MagicMock()
    agent.executor = MagicMock()
    agent.executor._pending_drift_alerts = [
        {'type': 'entry_drift_abandoned', 'symbol': 'XLM-USDT', 'side': 'long',
         'drift_pct': 0.072, 'gate': 'gate_1', 'timestamp': 1.0},
    ]
    agent.executor.open_position_with_plan = MagicMock(return_value=None)
    published = []

    async def mock_publish(topic, payload, **kw):
        published.append((topic, payload))

    agent.publish = mock_publish
    # NOTE: full _dispatch_decision invocation requires extensive setup;
    # this test asserts via the helper that builds attribution
    attr = agent._build_drift_attribution(agent.executor._pending_drift_alerts)
    assert attr['band'] == 'abandon'
    assert attr['decision'] == 'abandon'
    assert attr['drift_pct'] == pytest.approx(0.072)
    assert attr['gate'] == 'gate_1'
