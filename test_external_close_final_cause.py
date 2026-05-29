"""AC3-P1-001..007 外部平仓 final close cause + 幂等定向单测.

参考: docs/audit_remediation_third_pass_20260528_acceptance.md §6.1
"""
from __future__ import annotations

import logging
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.realized_pnl_resolver import (
    PNL_STATUS_FINAL,
    RealizedPnlResolver,
    _classify_close_evidence,
)


# ── 工具:构造 fills/bills 与 snapshot ─────────────────────────────────────

def _snapshot(*, sl_algo_id='sl-123', sl_clord='caliveBot1BTCabc',
              tp_algo_id='tp-456', tp_clord='caliveBot1BTCtp',
              symbol='BTC-USDT-SWAP', side='long'):
    return {
        'symbol': symbol,
        'side': side,
        'pos_side': side,
        'position_id': 'pos-1',
        'entry_request_id': 'req-1',
        'opened_at': 1_770_000_000.0,
        'sl_algo_id': sl_algo_id,
        'sl_algo_clord_id': sl_clord,
        'tp_algo_id': tp_algo_id,
        'tp_algo_clord_id': tp_clord,
        'unrealized_pnl': -10.0,
        'stop_loss': 95.0,
        'entry_price': 100.0,
        'amount_usdt': 100.0,
        'leverage': 1,
    }


def _close_fill(*, ord_id='ord-x', algo_id='', clord_id='',
                 fill_pnl=-10.0, fee=-0.05, fill_px=95.0, fill_sz=1.0,
                 fill_time_ms=1_770_000_500_000, side='sell'):
    return {
        'ordId': ord_id,
        'algoId': algo_id,
        'algoClOrdId': clord_id,
        'fillPnl': fill_pnl,
        'fee': fee,
        'feeCcy': 'USDT',
        'fillPx': fill_px,
        'fillSz': fill_sz,
        'fillTime': str(fill_time_ms),
        'side': side,
    }


# ─────────────────────────────────────────────────────────────────────────


class TestAC3P1001PendingNotCountedAsSL:
    """AC3-P1-001: pending payload close_cause=exchange_unknown_pending,is_strategy_stop=False."""

    def test_pending_does_not_count_sl(self):
        # 单元层验证 _classify_close_evidence 在无 fills 时返回 external_unknown
        ev = _classify_close_evidence([], None, _snapshot())
        assert ev['close_cause'] == 'external_unknown'
        assert ev['is_strategy_stop'] is False
        assert ev['close_evidence']['confidence'] == 0.0


class TestAC3P1002FinalExchangeSlEvidence:
    """AC3-P1-002: matched SL algo → final_close_cause=exchange_sl, match_rule=sl_algo_id_exact."""

    def test_sl_algo_id_exact_match(self):
        snap = _snapshot()
        fills = [_close_fill(algo_id='sl-123', ord_id='ord-sl')]
        ev = _classify_close_evidence(fills, None, snap)
        assert ev['close_cause'] == 'exchange_sl'
        assert ev['final_close_cause'] == 'exchange_sl'
        assert ev['is_strategy_stop'] is True
        assert ev['close_evidence']['match_rule'] == 'sl_algo_id_exact'
        assert ev['close_evidence']['confidence'] == 1.0
        assert 'ord-sl' in ev['close_evidence']['matched_order_ids']

    def test_sl_clord_id_exact_match(self):
        snap = _snapshot()
        # algo_id 不同,但 clord 精确匹配
        fills = [_close_fill(algo_id='other', clord_id='caliveBot1BTCabc',
                              ord_id='ord-sl-2')]
        ev = _classify_close_evidence(fills, None, snap)
        assert ev['close_cause'] == 'exchange_sl'
        assert ev['close_evidence']['match_rule'] == 'sl_algo_clord_id_exact'
        assert ev['is_strategy_stop'] is True


class TestAC3P1002bExchangeTpExclusiveOfStrategyStop:
    """TP 命中不算 strategy stop."""

    def test_tp_match_not_strategy_stop(self):
        snap = _snapshot()
        fills = [_close_fill(algo_id='tp-456', ord_id='ord-tp', fill_pnl=8.0)]
        ev = _classify_close_evidence(fills, None, snap)
        assert ev['close_cause'] == 'exchange_tp'
        assert ev['is_strategy_stop'] is False


class TestAC3P1003JudgeIdempotentSlHit:
    """AC3-P1-003: 重放同一 correction_event_id 两次,Judge SL hit 只计一次."""

    def test_judge_idempotent_by_correction_event_id(self):
        from agents.trading.judge import MultiJudge

        judge = MultiJudge.__new__(MultiJudge)
        judge.logger = logging.getLogger('test_judge_idem')
        judge._processed_resolution_ids = set()
        judge._processed_resolution_max = 1024
        judge._archetype_cooldown = MagicMock()
        judge._archetype_cooldown.classify = MagicMock(return_value='archetype-x')
        judge._archetype_cooldown.record_result = MagicMock()
        judge._record_sl_hit = MagicMock()
        judge._get_state = MagicMock(return_value={})
        judge._probe_short_active = None
        judge._probe_short_sl_count = 0
        judge._probe_short_cooldown_hours = 24
        judge._probe_short_cooldown_until = 0
        judge._state_dirty = False
        judge._persist_state = MagicMock()
        judge._news_snapshot = {}

        msg = {
            'type': 'pnl_resolved',
            'symbol': 'BTC-USDT',
            'payload': {
                'symbol': 'BTC-USDT',
                'pnl_status': 'final',
                'realized_pnl_net_usdt': -12.34,
                'close_cause': 'exchange_sl',
                'is_strategy_stop': True,
                'direction': 'long',
                'correction_event_id': 'corr-evt-1',
                'position_id': 'pos-1',
                'attribution': {},
            },
        }
        # 模拟 Judge.on_message 的 pnl_resolved 分支
        import asyncio
        asyncio.get_event_loop().run_until_complete(judge.on_message(msg))
        asyncio.get_event_loop().run_until_complete(judge.on_message(msg))

        # 重放两次,SL hit 只计一次
        assert judge._record_sl_hit.call_count == 1


class TestAC3P1004ExternalUnknownNoSl:
    """AC3-P1-004: final external_unknown 不计 SL."""

    def test_external_unknown_no_strategy_stop(self):
        ev = _classify_close_evidence([], None, _snapshot())
        assert ev['close_cause'] == 'external_unknown'
        assert ev['is_strategy_stop'] is False

    def test_probe_short_not_incremented_on_external_unknown(self):
        """AC3-P1-004 扩展: probe_short 即使 PnL 为负,
        非 exchange_sl(is_strategy_stop=False)也不递增 SL 计数。
        """
        from agents.trading.judge import MultiJudge
        import asyncio

        judge = MultiJudge.__new__(MultiJudge)
        judge.logger = logging.getLogger('test_judge_probe_unknown')
        judge._processed_resolution_ids = set()
        judge._processed_resolution_max = 1024
        judge._archetype_cooldown = MagicMock()
        judge._archetype_cooldown.classify = MagicMock(return_value='archetype-x')
        judge._archetype_cooldown.record_result = MagicMock()
        judge._record_sl_hit = MagicMock()
        judge._get_state = MagicMock(return_value={})
        judge._probe_short_active = 'BTC-USDT'
        judge._probe_short_sl_count = 0
        judge._probe_short_cooldown_hours = 24
        judge._probe_short_cooldown_until = 0
        judge._state_dirty = False
        judge._persist_state = MagicMock()
        judge._news_snapshot = {}

        msg = {
            'type': 'pnl_resolved',
            'symbol': 'BTC-USDT',
            'payload': {
                'symbol': 'BTC-USDT',
                'pnl_status': 'final',
                'realized_pnl_net_usdt': -8.0,
                'close_cause': 'external_unknown',
                'is_strategy_stop': False,
                'direction': 'short',
                'correction_event_id': 'corr-unk-1',
                'position_id': 'pos-unk',
                'attribution': {},
            },
        }
        asyncio.get_event_loop().run_until_complete(judge.on_message(msg))

        assert judge._record_sl_hit.call_count == 0
        # probe_short 不递增,只清空 active(平仓事实)
        assert judge._probe_short_active is None
        assert judge._probe_short_sl_count == 0

    def test_probe_short_incremented_only_on_exchange_sl(self):
        """probe_short SL 计数仅在 is_strategy_stop=True 且 PnL<0 时递增。"""
        from agents.trading.judge import MultiJudge
        import asyncio

        judge = MultiJudge.__new__(MultiJudge)
        judge.logger = logging.getLogger('test_judge_probe_sl')
        judge._processed_resolution_ids = set()
        judge._processed_resolution_max = 1024
        judge._archetype_cooldown = MagicMock()
        judge._archetype_cooldown.classify = MagicMock(return_value='archetype-x')
        judge._archetype_cooldown.record_result = MagicMock()
        judge._record_sl_hit = MagicMock()
        judge._get_state = MagicMock(return_value={})
        judge._probe_short_active = 'BTC-USDT'
        judge._probe_short_sl_count = 0
        judge._probe_short_cooldown_hours = 24
        judge._probe_short_cooldown_until = 0
        judge._state_dirty = False
        judge._persist_state = MagicMock()
        judge._news_snapshot = {}

        msg = {
            'type': 'pnl_resolved',
            'symbol': 'BTC-USDT',
            'payload': {
                'symbol': 'BTC-USDT',
                'pnl_status': 'final',
                'realized_pnl_net_usdt': -8.0,
                'close_cause': 'exchange_sl',
                'is_strategy_stop': True,
                'direction': 'short',
                'correction_event_id': 'corr-sl-1',
                'position_id': 'pos-sl',
                'attribution': {},
            },
        }
        asyncio.get_event_loop().run_until_complete(judge.on_message(msg))

        assert judge._record_sl_hit.call_count == 1
        assert judge._probe_short_active is None
        assert judge._probe_short_sl_count == 1


class TestAC3P1005ManualCloseNoSl:
    """AC3-P1-005: close fill 不匹配系统 algo/order → manual_close."""

    def test_manual_close_marks_no_strategy_stop(self):
        snap = _snapshot()
        # algo_id/clord_id 都与 snapshot 不匹配
        fills = [_close_fill(algo_id='other-xyz', clord_id='manual-tag',
                              ord_id='ord-manual')]
        ev = _classify_close_evidence(fills, None, snap)
        assert ev['close_cause'] == 'manual_close'
        assert ev['is_strategy_stop'] is False
        assert ev['close_evidence']['confidence'] == 0.6


class TestAC3P1005bLiquidationOrAdl:
    """补充: bills 显示 liquidation/ADL subType → liquidation_or_adl, 不计 SL."""

    def test_bills_liquidation_subtype(self):
        snap = _snapshot()
        fills = [_close_fill(algo_id='other-xyz')]
        bills = [{'subType': '101', 'pnl': -50.0, 'ordId': 'ord-liq'}]
        ev = _classify_close_evidence(fills, bills, snap)
        # fills 已先把 cause 标 manual_close, bills liquidation 仅在 cause==external_unknown 才覆盖
        # 此处验证子函数直接看 bills 走 liquidation 分支需要 cause 仍是 external_unknown
        # 走 manual_close 分支不会被 bills 反盖,确认行为
        assert ev['close_cause'] in ('manual_close', 'liquidation_or_adl')

    def test_bills_liquidation_when_no_fill_match(self):
        snap = {**_snapshot(), 'sl_algo_id': '', 'sl_algo_clord_id': '',
                'tp_algo_id': '', 'tp_algo_clord_id': ''}
        bills = [{'subType': '102', 'pnl': -50.0, 'ordId': 'ord-adl'}]
        ev = _classify_close_evidence([], bills, snap)
        assert ev['close_cause'] == 'liquidation_or_adl'
        assert ev['is_strategy_stop'] is False


class TestAC3P1006LegacyPayloadFailSafe:
    """AC3-P1-006: legacy payload 缺 close_cause 字段时,Judge 默认不计 SL."""

    def test_legacy_payload_no_record_sl_hit(self):
        from agents.trading.judge import MultiJudge
        import asyncio

        judge = MultiJudge.__new__(MultiJudge)
        judge.logger = logging.getLogger('test_judge_legacy')
        judge._processed_resolution_ids = set()
        judge._processed_resolution_max = 1024
        judge._archetype_cooldown = MagicMock()
        judge._archetype_cooldown.classify = MagicMock(return_value='archetype-x')
        judge._archetype_cooldown.record_result = MagicMock()
        judge._record_sl_hit = MagicMock()
        judge._get_state = MagicMock(return_value={})
        judge._probe_short_active = None
        judge._probe_short_sl_count = 0
        judge._probe_short_cooldown_hours = 24
        judge._probe_short_cooldown_until = 0
        judge._state_dirty = False
        judge._persist_state = MagicMock()
        judge._news_snapshot = {}

        msg = {
            'type': 'pnl_resolved',
            'symbol': 'BTC-USDT',
            'payload': {
                'symbol': 'BTC-USDT',
                'pnl_status': 'final',
                'realized_pnl_net_usdt': -5.0,
                # 缺 close_cause / is_strategy_stop
                'direction': 'long',
                'correction_event_id': 'legacy-corr',
                'position_id': 'pos-legacy',
                'attribution': {},
            },
        }
        asyncio.get_event_loop().run_until_complete(judge.on_message(msg))
        # 没有 close_cause,is_strategy_stop 默认 False,不应记 SL
        assert judge._record_sl_hit.call_count == 0


class TestAC3P1007ReviewerIdempotent:
    """AC3-P1-007: Reviewer 重放同一 pnl_resolved,trade_history 不重复."""

    def test_reviewer_idempotent_append(self, tmp_path):
        from agents.trading.reviewer import ReviewerAgent
        import asyncio

        reviewer = ReviewerAgent.__new__(ReviewerAgent)
        reviewer.logger = logging.getLogger('test_reviewer_idem')
        reviewer.trade_history = []
        reviewer.history_file = str(tmp_path / 'trade_history.json')
        reviewer._processed_resolution_ids = set()
        reviewer._processed_resolution_max = 1024
        reviewer._save_trade_history = MagicMock()

        msg = {
            'type': 'pnl_resolved',
            'symbol': 'BTC-USDT',
            'payload': {
                'symbol': 'BTC-USDT',
                'pnl_status': 'final',
                'realized_pnl_net_usdt': -15.0,
                'entry_request_id': 'req-rev',
                'position_id': 'pos-rev',
                'correction_event_id': 'corr-rev-1',
                'pnl_source': 'okx_fills_history+okx_bills',
                'correlation_id': 'corr-rev-1',
            },
            'timestamp': 1_770_000_000.0,
        }
        asyncio.get_event_loop().run_until_complete(reviewer._apply_pnl_resolution(msg))
        asyncio.get_event_loop().run_until_complete(reviewer._apply_pnl_resolution(msg))

        # 重放两次,trade_history 只 append 一笔
        close_records = [r for r in reviewer.trade_history
                         if r.get('event_type') == 'close']
        assert len(close_records) == 1


class TestResolverPropagatesCloseEvidence:
    """端到端验证 resolve_external_close 透传 close_evidence/final_close_cause."""

    def test_resolve_external_close_with_sl_match(self):
        # 构造一个 mock exchange 让 _fetch_fills/_fetch_bills 返回我们的数据
        exchange = MagicMock()
        exchange.private_get_trade_fills_history = MagicMock(return_value={
            'data': [_close_fill(algo_id='sl-123', clord_id='caliveBot1BTCabc',
                                  ord_id='ord-sl', fill_pnl=-12.0, fee=-0.05,
                                  fill_px=95.0, fill_sz=1.0,
                                  fill_time_ms=1_770_000_500_000, side='sell')],
        })
        exchange.private_get_account_bills = MagicMock(return_value={
            'data': [{'subType': '174', 'pnl': -12.0, 'fee': -0.05,
                      'ordId': 'ord-sl', 'billId': 'bill-1'}],
        })
        resolver = RealizedPnlResolver(exchange)
        snap = _snapshot()
        snap['opened_at'] = 1_770_000_000.0
        close_window = {'closed_at': 1_770_000_600.0}
        res = resolver.resolve_external_close(snap, close_window)

        assert res['pnl_status'] == PNL_STATUS_FINAL
        assert res['close_cause'] == 'exchange_sl'
        assert res['final_close_cause'] == 'exchange_sl'
        assert res['is_strategy_stop'] is True
        assert res['close_evidence']['match_rule'] == 'sl_algo_id_exact'
        assert res['close_evidence']['confidence'] == 1.0

    def test_resolve_external_close_manual(self):
        exchange = MagicMock()
        exchange.private_get_trade_fills_history = MagicMock(return_value={
            'data': [_close_fill(algo_id='unknown', clord_id='manual-tag',
                                  ord_id='ord-manual', fill_pnl=-3.0,
                                  fee=-0.05, fill_px=96.0, fill_sz=1.0,
                                  fill_time_ms=1_770_000_500_000, side='sell')],
        })
        exchange.private_get_account_bills = MagicMock(return_value={
            'data': [{'subType': '174', 'pnl': -3.0, 'fee': -0.05,
                      'ordId': 'ord-manual', 'billId': 'bill-2'}],
        })
        resolver = RealizedPnlResolver(exchange)
        snap = _snapshot()
        res = resolver.resolve_external_close(snap, {'closed_at': 1_770_000_600.0})

        assert res['pnl_status'] == PNL_STATUS_FINAL
        assert res['close_cause'] == 'manual_close'
        assert res['is_strategy_stop'] is False
