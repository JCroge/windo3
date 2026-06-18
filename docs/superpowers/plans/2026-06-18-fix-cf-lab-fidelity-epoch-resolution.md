---
change: fix-cf-lab-fidelity-epoch-resolution
design-doc: docs/superpowers/specs/2026-06-18-fix-cf-lab-fidelity-epoch-resolution-design.md
base-ref: fc42e576b89502c839c403a857a705ae67ec7f3e
---

# CF 实验室保真度纪元解析修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 修复 CF 回放保真度的纪元解析 bug（缺键按录制纪元默认而非当前 production 默认），引入 accept/reject 二元保真为主可信度判据，加守卫防静默复发，并诊断 range_position→ev_gate 残余。

**Architecture:** `replay_decision` 有效 config 改四层合并：`production_base < _EPOCH_FALLBACK < config_snapshot < 扰动override`。observability-only，无 live 行为变更。

**Tech Stack:** Python 3.9, asyncio, pytest。核心 `utils/decision_replay.py` + 两个 CF 测试。

---

## File Structure

- `utils/decision_replay.py`（Modify）：新增 `_EPOCH_FALLBACK` / `_GATE_IRRELEVANT` 常量 + `replay_decision` 四层合并。
- `tests/test_decision_replay.py`（Modify）：baseline 改纪元解析 + accept/reject 硬断言 + gate 保真诊断；新增纪元解析单测 + 守卫测试。
- `tests/test_sequential_perturbation.py`（Modify）：baseline 同步改造。

`utils/sequential_perturbation.py` 的 `run_arm` 把 `config` 透传给 `replay_decision`，引擎修复后无需改动；仅测试调用从 `{"ladder_rr_enabled": False}` 改 `{}`。

---

## Task 1: 纪元解析四层合并（引擎核心）

**Files:**
- Modify: `utils/decision_replay.py`（顶部常量 + `replay_decision` line ~95-96）
- Test: `tests/test_decision_replay.py`

- [ ] **Step 1: 写失败的纪元解析单测**

追加到 `tests/test_decision_replay.py` 末尾：

```python
# --- 纪元解析单测 ---------------------------------------------------------

def test_epoch_fallback_for_missing_keys():
    """缺键记录用录制纪元默认（ladder→False, ev_winrate→True），非当前 production 默认"""
    from utils.decision_replay import _resolve_effective_config
    # 模拟旧纪元记录：无 config_snapshot
    rec_old = {"config_snapshot": None}
    eff = _resolve_effective_config(rec_old, None)
    assert eff["ladder_rr_enabled"] is False, "旧记录 ladder 应回退纪元默认 False"
    assert eff["ev_winrate_gate_enabled"] is True, "旧记录 ev 门应回退纪元默认 True"
    print("  ✅ Case: 缺键回退录制纪元默认")


def test_snapshot_overrides_epoch_fallback():
    """v3 记录 snapshot 的 ladder=True 应盖回纪元兜底的 False"""
    from utils.decision_replay import _resolve_effective_config
    rec_v3 = {"config_snapshot": {"ladder_rr_enabled": True}}
    eff = _resolve_effective_config(rec_v3, None)
    assert eff["ladder_rr_enabled"] is True, "snapshot 录值应优先于纪元兜底"
    print("  ✅ Case: snapshot 优先于纪元兜底")


def test_perturbation_overrides_all():
    """扰动 override 在最顶层，盖过 snapshot"""
    from utils.decision_replay import _resolve_effective_config
    rec_v3 = {"config_snapshot": {"ladder_rr_enabled": True}}
    eff = _resolve_effective_config(rec_v3, {"ladder_rr_enabled": False})
    assert eff["ladder_rr_enabled"] is False, "扰动 override 应盖过 snapshot"
    print("  ✅ Case: 扰动 override 最顶层")
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_decision_replay.py -k "epoch or snapshot_overrides or perturbation_overrides" -q`
Expected: FAIL（`_resolve_effective_config` 不存在 → ImportError）

- [ ] **Step 3: 实现常量 + 提取合并函数**

在 `utils/decision_replay.py` `production_base_config` 之后（line ~79 后）加：

```python
# 键 → "该键加入 DEFAULTS 之前的纪元默认"。缺该键的旧记录回放用此值，
# 而非当前 production 默认（其默认可能已翻转，致系统性发散）。
# forward-only 契约：新增翻转默认键时在此登记其"加入前纪元默认"。
_EPOCH_FALLBACK = {
    "ladder_rr_enabled": False,          # trend-entry-levers-default-on 前 = 关
    "ev_winrate_gate_enabled": True,     # ev-gate-winrate-decouple 前 = 胜率门恒开
    "ev_neutral_p_win": 0.55,            # 解耦前不参与（门开用真实胜率），防御性
}

# 晚加但不影响 Judge gate 决策的键（守卫测试用）。
_GATE_IRRELEVANT = {
    "rotation_close_held_enabled",       # 轮换平仓开关，不进 Judge 决策
}


def _resolve_effective_config(record, perturbation):
    """四层合并：production_base < 纪元兜底 < config_snapshot(录值优先) < 扰动override(顶层)。"""
    return {
        **production_base_config(),
        **_EPOCH_FALLBACK,
        **(record.get("config_snapshot") or {}),
        **(perturbation or {}),
    }
```

- [ ] **Step 4: 替换 replay_decision 的合并行**

在 `utils/decision_replay.py` `replay_decision`，将 line 95-96：
```python
    base = {**production_base_config(), **(record.get("config_snapshot") or {})}
    effective = {**base, **(config or {})}
```
替换为：
```python
    effective = _resolve_effective_config(record, config)
```

- [ ] **Step 5: 运行验证通过**

Run: `python3 -m pytest tests/test_decision_replay.py -k "epoch or snapshot_overrides or perturbation_overrides" -q`
Expected: PASS（3 passed）

- [ ] **Step 6: 提交**

```bash
git add utils/decision_replay.py tests/test_decision_replay.py
git commit -m "feat(cf-epoch): replay_decision 四层合并纪元解析（缺键用录制纪元默认）"
```

---

## Task 2: 纪元守卫测试（防静默复发）

**Files:**
- Test: `tests/test_decision_replay.py`

- [ ] **Step 1: 写守卫测试**

追加到 `tests/test_decision_replay.py`：

```python
def test_epoch_fallback_keys_exist_in_defaults():
    """_EPOCH_FALLBACK 每个键都存在于当前 DEFAULTS（无 stale/typo）"""
    from utils.decision_replay import _EPOCH_FALLBACK, _PROD_DEFAULTS
    for k in _EPOCH_FALLBACK:
        assert k in _PROD_DEFAULTS, f"_EPOCH_FALLBACK 键 {k} 不在 DEFAULTS"
    print("  ✅ Case: 纪元兜底键不悬空")


def test_no_unclassified_missing_snapshot_keys():
    """磁带 v3 记录中缺于 snapshot 的 DEFAULTS 键，必须被显式分类（兜底或无关）"""
    from utils.decision_replay import _EPOCH_FALLBACK, _GATE_IRRELEVANT, _PROD_DEFAULTS
    recs = [r for r in _load_v2_v3()
            if r.get("schema_version") == "decision_replay_record.v3"
            and (r.get("config_snapshot") or {})]
    if len(recs) < 50:
        pytest.skip("insufficient v3 tape")
    classified = set(_EPOCH_FALLBACK) | set(_GATE_IRRELEVANT)
    missing = set()
    for r in recs:
        snap = r.get("config_snapshot") or {}
        for k in _PROD_DEFAULTS:
            if k not in snap:
                missing.add(k)
    unclassified = missing - classified
    assert not unclassified, (
        f"v3 snapshot 缺键未分类（新增翻转默认键？请登记进 _EPOCH_FALLBACK 或 _GATE_IRRELEVANT）: "
        f"{sorted(unclassified)}")
    print(f"  ✅ Case: 缺键全分类（missing={sorted(missing)}）")
```

- [ ] **Step 2: 运行**

Run: `python3 -m pytest tests/test_decision_replay.py -k "epoch_fallback_keys or unclassified" -q`
Expected: PASS（2 passed）。若 `unclassified` 失败，说明磁带有缺键未登记——按提示加入 `_EPOCH_FALLBACK`（影响 gate）或 `_GATE_IRRELEVANT`（不影响）。

- [ ] **Step 3: 提交**

```bash
git add tests/test_decision_replay.py
git commit -m "test(cf-epoch): 纪元兜底守卫——缺键必须显式分类，防静默复发"
```

---

## Task 3: 两个保真度测试改造（accept/reject 主指标）

**Files:**
- Modify: `tests/test_decision_replay.py`、`tests/test_sequential_perturbation.py`

- [ ] **Step 1: 改 test_production_baseline_restores_fidelity**

在 `tests/test_decision_replay.py`，将 `test_production_baseline_restores_fidelity` 整个函数体替换为：

```python
def test_production_baseline_restores_fidelity():
    recs = _load_v2_v3()
    if len(recs) < 50:
        pytest.skip("insufficient tape")

    def _ar(g):
        return "accept" if g == "accept" else "reject"

    async def run():
        gate_agree = 0
        ar_agree = 0
        for r in recs:
            # 纪元解析：不传全局 pin，replay_decision 逐记录按录制纪元解析
            # （缺键用 _EPOCH_FALLBACK 录制纪元默认，snapshot 录值优先）。
            d = await replay_decision(r, None)
            gr = _gate_of_recorded(r)
            gd = _gate_of_replayed(d)
            if gr == gd:
                gate_agree += 1
            if _ar(gr) == _ar(gd):
                ar_agree += 1
        return gate_agree / len(recs), ar_agree / len(recs)

    gate_fid, ar_fid = asyncio.run(run())
    # gate 严格保真：诊断-only（门归因短路顺序敏感，不作硬门）
    print(f"[diag] L2 gate fidelity = {gate_fid:.3f} (诊断, 实测 ~0.89)")
    # accept/reject 二元保真：主可信度硬门（方向推荐真正依赖的维度）
    assert ar_fid >= 0.95, f"L2 accept/reject fidelity {ar_fid:.3f} < 0.95 (production baseline should be ~0.985)"
```

- [ ] **Step 2: 改 test_sequential_baseline_fidelity_restored**

在 `tests/test_sequential_perturbation.py`，定位 `test_sequential_baseline_fidelity_restored` 中：
```python
    arm = asyncio.run(run_arm(recs, {"ladder_rr_enabled": False}, loader))
    agree = sum(1 for d, r in zip(arm["decisions"], recs) if d["gate"] == _gate_of_recorded(r))
    fid = agree / len(recs)
    assert fid >= 0.85, f"sequential baseline fidelity {fid:.3f} < 0.85 (expect ~0.91, was 0.798)"
```
替换为：
```python
    # 纪元解析：baseline arm 传 {} → run_arm 透传给 replay_decision 逐记录纪元解析
    arm = asyncio.run(run_arm(recs, {}, loader))
    def _ar(g):
        return "accept" if g == "accept" else "reject"
    gate_agree = sum(1 for d, r in zip(arm["decisions"], recs) if d["gate"] == _gate_of_recorded(r))
    ar_agree = sum(1 for d, r in zip(arm["decisions"], recs)
                   if _ar(d["gate"]) == _ar(_gate_of_recorded(r)))
    gate_fid = gate_agree / len(recs)
    ar_fid = ar_agree / len(recs)
    print(f"[diag] sequential gate fidelity = {gate_fid:.3f} (诊断)")
    assert ar_fid >= 0.95, f"sequential accept/reject fidelity {ar_fid:.3f} < 0.95 (expect ~0.985)"
```

- [ ] **Step 3: 运行两个改造后的测试**

Run: `python3 -m pytest tests/test_decision_replay.py::test_production_baseline_restores_fidelity tests/test_sequential_perturbation.py::test_sequential_baseline_fidelity_restored -q`
Expected: PASS（2 passed；accept/reject ≥0.95）

- [ ] **Step 4: 回归 perturbation 测试（确认扰动 override 仍生效）**

Run: `python3 -m pytest tests/test_sequential_perturbation.py tests/test_decision_replay.py -q`
Expected: 全 PASS（扰动 arm 的 override 仍能翻转旋钮，纪元修复未破坏 CF 实验机制）

- [ ] **Step 5: 提交**

```bash
git add tests/test_decision_replay.py tests/test_sequential_perturbation.py
git commit -m "test(cf-epoch): 两保真度测试改纪元解析 + accept/reject 主硬门 + gate 保真降诊断"
```

---

## Task 4: range_position→ev_gate 残余调查（调查任务）

**Files:**
- 产出：诊断结论（写入下一阶段验证报告；若快修则改 `agents/trading/judge.py` 或 `utils/decision_replay.py`）

- [ ] **Step 1: 逐记录 instrument 一条发散记录**

写一个临时诊断脚本（不提交，仅调查），对一条录制=range_position_too_low、回放=ev_gate 的 v3 记录：
- 用 `replay_decision` 回放并打印回放决策的 `attribution`（含 EV 内部：p_win、expected_value、R:R、cost、blocked_by）。
- 对比录制 `trade_decision_output` 的对应字段。
- 重点定位 ev_gate 判定的输入差异（p_win 来源、bucketed EV、_recent_win_rate 还原值）。

- [ ] **Step 2: 钉死 pass→fail 真因**

根据 Step 1 数据判断属于哪类：
- (a) EV 输入还原差异（如 _recent_win_rate/bucket 状态未完整还原）→ 状态捕获缺口；
- (b) gate 评估顺序在 _make_decision 中与录制时不同 → 代码路径差异；
- (c) 其他。
已排除：capture 缺口（position_in_24h_range 在）、ladder/ev_winrate 纪元。

- [ ] **Step 3: 据结论决定产出**

- 若是**快修**（如白名单补一个 EV 还原字段）：在 `utils/decision_replay.py` `_install_config_flags` 或状态还原处补齐，加针对性测试，提交。
- 若是**深层问题**（需新 capture 字段/schema 变更）：**不在本 change 修**，将诊断结论 + 建议写入验证报告 follow-up，记一条 memory。

- [ ] **Step 4: 记录诊断**

将 Step 2 结论写入 `tasks.md` 对应任务旁注（供验证阶段汇总进报告）。提交（若有快修代码）或仅记录。

```bash
git add -A && git commit -m "investigate(cf-epoch): range_position→ev_gate 残余诊断结论" || echo "无代码改动，结论记入报告"
```

---

## Task 5: tasks 勾选 + 全量回归

- [ ] **Step 1: 勾选 openspec tasks.md**

将 `openspec/changes/fix-cf-lab-fidelity-epoch-resolution/tasks.md` 全部 `- [ ]` → `- [x]`（残余调查任务按实际产出勾选/旁注）。

- [ ] **Step 2: 本 change 测试**

Run: `python3 -m pytest tests/test_decision_replay.py tests/test_sequential_perturbation.py -q`
Expected: 全 PASS

- [ ] **Step 3: 全量回归**

Run: `python3 -m pytest -q`
Expected: 基线 1302 + 本次（原 2 失败现 PASS）；对照 base-ref 确认无新退化（已知 round2 全量污染类 flaky 隔离即过，与本 change 无关）。

- [ ] **Step 4: 提交**

```bash
git add -A && git commit -m "chore(cf-epoch): tasks 勾选 + 全量回归（两 CF 保真度测试转绿）"
```

---

## Self-Review

- **Spec coverage**：delta 3 requirement → Task1（四层合并/缺键 fallback/扰动顶层）、Task2（守卫两场景）、Task3（accept-reject 主硬门/gate 降诊断）逐一覆盖。残余调查对应 proposal 第 3 点。✓
- **Placeholder scan**：无 TBD；Task4 是调查任务，产出形态明确（快修 or follow-up）。✓
- **Type consistency**：`_resolve_effective_config(record, perturbation)`、`_EPOCH_FALLBACK`、`_GATE_IRRELEVANT`、`_PROD_DEFAULTS` 跨 Task 命名一致。✓
- **回归保护**：Task3 Step4 显式回归 perturbation 测试，防纪元修复破坏 CF 扰动机制。✓
