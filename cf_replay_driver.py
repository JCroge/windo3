"""端到端被拒单反事实报表 driver：rejected_signal_events.jsonl + klines → resolve → build_cf_report。
observability-only —— 输出严禁交易决策读取。"""
import json
import os
import sqlite3
from utils.counterfactual_pnl import resolve_counterfactual
from replay_report import build_cf_report


def load_klines_window(db_path, symbol, created_at, window_sec=86400):
    """取 [created_at, created_at+window_sec] 的 bars（升序）。open_time 单位 ms。"""
    if not db_path or not os.path.exists(db_path):
        return []
    lo_ms = int(created_at * 1000)
    hi_ms = int((created_at + window_sec) * 1000)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT open_time, high, low, close FROM klines "
            "WHERE symbol=? AND open_time>=? AND open_time<=? ORDER BY open_time",
            (symbol, lo_ms, hi_ms)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{"open_time": t, "high": h, "low": l, "close": c} for t, h, l, c in rows]


def build_report_from_rejected(events_path, *, klines_1s_db, klines_db,
                               min_sample=30, lowconf_sample=100, window_sec=86400):
    rows = []
    skipped = 0
    if not os.path.exists(events_path):
        return {"buckets": {}, "total": 0, "skipped_no_data": 0}
    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            if evt.get("event_type") != "rejected_plan_created":
                continue
            rec = evt.get("record") or {}
            sym, created = rec.get("symbol"), rec.get("created_at")
            if not sym or created is None:
                skipped += 1
                continue
            bars = load_klines_window(klines_1s_db, sym, created, window_sec)
            source = "tape_exact"
            if not bars:
                bars = load_klines_window(klines_db, sym, created, window_sec)
                source = "attribution_reconstructed"
            if not bars:
                skipped += 1
                continue
            r = resolve_counterfactual(rec, bars, source=source)
            rows.append({
                "reject_reason": rec.get("reject_reason"),
                "effective_regime": rec.get("effective_regime"),
                "side": rec.get("side"),
                "outcome": r.outcome, "net_usdt": r.net_usdt,
                "price_ambiguous": r.price_ambiguous, "source": r.source,
            })
    report = build_cf_report(rows, min_sample=min_sample, lowconf_sample=lowconf_sample)
    report["skipped_no_data"] = skipped
    return report
