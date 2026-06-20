---
comet_change: fix-shadow-logger-replay-baseline-parity
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-20-fix-shadow-logger-replay-baseline-parity
status: final
---

# 影子记录器：两臂同复盘 + baseline 自检闸（技术设计）

> 需求事实源为 OpenSpec delta spec `openspec/changes/fix-shadow-logger-replay-baseline-parity/specs/shadow-decision-logger/spec.md`。本文档只描述 HOW，不重定义需求。

## 1. 问题与诊断（已实证）

影子记录器（`trend-entry-shadow-decision-logger`，2026-06-17）的 lever1 增量口径是 **`live(real) − replay(both-levers)`**：被减数是**真·live 决策**（无复盘偏差），减数是**复盘决策**（有复盘机器保真误差）。两者不同源，差里混入复盘偏差。

实测（2026-06-20，3809 条影子记录中的 37 条 `shadow_holds`）：

- 本地用同一磁带 bundle 重跑 `replay(lever2-only)` vs `replay(both-levers)`：两臂对这 37 条**零分歧** → **lever1 真实增量 = 0**。
- 其中 **13/37** 是 `replay(lever2-only)` 也复现不出 live 当时的 accept（复盘失真、方向偏保守 hold）。

→ 37 条 `shadow_holds` 全是复盘失真，非 lever1。原 config-parity 假设经实测**证伪**（34/37 条 `config_snapshot.ev_winrate_gate_enabled` 正确为 `False`，0 条 `True`）。

同仓库已有两套机制解决同类问题，本设计直接对齐：
- `perturbation_replay`：**baseline 复现自检闸**（replay-baseline 不复现 live record 即标 `baseline_mismatch`、排除出翻转统计）。
- `sequential_perturbation`：**两臂同估算 → 系统性偏差在 delta 抵消**（结论以 delta 为主、非绝对值）。

## 2. 方案

```
现状(错):
  live(real, 无复盘偏差) ── 减 ──> replay(both, 有复盘偏差)   差 = lever1 + 复盘偏差  ✗

改后(对):
  replay(lever2-only, baseline) ── 减 ──> replay(both, shadow)   差 = lever1（偏差两臂抵消）  ✓
  + 自检: _is_accept(baseline) == _is_accept(real_live) ?  否 → baseline_mismatch=True → 排除
```

改动几乎全部集中在 `utils/shadow_decision_logger.py`。`agents/trading/judge.py` 的 chokepoint **已经把 `real_decision` 传入** `log_shadow_decision`（accept 点 `judge.py:2023` 传真实 decision，reject 点 `judge.py:3151` 传 `{"action":"hold",...}`），自检所需的 live accept/reject 现成可用，judge.py 无需新增 plumbing。

## 3. 详细设计（`utils/shadow_decision_logger.py`）

### 3.1 两条复盘配置

```python
BASELINE_CONFIG = {"path_evidence_aligned_enabled": False, "ladder_rr_enabled": True}  # = live 现生效
SHADOW_CONFIG   = {"path_evidence_aligned_enabled": True,  "ladder_rr_enabled": True}  # both levers on
```

`BASELINE_CONFIG` 显式对齐 live 当前配置（lever2 默认开 / lever1 默认关）。两条都作为 `replay_decision` 的 `perturbation` 顶层 override 传入，覆盖磁带 `config_snapshot` 中对应两键。

### 3.2 `log_shadow_decision` 主流程

```python
async def log_shadow_decision(bundle, real_decision, log_path, *, enabled=True, logger=None):
    if not enabled:
        return None
    try:
        if not (bundle or {}).get("replayable"):
            return None
        baseline = await replay_decision(bundle, BASELINE_CONFIG)
        shadow   = await replay_decision(bundle, SHADOW_CONFIG)
        if baseline is None:          # baseline 无法复盘 → 自检不可判定 → 跳过不写
            return None
        rec = build_shadow_record(
            ts=bundle.get("timestamp", 0), symbol=bundle.get("symbol"),
            real=_summ(real_decision), baseline=_summ(baseline), shadow=_summ(shadow),
            tech_context=bundle.get("tech_analysis"))
        with open(log_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec
    except Exception as e:            # fail-safe：影子绝不破 live
        if logger:
            logger.warning(f"[shadow] log_shadow_decision skipped: {e}")
        return None
```

`_summ(decision)` = `{"action": action_or_hold, "gate": _gate_of(decision), "plan": decision.plan}`（plan 仅 shadow 需要，baseline/real 可省）。

### 3.3 自检与 flip_kind（纯函数，便于单测）

```python
def _is_accept(action):
    return action in ("open_long", "open_short")

def compute_flip_kind(baseline_action, shadow_action):       # 改基于 baseline vs shadow
    b, s = _is_accept(baseline_action), _is_accept(shadow_action)
    if b == s:
        return "same"
    return "shadow_opens" if s else "shadow_holds"

def compute_baseline_mismatch(baseline_action, real_action): # 自检闸
    return _is_accept(baseline_action) != _is_accept(real_action)
```

### 3.4 record 字段（jsonl）

```jsonc
{
  "timestamp", "symbol",
  "real_action", "real_gate",            // live 决策，仅供自检追溯
  "baseline_action", "baseline_gate",    // 新增：replay(lever2-only)
  "shadow_action", "shadow_gate", "shadow_plan",
  "baseline_mismatch",                   // 新增：bool，true=baseline 复盘背离 live
  "flip_kind",                           // 改：基于 baseline vs shadow
  "tech_context"
}
```

向后兼容：旧记录无 `baseline_*` 字段；离线驱动按缺字段 fail-safe（缺 `baseline_mismatch` 视为不可信、排除）。

## 4. 离线驱动 `cf_shadow_lever1_compare.py`

筛 `flip_kind=shadow_opens` 结算 lever1 增量前，**先剔除 `baseline_mismatch=True`（及缺该字段的旧记录）**；报表显式打印被排除条数（透明，不静默丢弃）。

## 5. 边界条件

| 情形 | 处理 |
|---|---|
| `bundle.replayable=False` | 前置短路返回 None（不变） |
| 任一臂 `replay_decision` 抛异常 | `try/except` fail-safe 跳过整条、返回 None、绝不破 live |
| baseline 复盘返回 None（不可判定自检） | 跳过不写，不伪造 mismatch |
| shadow 复盘返回 None 但 baseline 有值 | shadow_action 取 "hold"，正常记录（baseline 有效即可自检+对比） |
| 旧记录无 baseline 字段 | 离线驱动 fail-safe 当不可信排除 |

## 6. 成本

每信号现跑 2 次 `replay_decision`（原 1 次）。复盘是纯计算（mock `_update_balance`/`_ask_llm`/`publish` 三个外部 await、内联缓存 llm、无网络、`MultiJudge.__new__` 不碰 live 实例），经 `_schedule_shadow` fire-and-forget 在 live publish **之后**执行 → live 决策零额外延迟。翻倍的是后台 CPU，量级可接受。

## 7. 测试策略

`tests/test_shadow_decision_logger.py`（或现有同名）新增：

1. baseline 复盘复现 live accept（baseline=open, real=open）→ `baseline_mismatch=False`。
2. baseline 复盘背离 live（baseline=hold, real=open）→ `baseline_mismatch=True`。
3. 两臂相同（baseline=hold, shadow=hold）→ `flip_kind=same`；baseline=hold/shadow=open → `shadow_opens`；baseline=open/shadow=hold → `shadow_holds`。
4. 任一臂 `replay_decision` 抛异常 → `log_shadow_decision` 返回 None、不写文件、不抛（fail-safe）。
5. `compute_flip_kind` / `compute_baseline_mismatch` / `_is_accept` 纯函数表驱动单测。

红线守卫 `tests/test_cf_red_line_guard.py::test_decision_paths_do_not_read_shadow_products` 不回归。全量回归零退化。

## 8. 红线（不变）

- observability-only write-only：两条复盘臂绝不 publish 真实 bus / 不下单 / 不 mutate live Judge·portfolio·cooldown·daily-stop。
- fail-safe：任何异常绝不影响 live 决策的产出与发布。
- 不动 ev-gate config（`ev_winrate_gate_enabled` 等），不改 lever1/lever2 策略本身。

## 9. 非目标

- 不深挖复盘失真的具体未还原状态根因（baseline 自检闸对失真源不可知地兜底）。
- 不补影子日志 retention（既有 follow-up，另议）。
- 不改 `replay_decision` / `decision_replay.py` 四层合并机制（其行为正确，config_snapshot 已忠实捕获 live 值）。
