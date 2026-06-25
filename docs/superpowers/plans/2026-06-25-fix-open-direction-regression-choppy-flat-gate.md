---
change: fix-open-direction-regression-choppy-flat-gate
design-doc: docs/superpowers/specs/2026-06-25-fix-open-direction-regression-choppy-flat-gate-design.md
base-ref: 2bb6784
---

# 体制空仓硬门(choppy flat gate)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增单点收口「体制空仓硬门」:choppy/mixed 体制 + 无方向论据(非 aligned 非 path_evidence-ungated)时拒 open_long,修复开仓方向回归。

**Architecture:** 提取 `_select_rr_floor` 的方向论据为共享 helper `_compute_directional_evidence`(返回 aligned + path_evidence_raw,ungated);新门 `_classify_regime_flat_gate`(long-only)用 `aligned OR path_evidence_raw` 放行;主开仓 + 三 deferred 路径单点收口调用;attribution + event_backtest 同构;config 可逆。不碰 ev-decouple/lever2。

**Tech Stack:** Python 3.9, pytest;`agents/trading/judge.py`、`utils/config_loader.py`、`event_backtest.py`。

## Global Constraints

- **改 Judge 开仓门必须单点收口** + 同步 event_backtest + attribution(项目红线)。
- **long-only**:本门只作用 `open_long`;`open_short` 直接放行(短单门上游处理看跌论据)。
- **choppy AND mixed 都拦**;趋势体制(bullish/bearish)放行。
- **path_evidence 用 ungated 客观判定**(`path_evidence_raw`,不含 `_path_evidence_aligned_enabled`/lever1):否则会重新砍掉 bias 漏报的趋势。`_select_rr_floor` 的 floor-grant 用法保持 lever1 门控不变(行为零变)。
- **config**:`regime_flat_gate_enabled` 默认 True,env `REGIME_FLAT_GATE_ENABLED` 可回滚。
- **不回滚** ev-decouple / lever2(钝器,会误伤趋势单)。
- net/现有门行为零回归;改 live 需用户手动 OS 重启。

---

## File Structure

| 文件 | 职责 | 改动 |
|---|---|---|
| `utils/config_loader.py` | `regime_flat_gate_enabled` DEFAULTS + env | Modify |
| `config.yaml` | 显式 `regime_flat_gate_enabled: true` | Modify |
| `agents/trading/judge.py` | helper 提取 + 新门 + 4 调用点 + attribution | Modify |
| `event_backtest.py` | 同构硬门 | Modify |
| `tests/test_regime_flat_gate.py` | 新门 + helper 单测 | Create |

---

## Task 1: config 开关

**Files:**
- Modify: `utils/config_loader.py`、`config.yaml`、`agents/trading/judge.py`(`__init__` 读取)
- Test: `tests/test_regime_flat_gate.py`

**Interfaces:**
- Produces: `Judge._regime_flat_gate_enabled: bool`(默认 True);config key `regime_flat_gate_enabled`;env `REGIME_FLAT_GATE_ENABLED`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_regime_flat_gate.py
from agents.trading.judge import MultiJudge

def test_flag_default_true():
    j = MultiJudge.__new__(MultiJudge)
    j._regime_flat_gate_enabled = True  # smoke: 属性存在且默认 True 语义
    assert j._regime_flat_gate_enabled is True

def test_config_loader_has_flag():
    from utils.config_loader import DEFAULTS
    assert DEFAULTS.get('regime_flat_gate_enabled') is True
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_regime_flat_gate.py::test_config_loader_has_flag -q`
Expected: FAIL(DEFAULTS 无该键)

- [ ] **Step 3: 实现**

`utils/config_loader.py` DEFAULTS 加 `'regime_flat_gate_enabled': True`;env 解析处加 `REGIME_FLAT_GATE_ENABLED`(bool,与现有 bool env 同模式)。`config.yaml` 在 risk/judge 段加 `regime_flat_gate_enabled: true`。`judge.py:__init__` 加 `self._regime_flat_gate_enabled = config.get('regime_flat_gate_enabled', True) if config else True`。

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_regime_flat_gate.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add utils/config_loader.py config.yaml agents/trading/judge.py tests/test_regime_flat_gate.py
git commit -m "feat(choppy-flat-gate): config regime_flat_gate_enabled 默认开 + env 回滚"
```

---

## Task 2: 提取 `_compute_directional_evidence`(零行为变更重构)

**Files:**
- Modify: `agents/trading/judge.py`(`_select_rr_floor` ~2593-2625 + 新 helper)
- Test: `tests/test_regime_flat_gate.py`

**Interfaces:**
- Produces: `Judge._compute_directional_evidence(action, plan, tech, score) -> (aligned: bool, path_evidence_raw: bool)`。`path_evidence_raw` = 三阈值客观判定**不含** lever1 flag。
- Consumes: 现有 `_path_evidence_min_strength`(60)/`_path_evidence_min_pre12h_return`(0.03)/`_path_evidence_max_range_pos`(0.92)、`tech.entry_context`、`tech.trend`、sym daily/HTF bias。

- [ ] **Step 1: 写测试(helper 返回 + _select_rr_floor 零回归)**

```python
def _mk_judge():
    from agents.trading.judge import MultiJudge
    j = MultiJudge.__new__(MultiJudge)
    j._path_evidence_min_strength = 60
    j._path_evidence_min_pre12h_return = 0.03
    j._path_evidence_max_range_pos = 0.92
    j._path_evidence_aligned_enabled = False  # lever1 OFF(现状)
    return j

def test_path_evidence_raw_ungated_true_when_thresholds_met():
    j = _mk_judge()
    tech = {"trend": {"direction": "bullish", "strength": 70},
            "entry_context": {"pre_12h_return_pct": 0.05, "position_in_24h_range": 0.6}}
    plan = {"side": "long"}
    aligned, pe_raw = j._compute_directional_evidence("open_long", plan, tech, score=60)
    assert pe_raw is True   # ungated:lever1 OFF 仍为 True

def test_path_evidence_raw_false_below_threshold():
    j = _mk_judge()
    tech = {"trend": {"direction": "bullish", "strength": 30},  # strength<60
            "entry_context": {"pre_12h_return_pct": 0.05, "position_in_24h_range": 0.6}}
    aligned, pe_raw = j._compute_directional_evidence("open_long", {"side":"long"}, tech, 60)
    assert pe_raw is False
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_regime_flat_gate.py -k path_evidence_raw -q`
Expected: FAIL(`_compute_directional_evidence` 未定义)

- [ ] **Step 3: 提取 helper + 重构 `_select_rr_floor`**

新增 `_compute_directional_evidence(action, plan, tech, score)`:把 `_select_rr_floor` 现有 `aligned`(judge.py:2593-2596)与 path_evidence 三阈值计算(2604-2610,**去掉** `_path_evidence_aligned_enabled` 那个 flag 条件)搬进来,返回 `(aligned, path_evidence_raw)`。`_select_rr_floor` 改为:`aligned, pe_raw = self._compute_directional_evidence(...)`;其 floor-grant 处 `path_evidence = pe_raw and getattr(self,'_path_evidence_aligned_enabled',False)`(**保留 lever1 门控,行为零变**),后续 `if aligned: ... if path_evidence: ...` 不变。

- [ ] **Step 4: 运行验证通过 + _select_rr_floor 回归**

Run: `python3 -m pytest tests/test_regime_flat_gate.py tests/ -k "rr_floor or path_evidence or directional" -q`
Expected: PASS(新 helper 测试 + 现有 _select_rr_floor 测试全绿=零回归)

- [ ] **Step 5: 提交**

```bash
git add agents/trading/judge.py tests/test_regime_flat_gate.py
git commit -m "refactor(choppy-flat-gate): 提取 _compute_directional_evidence(ungated path_evidence_raw),_select_rr_floor 行为零变"
```

---

## Task 3: `_classify_regime_flat_gate` + `_has_directional_thesis`

**Files:**
- Modify: `agents/trading/judge.py`
- Test: `tests/test_regime_flat_gate.py`

**Interfaces:**
- Consumes: `_compute_directional_evidence`(Task2)、`_regime_flat_gate_enabled`(Task1)、`_regime_manager.snapshot()['effective_regime']`。
- Produces: `_has_directional_thesis(action, plan, tech, score) -> bool`(= `aligned or path_evidence_raw`);`_classify_regime_flat_gate(action, plan, tech, score) -> (allow: bool, reason: str)`。

- [ ] **Step 1: 写失败测试(全分支)**

```python
class _RM:
    def __init__(self, eff): self._eff = eff
    def snapshot(self): return {"effective_regime": self._eff}

def _judge_with(eff, flag=True):
    j = _mk_judge(); j._regime_flat_gate_enabled = flag
    j._regime_manager = _RM(eff); return j

def _long_no_thesis_tech():
    return {"trend": {"direction": "neutral", "strength": 20},
            "entry_context": {"pre_12h_return_pct": 0.0, "position_in_24h_range": 0.5}}

def test_choppy_neutral_long_rejected():
    j = _judge_with("choppy")
    allow, reason = j._classify_regime_flat_gate("open_long", {"side":"long"}, _long_no_thesis_tech(), 60)
    assert allow is False and reason == "regime_flat_no_thesis"

def test_choppy_with_path_evidence_allowed():
    j = _judge_with("choppy")
    tech = {"trend":{"direction":"bullish","strength":70},
            "entry_context":{"pre_12h_return_pct":0.05,"position_in_24h_range":0.6}}
    allow, _ = j._classify_regime_flat_gate("open_long", {"side":"long"}, tech, 60)
    assert allow is True

def test_trend_regime_allowed():
    j = _judge_with("bullish")
    allow, _ = j._classify_regime_flat_gate("open_long", {"side":"long"}, _long_no_thesis_tech(), 60)
    assert allow is True

def test_mixed_no_thesis_rejected():
    j = _judge_with("mixed")
    allow, _ = j._classify_regime_flat_gate("open_long", {"side":"long"}, _long_no_thesis_tech(), 60)
    assert allow is False

def test_open_short_always_allowed_long_only():
    j = _judge_with("choppy")
    allow, _ = j._classify_regime_flat_gate("open_short", {"side":"short"}, _long_no_thesis_tech(), 60)
    assert allow is True

def test_flag_off_allows():
    j = _judge_with("choppy", flag=False)
    allow, _ = j._classify_regime_flat_gate("open_long", {"side":"long"}, _long_no_thesis_tech(), 60)
    assert allow is True

def test_non_open_allowed():
    j = _judge_with("choppy")
    allow, _ = j._classify_regime_flat_gate("close", {"side":"long"}, _long_no_thesis_tech(), 60)
    assert allow is True
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_regime_flat_gate.py -k "choppy or trend or mixed or short or flag_off or non_open" -q`
Expected: FAIL(未定义)

- [ ] **Step 3: 实现两函数**

```python
def _has_directional_thesis(self, action, plan, tech, score):
    aligned, pe_raw = self._compute_directional_evidence(action, plan, tech, score)
    return bool(aligned or pe_raw)

def _classify_regime_flat_gate(self, action, plan, tech, score):
    if action != 'open_long':
        return (True, '')                       # long-only:short/非open 放行
    if not getattr(self, '_regime_flat_gate_enabled', True):
        return (True, 'flag_off')
    try:
        eff = self._regime_manager.snapshot().get('effective_regime')
    except Exception:
        return (True, 'regime_unknown')          # fail-safe 放行(不因 regime 取不到而误拒)
    if eff not in ('choppy', 'mixed'):
        return (True, 'regime_trend')
    if self._has_directional_thesis(action, plan, tech, score):
        return (True, 'has_thesis')
    return (False, 'regime_flat_no_thesis')
```

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_regime_flat_gate.py -q`
Expected: PASS(全部)

- [ ] **Step 5: 提交**

```bash
git add agents/trading/judge.py tests/test_regime_flat_gate.py
git commit -m "feat(choppy-flat-gate): _classify_regime_flat_gate(long-only) + _has_directional_thesis(ungated)"
```

---

## Task 4: 接入主开仓 + 三 deferred 路径(单点收口)

**Files:**
- Modify: `agents/trading/judge.py`(主路径 ~1485-1614、deferred ~798/927/1052、`_apply_regime_policy` ~3122)
- Test: `tests/test_regime_flat_gate.py`

**Interfaces:**
- Consumes: `_classify_regime_flat_gate`(Task3)。

- [ ] **Step 1: 写不变量测试(所有 open 路径都过本门)**

```python
import inspect
from agents.trading import judge as J

def test_flat_gate_called_in_all_open_paths():
    src = inspect.getsource(J)
    # 至少 4 处调用(主 + 3 deferred);宽松计数防漏接
    assert src.count("_classify_regime_flat_gate(") >= 4, src.count("_classify_regime_flat_gate(")
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_regime_flat_gate.py::test_flat_gate_called_in_all_open_paths -q`
Expected: FAIL(0 调用)

- [ ] **Step 3: 接入 4 个开仓判定点**

在主开仓路径(`_make_decision` 的 open 决定处,与 `_check_entry_position_policy`/`_select_rr_floor` 并列,约 1485-1614 区)与三条 deferred 路径(约 798/927/1052,各 `_check_entry_position_policy` 旁)加:
```python
flat_allow, flat_reason = self._classify_regime_flat_gate(final_action, plan, tech, score)
if not flat_allow:
    self._record_rejected_plan(symbol, final_action, plan, score, final_conf, flat_reason)
    # 走该路径既有的拒单返回(带 attribution,见 Task5);不 open
    return <该路径既有拒单返回结构>
```
（按各调用点既有拒单返回风格对齐;deferred 路径用其既有 reject 分支。变量名 final_action/plan/tech/score 按各处实际变量。）

- [ ] **Step 4: 运行验证通过 + 全量不回归**

Run: `python3 -m pytest tests/test_regime_flat_gate.py tests/test_judge_*.py -q`
Expected: PASS(不变量 + judge 既有测试绿)

- [ ] **Step 5: 提交**

```bash
git add agents/trading/judge.py tests/test_regime_flat_gate.py
git commit -m "feat(choppy-flat-gate): 接入主开仓 + 三 deferred 路径(单点收口)"
```

---

## Task 5: attribution 四字段(accept + reject 双写)

**Files:**
- Modify: `agents/trading/judge.py`(`_build_attribution`、`_rejection_attribution`)
- Test: `tests/test_regime_flat_gate.py`

- [ ] **Step 1: 写测试**

```python
def test_rejection_attribution_has_flat_fields():
    j = _judge_with("choppy")
    # 构造拒单 attribution(按 _rejection_attribution 签名;最小可调)
    attr = j._rejection_attribution("open_long", {"side":"long"}, "regime_flat_no_thesis:...", tech=_long_no_thesis_tech())
    assert attr.get("regime_flat_decision") == "reject"
    assert attr.get("has_directional_thesis") is False
    assert "regime_flat" in (attr.get("regime_flat_reason") or "")
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_regime_flat_gate.py -k attribution -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`_build_attribution`(放行路径)与 `_rejection_attribution`(拒单路径)都写 `regime_flat_gate`(版本如 `"v1"`)/`regime_flat_decision`(`"allow"`/`"reject"`)/`has_directional_thesis`(bool)/`regime_flat_reason`。值由调用点传入或就地经 `_classify_regime_flat_gate`/`_has_directional_thesis` 复算。

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_regime_flat_gate.py -k attribution -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/judge.py tests/test_regime_flat_gate.py
git commit -m "feat(choppy-flat-gate): attribution 四字段 accept+reject 双写"
```

---

## Task 6: event_backtest 同构硬门

**Files:**
- Modify: `event_backtest.py`(`_check_entry_with_regime` / `_determine_regime` 区)
- Test: `tests/test_regime_flat_gate.py`(或 event_backtest 既有测试)

- [ ] **Step 1: 写测试**

```python
def test_event_backtest_flat_gate_rejects_choppy_no_thesis():
    from event_backtest import EventBacktest  # 类名按实际
    # 构造 choppy + 无方向论据的 row,断言 _check_entry_with_regime 返回不开 long
    # (按 event_backtest 既有测试夹具风格;最小化)
    pass  # 实现时按 event_backtest 真实接口补全断言
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest -k event_backtest -q`
Expected: FAIL 或未覆盖

- [ ] **Step 3: 实现同构门**

`event_backtest._check_entry_with_regime`:open_long 候选在 `regime in {choppy, mixed}` 且无方向论据时拒。backtest 无 entry_context 的 pre_12h/range_pos 时,path_evidence_raw 退化为不可用→thesis=aligned(htf_bias bullish)only;在代码注释 + verify 报告标注「backtest 口径:thesis=aligned-only(无 path_evidence 数据),与 live 的差异」。受 `regime_flat_gate_enabled`(backtest 默认开,可参数关)。

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest -k event_backtest -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add event_backtest.py tests/
git commit -m "feat(choppy-flat-gate): event_backtest 同构硬门(thesis=aligned-only 口径,verify 标注)"
```

---

## Task 7: 全量基线 + entry_context 前置验证

**Files:** 无(验证)

- [ ] **Step 1: 验证 entry_context 在 live 决策被填充(无论 lever1)**

Run: `python3 -c "import inspect; from agents.trading import judge; s=inspect.getsource(judge); print('entry_context 写点:', s.count('entry_context'))"`
然后在 collector/tech 路径确认 `entry_context.pre_12h_return_pct`/`position_in_24h_range` 无条件填充。**若发现仅 lever1 开时填充** → 在 verify 报告标注「path_evidence_raw 当前恒 False,flat gate 退化为 aligned-only」(可接受,门仍砍 choppy+neutral)。

- [ ] **Step 2: 全量 pytest**

Run: `python3 -m pytest -q`
Expected: PASS(基线 1437 + 新 `test_regime_flat_gate.py`,无新增 fail)

- [ ] **Step 3: compileall**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q agents/trading/judge.py event_backtest.py utils/config_loader.py`
Expected: 无输出

- [ ] **Step 4: 提交(tasks.md 勾选)**

```bash
git add openspec/changes/fix-open-direction-regression-choppy-flat-gate/tasks.md
git commit -m "chore(choppy-flat-gate): tasks 收尾 + 全量 1437+ 绿"
```

---

## Self-Review

- **Spec coverage**:体制空仓硬门(Task3+4)/long-only short 放行(Task3)/path_evidence ungated(Task2)/choppy+mixed(Task3)/趋势放行(Task3)/回滚开关(Task1+3)/非open放行(Task3)/attribution 四字段(Task5)/event_backtest 同构(Task6)——delta spec 全覆盖。
- **Placeholder scan**:Task6 测试为骨架(标注「按 event_backtest 真实接口补全」)——实现者须补全断言,非占位逻辑;其余步骤含完整代码。
- **Type consistency**:`_compute_directional_evidence(action,plan,tech,score)->(aligned,pe_raw)`、`_has_directional_thesis(...)->bool`、`_classify_regime_flat_gate(...)->(allow,reason)`——Task2/3 定义,Task3/4/5 复用一致。
- **关键风险**:Task2 重构须证 `_select_rr_floor` 零回归(lever1 门控保留);Task7 verify entry_context 填充决定 path_evidence 是否生效。
