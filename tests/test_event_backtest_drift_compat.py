"""Old plans (no entry_ref/sl_pct/tp_pct) must still flow through executor as fail-safe accept."""
from unittest.mock import MagicMock
from executor import ContractExecutor


def test_old_plan_skips_drift_gate_failsafe():
    """Legacy plan without entry_ref/sl_pct/tp_pct should fail-safe accept with drift_pct=0.0."""
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = MagicMock()
    ex._pending_drift_alerts = []

    # Old plan format (no entry_ref/sl_pct/tp_pct)
    legacy_plan = {
        'side': 'long',
        'entry_zone': [99.9, 100.1],
        'stop_loss': 97.5,
        'take_profit': [105.0, 110.0],
        'leverage': 10,
        'size_usdt': 100,
        'order_type': 'limit',
    }

    # Call drift gate with live price far from entry_zone
    decision = ex._classify_entry_drift(legacy_plan, live_price=120.0)

    # Should accept as-is (fail-safe)
    assert decision.decision == 'accept'
    assert decision.drift_pct == 0.0
    assert decision.band == 'accept'
    assert decision.reason is None

    # Should enqueue a plan_missing_entry_ref alert
    assert len(ex._pending_drift_alerts) > 0
    assert any(a['type'] == 'plan_missing_entry_ref'
               for a in ex._pending_drift_alerts)
