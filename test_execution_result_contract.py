"""统一 execution_result 契约测试 — 验证所有非 open 路径发布 v2 schema"""

import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from agents.trading.executor import MultiExecutor


V2_REQUIRED_FIELDS = {'schema_version', 'status', 'action', 'symbol', 'source',
                      'request_id', 'correlation_id', 'reason', 'result', 'timestamp'}


def _make_executor():
    config = {'exchange': 'okx', 'leverage': 3, 'min_confidence': 60}
    ex = MultiExecutor(config)
    ex.executor = MagicMock()
    ex.executor._normalize_symbol = lambda s: s
    ex.executor.positions = {}
    ex.executor.get_all_positions = MagicMock(return_value={})
    ex.executor.get_position = MagicMock(return_value=None)
    ex.executor.close_position = MagicMock(return_value={'pnl': -5.0, 'symbol': 'BTC-USDT'})
    ex.executor.reduce_position = MagicMock(return_value={'realized_pnl': -2.0})
    ex.executor.cancel_order = MagicMock()
    ex.executor.get_newly_synced = MagicMock(return_value=[])
    ex.executor.get_removed_symbols = MagicMock(return_value=[])
    ex.executor.get_removed_positions_data = MagicMock(return_value=[])
    ex.publish = AsyncMock()
    ex.logger = MagicMock()
    return ex


def _assert_v2(payload: dict, expected_source: str, expected_status: str):
    missing = V2_REQUIRED_FIELDS - set(payload.keys())
    assert not missing, f"Missing v2 fields: {missing}"
    assert payload['schema_version'] == 'execution_result.v2'
    assert payload['source'] == expected_source
    assert payload['status'] == expected_status
    assert isinstance(payload['timestamp'], float)
    assert payload['request_id'] or payload['correlation_id'], "Must have request_id or correlation_id"


# --- risk_alert: emergency_close ---

@pytest.mark.asyncio
async def test_risk_alert_emergency_close_v2():
    ex = _make_executor()
    ex.executor.get_position = MagicMock(return_value={'side': 'long'})
    ex.executor.positions = {'BTC-USDT': {'side': 'long', 'sl_order_id': 'sl1', 'request_id': 'req-123'}}

    await ex._handle_risk_alert({'type': 'emergency_close', 'symbol': 'BTC-USDT', 'reason': 'daily_hard_stop'})

    ex.publish.assert_called_once()
    args = ex.publish.call_args
    payload = args[0][1]
    _assert_v2(payload, expected_source='risk_alert', expected_status='force_closed')
    assert payload['action'] == 'close'
    assert payload['symbol'] == 'BTC-USDT'
    assert payload['reason'] == 'daily_hard_stop'
    assert payload['request_id'] == 'req-123'
    assert payload['result']['entry_request_id'] == 'req-123'


# --- risk_alert: flash_move ---

@pytest.mark.asyncio
async def test_risk_alert_flash_move_v2():
    ex = _make_executor()
    ex.executor.get_position = MagicMock(return_value={'side': 'short'})
    ex.executor.positions = {'ETH-USDT': {'side': 'short', 'sl_order_id': 'sl2', 'request_id': ''}}

    await ex._handle_risk_alert({'type': 'flash_move', 'symbol': 'ETH-USDT', 'scope': 'symbol'})

    ex.publish.assert_called_once()
    payload = ex.publish.call_args[0][1]
    _assert_v2(payload, expected_source='risk_alert', expected_status='force_closed')
    assert payload['reason'] == 'flash_move'
    assert payload['correlation_id']  # no request_id → must have correlation_id


# --- risk_alert: position_danger ---

@pytest.mark.asyncio
async def test_risk_alert_position_danger_v2():
    ex = _make_executor()
    ex.executor.get_position = MagicMock(return_value={'side': 'long'})
    ex.executor.positions = {'SOL-USDT': {'side': 'long', 'sl_order_id': None, 'request_id': 'req-sol'}}

    await ex._handle_risk_alert({'type': 'position_danger', 'symbol': 'SOL-USDT'})

    payload = ex.publish.call_args[0][1]
    _assert_v2(payload, expected_source='risk_alert', expected_status='force_closed')
    assert payload['reason'] == 'position_danger'


# --- risk_alert: portfolio_exposure (reduce) ---

@pytest.mark.asyncio
async def test_risk_alert_portfolio_reduce_v2():
    ex = _make_executor()
    ex.executor.get_all_positions = MagicMock(return_value={
        'BTC-USDT': {'amount_usdt': 500, 'request_id': 'req-btc'},
        'ETH-USDT': {'amount_usdt': 200, 'request_id': 'req-eth'},
    })
    # F4-001: reduce_position must return structured result with reduce_ok=True
    ex.executor.reduce_position = MagicMock(return_value={
        'reduce_ok': True, 'ok': True,
        'protective_update_state': 'protected',
        'protection_state': 'protected',
        'actual_reduce_amount': 250.0,
        'requested_reduce_amount': 500.0,
    })

    await ex._handle_risk_alert({'type': 'portfolio_exposure'})

    payload = ex.publish.call_args[0][1]
    _assert_v2(payload, expected_source='risk_alert', expected_status='risk_reduced')
    assert payload['action'] == 'reduce'
    assert payload['reduce_pct'] == pytest.approx(0.25)  # (250/500)*0.5
    assert payload['symbol'] == 'BTC-USDT'


# --- close_all ---

@pytest.mark.asyncio
async def test_close_all_v2():
    ex = _make_executor()
    ex.executor.get_all_positions = MagicMock(return_value={
        'BTC-USDT': {'sl_order_id': 'sl1', 'request_id': 'req-1'},
        'ETH-USDT': {'sl_order_id': None, 'request_id': 'req-2'},
    })
    ex.executor.cancel_order = MagicMock()
    ex.executor.close_position = MagicMock(return_value={'pnl': -3.0})

    await ex._close_all_positions("daily_hard_stop_daily_loss_limit")

    assert ex.publish.call_count == 2
    for call in ex.publish.call_args_list:
        payload = call[0][1]
        _assert_v2(payload, expected_source='close_all', expected_status='force_closed')
        assert payload['reason'] == 'daily_hard_stop_daily_loss_limit'


# --- sync ---

@pytest.mark.asyncio
async def test_notify_synced_v2():
    ex = _make_executor()
    ex.executor.get_newly_synced = MagicMock(return_value=[
        {'symbol': 'DOGE-USDT', 'side': 'long', 'amount_usdt': 50}
    ])

    await ex._notify_synced_positions()

    payload = ex.publish.call_args[0][1]
    _assert_v2(payload, expected_source='sync', expected_status='executed')
    assert payload['action'] == 'open_long'
    assert payload['correlation_id']  # no request_id for synced positions


# --- external_close ---

@pytest.mark.asyncio
async def test_notify_removed_v2():
    ex = _make_executor()
    ex.executor.get_removed_symbols = MagicMock(return_value=['ZEC-USDT'])
    ex.executor.get_removed_positions_data = MagicMock(return_value=[
        {'symbol': 'ZEC-USDT', 'side': 'short', 'entry_price': 30.0,
         'amount_usdt': 100, 'request_id': 'req-zec', 'attribution': {}}
    ])
    ex.executor.ledger = None

    await ex._notify_removed_positions()

    payload = ex.publish.call_args[0][1]
    _assert_v2(payload, expected_source='external_close', expected_status='closed_externally')
    assert payload['action'] == 'close'
    assert payload['result']['entry_request_id'] == 'req-zec'
    assert payload['request_id'] == 'req-zec'


# --- local_stop: price_fetch_failed ---

@pytest.mark.asyncio
async def test_local_stop_price_failed_v2():
    ex = _make_executor()
    ex.executor.get_all_positions = MagicMock(return_value={'BTC-USDT': {'request_id': 'req-x'}})
    ex.executor.positions = {'BTC-USDT': {'sl_order_id': 'sl9', 'request_id': 'req-x'}}
    ex.executor.check_stop_loss_take_profit = MagicMock(return_value='price_fetch_failed')
    ex.executor.close_position = MagicMock(return_value={'pnl': -10.0})
    ex.config = {'early_review_enabled': False}

    await ex._check_all_positions()

    payload = ex.publish.call_args[0][1]
    _assert_v2(payload, expected_source='local_stop', expected_status='force_closed')
    assert payload['reason'] == 'price_fetch_failed'


# --- partial_tp ---

@pytest.mark.asyncio
async def test_partial_tp_v2():
    ex = _make_executor()
    ex.executor.get_all_positions = MagicMock(return_value={'BTC-USDT': {'request_id': 'req-tp'}})
    ex.executor.positions = {'BTC-USDT': {'request_id': 'req-tp'}}
    ex.executor.check_stop_loss_take_profit = MagicMock(return_value='partial_tp_1')
    # F4-001: reduce_position must return structured result with reduce_ok=True
    ex.executor.reduce_position = MagicMock(return_value={
        'reduce_ok': True, 'ok': True,
        'protective_update_state': 'protected',
        'protection_state': 'protected',
        'actual_reduce_amount': 50.0,
        'requested_reduce_amount': 100.0,
        'realized_pnl': 5.0,
    })
    ex.config = {'early_review_enabled': False}

    await ex._check_all_positions()

    payload = ex.publish.call_args[0][1]
    _assert_v2(payload, expected_source='partial_tp', expected_status='risk_reduced')
    assert payload['reduce_pct'] == pytest.approx(0.25)  # (50/100)*0.5
    assert payload['reason'] == 'partial_tp_1'


# --- local_stop: SL trigger ---

@pytest.mark.asyncio
async def test_local_stop_sl_trigger_v2():
    ex = _make_executor()
    ex.executor.get_all_positions = MagicMock(return_value={'ETH-USDT': {'request_id': 'req-sl'}})
    ex.executor.positions = {'ETH-USDT': {'sl_order_id': 'sl-eth', 'request_id': 'req-sl'}}
    ex.executor.check_stop_loss_take_profit = MagicMock(return_value='stop_loss')
    ex.executor.close_position = MagicMock(return_value={'pnl': -8.0})
    ex.config = {'early_review_enabled': False}

    await ex._check_all_positions()

    payload = ex.publish.call_args[0][1]
    _assert_v2(payload, expected_source='local_stop', expected_status='force_closed')
    assert payload['reason'] == 'stop_loss'


# --- Reviewer compatibility ---

@pytest.mark.asyncio
async def test_reviewer_consumes_all_sources():
    """Reviewer should not crash on any source type"""
    from agents.trading.reviewer import ReviewerAgent

    reviewer = ReviewerAgent({'data_dir': '/tmp/test_reviewer_contract'})
    reviewer.trade_history = []
    reviewer._save_trade_history = MagicMock()
    reviewer.logger = MagicMock()
    reviewer.publish = AsyncMock()
    reviewer._hard_stop_triggered_date = ''

    sources = [
        ('risk_alert', 'force_closed'),
        ('close_all', 'force_closed'),
        ('external_close', 'closed_externally'),
        ('local_stop', 'force_closed'),
        ('partial_tp', 'risk_reduced'),
    ]

    for source, status in sources:
        msg = {
            'type': 'execution_result',
            'timestamp': time.time(),
            'symbol': 'BTC-USDT',
            'payload': {
                'schema_version': 'execution_result.v2',
                'status': status,
                'action': 'close',
                'symbol': 'BTC-USDT',
                'source': source,
                'request_id': 'req-test',
                'correlation_id': '',
                'reason': 'test',
                'result': {'pnl': -1.0, 'entry_request_id': 'req-test'},
                'timestamp': time.time(),
                'reduce_pct': 0.5 if status == 'risk_reduced' else None,
            }
        }
        await reviewer._process_trade_result(msg)

    # risk_reduced with pnl records + force_closed/closed_externally records
    assert len(reviewer.trade_history) >= 4
    for record in reviewer.trade_history:
        assert 'source' in record or record.get('event_type') == 'reduce'
