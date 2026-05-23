"""AC-04: request_id全链路测试"""
import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock
from agents.trading.judge import MultiJudge


def _make_judge():
    judge = MultiJudge.__new__(MultiJudge)
    judge._confidence_split_enabled = False
    judge._trend_saturation_enabled = False
    judge._momentum_probe_long_enabled = False
    judge._bucketed_ev_enabled = False
    judge._request_id_enabled = True
    judge._probe_long_max_concurrent = 1
    judge._probe_short_max_concurrent = 1
    judge._max_concurrent_positions = 3
    judge._open_positions = set()
    judge._pending_open_symbols = set()
    judge._pending_open_ts = {}
    judge._pending_open_slots = {}
    judge._position_slots = {}
    judge._rank_flush_task = None
    judge._rank_flush_delay = 5.0
    judge._symbol_states = {}
    judge.publish = AsyncMock()
    judge.logger = MagicMock()

    class _MockRegime:
        _effective_regime = 'mixed'
        def snapshot(self):
            return {'effective_regime': 'mixed', 'raw_regime': 'mixed', 'confidence': 50}
    judge._regime_manager = _MockRegime()

    class _MockLedger:
        _enabled = False
    judge._counterfactual_ledger = _MockLedger()

    from utils.candidate_ranker import CandidateRanker
    judge._candidate_ranker = CandidateRanker(max_slots=3, enabled=False)
    return judge


class TestRequestIdFlow:
    @pytest.mark.asyncio
    async def test_dispatcher_generates_request_id(self):
        """_gate_and_publish_open must generate request_id"""
        judge = _make_judge()
        decision = {
            'symbol': 'BTC-USDT', 'action': 'open_long', 'confidence': 70,
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
            'attribution': {},
        }
        result = await judge._gate_and_publish_open('BTC-USDT', decision, {})
        assert result is True
        assert 'request_id' in decision
        assert len(decision['request_id']) > 10
        assert 'BTC' in decision['request_id']
        assert decision['attribution']['request_id'] == decision['request_id']

    @pytest.mark.asyncio
    async def test_request_id_format(self):
        """request_id format: YYYYMMDD-SYMBOL-uuid8"""
        judge = _make_judge()
        decision = {
            'symbol': 'ETH-USDT', 'action': 'open_short', 'confidence': 65,
            'plan': {'slot_type': 'main', 'size_usdt': 5, 'leverage': 2},
            'attribution': {},
        }
        await judge._gate_and_publish_open('ETH-USDT', decision, {})
        req_id = decision['request_id']
        parts = req_id.split('-')
        assert len(parts) == 3
        assert len(parts[0]) == 8  # YYYYMMDD
        assert parts[1] == 'ETH'
        assert len(parts[2]) == 8  # uuid8

    @pytest.mark.asyncio
    async def test_schema_version_present(self):
        """open decision must have schema_version"""
        judge = _make_judge()
        decision = {
            'symbol': 'SOL-USDT', 'action': 'open_long', 'confidence': 60,
            'plan': {'slot_type': 'main', 'size_usdt': 5, 'leverage': 2},
            'attribution': {},
        }
        await judge._gate_and_publish_open('SOL-USDT', decision, {})
        assert decision['schema_version'] == 'trade_decision.v2'

    @pytest.mark.asyncio
    async def test_split_fields_present(self):
        """open decision must have signal_score, execution_confidence, position_scale"""
        judge = _make_judge()
        decision = {
            'symbol': 'BTC-USDT', 'action': 'open_long', 'confidence': 70,
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
            'attribution': {'signal_score': 65, 'execution_confidence': 70, 'position_scale': 0.8},
        }
        await judge._gate_and_publish_open('BTC-USDT', decision, {})
        assert decision['signal_score'] == 65
        assert decision['execution_confidence'] == 70
        assert decision['position_scale'] == 0.8

    @pytest.mark.asyncio
    async def test_rejected_slot_full_has_request_id_in_attribution(self):
        """Slot full rejection attribution must have dispatch_path"""
        judge = _make_judge()
        judge._open_positions = {'A-USDT', 'B-USDT', 'C-USDT'}
        judge._position_slots = {'A-USDT': 'main', 'B-USDT': 'main', 'C-USDT': 'main'}
        decision = {
            'symbol': 'NEW-USDT', 'action': 'open_long', 'confidence': 60,
            'plan': {'slot_type': 'main', 'size_usdt': 10, 'leverage': 3},
        }
        result = await judge._gate_and_publish_open('NEW-USDT', decision, {})
        assert result is False
        hold_call = judge.publish.call_args_list[-1]
        attr = hold_call[0][1]['attribution']
        assert 'dispatch_path' in attr
