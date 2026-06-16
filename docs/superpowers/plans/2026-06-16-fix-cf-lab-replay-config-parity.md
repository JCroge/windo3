---
change: fix-cf-lab-replay-config-parity
design-doc: docs/superpowers/specs/2026-06-16-fix-cf-lab-replay-config-parity-design.md
base-ref: 21159c5210513b04775999458a05acade3fb2995
archived-with: 2026-06-16-fix-cf-lab-replay-config-parity
---

# CF Lab Replay Config Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 让反事实实验室回放用 live 生产 config 基线(而非空 config),把 baseline_fidelity 从 0.34 拉到 ~0.90;并录决策时 config 防未来漂移。

**Architecture:** 单一 chokepoint `replay_decision` 把有效 config = (record.config_snapshot 或 production_base_config) + 传入扰动覆盖;decision_tape 录 config_snapshot(schema v3)。observability-only。

**Tech Stack:** Python 3.9, pytest, asyncio。`utils/decision_replay.py`、`utils/decision_tape.py`、`agents/trading/judge.py`(仅录制点传 config)。

archived-with: 2026-06-16-fix-cf-lab-replay-config-parity
---

### Task 1: production_base_config + replay_decision 用生产基线

**Files:**
- Modify: `utils/decision_replay.py`(import config_loader;新增 `production_base_config()`;`replay_decision` 合并 config)
- Test: `tests/test_decision_replay.py`(若不存在则新建)

- [ ] **Step 1: 失败测试**

加到 `tests/test_decision_replay.py`(无则新建,顶部 `import json, asyncio` + 被测导入):

```python
from utils.decision_replay import production_base_config


def test_production_base_config_has_phase2_true():
    cfg = production_base_config()
    assert cfg["phase2_signal_confidence_split_enabled"] is True
    assert cfg["phase2_momentum_probe_long_enabled"] is True
    assert cfg["phase2_trend_saturation_enabled"] is True
    assert cfg["phase2_bucketed_ev_enabled"] is True
    assert cfg["rr_floor_default"] == 1.50
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_decision_replay.py -k production_base_config -q`
Expected: FAIL (`production_base_config` 不存在)

- [ ] **Step 3: 实现 production_base_config + 合并逻辑**

`utils/decision_replay.py` 顶部 import 区加:

```python
from utils.config_loader import DEFAULTS as _PROD_DEFAULTS
```

在 `replay_decision` 定义之前新增:

```python
def production_base_config():
    """live 生产决策 config 基线(config_loader 生产默认)。

    回放/CF-sim baseline 须以此为基线而非空 config —— 空 config 会让
    _install_config_flags 把 Phase-2 等 flag 默认到与生产相反的值，致
    confidence/gate 路径系统性发散(baseline_fidelity 虚低)。observability-only:
    只读 config_loader 静态默认，不读任何 live 运行态。
    """
    return dict(_PROD_DEFAULTS)
```

在 `replay_decision` 内，把:

```python
    judge = MultiJudge.__new__(MultiJudge)
    judge.config = config or {}
    judge.logger = mock.MagicMock()
    _install_config_flags(judge, config or {})
```

改为:

```python
    # 有效 config = 生产基线(record 录制的 config_snapshot 优先, 缺则 config_loader 生产默认)
    #              + 传入 config 作为扰动覆盖(只覆盖目标旋钮)。
    base = {**production_base_config(), **(record.get("config_snapshot") or {})}
    effective = {**base, **(config or {})}
    judge = MultiJudge.__new__(MultiJudge)
    judge.config = effective
    judge.logger = mock.MagicMock()
    _install_config_flags(judge, effective)
```

- [ ] **Step 4: 测试通过**

Run: `python3 -m pytest tests/test_decision_replay.py -k production_base_config -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add utils/decision_replay.py tests/test_decision_replay.py
git commit -m "feat(cf): replay uses production config baseline (config_snapshot or DEFAULTS) + perturbation overlay

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-fix-cf-lab-replay-config-parity
---

### Task 2: decision_tape 录 config_snapshot (schema v3)

**Files:**
- Modify: `utils/decision_tape.py`(SCHEMA_VERSION v3;build_bundle 加 config_snapshot 参数+字段)
- Modify: `agents/trading/judge.py`(两个 build_bundle 调用点传 config_snapshot)
- Test: `tests/test_decision_tape.py`

- [ ] **Step 1: 失败测试**

加到 `tests/test_decision_tape.py`:

```python
from utils.decision_tape import build_bundle, SCHEMA_VERSION


def test_build_bundle_records_config_snapshot():
    b = build_bundle(
        symbol="X-USDT", decision="reject", request_id=None,
        tech_analysis={"rule_signal": {}}, price_at_decision=1.0,
        regime_state="mixed", llm_output=None, llm_audit_ref=None,
        trade_decision_output={}, state_snapshot={"_recent_wins": 1},
        config_snapshot={"rr_floor_default": 1.5, "phase2_bucketed_ev_enabled": True},
    )
    assert b["config_snapshot"] == {"rr_floor_default": 1.5, "phase2_bucketed_ev_enabled": True}
    assert b["schema_version"] == "decision_replay_record.v3"


def test_build_bundle_config_snapshot_optional():
    b = build_bundle(
        symbol="X-USDT", decision="reject", request_id=None,
        tech_analysis={}, price_at_decision=1.0, regime_state="mixed",
        llm_output=None, llm_audit_ref=None, trade_decision_output={},
    )
    assert b.get("config_snapshot") is None
```

- [ ] **Step 2: 确认失败**

Run: `python3 -m pytest tests/test_decision_tape.py -k config_snapshot -q`
Expected: FAIL (build_bundle 无 config_snapshot 参数 / schema 仍 v2)

- [ ] **Step 3: 实现**

`utils/decision_tape.py`:
- `SCHEMA_VERSION = "decision_replay_record.v3"`
- `build_bundle` 签名加 `config_snapshot=None`(放 `state_snapshot=None` 后):
  ```python
  def build_bundle(*, symbol, decision, request_id, tech_analysis, price_at_decision,
                   regime_state, llm_output, llm_audit_ref, trade_decision_output,
                   state_snapshot=None, config_snapshot=None):
  ```
- 返回 dict 中加一行 `"config_snapshot": config_snapshot,`(放 state_snapshot 同级)。

`agents/trading/judge.py` 两个 build_bundle 调用点(accept ~1995-2007、reject path 的 `_record_rejected_plan` 内 ~3052),各加参数 `config_snapshot=dict(getattr(self, "config", {}) or {})`。先 grep 定位精确行:`grep -n "build_bundle(" agents/trading/judge.py`,在每个 `build_bundle(` 调用的末尾参数(`state_snapshot=...` 那行后)追加 `config_snapshot=dict(getattr(self, "config", {}) or {}),`。

- [ ] **Step 4: 测试通过**

Run: `python3 -m pytest tests/test_decision_tape.py -q`
Expected: PASS(注意若已有断言 `schema_version == v2` 的旧用例,按 v3 更新——记录在报告)

- [ ] **Step 5: 提交**

```bash
git add utils/decision_tape.py agents/trading/judge.py tests/test_decision_tape.py
git commit -m "feat(cf): tape records config_snapshot at decision time (schema v3, config-drift-proof replay)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-fix-cf-lab-replay-config-parity
---

### Task 3: 坐实保真 + perturbation 叠加 + 全量回归

**Files:**
- Test: `tests/test_decision_replay.py`、`tests/test_sequential_perturbation.py`

- [ ] **Step 1: 失败/坐实测试**

加到 `tests/test_decision_replay.py`(端到端,真实磁带,skip-if-absent):

```python
import json, os, asyncio
from utils.decision_replay import replay_decision
from utils.sequential_perturbation import _gate_of_recorded, _gate_of_replayed

_TAPE = os.path.join(os.path.dirname(__file__), "..", "data", "decision_replay_tape.jsonl")


def _load_v2(limit=None):
    import pytest
    if not os.path.exists(_TAPE):
        pytest.skip("no live tape")
    out = []
    for line in open(_TAPE):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("schema_version") not in ("decision_replay_record.v2", "decision_replay_record.v3"):
            continue
        if not (r.get("tech_analysis") or {}):
            continue
        out.append(r)
        if limit and len(out) >= limit:
            break
    return out


def test_production_baseline_restores_fidelity():
    recs = _load_v2()
    if len(recs) < 50:
        import pytest
        pytest.skip("insufficient tape")

    async def run():
        agree = 0
        for r in recs:
            d = await replay_decision(r, None)  # config=None → 生产基线
            if _gate_of_recorded(r) == _gate_of_replayed(d):
                agree += 1
        return agree / len(recs)
    fid = asyncio.get_event_loop().run_until_complete(run())
    assert fid >= 0.85, f"L2 fidelity {fid:.3f} < 0.85 (生产基线应坐实 ~0.90)"
```

加到 `tests/test_sequential_perturbation.py`(perturbation 叠加只覆盖目标旋钮):

```python
def test_perturbation_overlays_on_production_base_only_target():
    from utils.decision_replay import production_base_config
    base = production_base_config()
    # 模拟 replay_decision 的合并: base + 扰动覆盖
    perturb = {"rr_floor_default": 0.3}
    effective = {**base, **perturb}
    assert effective["rr_floor_default"] == 0.3            # 目标旋钮被覆盖
    assert effective["phase2_signal_confidence_split_enabled"] is True  # 其它旋钮保持生产基线
    assert effective["min_confidence"] == base["min_confidence"]
```

- [ ] **Step 2: 跑测试**

Run: `python3 -m pytest tests/test_decision_replay.py tests/test_sequential_perturbation.py -q`
Expected: 新测试 PASS(`test_production_baseline_restores_fidelity` fid≥0.85)。

- [ ] **Step 3: 修可能回归的既有回放测试**

Run: `python3 -m pytest tests/test_decision_replay.py tests/test_sequential_perturbation.py tests/test_perturbation_replay.py -q`
既有用例若因「现在默认走生产基线(phase2=True)」而断言变化:逐一核对——若旧断言基于 phase2=False 的硬默认,按生产基线语义更新断言(记录 before/after);若测试显式传了完整 config 期望被尊重,确认合并语义(base+override)未破坏其意图。**不得**为迁就旧断言而回退生产基线逻辑。报告所有改动的测试。

- [ ] **Step 4: 红线 + 全量回归**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -q` → PASS
Run: `python3 -m pytest -q` → summary 行;总 passed ≥ 1247(基线不回退;新增测试上升)。失败则报告,不标 DONE。

- [ ] **Step 5: 提交**

```bash
git add tests/test_decision_replay.py tests/test_sequential_perturbation.py
git commit -m "test(cf): production baseline restores L2 fidelity (>=0.85) + perturbation overlay + regression

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-16-fix-cf-lab-replay-config-parity
---

## Self-Review
- **Spec coverage**:decision-replay-tape(config_snapshot 录制)=Task2;deterministic-replay-harness(生产基线/config_snapshot 优先/fallback)=Task1;sequential-perturbation-driver(两臂同基线+扰动覆盖)=Task1 合并语义+Task3 验证;replay-report-driver(驱动经 replay_decision 自动获益)=Task1+Task3 端到端坐实。
- **Placeholder scan**:每步真实代码/命令/期望;judge.py 调用点用 grep 定位(行号会随上个 change 漂移,故指明用 grep 而非硬行号)。
- **Type consistency**:`production_base_config()`、`config_snapshot`、合并 `{**base, **override}` 在各 Task 一致。
- **保真坦白**:Task3 断言 ≥0.85(实测 0.90),不强求 actionable direction;残留 ~10% 非目标。
