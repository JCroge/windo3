---
change: trend-entry-levers-default-on
design-doc: docs/superpowers/specs/2026-06-17-trend-entry-levers-default-on-design.md
base-ref: 5e1bcf0dbc506b6406f29b30757b78b7510292db
---

# lever2 默认开 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development 或 superpowers:executing-plans 逐任务实现。步骤用 `- [ ]` 跟踪。

**Goal:** 把 lever2（`ladder_rr_enabled`，阶梯加权 effective_rr 口径修正）默认开，保留 env 逃生阀；lever1 不动。

**Architecture:** config 层翻默认值（DEFAULTS + env map）+ Judge/replay 兜底对齐 + 测试同步。lever 本体逻辑零改动。

**Tech Stack:** Python 3.9，`utils/config_loader`，`agents/trading/judge.py`，`utils/decision_replay.py`，pytest。

---

### Task 1: config_loader 默认开 + env 逃生阀

**Files:**
- Modify: `utils/config_loader.py`（DEFAULTS ~line 133 区；env 映射 ~line 273 区）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_ladder_rr_default_on.py`：

```python
import os
from utils.config_loader import load_config, DEFAULTS

def test_ladder_rr_enabled_default_true():
    assert DEFAULTS.get("ladder_rr_enabled") is True

def test_ladder_rr_env_escape_valve(monkeypatch):
    monkeypatch.setenv("LADDER_RR_ENABLED", "false")
    cfg = load_config()
    assert cfg["ladder_rr_enabled"] is False

def test_path_evidence_stays_default_off():
    # lever1 本 change 不动
    assert DEFAULTS.get("path_evidence_aligned_enabled") in (None, False)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_ladder_rr_default_on.py -q`
Expected: FAIL（`DEFAULTS` 无 `ladder_rr_enabled` 键 → KeyError/None）

- [ ] **Step 3: 实现**

`utils/config_loader.py` DEFAULTS 区（紧邻 `"low_rr_slot_enabled": True,` line 132）加：

```python
    "ladder_rr_enabled": True,
```

env 映射区（紧邻 `"LOW_RR_SLOT_ENABLED": ("low_rr_slot_enabled", _to_bool),` line 273）加：

```python
        "LADDER_RR_ENABLED": ("ladder_rr_enabled", _to_bool),
```

不加 HARD_LIMITS（布尔 flag 不在 HARD_LIMITS）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_ladder_rr_default_on.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add utils/config_loader.py tests/test_ladder_rr_default_on.py
git commit -m "feat(levers): ladder_rr_enabled default-on in config + LADDER_RR_ENABLED escape valve"
```

### Task 2: Judge + replay 兜底对齐 True

**Files:**
- Modify: `agents/trading/judge.py:174`
- Modify: `utils/decision_replay.py:196`
- Modify: `tests/test_rr_fidelity_knob_injection.py`（`test_install_config_flags_defaults_off`）

- [ ] **Step 1: 改 judge.py 兜底**

`agents/trading/judge.py:174`：
```python
        self._ladder_rr_enabled = config.get('ladder_rr_enabled', True) if config else True
```
（lever1 `:169` `path_evidence_aligned_enabled` 维持 `False` 不动。）

- [ ] **Step 2: 改 decision_replay 兜底（docstring 要求与 __init__ 一致）**

`utils/decision_replay.py:196`：
```python
    judge._ladder_rr_enabled = g("ladder_rr_enabled", True)
```
（`path_evidence_aligned_enabled` :192 维持 `False`。理由：`_install_config_flags` docstring「默认值与 MultiJudge.__init__ 保持一致」；production_base_config 已携 True，本改只修空-config 兜底的一致性。）

- [ ] **Step 3: 更新被默认值翻转打破的注入测试**

`tests/test_rr_fidelity_knob_injection.py::test_install_config_flags_defaults_off`：ladder 现默认 True，path_evidence 仍 False：
```python
def test_install_config_flags_defaults_off():
    j = _BareJudge()
    _install_config_flags(j, {})
    assert j._path_evidence_aligned_enabled is False   # lever1 仍默认关
    assert j._ladder_rr_enabled is True                # lever2 默认开（与 __init__ 一致）
```
（函数名虽含 defaults_off，语义改为「lever1 关/lever2 开」；若可改名则改 `test_install_config_flags_defaults_lever1_off_lever2_on`。）

- [ ] **Step 4: 跑相关测试**

Run: `python3 -m pytest tests/test_rr_fidelity_knob_injection.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add agents/trading/judge.py utils/decision_replay.py tests/test_rr_fidelity_knob_injection.py
git commit -m "feat(levers): align Judge/replay fallback to ladder_rr default-on (lever1 stays off)"
```

### Task 3: 全量回归 + 受影响测试三角

**Files:** 视失败而定（候选：`tests/test_judge_plan_anchor_fields.py`、`tests/test_rejected_tech_context.py`、`tests/test_short_main_path_risk_guard.py`、`tests/test_pullback_atr_policy.py`）

- [ ] **Step 1: 跑全量找断点**

Run: `python3 -m pytest -q`
Expected: 若干失败——断言了「默认 TP1 口径」effective_rr 具体值的测试，现走 ladder 口径值变。

- [ ] **Step 2: 逐个三角并修**

对每个失败：
- 若测试**意在验 TP1 口径专属行为** → 在该测试 setup 显式 `ladder_rr_enabled=False`（pin 旧口径），保持其本意。
- 若测试只是断言「某计划的 effective_rr=X」而不关心口径 → 更新期望值为 ladder 口径结果（用实际跑出的值，注释说明因默认开 lever2）。
- 不得为「让测试过」而弱化断言或删覆盖。

- [ ] **Step 3: 复跑全量绿**

Run: `python3 -m pytest -q`
Expected: `<baseline+N> passed`（基线 1285 + 本 change 新增；无 fail）

- [ ] **Step 4: 提交**

```bash
git add tests/
git commit -m "test(levers): update effective_rr assertions for ladder default-on (pin TP1 where intended)"
```

### Task 4: 验证证据（非代码任务，结果入验证报告）

**Files:** 无（运行既有驱动 + 记录）

- [ ] **Step 1: 同构历史验证——重跑 rejected 流 A/B**

Run: `python3 cf_lever2_rejected_ab.py 2>&1 | grep -v Warning | tail -30`
记录：lever2 臂 net R/簇、含亏单期望、可结算簇数。作验证报告主证据。

- [ ] **Step 2: event_backtest 非回归 sanity**

Run: `python3 event_backtest.py 2>&1 | tail -20`（或项目既定调用）
确认非崩溃、行为与改动前一致（event_backtest 不读 flag，预期零差异）；记录其不适用原因。

- [ ] **Step 3: 全量回归再确认**

Run: `python3 -m pytest -q`
Expected: 全绿。

---

## Self-Review

- **Spec coverage**：delta「默认启用」→ Task1（DEFAULTS）；「env 逃生阀」→ Task1 env map + 测试；「lever1 不随之开」→ Task2 path_evidence 维持 False + 测试；「过正常地板不走 low_rr_policies」→ lever2 抬高 R:R 的语义由既有逻辑保证，Task3 回归覆盖。验证栈（rejected A/B + event_backtest 非回归）→ Task4。无遗漏。
- **Placeholder scan**：无 TBD；每步含实际代码/命令/期望。Task3 的「视失败而定」是三角程序非占位（给了明确判据）。
- **Type consistency**：`ladder_rr_enabled` 键名贯穿 config_loader / judge / decision_replay / 测试一致；`_to_bool` 沿用既有。
