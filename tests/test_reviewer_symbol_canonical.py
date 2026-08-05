"""fix-reviewer-symbol-format: reviewer trade record symbol 归一为内部格式。

注：计划测试假设入口 `_handle_execution_result`；以实读为准，reviewer 消费
`execution_result` 的真实入口是 `_process_trade_result(msg)`（`on_message` 分发），
故测试改调真实入口，断言不变=trade_record['symbol'] 归一为内部 BASE-USDT。
"""
from unittest import mock

from agents.trading.reviewer import ReviewerAgent


def _bare_reviewer():
    r = ReviewerAgent.__new__(ReviewerAgent)
    r.logger = mock.MagicMock()
    r.trade_history = []
    r._save_trade_history = mock.MagicMock()
    r._processed_resolution_ids = set()
    r._processed_resolution_max = 1024
    return r


async def test_trade_record_symbol_normalized_swap():
    # execution_result close payload 带 -SWAP → trade_record['symbol'] 归一为 BASE-USDT
    r = _bare_reviewer()
    msg = {"timestamp": 1.0, "symbol": "XRP-USDT-SWAP",
           "payload": {"symbol": "XRP-USDT-SWAP", "action": "close",
                       "status": "executed",
                       "result": {"realized_pnl_net_usdt": -0.58, "pnl_is_final": True,
                                  "side": "short", "attribution": {}}}}
    await r._process_trade_result(msg)
    recs = [t for t in r.trade_history if t.get("symbol")]
    assert recs, "应记录一笔"
    assert all(t["symbol"] == "XRP-USDT" for t in recs)   # 归一, 无 -SWAP


def test_trade_record_symbol_idempotent_and_none_safe():
    from utils.symbol import to_internal
    assert to_internal("XRP-USDT") == "XRP-USDT"            # 幂等
    assert to_internal(None) in (None, "")                 # None fail-safe 不抛


def test_settle_from_lifecycle_normalizes_and_joins():
    from scripts.track_marginal60 import settle_fill_from_lifecycle
    lifecycle = {
        "ETH-USDT-SWAP-aaa-long": {"symbol": "ETH-USDT-SWAP", "side": "long",
            "opened_at": 1000.0, "status": "closed",
            "total_realized_pnl": 0.86, "reconcile_status": "matched"},
    }
    # fill: ETH-USDT @ ts=1010 (窗内, 归一后 symbol 匹配)
    pnl, used = settle_fill_from_lifecycle("ETH-USDT", "long", 1010.0, lifecycle, set(), tol=300)
    assert abs(pnl - 0.86) < 1e-9
    assert used   # 标记已消费的 lifecycle key


def test_settle_pending_or_out_of_window_unsettled():
    from scripts.track_marginal60 import settle_fill_from_lifecycle
    lc_pending = {"X-USDT-SWAP-bbb-long": {"symbol": "X-USDT-SWAP", "side": "long",
        "opened_at": 1000.0, "status": "open", "total_realized_pnl": None,
        "reconcile_status": "pending"}}
    pnl, _ = settle_fill_from_lifecycle("X-USDT", "long", 1010.0, lc_pending, set(), tol=300)
    assert pnl is None                                   # pending → 未结算
    # 窗外
    pnl2, _ = settle_fill_from_lifecycle("X-USDT", "long", 9999.0, lc_pending, set(), tol=300)
    assert pnl2 is None


async def test_pnl_resolution_preserves_tactical_v2_close_metadata():
    reviewer = _bare_reviewer()
    payload = {
        "symbol": "WLD-USDT-SWAP",
        "pnl_status": "final",
        "resolution_id": "r1",
        "realized_pnl_net_usdt": 2.5,
        "position_id": "tv2-position-1",
        "entry_request_id": "entry-client-1",
        "strategy_owner": "tactical_v2",
        "intent_id": "intent-1",
        "episode_id": "episode-1",
        "plan_hash": "plan-1",
        "close_reason": "exchange_tp",
        "tp_algo_ids": ["tp-1"],
        "sl_algo_ids": ["sl-1"],
        "attribution": {"strategy_owner": "tactical_v2"},
    }

    await reviewer._apply_pnl_resolution({"timestamp": 1000.0, "payload": payload})

    record = reviewer.trade_history[0]
    assert record["strategy_owner"] == "tactical_v2"
    assert record["intent_id"] == "intent-1"
    assert record["episode_id"] == "episode-1"
    assert record["plan_hash"] == "plan-1"
    assert record["close_reason"] == "exchange_tp"
    assert record["tp_algo_ids"] == ["tp-1"]
    assert record["sl_algo_ids"] == ["sl-1"]
