"""trend-entry-rr-fidelity 四臂 A/B 观测驱动 (L3b CF-replay)。

observability-only —— 仅读磁带 data/decision_replay_tape.jsonl (schema v2 + 非空
tech_analysis) + klines data/klines_1s.db, 对两个灰度杠杆
  lever1 = path_evidence_aligned_enabled (客观路径证据授对齐 R:R 地板)
  lever2 = ladder_rr_enabled            (阶梯加权 effective_rr)
跑 build_delta_report 报 baseline_fidelity / untrustworthy / divergence /
CF 开仓数 / delta(net_pnl,win_rate,max_drawdown)。
输出严禁任何交易决策路径消费; 绝不自动改线上 config; 不出方向结论(交由控制方解读)。
"""
import asyncio
import json
import sqlite3

from utils.sequential_perturbation import build_delta_report

TAPE = "data/decision_replay_tape.jsonl"
KLINES_1S = "data/klines_1s.db"

ARMS = {
    "lever1_only": {"path_evidence_aligned_enabled": True},
    "lever2_only": {"ladder_rr_enabled": True},
    "lever1_plus_2": {"path_evidence_aligned_enabled": True, "ladder_rr_enabled": True},
}


def load_records():
    recs = []
    with open(TAPE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            tech = r.get("tech_analysis")
            if r.get("schema_version") == "decision_replay_record.v2" and tech:
                recs.append(r)
    return recs


def make_price_loader(db_path):
    def loader(symbol, created_at, window_sec):
        lo = int(created_at * 1000)
        hi = int((created_at + window_sec) * 1000)
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT open_time, high, low, close FROM klines "
                "WHERE symbol=? AND open_time>=? AND open_time<=? ORDER BY open_time",
                (symbol, lo, hi)).fetchall()
        except Exception:
            return []
        finally:
            conn.close()
        return [{"open_time": t, "high": h, "low": l, "close": c} for t, h, l, c in rows]
    return loader


def _fmt_delta(d):
    if not d:
        return "DELTA=None"
    return (f"net_pnl={d['net_pnl']:+.4f}  win_rate={d['win_rate']:+.4f}  "
            f"max_drawdown={d['max_drawdown']:+.4f}")


async def main():
    recs = load_records()
    price_loader = make_price_loader(KLINES_1S)
    print(f"=== 磁带载入: {len(recs)} 条 (schema v2 + 非空 tech_analysis) ===\n")

    for arm_name, knobs in ARMS.items():
        print(f"=== ARM: {arm_name}  knobs={json.dumps(knobs, ensure_ascii=False)} ===")
        report = await build_delta_report(recs, {}, knobs, price_loader, fidelity_threshold=0.0)
        meta = report["metadata"]
        fid = meta.get("baseline_fidelity")
        print(f"  baseline_fidelity:        {fid:.4f}" if fid is not None else
              "  baseline_fidelity:        None")
        print(f"  untrustworthy:            {meta.get('untrustworthy')}")
        print(f"  sequence_len:             {meta.get('sequence_len')}")
        print(f"  divergence_ratio:         {meta.get('divergence_ratio')}")
        print(f"  baseline_cf_open_count:   {meta.get('baseline_cf_open_count')}")
        print(f"  perturbed_cf_open_count:  {meta.get('perturbed_cf_open_count')}")
        print(f"  delta:                    {_fmt_delta(report.get('delta'))}")
        note = meta.get("fidelity_note")
        if note:
            print(f"  fidelity_note:            {note}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
