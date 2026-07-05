"""Tests for low_rr early trailing exit mechanism."""
import logging
import threading
import pytest
from unittest.mock import MagicMock

from executor import ContractExecutor


def _make_executor(config=None):
    """Create a minimal ContractExecutor for trailing tests."""
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = logging.getLogger('test_low_rr_early_trailing')
    ex.exchange_id = 'okx'
    ex.testnet = True
    ex.exchange = MagicMock()
    ex.positions = {}
    ex._config = config or {}
    ex._last_sl_update = {}
    ex._exit_locks = {}
    ex._exit_lock_mu = threading.Lock()
    ex._pending_drift_alerts = []
    ex._halted_symbols = set()
    return ex


def _make_position(entry, sl, tp_levels, side='long', slot_type='low_rr_extra'):
    return {
        'symbol': 'SOL-USDT-SWAP',
        'side': side,
        'entry_price': entry,
        'stop_loss': sl,
        'original_sl': sl,
        'take_profit': tp_levels[0] if tp_levels else entry * 1.03,
        'take_profit_levels': tp_levels,
        'tp_filled': 0,
        'highest_price': entry,
        'lowest_price': entry,
        'atr_pct': 0.02,
        'slot_type': slot_type,
    }


class TestLowRrEarlyTrailing:

    def test_trailing_activates_at_05r(self):
        """Trailing SL moves when profit reaches +0.5R."""
        ex = _make_executor()
        entry, sl = 100.0, 98.0  # R = 2.0
        pos = _make_position(entry, sl, [103.0])
        pos['highest_price'] = 101.0
        price = 101.0

        result = ex._update_trailing('SOL-USDT-SWAP', pos, price)

        assert result is None
        # SL = highest - 0.3R*entry*R_pct = 101.0 - 2.0*0.3 = 100.4? No.
        # R = abs(entry - sl) / entry = 2.0/100.0 = 0.02
        # trail_dist_abs = R * trail_dist * entry = 0.02 * 0.3 * 100 = 0.6
        # new_sl = 101.0 - 0.6 = 100.4
        assert pos['stop_loss'] == pytest.approx(100.4, rel=1e-4)

    def test_trailing_does_not_activate_below_05r(self):
        """No trailing when profit < +0.5R."""
        ex = _make_executor()
        entry, sl = 100.0, 98.0
        pos = _make_position(entry, sl, [103.0])
        pos['highest_price'] = 100.8  # +0.4R
        price = 100.8

        ex._update_trailing('SOL-USDT-SWAP', pos, price)

        assert pos['stop_loss'] == sl

    def test_trailing_sl_ratchets_up(self):
        """Trailing SL only moves in favorable direction."""
        ex = _make_executor()
        entry, sl = 100.0, 98.0
        pos = _make_position(entry, sl, [103.0])

        # Price to +0.7R = 101.4
        pos['highest_price'] = 101.4
        ex._update_trailing('SOL-USDT-SWAP', pos, 101.4)
        sl_first = pos['stop_loss']
        assert sl_first > sl

        # Price retraces but highest stays
        ex._update_trailing('SOL-USDT-SWAP', pos, 101.0)
        assert pos['stop_loss'] == sl_first

        # New high
        pos['highest_price'] = 102.0
        ex._update_trailing('SOL-USDT-SWAP', pos, 102.0)
        assert pos['stop_loss'] > sl_first

    def test_tp1_still_triggers(self):
        """TP1 fires even with early trailing active."""
        ex = _make_executor()
        entry, sl = 100.0, 98.0
        pos = _make_position(entry, sl, [103.0])
        pos['highest_price'] = 103.5
        price = 103.5

        result = ex._update_trailing('SOL-USDT-SWAP', pos, price)

        assert result == 'partial_tp_1'

    def test_main_slot_not_affected(self):
        """Main slot uses original BE/lock logic, no early trailing."""
        ex = _make_executor()
        entry, sl = 100.0, 98.0
        pos = _make_position(entry, sl, [103.0], slot_type='main')
        pos['highest_price'] = 101.0
        price = 101.0

        ex._update_trailing('SOL-USDT-SWAP', pos, price)

        assert pos['stop_loss'] == sl

    def test_custom_config_params(self):
        """Config overrides trail_start and trail_dist."""
        ex = _make_executor(config={
            'low_rr_trail_start_r': 0.6,
            'low_rr_trail_dist_r': 0.4,
        })
        entry, sl = 100.0, 98.0
        pos = _make_position(entry, sl, [103.0])

        # +0.5R: should NOT activate (threshold 0.6)
        pos['highest_price'] = 101.0
        ex._update_trailing('SOL-USDT-SWAP', pos, 101.0)
        assert pos['stop_loss'] == sl

        # +0.6R: activates with 0.4R distance
        pos['highest_price'] = 101.2
        ex._update_trailing('SOL-USDT-SWAP', pos, 101.2)
        # trail_dist_abs = 0.02 * 0.4 * 100 = 0.8
        assert pos['stop_loss'] == pytest.approx(101.2 - 0.8, rel=1e-4)

    def test_short_side_trailing(self):
        """Early trailing works for short low_rr positions."""
        ex = _make_executor()
        entry, sl = 100.0, 102.0  # short, R = 2.0
        pos = _make_position(entry, sl, [97.0], side='short', slot_type='low_rr_extra')
        pos['lowest_price'] = 99.0
        price = 99.0

        ex._update_trailing('SOL-USDT-SWAP', pos, price)

        # trail_dist_abs = 0.02 * 0.3 * 100 = 0.6
        # new_sl = lowest + 0.6 = 99.6
        assert pos['stop_loss'] == pytest.approx(99.6, rel=1e-4)
