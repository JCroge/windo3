"""Phase 3 backfill 脚本 — 把历史 pending/estimated external_close 升级 final

PRD §6.7 / 验收 AC-A10/A11:
- 扫 events.jsonl 里 pnl_status=pending 或缺失 pnl_status 的 external_close 事件
- 调 RealizedPnlResolver.resolve_external_close() 拉 OKX fills+bills
- dry-run: 输出每条 旧 PnL / 新 PnL / delta / 数据来源,不改任何文件
- apply: 经 LiveLedger.apply_pnl_resolution 写 correction event(supersedes 旧 pending)
- 重复 apply 幂等(apply_pnl_resolution 里 close_match_key 找不到 pending 时
  写 standalone correction;但 daily_realized_pnl 不双计因为旧 pending realized=0)

Usage:
  python3 scripts/backfill_realized_pnl.py --since 2026-05-28T00:00:00+08:00 \
    --until 2026-05-28T23:59:59+08:00 --symbol JTO-USDT-SWAP --dry-run
  python3 scripts/backfill_realized_pnl.py --since 2026-05-28 --apply
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Optional, List, Dict, Any

# 让 scripts 子目录直接 `python3 scripts/...` 也能 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.live_ledger import LiveLedger, PNL_STATUS_PENDING, PNL_STATUS_FINAL
from utils.realized_pnl_resolver import (
    RealizedPnlResolver,
    PNL_STATUS_FINAL as R_FINAL,
    PNL_STATUS_PENDING as R_PENDING,
    PNL_STATUS_MISMATCH,
    PNL_STATUS_PENDING_FX,
)
from utils.state_paths import get_state_paths


def _parse_ts(raw: Optional[str]) -> Optional[float]:
    """支持 '2026-05-28' / '2026-05-28T08:00:00' / '...+08:00' / unix 秒"""
    if not raw:
        return None
    raw = raw.strip()
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        # 'YYYY-MM-DD' 补 T00:00:00
        if 'T' not in raw and len(raw) == 10:
            raw = raw + 'T00:00:00'
        # python 3.9 fromisoformat 不支持 'Z' 后缀
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        dt = _dt.datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.timestamp()
    except ValueError as e:
        raise SystemExit(f"无法解析时间 '{raw}': {e}")


def _candidate_events(ledger: LiveLedger,
                       since_ts: Optional[float],
                       until_ts: Optional[float],
                       symbol: Optional[str]) -> List[dict]:
    """选出待回填的事件:pending external_close + 历史 estimated external_close

    历史事件可能没有 pnl_status 字段(老版本写入),按 source='estimated' 兜底识别。
    """
    events = ledger._read_events()
    superseded_ids = {ev.get("supersedes_event_id") for ev in events
                      if ev.get("supersedes_event_id")}
    out: List[dict] = []
    for ev in events:
        if ev.get("event_type") != "external_close":
            continue
        if ev.get("event_id") in superseded_ids:
            continue
        status = ev.get("pnl_status")
        is_pending = (status == PNL_STATUS_PENDING)
        is_legacy_estimated = (status is None and ev.get("source") == "estimated")
        if not (is_pending or is_legacy_estimated):
            continue
        if symbol and ev.get("symbol") != symbol:
            continue
        ts = ev.get("ts", 0) or 0
        if since_ts is not None and ts < since_ts:
            continue
        if until_ts is not None and ts > until_ts:
            continue
        out.append(ev)
    return out


def _snapshot_from_event(ev: dict) -> Dict[str, Any]:
    """把 ledger event 转换成 resolver.resolve_external_close 接受的 snapshot dict"""
    return {
        "symbol": ev.get("symbol", ""),
        "side": ev.get("side", ""),
        "position_id": ev.get("position_id", "") or "",
        "entry_request_id": ev.get("entry_request_id", "") or "",
        "opened_at": 0,  # 老 pending 没存,resolver 用 lookback 兜底
        "unrealized_pnl": ev.get("estimated_pnl") or 0,
        "entry_price": ev.get("entry_price", 0) or 0,
        "amount_usdt": ev.get("amount_usdt", 0) or 0,
        "leverage": ev.get("leverage", 1) or 1,
    }


def _format_pnl(value) -> str:
    if value is None:
        return "      —"
    try:
        return f"{float(value):+8.4f}"
    except (TypeError, ValueError):
        return f"{value!r:>8}"


def _print_table(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("  (无候选事件)")
        return
    print(f"  {'symbol':<20} {'side':<6} {'old_pnl':>10} {'new_pnl':>10} "
          f"{'delta':>10} {'status':<12} source")
    print(f"  {'-'*20} {'-'*6} {'-'*10} {'-'*10} {'-'*10} {'-'*12} ------")
    for r in rows:
        old = r.get("old_pnl")
        new = r.get("new_pnl")
        if old is not None and new is not None:
            delta = new - old
        else:
            delta = None
        print(f"  {r['symbol']:<20} {r['side']:<6} "
              f"{_format_pnl(old)} {_format_pnl(new)} {_format_pnl(delta)} "
              f"{r['status']:<12} {r.get('pnl_source','')}")


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    summary = {
        "total": len(rows),
        "resolved": 0,
        "pending": 0,
        "mismatch": 0,
        "pending_fx": 0,
        "skipped": 0,
        "needs_exchange_data": 0,
    }
    for r in rows:
        st = r.get("status", "")
        if st == R_FINAL:
            summary["resolved"] += 1
        elif st == PNL_STATUS_MISMATCH:
            summary["mismatch"] += 1
        elif st == PNL_STATUS_PENDING_FX:
            summary["pending_fx"] += 1
        elif st == R_PENDING:
            summary["pending"] += 1
            if r.get("needs_exchange_data"):
                summary["needs_exchange_data"] += 1
        else:
            summary["skipped"] += 1
    return summary


def _build_exchange(use_testnet: bool):
    """构造一个真实 exchange,只用于 read-only fills/bills 查询"""
    from utils.exchange_factory import create_exchange
    config = {
        "exchange": "okx",
        "use_testnet": use_testnet,
    }
    return create_exchange(config, require_private=True,
                           purpose="backfill_realized_pnl")


def run(args: argparse.Namespace,
         ledger: Optional[LiveLedger] = None,
         resolver: Optional[RealizedPnlResolver] = None,
         exchange=None) -> Dict[str, Any]:
    """主流程,可注入 ledger/resolver/exchange 便于单测"""
    since_ts = _parse_ts(args.since)
    until_ts = _parse_ts(args.until)

    # 解析 ledger 路径
    if ledger is None:
        paths = get_state_paths()
        events_path = args.events_path or paths.live_order_events
        lifecycle_path = args.lifecycle_path or paths.live_position_lifecycle
        if exchange is None:
            exchange = _build_exchange(use_testnet=args.testnet)
        ledger = LiveLedger(exchange=exchange,
                            events_path=events_path,
                            lifecycle_path=lifecycle_path)
    if resolver is None:
        if exchange is None:
            exchange = ledger.exchange
        resolver = RealizedPnlResolver(exchange)

    candidates = _candidate_events(ledger, since_ts, until_ts, args.symbol)

    rows: List[Dict[str, Any]] = []
    for ev in candidates:
        snapshot = _snapshot_from_event(ev)
        close_window = {"closed_at": ev.get("ts", 0) or 0}
        try:
            resolution = resolver.resolve_external_close(snapshot, close_window)
        except Exception as e:
            rows.append({
                "event_id": ev.get("event_id", ""),
                "symbol": ev.get("symbol", ""),
                "side": ev.get("side", ""),
                "old_pnl": ev.get("estimated_pnl"),
                "new_pnl": None,
                "status": "error",
                "pnl_source": "",
                "error": str(e),
                "needs_exchange_data": True,
                "resolution": None,
                "event": ev,
            })
            continue

        new_pnl = resolution.get("realized_pnl_net_usdt")
        old_pnl = ev.get("estimated_pnl")
        rows.append({
            "event_id": ev.get("event_id", ""),
            "symbol": ev.get("symbol", ""),
            "side": ev.get("side", ""),
            "old_pnl": old_pnl,
            "new_pnl": new_pnl,
            "status": resolution.get("pnl_status", ""),
            "pnl_source": resolution.get("pnl_source", ""),
            "warnings": resolution.get("warnings", []),
            "needs_exchange_data": (
                resolution.get("pnl_status") == R_PENDING
                and "no_close_fills_found" in (resolution.get("warnings") or [])
            ),
            "resolution": resolution,
            "event": ev,
            "applied": False,
            "correction_event_id": "",
        })

    if args.dry_run:
        print(f"[backfill] dry-run, 候选 {len(rows)} 条:")
        _print_table(rows)
    else:
        applied = 0
        for r in rows:
            if r.get("status") not in (R_FINAL, PNL_STATUS_MISMATCH, PNL_STATUS_PENDING_FX):
                continue
            try:
                correction = ledger.apply_pnl_resolution(r["resolution"])
            except Exception as e:
                r["error"] = f"apply failed: {e}"
                continue
            r["applied"] = True
            r["correction_event_id"] = (correction or {}).get("event_id", "")
            applied += 1
        print(f"[backfill] apply 完成,写入 correction {applied} 条:")
        _print_table(rows)

    summary = _summarize(rows)
    print()
    print(f"[summary] total={summary['total']} resolved={summary['resolved']} "
          f"pending={summary['pending']} mismatch={summary['mismatch']} "
          f"pending_fx={summary['pending_fx']} skipped={summary['skipped']} "
          f"needs_exchange_data={summary['needs_exchange_data']}")

    if args.json_out:
        out_payload = {
            "summary": summary,
            "dry_run": bool(args.dry_run),
            "rows": [
                {k: v for k, v in r.items()
                 if k not in ("resolution", "event")}
                for r in rows
            ],
        }
        with open(args.json_out, "w") as f:
            json.dump(out_payload, f, indent=2, default=str)
        print(f"[backfill] 详细结果写入 {args.json_out}")

    return {"summary": summary, "rows": rows}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backfill_realized_pnl",
        description="把历史 external_close pending/estimated 事件升级 final",
    )
    p.add_argument("--since", help="起始时间 (ISO 8601 或 unix 秒)", default=None)
    p.add_argument("--until", help="结束时间 (ISO 8601 或 unix 秒)", default=None)
    p.add_argument("--symbol", help="只处理该 symbol", default=None)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="只输出候选,不写文件 (默认)")
    mode.add_argument("--apply", dest="dry_run", action="store_false",
                      help="真的写 correction event")
    p.add_argument("--testnet", action="store_true",
                   help="使用 OKX testnet (默认 live)")
    p.add_argument("--events-path", default=None,
                   help="覆盖 events.jsonl 路径 (默认按 STATE_NAMESPACE 解析)")
    p.add_argument("--lifecycle-path", default=None,
                   help="覆盖 lifecycle.json 路径")
    p.add_argument("--json-out", default=None,
                   help="同时把表格结果写到 JSON 文件")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run(args)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[backfill] 失败: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
