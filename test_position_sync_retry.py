import time
from unittest.mock import MagicMock
import ccxt
import pytest
from executor import ContractExecutor


def _exec():
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.exchange = MagicMock()
    ex.logger = MagicMock()
    ex.positions = {}
    ex._last_sync_result = None
    return ex


def test_transient_then_success(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)  # 不真睡
    ex = _exec()
    ex.exchange.fetch_positions.side_effect = [ccxt.RequestTimeout("okx GET ..."), [{"ok": 1}]]
    result = ex._fetch_positions_with_retry()
    assert result == [{"ok": 1}]
    assert ex.logger.warning.call_count == 1          # 一次瞬时重试 WARNING
    ex.logger.error.assert_not_called()


def test_transient_exhausts_raises(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    ex = _exec()
    ex.exchange.fetch_positions.side_effect = ccxt.RequestTimeout("okx GET ...")
    with pytest.raises(ccxt.NetworkError):
        ex._fetch_positions_with_retry()
    assert ex.exchange.fetch_positions.call_count == 3  # 有界 3 次
    assert ex.logger.warning.call_count == 3
    ex.logger.error.assert_not_called()                 # ERROR 由外层 sync_positions 记


def test_non_transient_not_retried(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    ex = _exec()
    ex.exchange.fetch_positions.side_effect = ccxt.AuthenticationError("bad key")
    with pytest.raises(ccxt.AuthenticationError):
        ex._fetch_positions_with_retry()
    assert ex.exchange.fetch_positions.call_count == 1  # 不重试
    ex.logger.warning.assert_not_called()


def test_sync_positions_error_includes_type():
    ex = _exec()
    ex.positions = {"BTC-USDT-SWAP": {"symbol": "BTC-USDT-SWAP", "contracts": 1}}
    ex._fetch_positions_with_retry = MagicMock(
        side_effect=ccxt.RequestTimeout("okx GET .../positions"))
    result = ex.sync_positions()
    ex.logger.error.assert_called_once()
    msg = ex.logger.error.call_args[0][0]
    assert "仓位同步失败" in msg and "RequestTimeout" in msg  # ERROR 带异常类型
    assert ex._last_sync_result == []
    assert result == ex.positions                            # 本地持仓保留
