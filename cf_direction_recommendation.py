"""反事实策略实验室 L2 终验 + L4 方向推荐 一次性分析驱动。

用真实磁带 data/decision_replay_tape.jsonl（全 reject）跑：
  1. L2 终验：baseline 默认 config 回放，报 baseline_fidelity（reject 复现率）= 信任锚。
  2. L4 扫描：对 rr_floor_default 与 min_confidence 单旋钮 grid 扫 L3b build_delta_report
     + recommend_direction（诚实门控 + 多重比较守卫）。

observability-only —— 仅读磁带 + klines，输出严禁任何交易决策路径消费；绝不自动改线上 config。
"""
import asyncio
import json
import sqlite3

from utils.knob_sweep import sweep_knob, recommend_direction
from utils.joint_knob_sweep import sweep_grid, compute_interactions, recommend_direction_nd
from utils.sequential_perturbation import build_delta_report
from utils.config_loader import load_config

TAPE = "data/decision_replay_tape.jsonl"
KLINES_1S = "data/klines_1s.db"


def _live_portfolio_kwargs():
    """从 live config 派生 CF 组合参数，对齐 run_agents 运行时（observability-only）。
    库默认是 -50/1000，与 live config.yaml(-300)/.env(EFFECTIVE_BALANCE_CAP=300) 不符，
    会在未来 accept 变多时让 CF 比 live 更早熔断、污染 delta。显式对齐。"""
    cfg = load_config()
    return {
        "initial_equity": cfg.get("effective_balance_cap") or 1000.0,
        "max_slots": cfg.get("max_concurrent_positions", 3),
        "daily_pnl_hard_stop": cfg.get("daily_pnl_hard_stop", -50.0),
        "consecutive_loss_limit": cfg.get("consecutive_loss_limit", 3),
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
            # 按内容判定可回放, 不盲信 stale replayable: 旧 v1 空记录写入时即标 true。
            tech = r.get("tech_analysis")
            if r.get("schema_version") == "decision_replay_record.v2" and tech:
                recs.append(r)
    return recs


def make_price_loader(db_path):
    """price_loader(symbol, created_at_sec, window_sec) -> [{open_time(ms),high,low,close}] 升序。"""
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


async def main():
    recs = load_records()
    price_loader = make_price_loader(KLINES_1S)
    pf = _live_portfolio_kwargs()
    print(f"=== 磁带载入: {len(recs)} 条 (全部 reject) ===")
    print(f"=== CF 组合参数(对齐 live): {pf} ===\n")

    # ── L2 终验: baseline 默认 config 单臂回放, 报 fidelity ──
    # build_delta_report 内部先跑 baseline 臂并算 fidelity; 用零扰动探针拿 baseline_fidelity。
    print("=== L2 终验: baseline 复现率 (信任锚) ===")
    probe = await build_delta_report(recs, {}, {}, price_loader, fidelity_threshold=0.0, **pf)
    meta = probe["metadata"]
    print(f"  baseline_fidelity (reject 复现率): {meta['baseline_fidelity']:.4f}")
    print(f"  sequence_len: {meta['sequence_len']}")
    print(f"  divergence_ratio (零扰动应≈0): {meta.get('divergence_ratio')}")
    print(f"  baseline_cf_open_count: {meta.get('baseline_cf_open_count')}")
    print()

    # ── L4 扫描 #1: rr_floor_default (红线关注的 choppy 地板) ──
    print("=== L4 扫描 #1: rr_floor_default [1.50→1.20] ===")
    rr_values = [1.50, 1.45, 1.40, 1.35, 1.30, 1.25, 1.20]
    rr_sweep = await sweep_knob(recs, "rr_floor_default", rr_values, price_loader, **pf)
    for r in rr_sweep:
        d = r["delta"]
        ds = f"net={d['net_pnl']:+.2f} wr={d['win_rate']:+.3f} mdd={d['max_drawdown']:+.2f}" if d else "DELTA=None"
        print(f"  floor={r['value']:.2f}  fid={r['baseline_fidelity']:.3f}  "
              f"untrust={r['untrustworthy']}  div={r['divergence_ratio']}  n={r['sequence_len']}  {ds}")
    rr_rec = recommend_direction(rr_sweep)
    print(f"\n  >>> recommend_direction(rr_floor_default): {json.dumps(rr_rec, ensure_ascii=False, default=str)[:600]}")
    print()

    # ── L4 扫描 #2: min_confidence (质量门, 426 单被它拦) ──
    print("=== L4 扫描 #2: min_confidence [60→40] ===")
    conf_values = [60, 55, 50, 45, 40]
    conf_sweep = await sweep_knob(recs, "min_confidence", conf_values, price_loader, **pf)
    for r in conf_sweep:
        d = r["delta"]
        ds = f"net={d['net_pnl']:+.2f} wr={d['win_rate']:+.3f} mdd={d['max_drawdown']:+.2f}" if d else "DELTA=None"
        print(f"  min_conf={r['value']}  fid={r['baseline_fidelity']:.3f}  "
              f"untrust={r['untrustworthy']}  div={r['divergence_ratio']}  n={r['sequence_len']}  {ds}")
    conf_rec = recommend_direction(conf_sweep)
    print(f"\n  >>> recommend_direction(min_confidence): {json.dumps(conf_rec, ensure_ascii=False, default=str)[:600]}")

    # ── L4 联合扫描: rr_floor_default × min_confidence 交互效应 ──
    print("\n=== L4 联合扫描: rr_floor_default × min_confidence (交互效应) ===")
    base_values = {"rr_floor_default": 1.50, "min_confidence": 60}
    knob_grids = {"rr_floor_default": [1.50, 1.40, 1.30, 1.20],
                  "min_confidence": [60, 50, 40]}
    grid = await sweep_grid(recs, knob_grids, price_loader, baseline_config={}, **pf)
    if grid.get("untrustworthy"):
        print(f"  untrustworthy (baseline_fidelity={grid['baseline_fidelity']:.3f}) → 拒答")
    else:
        print(f"  baseline_fidelity={grid['baseline_fidelity']:.3f}  组合数={len(grid['combos'])}")
        for c in grid["combos"]:
            d = c["delta"]
            print(f"    {c['combo']}  net={d['net_pnl']:+.2f}  div={c['divergence_ratio']:.3f}")
        inter = compute_interactions(grid, base_values)
        print(f"\n  交互矩阵 (anchor_ok={inter['anchor_ok']}, "
              f"threshold={inter['effective_threshold']:.2f}):")
        for i in inter["interactions"]:
            if i["interaction"] is None:
                print(f"    {i['combo']}  {i['classification']}")
            else:
                print(f"    {i['combo']}  interaction={i['interaction']:+.2f}  → {i['classification']}")
        rec = recommend_direction_nd(grid, base_values, knob_grids=knob_grids)
        print(f"\n  >>> recommend_direction_nd: {json.dumps(rec, ensure_ascii=False, default=str)[:600]}")

    if meta.get("fidelity_note"):
        print(f"\n[fidelity_note] {meta['fidelity_note']}")


if __name__ == "__main__":
    asyncio.run(main())
