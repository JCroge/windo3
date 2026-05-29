"""Tests for scripts/backfill_realized_pnl.py (Phase 3)

覆盖 docs/exchange_realized_pnl_ledger_acceptance.md AC-A10/A11:

- AC-A10 dry-run 不动 events.jsonl,输出 old/new PnL + delta
- AC-A11 apply 写 correction(supersedes 旧 pending),不删旧 JSONL,
  幂等(重复 apply 不改 daily_realized_pnl 累计)
- 候选筛选:legacy estimated(pnl_status 缺失 + source='estimated')
- 候选筛选:--symbol / --since / --until 过滤
- summary 计数:resolved/pending/mismatch/skipped
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from typing import List
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scripts.backfill_realized_pnl import (
    _candidate_events,
    _snapshot_from_event,
    _summarize,
    build_parser,
    run,
)
from utils.live_ledger import (
    LiveLedger,
    PNL_STATUS_FINAL,
    PNL_STATUS_PENDING,
)
from utils.realized_pnl_resolver import (
    PNL_STATUS_FINAL as R_FINAL,
    PNL_STATUS_PENDING as R_PENDING,
    PNL_STATUS_MISMATCH,
    PNL_STATUS_PENDING_FX,
)


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_exchange():
    return MagicMock()


@pytest.fixture
def ledger(tmp_dir, mock_exchange):
    events = os.path.join(tmp_dir, "events.jsonl")
    lifecycle = os.path.join(tmp_dir, "lifecycle.json")
    return LiveLedger(mock_exchange, events_path=events, lifecycle_path=lifecycle)


def _seed_pending(ledger, *, symbol="JTO-USDT-SWAP", side="long",
                   pid="pid-jto-1", entry=0.55, amount=10, leverage=2,
                   estimated_pnl=-0.543, opened_at=1779922000.0,
                   closed_at=1779922800.0, request_id="req-1") -> dict:
    """直接 record_pending_external_close,跳过 record_open(避免改 lifecycle)"""
    return ledger.record_pending_external_close(
        symbol=symbol, side=side, entry_price=entry,
        amount_usdt=amount, leverage=leverage,
        estimated_pnl=estimated_pnl,
        position_id=pid, entry_request_id=request_id,
        opened_at=opened_at, closed_at=closed_at,
    )


def _seed_legacy_estimated(ledger, *, symbol="JTO-USDT-SWAP", side="long",
                            pid="pid-legacy-1", ts=1779000000.0,
                            estimated_pnl=-0.30) -> dict:
    """老版本写的 external_close: 没有 pnl_status 字段,source='estimated'"""
    import uuid
    event = {
        "event_id": str(uuid.uuid4()),
        "ts": ts,
        "position_id": pid,
        "symbol": symbol,
        "event_type": "external_close",
        "side": side,
        "order_id": None,
        "fill_price": None,
        "filled_amount": None,
        "fee": 0.0,
        "fee_currency": "USDT",
        "amount_usdt": 10,
        "leverage": 2,
        "realized_pnl": estimated_pnl,
        "estimated_pnl": estimated_pnl,
        "source": "estimated",
        # 故意不写 pnl_status / close_match_key
        "entry_price": 0.55,
    }
    ledger._write_event(event)
    return event


def _make_args(**overrides) -> argparse.Namespace:
    """Default args for run() — dry-run, no filter"""
    defaults = dict(
        since=None, until=None, symbol=None,
        dry_run=True, testnet=False,
        events_path=None, lifecycle_path=None,
        json_out=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_resolver_for_jto(net_pnl: float = -1.73,
                            status: str = R_FINAL) -> MagicMock:
    """造一个返回 final PnL 的 resolver"""
    resolver = MagicMock()
    def _resolve(snapshot, close_window):
        return {
            "pnl_status": status,
            "pnl_source": "okx_fills_history+okx_bills",
            "symbol": snapshot.get("symbol"),
            "side": snapshot.get("side"),
            "position_id": snapshot.get("position_id", ""),
            "entry_request_id": snapshot.get("entry_request_id", ""),
            "opened_at": snapshot.get("opened_at", 0),
            "closed_at": close_window.get("closed_at", 0),
            "realized_pnl_net_usdt": net_pnl if status != R_PENDING else None,
            "estimated_pnl": snapshot.get("unrealized_pnl"),
            "gross_close_pnl_usdt": net_pnl + 0.15 if status == R_FINAL else 0,
            "fee_usdt": -0.15 if status == R_FINAL else 0,
            "funding_usdt": 0,
            "order_ids": ["close_1"],
            "bill_ids": ["bill_1"],
            "match_confidence": 0.98,
            "warnings": [],
            "pnl_pending_reason": "",
        }
    resolver.resolve_external_close.side_effect = _resolve
    return resolver


# ── AC-A10 ──────────────────────────────────────────────────────────────────


def test_dry_run_does_not_mutate_events(ledger, mock_exchange, capsys):
    """AC-A10: dry-run 不改 events.jsonl(byte 级一致)"""
    _seed_pending(ledger)
    before = open(ledger.events_path, 'rb').read()

    resolver = _make_resolver_for_jto(net_pnl=-1.73)
    args = _make_args(events_path=ledger.events_path,
                       lifecycle_path=ledger.lifecycle_path,
                       dry_run=True)
    out = run(args, ledger=ledger, resolver=resolver, exchange=mock_exchange)

    after = open(ledger.events_path, 'rb').read()
    assert after == before, "dry-run 不能修改 events.jsonl"
    assert out["summary"]["total"] == 1
    assert out["summary"]["resolved"] == 1


def test_dry_run_outputs_delta_old_new(ledger, mock_exchange, capsys):
    """AC-A10: dry-run 表格含 old_pnl / new_pnl / delta"""
    _seed_pending(ledger, estimated_pnl=-0.543)
    resolver = _make_resolver_for_jto(net_pnl=-1.73)
    args = _make_args(events_path=ledger.events_path,
                       lifecycle_path=ledger.lifecycle_path,
                       dry_run=True)
    out = run(args, ledger=ledger, resolver=resolver, exchange=mock_exchange)
    captured = capsys.readouterr().out

    # 表头/数据行都应该出现
    assert "symbol" in captured and "old_pnl" in captured \
        and "new_pnl" in captured and "delta" in captured
    # delta = -1.73 - (-0.543) = -1.187
    assert "JTO-USDT-SWAP" in captured
    row = out["rows"][0]
    assert abs(row["old_pnl"] - (-0.543)) < 1e-6
    assert abs(row["new_pnl"] - (-1.73)) < 1e-6


# ── AC-A11 ──────────────────────────────────────────────────────────────────


def test_apply_writes_correction(ledger, mock_exchange):
    """AC-A11: apply 模式写 correction,supersedes 原 pending"""
    pending = _seed_pending(ledger)
    resolver = _make_resolver_for_jto(net_pnl=-1.73)
    args = _make_args(events_path=ledger.events_path,
                       lifecycle_path=ledger.lifecycle_path,
                       dry_run=False)
    out = run(args, ledger=ledger, resolver=resolver, exchange=mock_exchange)

    # correction 已写
    events = ledger._read_events()
    corrections = [e for e in events
                   if e.get("event_type") == "external_close_correction"]
    assert len(corrections) == 1
    assert corrections[0]["supersedes_event_id"] == pending["event_id"]
    assert corrections[0]["pnl_status"] == PNL_STATUS_FINAL

    # row 标记 applied
    assert out["rows"][0]["applied"] is True
    assert out["rows"][0]["correction_event_id"] == corrections[0]["event_id"]


def test_apply_does_not_delete_old_jsonl(ledger, mock_exchange):
    """AC-A11: 旧 pending 事件仍在 JSONL 中(append-only)"""
    pending = _seed_pending(ledger)
    resolver = _make_resolver_for_jto(net_pnl=-1.73)
    args = _make_args(events_path=ledger.events_path,
                       lifecycle_path=ledger.lifecycle_path,
                       dry_run=False)
    run(args, ledger=ledger, resolver=resolver, exchange=mock_exchange)

    events = ledger._read_events()
    pending_ids = [e["event_id"] for e in events
                   if e.get("event_type") == "external_close"]
    assert pending["event_id"] in pending_ids


def test_apply_idempotent_repeat(ledger, mock_exchange):
    """AC-A11: 重复 apply daily_realized_pnl 不变(老 pending realized_pnl=0)"""
    _seed_pending(ledger, opened_at=1779922000.0, closed_at=1779922800.0)
    resolver = _make_resolver_for_jto(net_pnl=-1.73)

    args = _make_args(events_path=ledger.events_path,
                       lifecycle_path=ledger.lifecycle_path,
                       dry_run=False)

    # 第一次 apply
    run(args, ledger=ledger, resolver=resolver, exchange=mock_exchange)
    first_lc = json.loads(open(ledger.lifecycle_path).read()) \
        if os.path.exists(ledger.lifecycle_path) else {}

    # 第二次 apply: 原 pending 已被 superseded,应该没有候选了
    out2 = run(args, ledger=ledger, resolver=resolver, exchange=mock_exchange)
    assert out2["summary"]["total"] == 0, \
        "原 pending 已被 superseded,第二次 apply 不该再选中"

    second_lc = json.loads(open(ledger.lifecycle_path).read()) \
        if os.path.exists(ledger.lifecycle_path) else {}
    assert first_lc == second_lc, "重复 apply 不能改变 lifecycle 累计值"


def test_apply_summary_counts(ledger, mock_exchange):
    """AC-A11: summary 含 resolved/pending/mismatch/skipped 计数"""
    # 三条 pending: 一条 final, 一条 mismatch, 一条 pending(无成交)
    _seed_pending(ledger, pid="pid-A", request_id="req-A")
    _seed_pending(ledger, pid="pid-B", request_id="req-B")
    _seed_pending(ledger, pid="pid-C", request_id="req-C")

    statuses = iter([R_FINAL, PNL_STATUS_MISMATCH, R_PENDING])
    resolver = MagicMock()
    def _resolve(snapshot, close_window):
        st = next(statuses)
        return {
            "pnl_status": st,
            "pnl_source": "okx_fills_history" if st != R_PENDING else "estimated_local",
            "symbol": snapshot["symbol"], "side": snapshot["side"],
            "position_id": snapshot.get("position_id", ""),
            "entry_request_id": snapshot.get("entry_request_id", ""),
            "opened_at": snapshot["opened_at"],
            "closed_at": close_window["closed_at"],
            "realized_pnl_net_usdt": -1.73 if st == R_FINAL else None,
            "estimated_pnl": snapshot["unrealized_pnl"],
            "gross_close_pnl_usdt": -1.58 if st == R_FINAL else 0,
            "fee_usdt": -0.15 if st == R_FINAL else 0,
            "funding_usdt": 0,
            "order_ids": ["close_1"] if st != R_PENDING else [],
            "bill_ids": [], "match_confidence": 0.9 if st == R_FINAL else 0,
            "warnings": [] if st == R_FINAL else (["fills_bills_mismatch"]
                                                    if st == PNL_STATUS_MISMATCH else
                                                    ["no_close_fills_found"]),
            "pnl_pending_reason": "" if st == R_FINAL else "no_close_fills_in_window",
        }
    resolver.resolve_external_close.side_effect = _resolve

    args = _make_args(events_path=ledger.events_path,
                       lifecycle_path=ledger.lifecycle_path,
                       dry_run=False)
    out = run(args, ledger=ledger, resolver=resolver, exchange=mock_exchange)

    s = out["summary"]
    assert s["total"] == 3
    assert s["resolved"] == 1
    assert s["mismatch"] == 1
    assert s["pending"] == 1
    assert s["needs_exchange_data"] == 1


# ── 候选筛选 ────────────────────────────────────────────────────────────────


def test_legacy_estimated_event_detected(ledger, mock_exchange):
    """老版本写的 external_close (无 pnl_status, source=estimated) 也是候选"""
    _seed_legacy_estimated(ledger, ts=1779000000.0)
    candidates = _candidate_events(ledger, since_ts=None, until_ts=None,
                                    symbol=None)
    assert len(candidates) == 1
    assert candidates[0].get("pnl_status") is None
    assert candidates[0]["source"] == "estimated"


def test_superseded_pending_excluded(ledger, mock_exchange):
    """已经被 correction superseded 的 pending 不再出现在候选里"""
    _seed_pending(ledger, pid="pid-1", request_id="req-1")
    resolver = _make_resolver_for_jto(net_pnl=-1.73)
    args = _make_args(events_path=ledger.events_path,
                       lifecycle_path=ledger.lifecycle_path,
                       dry_run=False)
    run(args, ledger=ledger, resolver=resolver, exchange=mock_exchange)

    # 现在再选一次应该是空的
    candidates = _candidate_events(ledger, since_ts=None, until_ts=None,
                                    symbol=None)
    assert candidates == []


def test_symbol_filter(ledger, mock_exchange):
    """--symbol 只筛选目标标的"""
    _seed_pending(ledger, symbol="JTO-USDT-SWAP", pid="pid-jto")
    _seed_pending(ledger, symbol="DOGE-USDT-SWAP", pid="pid-doge")
    candidates = _candidate_events(ledger, since_ts=None, until_ts=None,
                                    symbol="JTO-USDT-SWAP")
    assert len(candidates) == 1
    assert candidates[0]["symbol"] == "JTO-USDT-SWAP"


def test_since_until_filter(ledger, mock_exchange):
    """--since/--until 按 ts 过滤"""
    _seed_pending(ledger, pid="pid-old",
                   opened_at=1700000000.0, closed_at=1700000100.0)
    _seed_pending(ledger, pid="pid-new",
                   opened_at=1779922000.0, closed_at=1779922800.0)

    # 只选新的
    candidates = _candidate_events(ledger,
                                    since_ts=1779000000.0,
                                    until_ts=None, symbol=None)
    assert len(candidates) == 1
    assert candidates[0]["position_id"] == "pid-new"

    # 反向只选老的
    candidates = _candidate_events(ledger,
                                    since_ts=None,
                                    until_ts=1779000000.0, symbol=None)
    assert len(candidates) == 1
    assert candidates[0]["position_id"] == "pid-old"


# ── snapshot 转换 ─────────────────────────────────────────────────────────────


def test_snapshot_from_event_shape():
    """_snapshot_from_event 字段齐全,resolver 可消费"""
    ev = {
        "symbol": "JTO-USDT-SWAP", "side": "long",
        "position_id": "pid-1", "entry_request_id": "req-1",
        "estimated_pnl": -0.543, "entry_price": 0.55,
        "amount_usdt": 10, "leverage": 2,
    }
    snap = _snapshot_from_event(ev)
    assert snap["symbol"] == "JTO-USDT-SWAP"
    assert snap["side"] == "long"
    assert snap["position_id"] == "pid-1"
    assert snap["entry_request_id"] == "req-1"
    assert snap["unrealized_pnl"] == -0.543
    assert snap["entry_price"] == 0.55


# ── parser ──────────────────────────────────────────────────────────────────


def test_parser_dry_run_default():
    """build_parser 默认 dry_run=True"""
    parser = build_parser()
    args = parser.parse_args([])
    assert args.dry_run is True


def test_parser_apply_flips_dry_run():
    """--apply 翻转 dry_run=False"""
    parser = build_parser()
    args = parser.parse_args(["--apply"])
    assert args.dry_run is False


def test_parser_dry_run_apply_mutex():
    """--dry-run 与 --apply 互斥"""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--dry-run", "--apply"])


# ── _summarize ──────────────────────────────────────────────────────────────


def test_summarize_buckets_all_statuses():
    rows: List[dict] = [
        {"status": R_FINAL},
        {"status": PNL_STATUS_MISMATCH},
        {"status": PNL_STATUS_PENDING_FX},
        {"status": R_PENDING, "needs_exchange_data": True},
        {"status": R_PENDING, "needs_exchange_data": False},
        {"status": "error"},
    ]
    s = _summarize(rows)
    assert s["total"] == 6
    assert s["resolved"] == 1
    assert s["mismatch"] == 1
    assert s["pending_fx"] == 1
    assert s["pending"] == 2
    assert s["needs_exchange_data"] == 1
    assert s["skipped"] == 1


# ── --json-out ──────────────────────────────────────────────────────────────


def test_json_out_writes_audit_trail(tmp_dir, ledger, mock_exchange):
    _seed_pending(ledger)
    resolver = _make_resolver_for_jto(net_pnl=-1.73)
    json_out = os.path.join(tmp_dir, "audit.json")
    args = _make_args(events_path=ledger.events_path,
                       lifecycle_path=ledger.lifecycle_path,
                       dry_run=True, json_out=json_out)
    run(args, ledger=ledger, resolver=resolver, exchange=mock_exchange)

    assert os.path.exists(json_out)
    payload = json.loads(open(json_out).read())
    assert payload["dry_run"] is True
    assert payload["summary"]["total"] == 1
    assert len(payload["rows"]) == 1
    # 不应该包含 resolution/event 这种大对象
    assert "resolution" not in payload["rows"][0]
    assert "event" not in payload["rows"][0]
