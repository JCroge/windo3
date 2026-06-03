"""Paper Executor — Limit Fill Paths + Tick Staleness Gating

Covers paper-executor/spec.md Req1-Req8 / all 22 scenarios.
Uses freezegun to control time; _MockBus to capture risk_alert publishes.

Steps 6.1-6.16 from docs/superpowers/plans/2026-06-03-pullback-entry-paper-parity.md
"""

import asyncio
import json
import pytest
from datetime import timedelta
from freezegun import freeze_time
from agents.trading.paper_executor import PaperExecutor


def _isolate_paper_files(tmp_path, monkeypatch):
    """Pin paper_executor module constants to tmp_path so an earlier test
    that monkey-patched them globally (test_paper_executor.py at repo root)
    cannot leak state into our fixture."""
    import agents.trading.paper_executor as pe_mod
    data_dir = tmp_path / 'data'
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(pe_mod, 'PAPER_TRADES_FILE', str(data_dir / 'paper_trades.jsonl'))
    monkeypatch.setattr(pe_mod, 'PAPER_POSITIONS_FILE', str(data_dir / 'paper_positions.json'))
    monkeypatch.setattr(pe_mod, 'PAPER_EQUITY_FILE', str(data_dir / 'paper_equity.json'))


class _MockBus:
    def __init__(self):
        self.published = []

    async def publish(self, sender, msg_type, payload, to='broadcast', symbol=None):
        self.published.append({'type': msg_type, 'payload': payload, 'symbol': symbol})


@pytest.fixture
def pe(tmp_path, monkeypatch):
    _isolate_paper_files(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    executor = PaperExecutor({
        'effective_balance_cap': 1000.0,
        'min_confidence': 60,
        'max_trade_amount': 30,
        'paper_limit_tick_staleness_sec': 60,
    })
    executor.bus = _MockBus()
    asyncio.get_event_loop().run_until_complete(executor.setup())
    return executor


def _decision(action='open_short', confidence=70, request_id='REQ-1',
              symbol='WLD-USDT', plan=None):
    return {
        'type': 'trade_decision',
        'symbol': symbol,
        'payload': {
            'action': action, 'confidence': confidence, 'symbol': symbol,
            'plan': plan, 'request_id': request_id,
            'attribution': {'entry_type': 'ma_aligned'},
        },
    }


def _limit_plan(side='short', entry_low=0.4043, entry_high=0.4047,
                timeout_sec=1800, no_fallback=True):
    sl = 0.4362 if side == 'short' else 0.3700
    tp = 0.3411 if side == 'short' else 0.4500
    return {
        'side': side,
        'entry_zone': [entry_low, entry_high],
        'order_type': 'limit',
        'limit_timeout_sec': timeout_sec,
        'limit_no_fallback': no_fallback,
        'size_usdt': 30, 'leverage': 10,
        'stop_loss': sl, 'take_profit': [tp],
        'atr_pct': 0.0392,
    }


async def _send(executor, msg):
    await executor.on_message(msg)


# ---------------------------------------------------------------------------
# Step 6.1 — Limit plan enters _pending_limits, no immediate position
# Spec: Req1 Scenario 1 — "Limit plan defers to wait_paper_limit_fill"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_limit_plan_enters_pending_not_position(pe):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    assert 'WLD-USDT' in pe._pending_limits
    assert 'WLD-USDT' not in pe._positions
    assert pe._pending_limits['WLD-USDT']['limit_no_fallback'] is True


# ---------------------------------------------------------------------------
# Step 6.2 — Market plan keeps legacy immediate fill, entry_method='market'
# Spec: Req1 Scenario 2 + Req4 Scenario 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_market_plan_immediate_fill(pe):
    plan = _limit_plan()
    plan['order_type'] = 'market'
    pe._latest_price['WLD-USDT'] = 0.40
    await _send(pe, _decision(plan=plan))
    assert 'WLD-USDT' in pe._positions
    assert pe._positions['WLD-USDT']['entry_method'] == 'market'


# ---------------------------------------------------------------------------
# Step 6.3 — Limit plan with empty entry_zone fails over to market
# Spec: Req1 Scenario 3 — fail-safe, never silently drop trade_decision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_limit_plan_missing_entry_zone_falls_back_to_market(pe):
    plan = _limit_plan()
    plan['entry_zone'] = []
    pe._latest_price['WLD-USDT'] = 0.40
    await _send(pe, _decision(plan=plan))
    assert 'WLD-USDT' in pe._positions
    assert pe._positions['WLD-USDT']['entry_method'] == 'market'
    assert 'WLD-USDT' not in pe._pending_limits


# ---------------------------------------------------------------------------
# Step 6.4 — Tick inside zone fills at midpoint, entry_method='limit_filled'
# Spec: Req2 Scenario 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tick_inside_zone_fills_at_midpoint(pe):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    await _send(pe, {'type': 'price_tick', 'symbol': 'WLD-USDT',
                     'payload': {'symbol': 'WLD-USDT', 'price': 0.4044}})
    assert 'WLD-USDT' in pe._positions
    pos = pe._positions['WLD-USDT']
    assert abs(pos['entry_price'] - 0.4045) < 1e-9
    assert pos['entry_method'] == 'limit_filled'
    assert 'WLD-USDT' not in pe._pending_limits


# ---------------------------------------------------------------------------
# Step 6.5 — Tick crossing zone instantaneously still fills
# Spec: Req2 Scenario 2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tick_crossing_zone_fills(pe):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    for price in (0.4042, 0.4046, 0.4060):
        await _send(pe, {'type': 'price_tick', 'symbol': 'WLD-USDT',
                         'payload': {'symbol': 'WLD-USDT', 'price': price}})
    assert pe._positions['WLD-USDT']['entry_method'] == 'limit_filled'


# ---------------------------------------------------------------------------
# Step 6.6 — Timeout no_fallback=True → paper_unfilled risk_alert
# Spec: Req3 Scenario 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_no_fallback_paper_unfilled(pe):
    plan = _limit_plan(no_fallback=True)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await _send(pe, _decision(plan=plan))
        frozen.tick(delta=timedelta(seconds=1801))
        await pe._scan_pending_limits()
    assert 'WLD-USDT' not in pe._positions
    assert 'WLD-USDT' not in pe._pending_limits
    alerts = [m for m in pe.bus.published if m['type'] == 'risk_alert']
    assert len(alerts) == 1
    a = alerts[0]['payload']
    assert a['type'] == 'paper_unfilled'
    assert a['source'] == 'paper_executor'
    assert 'subtype' not in a
    rejected = [r for r in pe._rejected_log if r['reason'] == 'paper_unfilled']
    assert len(rejected) == 1
    assert rejected[0]['entry_method'] == 'limit_unfilled'


# ---------------------------------------------------------------------------
# Step 6.7 — Timeout no_fallback=False, fresh tick → market fallback, no alert
# Spec: Req3 Scenario 2 + Req7 Scenario 2 (fresh tick allows fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_fallback_market_with_fresh_tick(pe):
    plan = _limit_plan(no_fallback=False)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await _send(pe, _decision(plan=plan))
        # tick arrives 1750s in (50s before deadline) — well within 60s staleness window
        frozen.tick(delta=timedelta(seconds=1750))
        await _send(pe, {'type': 'price_tick', 'symbol': 'WLD-USDT',
                         'payload': {'symbol': 'WLD-USDT', 'price': 0.4100}})
        frozen.tick(delta=timedelta(seconds=51))
        await pe._scan_pending_limits()
    assert 'WLD-USDT' in pe._positions
    assert pe._positions['WLD-USDT']['entry_method'] == 'market'
    assert abs(pe._positions['WLD-USDT']['entry_price'] - 0.4100) < 1e-9
    alerts = [m for m in pe.bus.published if m['type'] == 'risk_alert']
    assert not alerts  # success path — no alert


# ---------------------------------------------------------------------------
# Step 6.8 — Timeout no_fallback=False, no tick at all → no_tick rejection
# Spec: Req3 Scenario 4 + Req7 Scenario 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_fallback_no_tick(pe):
    plan = _limit_plan(no_fallback=False)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await _send(pe, _decision(plan=plan))
        frozen.tick(delta=timedelta(seconds=1801))
        await pe._scan_pending_limits()
    assert 'WLD-USDT' not in pe._positions
    alerts = [m for m in pe.bus.published if m['type'] == 'risk_alert']
    paper = [a for a in alerts if a['payload']['type'] == 'paper_unfilled']
    assert paper and paper[0]['payload'].get('subtype') == 'no_tick'


# ---------------------------------------------------------------------------
# Step 6.9 — Timeout no_fallback=False, stale tick → no_tick rejection
# Spec: Req7 Scenario 1 (stale tick gated out)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_fallback_stale_tick(pe):
    plan = _limit_plan(no_fallback=False)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await _send(pe, _decision(plan=plan))
        # Tick at t=0 — then freeze advances 1801s, making tick 1801s old (>> 60s staleness)
        await _send(pe, {'type': 'price_tick', 'symbol': 'WLD-USDT',
                         'payload': {'symbol': 'WLD-USDT', 'price': 0.4100}})
        frozen.tick(delta=timedelta(seconds=1801))
        await pe._scan_pending_limits()
    assert 'WLD-USDT' not in pe._positions
    alerts = [m for m in pe.bus.published if m['type'] == 'risk_alert']
    paper = [a for a in alerts if a['payload']['type'] == 'paper_unfilled']
    assert paper and paper[0]['payload'].get('subtype') == 'no_tick'


# ---------------------------------------------------------------------------
# Step 6.10 — Custom staleness threshold is honored
# Spec: Req7 Scenario 3
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_custom_staleness_threshold(tmp_path, monkeypatch):
    _isolate_paper_files(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    executor = PaperExecutor({
        'effective_balance_cap': 1000.0, 'min_confidence': 60,
        'max_trade_amount': 30, 'paper_limit_tick_staleness_sec': 120,
    })
    executor.bus = _MockBus()
    await executor.setup()
    assert executor._tick_staleness_sec == 120.0

    plan = _limit_plan(no_fallback=False, timeout_sec=600)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await executor.on_message(_decision(plan=plan))
        # Tick arrives at t=500s (100s before deadline); it will be 101s old at scan time
        frozen.tick(delta=timedelta(seconds=500))
        await executor.on_message({'type': 'price_tick', 'symbol': 'WLD-USDT',
                                   'payload': {'symbol': 'WLD-USDT', 'price': 0.4100}})
        # Now advance to t=601 (1s past deadline); tick is 101s old < 120s staleness threshold
        frozen.tick(delta=timedelta(seconds=101))
        await executor._scan_pending_limits()
    # 101s < 120s → tick is fresh → market fallback succeeds
    assert executor._positions['WLD-USDT']['entry_method'] == 'market'


# ---------------------------------------------------------------------------
# Step 6.11 — Duplicate open during pending is skipped (first wins)
# Spec: Req5 Scenario 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_open_during_pending_skipped(pe):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan, request_id='REQ-1'))
    await _send(pe, _decision(plan=plan, request_id='REQ-2'))
    assert pe._pending_limits['WLD-USDT']['decision']['request_id'] == 'REQ-1'


# ---------------------------------------------------------------------------
# Step 6.12 — Close cancels pending without opening a position
# Spec: Req6 Scenario 2 (close drops pending limit cleanly)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_cancels_pending(pe):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    assert 'WLD-USDT' in pe._pending_limits
    close_decision = {
        'type': 'trade_decision', 'symbol': 'WLD-USDT',
        'payload': {'action': 'close', 'confidence': 0, 'symbol': 'WLD-USDT'},
    }
    await _send(pe, close_decision)
    assert 'WLD-USDT' not in pe._pending_limits
    assert 'WLD-USDT' not in pe._positions


# ---------------------------------------------------------------------------
# Step 6.13 — Restart drops _pending_limits (no persistence)
# Spec: Req6 Scenario 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restart_drops_pending_limits(tmp_path, monkeypatch):
    _isolate_paper_files(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    cfg = {'effective_balance_cap': 1000.0, 'min_confidence': 60, 'max_trade_amount': 30}

    pe1 = PaperExecutor(cfg)
    pe1.bus = _MockBus()
    await pe1.setup()
    plan = _limit_plan()
    await pe1.on_message(_decision(plan=plan))
    assert 'WLD-USDT' in pe1._pending_limits
    pe1._persist_state()

    pe2 = PaperExecutor(cfg)
    pe2.bus = _MockBus()
    await pe2.setup()
    assert pe2._pending_limits == {}


# ---------------------------------------------------------------------------
# Step 6.14 — _persist_state does NOT serialize _pending_limits
# Spec: Req6 Scenario 1 (write side — positions file stays clean)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_state_does_not_serialize_pending(pe, tmp_path):
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    pe._persist_state()
    with open('data/paper_positions.json') as f:
        data = json.load(f)
    assert 'pending_limits' not in data
    # No position was opened yet — should be empty dict
    assert data == {}


# ---------------------------------------------------------------------------
# Step 6.15 — Close trade record carries entry_method; legacy positions default to 'market'
# Spec: Req4 Scenario 2 (close event) + Req4 Scenario 3 (legacy fail-safe)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_record_carries_entry_method_and_legacy_default(pe):
    # Path A: limit_filled propagates to close record
    plan = _limit_plan()
    await _send(pe, _decision(plan=plan))
    await _send(pe, {'type': 'price_tick', 'symbol': 'WLD-USDT',
                     'payload': {'symbol': 'WLD-USDT', 'price': 0.4044}})
    assert pe._positions['WLD-USDT']['entry_method'] == 'limit_filled'
    await pe._close_paper('WLD-USDT', pe._positions['WLD-USDT'], reason='manual')
    with open('data/paper_trades.jsonl') as f:
        rec = json.loads(f.readlines()[-1])
    assert rec['entry_method'] == 'limit_filled'

    # Path B: legacy position missing entry_method → 'market' default on close
    pe._positions['LEGACY-USDT'] = {
        'symbol': 'LEGACY-USDT', 'side': 'long', 'entry_price': 1.0,
        'sl': 0.95, 'tp': 1.1, 'margin': 30, 'leverage': 10, 'notional': 300,
        'opened_at': 1.0, 'entry_fee': 0.3, 'atr_pct': 0.02,
        # NO entry_method field — simulates a pre-change position record
    }
    pe._latest_price['LEGACY-USDT'] = 1.05
    await pe._close_paper('LEGACY-USDT', pe._positions['LEGACY-USDT'], reason='manual')
    with open('data/paper_trades.jsonl') as f:
        rec = json.loads(f.readlines()[-1])
    assert rec['entry_method'] == 'market'  # fail-safe default


# ---------------------------------------------------------------------------
# Step 6.16 — Cleanup loop runs in tick(), processes deadline-elapsed entries
# Spec: Req8 Scenario 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_loop_runs_each_tick(pe, monkeypatch):
    plan = _limit_plan(timeout_sec=10)
    # Patch asyncio.sleep to a no-op so tick() doesn't actually sleep 30s.
    # Capture the real coroutine function first to avoid self-referential recursion.
    import asyncio as _asyncio
    _real_sleep = _asyncio.sleep

    async def _noop_sleep(*_a, **_k):
        await _real_sleep(0)

    monkeypatch.setattr(_asyncio, 'sleep', _noop_sleep)
    with freeze_time("2026-06-03 12:00:00") as frozen:
        await _send(pe, _decision(plan=plan))
        assert 'WLD-USDT' in pe._pending_limits
        frozen.tick(delta=timedelta(seconds=11))
        await pe.tick()
    assert 'WLD-USDT' not in pe._pending_limits


# ---------------------------------------------------------------------------
# Step 6.17 — Cleanup loop with empty pending limits is no-op
# Spec: Req8 Scenario 2 — "Empty pending limits does no I/O / publish"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_loop_empty_pending_no_op(pe, monkeypatch):
    """Req8 Scenario 2: empty _pending_limits → tick() does no I/O / publish."""
    import asyncio as _asyncio
    _real_sleep = _asyncio.sleep
    async def _noop_sleep(*_a, **_k):
        await _real_sleep(0)
    monkeypatch.setattr(_asyncio, 'sleep', _noop_sleep)
    assert pe._pending_limits == {}
    pe.bus.published.clear()
    await pe.tick()
    risk_alerts = [m for m in pe.bus.published if m['type'] == 'risk_alert']
    paper_results = [m for m in pe.bus.published if m['type'] == 'paper_execution_result']
    assert risk_alerts == []
    assert paper_results == []
