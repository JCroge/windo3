"""F4-002 pnl_resolved/pnl_mismatch 总线事件契约测试矩阵。"""

import pytest
from utils.realized_pnl_resolver import make_resolution_id


class TestMakeResolutionId:
    def test_correction_event_id_takes_priority(self):
        resolution = {"position_id": "p1", "order_ids": ["o1"]}
        correction = {"event_id": "E-123", "supersedes_event_id": "E-old"}
        rid = make_resolution_id(resolution, correction)
        assert rid == "corr:E-123"

    def test_supersedes_when_no_event_id(self):
        resolution = {"position_id": "p1"}
        correction = {"supersedes_event_id": "E-old"}
        rid = make_resolution_id(resolution, correction)
        assert rid == "sup:E-old"

    def test_close_match_key_when_no_correction(self):
        resolution = {"position_id": "p1", "close_match_key": "K-7"}
        rid = make_resolution_id(resolution, None)
        assert rid == "key:K-7"

    def test_pos_orders_fallback(self):
        resolution = {"position_id": "p1", "order_ids": ["o2", "o1"]}
        rid = make_resolution_id(resolution, None)
        assert rid == "pos:p1|orders:o1,o2"

    def test_empty_orders_fallback(self):
        resolution = {"position_id": "", "order_ids": []}
        rid = make_resolution_id(resolution, None)
        assert rid == "pos:|orders:"

    def test_same_resolution_same_id(self):
        resolution = {"position_id": "p1", "order_ids": ["o1"]}
        correction = {"event_id": "E-1"}
        a = make_resolution_id(resolution, correction)
        b = make_resolution_id(resolution, correction)
        assert a == b

    def test_empty_correction_dict_falls_through(self):
        """correction={} (falsy) 应当 fall through 到 close_match_key/pos 兜底。"""
        resolution = {"position_id": "p1", "close_match_key": "K-7"}
        rid = make_resolution_id(resolution, {})
        assert rid == "key:K-7"

    def test_pos_fallback_idempotent_under_order_shuffle(self):
        """order_ids 顺序变化不应改变 resolution_id (基于 sort)。"""
        a = make_resolution_id(
            {"position_id": "p1", "order_ids": ["o3", "o1", "o2"]}, None)
        b = make_resolution_id(
            {"position_id": "p1", "order_ids": ["o1", "o2", "o3"]}, None)
        assert a == b == "pos:p1|orders:o1,o2,o3"
