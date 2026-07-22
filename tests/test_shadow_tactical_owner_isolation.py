from unittest.mock import MagicMock

from executor import ContractExecutor


def _executor():
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.exchange_id = "okx"
    ex.testnet = False
    ex.logger = MagicMock()
    ex.positions = {}
    ex._close_cooldown = {}
    ex._pending_resync = {}
    ex._removed_positions_data = []
    ex._last_removed_symbols = []
    ex._sl_check_failures = {}
    ex._last_protection_alert = {}
    ex._halted_symbols = {}
    ex._config = {"position_resync_confirm_ticks": 1}
    ex._save_positions = MagicMock()
    ex._migrate_all_symbols_algos = MagicMock()
    ex._maybe_auto_clear_protection_halt = MagicMock()
    ex._load_sidecar_owner_registry = MagicMock(return_value=None)
    return ex


def _raw_pos():
    return {
        "symbol": "WLD/USDT:USDT",
        "contracts": 10,
        "side": "long",
        "leverage": 20,
        "notional": 25.0,
        "entryPrice": 1.25,
        "unrealizedPnl": 0.0,
    }


def test_sync_positions_skips_sidecar_owned_backfill():
    ex = _executor()
    owners = MagicMock()
    owners.matches_position.return_value = True
    ex._load_sidecar_owner_registry.return_value = owners
    ex._fetch_positions_with_retry = MagicMock(return_value=[_raw_pos()])

    ex.sync_positions()

    assert "WLD-USDT-SWAP" not in ex.positions
    owners.matches_position.assert_called_once_with("WLD-USDT-SWAP", "long")


def test_sync_positions_still_backfills_non_sidecar_position():
    ex = _executor()
    owners = MagicMock()
    owners.matches_position.return_value = False
    ex._load_sidecar_owner_registry.return_value = owners
    ex._fetch_positions_with_retry = MagicMock(return_value=[_raw_pos()])

    ex.sync_positions()

    assert "WLD-USDT-SWAP" in ex.positions


def test_migration_does_not_cancel_foreign_owner_tag_without_local_position():
    ex = _executor()
    ex.positions = {}
    ex._list_pending_algos = MagicMock(
        return_value=[
            {
                "algoId": "algo-sidecar",
                "algoClOrdId": "castliveWLDabc",
                "sl_trigger": "1.20",
                "tp_trigger": "",
                "ordType": "conditional",
            }
        ]
    )
    ex._is_foreign_owner_clord_id = MagicMock(return_value=True)
    ex._cancel_algo_by_id = MagicMock()

    summary = ex._migrate_okx_algos_for_symbol("WLD-USDT-SWAP")

    assert summary["orphan_sl"] == 0
    assert summary["foreign_algos"] == 1
    ex._cancel_algo_by_id.assert_not_called()


def test_migration_preserves_manual_oco_for_sidecar_owned_present_exposure():
    ex = _executor()
    ex.positions = {}
    ex._list_pending_algos = MagicMock(
        return_value=[
            {
                "algoId": "manual-oco-ada",
                "algoClOrdId": "manual-okx-ui",
                "sl_trigger": "0.168",
                "tp_trigger": "0.180",
                "ordType": "oco",
            }
        ]
    )
    ex._is_sidecar_owned_algo_clord_id = MagicMock(return_value=False)
    ex._is_foreign_owner_clord_id = MagicMock(return_value=False)
    ex._sidecar_symbol_exchange_state = MagicMock(return_value="present")
    ex._cancel_algo_by_id = MagicMock()

    summary = ex._migrate_okx_algos_for_symbol("ADA-USDT-SWAP")

    assert summary["orphan_sl"] == 0
    assert summary["sidecar_protected_algos"] == 1
    ex._cancel_algo_by_id.assert_not_called()


def test_migration_preserves_manual_conditional_sl_for_sidecar_owned_unknown_exposure():
    ex = _executor()
    ex.positions = {}
    ex._list_pending_algos = MagicMock(
        return_value=[
            {
                "algoId": "manual-sl-ada",
                "algoClOrdId": "manual-sl",
                "sl_trigger": "0.168",
                "tp_trigger": "",
                "ordType": "conditional",
            }
        ]
    )
    ex._is_sidecar_owned_algo_clord_id = MagicMock(return_value=False)
    ex._is_foreign_owner_clord_id = MagicMock(return_value=False)
    ex._sidecar_symbol_exchange_state = MagicMock(return_value="unknown")
    ex._cancel_algo_by_id = MagicMock()

    summary = ex._migrate_okx_algos_for_symbol("ADA-USDT-SWAP")

    assert summary["orphan_sl"] == 0
    assert summary["sidecar_protected_algos"] == 1
    ex._cancel_algo_by_id.assert_not_called()


def test_migration_preserves_manual_tp_only_for_sidecar_owned_present_exposure():
    ex = _executor()
    ex.positions = {}
    ex._list_pending_algos = MagicMock(
        return_value=[
            {
                "algoId": "manual-tp-ada",
                "algoClOrdId": "manual-tp",
                "sl_trigger": "",
                "tp_trigger": "0.180",
                "ordType": "conditional",
            }
        ]
    )
    ex._is_sidecar_owned_algo_clord_id = MagicMock(return_value=False)
    ex._is_foreign_owner_clord_id = MagicMock(return_value=False)
    ex._sidecar_symbol_exchange_state = MagicMock(return_value="present")
    ex._cancel_algo_by_id = MagicMock()

    summary = ex._migrate_okx_algos_for_symbol("ADA-USDT-SWAP")

    assert summary["cancelled_tp"] == 0
    assert summary["orphan_sl"] == 0
    assert summary["sidecar_protected_algos"] == 1
    ex._cancel_algo_by_id.assert_not_called()


def test_sidecar_symbol_exchange_state_unknown_when_exchange_lookup_fails():
    ex = _executor()
    owners = MagicMock()
    owners.matches_position.side_effect = lambda symbol, side: (
        symbol == "ADA-USDT-SWAP" and side == "long"
    )
    ex._load_sidecar_owner_registry.return_value = owners
    ex.exchange = MagicMock()
    ex.exchange.fetch_positions.side_effect = RuntimeError("okx unavailable")

    state = ex._sidecar_symbol_exchange_state("ADA-USDT-SWAP")

    assert state == "unknown"


def test_migration_preserves_manual_sl_when_sidecar_registry_unavailable():
    ex = _executor()
    ex.positions = {}
    ex._load_sidecar_owner_registry.return_value = None
    ex._list_pending_algos = MagicMock(
        return_value=[
            {
                "algoId": "manual-sl-ada-registry-down",
                "algoClOrdId": "manual-sl",
                "sl_trigger": "0.168",
                "tp_trigger": "",
                "ordType": "conditional",
            }
        ]
    )
    ex._is_sidecar_owned_algo_clord_id = MagicMock(return_value=False)
    ex._is_foreign_owner_clord_id = MagicMock(return_value=False)
    ex._cancel_algo_by_id = MagicMock()

    summary = ex._migrate_okx_algos_for_symbol("ADA-USDT-SWAP")

    assert summary["orphan_sl"] == 0
    assert summary["sidecar_protected_algos"] == 1
    ex._cancel_algo_by_id.assert_not_called()


def test_migration_cleans_orphan_sl_when_sidecar_owner_confirmed_flat():
    ex = _executor()
    ex.positions = {}
    owners = MagicMock()
    owners.matches_position.side_effect = lambda symbol, side: (
        symbol == "ADA-USDT-SWAP" and side == "long"
    )
    ex._load_sidecar_owner_registry.return_value = owners
    ex._fetch_okx_position_state = MagicMock(return_value=None)
    ex._list_pending_algos = MagicMock(
        return_value=[
            {
                "algoId": "manual-sl-ada-flat",
                "algoClOrdId": "manual-sl",
                "sl_trigger": "0.168",
                "tp_trigger": "",
                "ordType": "conditional",
            }
        ]
    )
    ex._is_sidecar_owned_algo_clord_id = MagicMock(return_value=False)
    ex._is_foreign_owner_clord_id = MagicMock(return_value=False)
    ex._cancel_algo_by_id = MagicMock(return_value=True)

    summary = ex._migrate_okx_algos_for_symbol("ADA-USDT-SWAP")

    assert summary["orphan_sl"] == 1
    assert summary["sidecar_protected_algos"] == 0
    ex._cancel_algo_by_id.assert_called_once_with(
        "ADA-USDT-SWAP", "manual-sl-ada-flat"
    )


def test_migration_cleans_tp_only_when_sidecar_owner_confirmed_flat():
    ex = _executor()
    ex.positions = {}
    owners = MagicMock()
    owners.matches_position.side_effect = lambda symbol, side: (
        symbol == "ADA-USDT-SWAP" and side == "long"
    )
    ex._load_sidecar_owner_registry.return_value = owners
    ex._fetch_okx_position_state = MagicMock(return_value=None)
    ex._list_pending_algos = MagicMock(
        return_value=[
            {
                "algoId": "manual-tp-ada-flat",
                "algoClOrdId": "manual-tp",
                "sl_trigger": "",
                "tp_trigger": "0.180",
                "ordType": "conditional",
            }
        ]
    )
    ex._is_sidecar_owned_algo_clord_id = MagicMock(return_value=False)
    ex._is_foreign_owner_clord_id = MagicMock(return_value=False)
    ex._cancel_algo_by_id = MagicMock(return_value=True)

    summary = ex._migrate_okx_algos_for_symbol("ADA-USDT-SWAP")

    assert summary["cancelled_tp"] == 1
    assert summary["orphan_sl"] == 0
    assert summary["sidecar_protected_algos"] == 0
    ex._cancel_algo_by_id.assert_called_once_with(
        "ADA-USDT-SWAP", "manual-tp-ada-flat"
    )
