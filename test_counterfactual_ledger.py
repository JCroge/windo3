"""Tests for CounterfactualLedger."""
import time
import json
import pytest
from utils.counterfactual_ledger import CounterfactualLedger


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    return CounterfactualLedger(enabled=True, logger=None)


class TestRecordRejection:
    """AC-LEDGER-01: Rejected plan recorded with all fields."""

    def test_record_creates_event(self, ledger, tmp_path):
        plan = {
            'entry_zone': [0.38, 0.39],
            'stop_loss': 0.36,
            'take_profit': [0.42],
            'effective_risk_reward_ratio': 1.35,
            'leverage': 10,
        }
        ledger.record_rejection(
            symbol='ONDO-USDT', side='long', plan=plan,
            regime='bullish', score=53, confidence=40,
            reject_reason='confidence<60'
        )

        assert ledger.active_count() == 1
        events_file = tmp_path / "data" / "rejected_signal_events.jsonl"
        assert events_file.exists()
        line = events_file.read_text().strip()
        evt = json.loads(line)
        assert evt['event_type'] == 'rejected_plan_created'
        rec = evt['record']
        assert rec['symbol'] == 'ONDO-USDT'
        assert rec['side'] == 'long'
        assert rec['effective_regime'] == 'bullish'
        assert rec['reject_reason'] == 'confidence<60'
        assert rec['stop_loss'] == 0.36

    def test_disabled_ledger_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir(exist_ok=True)
        ledger = CounterfactualLedger(enabled=False)
        ledger.record_rejection('X', 'long', {}, 'mixed', 50, 40, 'test')
        assert ledger.active_count() == 0


class TestShadowResolution:
    """AC-LEDGER-02: Shadow TP/SL resolution on price tick."""

    def test_shadow_tp_long(self, ledger):
        plan = {
            'entry_zone': [100.0, 100.0],
            'stop_loss': 95.0,
            'take_profit': [110.0],
            'effective_risk_reward_ratio': 2.0,
            'leverage': 5,
        }
        ledger.record_rejection('BTC-USDT', 'long', plan, 'bullish', 50, 40, 'test')
        assert ledger.active_count() == 1

        # Price hits TP
        ledger.check_price('BTC-USDT', 111.0, time.time())
        assert ledger.active_count() == 0

        stats = ledger.get_stats()
        assert stats['tp'] == 1
        assert stats['sl'] == 0

    def test_shadow_sl_short(self, ledger):
        plan = {
            'entry_zone': [100.0, 100.0],
            'stop_loss': 105.0,
            'take_profit': [90.0],
            'effective_risk_reward_ratio': 2.0,
            'leverage': 5,
        }
        ledger.record_rejection('ETH-USDT', 'short', plan, 'bullish', -50, 40, 'guard')
        # Price hits SL (goes above 105)
        ledger.check_price('ETH-USDT', 106.0, time.time())
        assert ledger.active_count() == 0

        stats = ledger.get_stats()
        assert stats['sl'] == 1

    def test_no_trigger_on_different_symbol(self, ledger):
        plan = {
            'entry_zone': [100.0, 100.0],
            'stop_loss': 95.0,
            'take_profit': [110.0],
            'effective_risk_reward_ratio': 2.0,
            'leverage': 5,
        }
        ledger.record_rejection('BTC-USDT', 'long', plan, 'bullish', 50, 40, 'test')
        ledger.check_price('ETH-USDT', 111.0, time.time())
        assert ledger.active_count() == 1  # not resolved

    def test_expiry_after_24h(self, ledger):
        """AC-LEDGER-03: 24h expiry."""
        plan = {
            'entry_zone': [100.0, 100.0],
            'stop_loss': 95.0,
            'take_profit': [110.0],
            'effective_risk_reward_ratio': 2.0,
            'leverage': 5,
        }
        ledger.record_rejection('BTC-USDT', 'long', plan, 'bullish', 50, 40, 'test')
        # Simulate 25h later
        future = time.time() + 25 * 3600
        ledger.check_price('BTC-USDT', 101.0, future)
        assert ledger.active_count() == 0

        stats = ledger.get_stats()
        assert stats['expired'] == 1

    def test_tactical_shadow_max_hold_resolves_with_current_price(self, ledger):
        plan = {
            'entry_zone': [100.0, 100.0],
            'stop_loss': 95.0,
            'take_profit': [110.0],
            'effective_risk_reward_ratio': 1.0,
            'leverage': 5,
            'track': 'tactical',
            'exit_profile': 'tactical_v1',
            'tactical_max_hold_minutes': 90,
        }
        ledger.record_rejection('BTC-USDT', 'long', plan, 'mixed', 58, 65, 'tactical_shadow_only')
        rec = next(iter(ledger._active.values()))
        future = rec['created_at'] + 91 * 60

        ledger.check_price('BTC-USDT', 102.0, future)

        assert ledger.active_count() == 0
        events = [
            json.loads(line)
            for line in open(ledger._events_path)
            if line.strip()
        ]
        assert events[-1]['event_type'] == 'shadow_tactical_max_hold'
        assert events[-1]['record']['status'] == 'shadow_tactical_max_hold'
        assert events[-1]['record']['resolved_price'] == 102.0
        assert events[-1]['record']['pnl_pct'] == 2.0

    def test_filtered_tactical_shadow_only_still_uses_tactical_max_hold(self, ledger):
        plan = {
            'entry_zone': [100.0, 100.0],
            'stop_loss': 95.0,
            'take_profit': [110.0],
            'effective_risk_reward_ratio': 0.75,
            'leverage': 5,
            'track': 'shadow_only',
            'exit_profile': 'tactical_v1',
            'tactical_track_gate': 'fail',
            'tactical_gate_failed': 'min_rr_or_ev',
            'tactical_max_hold_minutes': 90,
        }
        ledger.record_rejection(
            'BTC-USDT', 'long', plan, 'mixed', 58, 65,
            'tactical_gate_failed:min_rr_or_ev',
        )
        rec = next(iter(ledger._active.values()))
        assert rec['tactical_track_gate'] == 'fail'
        assert rec['tactical_gate_failed'] == 'min_rr_or_ev'
        future = rec['created_at'] + 91 * 60

        ledger.check_price('BTC-USDT', 102.0, future)

        assert ledger.active_count() == 0
        events = [
            json.loads(line)
            for line in open(ledger._events_path)
            if line.strip()
        ]
        assert events[-1]['event_type'] == 'shadow_tactical_max_hold'
        assert events[-1]['record']['track'] == 'shadow_only'
        assert events[-1]['record']['exit_profile'] == 'tactical_v1'
        assert events[-1]['record']['pnl_pct'] == 2.0


class TestStats:
    def test_stats_by_side(self, ledger):
        plan_long = {'entry_zone': [100], 'stop_loss': 95, 'take_profit': [110],
                     'effective_risk_reward_ratio': 2.0, 'leverage': 5}
        plan_short = {'entry_zone': [100], 'stop_loss': 105, 'take_profit': [90],
                      'effective_risk_reward_ratio': 2.0, 'leverage': 5}

        ledger.record_rejection('A-USDT', 'long', plan_long, 'bullish', 50, 40, 'test')
        ledger.record_rejection('B-USDT', 'short', plan_short, 'bullish', -50, 40, 'guard')

        ledger.check_price('A-USDT', 111.0, time.time())  # TP
        ledger.check_price('B-USDT', 106.0, time.time())  # SL

        long_stats = ledger.get_stats(side='long')
        assert long_stats['tp'] == 1
        assert long_stats['sl'] == 0

        short_stats = ledger.get_stats(side='short')
        assert short_stats['tp'] == 0
        assert short_stats['sl'] == 1
