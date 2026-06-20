"""fix-phantom-position-resync: 幽灵持仓补录双确认 + 症状硬化单测。"""
from unittest.mock import MagicMock

from executor import ContractExecutor


def test_config_resync_confirm_ticks_default():
    from utils.config_loader import DEFAULTS
    assert DEFAULTS.get("position_resync_confirm_ticks") == 2


def _mk_executor():
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.exchange = MagicMock()
    ex.exchange_id = "okx"          # 走 _migrate_all_symbols_algos 下游(已 stub)
    ex.logger = MagicMock()
    ex.positions = {}
    ex._close_cooldown = {}
    ex._pending_resync = {}
    ex._last_protection_alert = {}
    ex._halted_symbols = {}
    ex._removed_positions_data = []
    ex._last_removed_symbols = []
    ex._sl_check_failures = {}
    ex._config = {"position_resync_confirm_ticks": 2}
    # stub 重下游, 隔离双确认逻辑（实读真实下游名: _migrate_all_symbols_algos）
    ex._save_positions = MagicMock()
    ex._migrate_all_symbols_algos = MagicMock()  # sync 尾部 [Migrate]/algo 迁移入口
    ex.clear_symbol_halt = MagicMock(return_value=0)
    return ex


def _ex_pos(sym="XRP-USDT-SWAP", side="short"):
    # _fetch_positions_with_retry 返回的原始 ccxt 持仓格式
    return {"symbol": sym, "contracts": 3.7, "side": side, "leverage": 20,
            "notional": 74.0, "entryPrice": 1.13, "unrealizedPnl": 0.0}


def test_phantom_not_imported(monkeypatch):
    # 幽灵: tick1 交易所见到 XRP, tick2 消失 → 永不补录
    ex = _mk_executor()
    ex._fetch_positions_with_retry = MagicMock(side_effect=[[_ex_pos()], []])
    ex.sync_positions()                       # tick1
    assert "XRP-USDT-SWAP" not in ex.positions    # 未补录
    assert ex._pending_resync.get("XRP-USDT-SWAP") == 1
    ex.sync_positions()                       # tick2 幽灵消失
    assert "XRP-USDT-SWAP" not in ex.positions
    assert "XRP-USDT-SWAP" not in ex._pending_resync   # 计数清除


def test_real_position_imported_after_2_ticks():
    # 真仓: 连续 2 tick 都见 → 第 2 tick 补录
    ex = _mk_executor()
    ex._fetch_positions_with_retry = MagicMock(side_effect=[[_ex_pos()], [_ex_pos()]])
    ex.sync_positions()                       # tick1: pending
    assert "XRP-USDT-SWAP" not in ex.positions
    ex.sync_positions()                       # tick2: 补录
    assert "XRP-USDT-SWAP" in ex.positions
    assert "XRP-USDT-SWAP" not in ex._pending_resync


def test_cooldown_skips_resync():
    # 冷却期内交易所仍上报 → 跳过, 不计双确认 tick
    import time as _t
    ex = _mk_executor()
    ex._close_cooldown = {"XRP-USDT-SWAP": _t.time() + 60}
    ex._fetch_positions_with_retry = MagicMock(side_effect=[[_ex_pos()]])
    ex.sync_positions()
    assert "XRP-USDT-SWAP" not in ex.positions
    assert "XRP-USDT-SWAP" not in ex._pending_resync   # 冷却跳过, 不计 tick


def test_protection_unknown_error_deduped():
    # 同 symbol+reason 连续两个 tick protection-unknown → ERROR 仅首次
    ex = _mk_executor()
    ex._halt_symbol = MagicMock()
    ex._last_protection_alert = {}
    first = ex._alert_protection_unknown("XRP-USDT-SWAP")
    second = ex._alert_protection_unknown("XRP-USDT-SWAP")
    assert first is True and second is False        # 首次告警, 第二次去重静默
    assert ex.logger.error.call_count == 1


def test_protection_alert_resets_on_clear():
    ex = _mk_executor()
    ex._halt_symbol = MagicMock()
    ex._alert_protection_unknown("XRP-USDT-SWAP")
    ex._last_protection_alert.pop("XRP-USDT-SWAP", None)   # 状态恢复
    again = ex._alert_protection_unknown("XRP-USDT-SWAP")
    assert again is True                            # 恢复后能重新告警
