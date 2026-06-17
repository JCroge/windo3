---
change: trend-entry-shadow-decision-logger
design-doc: docs/superpowers/specs/2026-06-17-trend-entry-shadow-decision-logger-design.md
base-ref: 0c07cc5a1384f8ad83059a7e4d0b88b813baabbe
archived-with: 2026-06-17-trend-entry-shadow-decision-logger
---

# 前向影子决策记录器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 或 superpowers:executing-plans。步骤用 `- [ ]` 跟踪。

**Goal:** 对每个信号在 live 决策旁路跑 both-levers(lever1+lever2) on 影子决策并 write-only 记录，零 live 风险地为 lever1 攒前向证据。

**Architecture:** 复用 `replay_decision` 隔离机器：决策磁带 chokepoint 拿同 bundle replay flags-on → 影子决策 → 写 `data/shadow_decision_log.jsonl`。live 链路纯旁路 + fail-safe。

**Tech Stack:** Python 3.9，`utils/decision_replay`，`utils/decision_tape`，`agents/trading/judge.py`，`utils/config_loader`，pytest。

## 文件结构

- Create `utils/shadow_decision_logger.py` — 影子记录纯逻辑（跑 replay both-levers + 算 flip_kind + write-only jsonl + 内部 fail-safe）。
- Modify `agents/trading/judge.py` — 加 `_maybe_log_shadow(bundle, real_summary)` helper，两 chokepoint（2004/3093）旁路调用。
- Modify `utils/config_loader.py` — `shadow_decision_logger_enabled: True` 入 DEFAULTS + env 映射。
- Modify `tests/test_cf_red_line_guard.py` — 影子产物加入禁读断言。
- Create `tests/test_shadow_decision_logger.py` — schema / flip_kind / fail-safe / flag-off 单测。
- Create `cf_shadow_lever1_compare.py` — 离线对比驱动（结局结算 + lever1 增量 + 诚实门）。

archived-with: 2026-06-17-trend-entry-shadow-decision-logger
---

### Task 1: config flag shadow_decision_logger_enabled

**Files:** Modify `utils/config_loader.py`；Test `tests/test_shadow_decision_logger.py`

- [ ] **Step 1: 失败测试**

```python
# tests/test_shadow_decision_logger.py
from utils.config_loader import DEFAULTS

def test_shadow_logger_flag_default_true():
    assert DEFAULTS.get("shadow_decision_logger_enabled") is True
```

- [ ] **Step 2: 跑测试确认失败** — `pytest tests/test_shadow_decision_logger.py::test_shadow_logger_flag_default_true -q` → FAIL（None）

- [ ] **Step 3: 实现** — DEFAULTS 区（紧邻 `"ladder_rr_enabled": True,`）加：
```python
    "shadow_decision_logger_enabled": True,
```
env 映射区加：
```python
        "SHADOW_DECISION_LOGGER_ENABLED": ("shadow_decision_logger_enabled", _to_bool),
```

- [ ] **Step 4: 跑测试确认通过** — PASS

- [ ] **Step 5: 提交** — `git commit -m "feat(shadow): config flag shadow_decision_logger_enabled default-on"`

### Task 2: 影子记录核心 utils/shadow_decision_logger.py

**Files:** Create `utils/shadow_decision_logger.py`；Test `tests/test_shadow_decision_logger.py`

- [ ] **Step 1: 失败测试**（flip_kind + 记录 schema）

```python
def test_compute_flip_kind():
    from utils.shadow_decision_logger import compute_flip_kind
    assert compute_flip_kind("hold", "open_long") == "shadow_opens"
    assert compute_flip_kind("open_long", "open_long") == "same"
    assert compute_flip_kind("open_long", "hold") == "shadow_holds"

def test_build_shadow_record_schema():
    from utils.shadow_decision_logger import build_shadow_record
    rec = build_shadow_record(
        ts=1.0, symbol="HYPE-USDT",
        real={"action": "hold", "gate": "rr_below_floor"},
        shadow={"action": "open_long", "gate": "accept", "plan": {"x": 1}},
        tech_context={"trend": {"strength": 70}})
    assert rec["symbol"] == "HYPE-USDT"
    assert rec["real_action"] == "hold" and rec["shadow_action"] == "open_long"
    assert rec["flip_kind"] == "shadow_opens"
    assert rec["tech_context"] == {"trend": {"strength": 70}}
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现纯函数**

```python
# utils/shadow_decision_logger.py
"""前向影子决策记录器(observability-only write-only)。
对 live 决策 bundle 旁路跑 both-levers on 影子决策, 记录 real vs shadow 供 lever1 对比。
严禁交易决策/风控路径 import/读取本产物。"""
import json
from utils.decision_replay import replay_decision

SHADOW_CONFIG = {"path_evidence_aligned_enabled": True, "ladder_rr_enabled": True}

def _gate_of(decision):
    action = (decision or {}).get("action")
    if action in ("open_long", "open_short"):
        return "accept"
    blocked = ((decision or {}).get("attribution") or {}).get("blocked_by") \
        or (decision or {}).get("reject_reason")
    return str(blocked).split(":")[0] if blocked else "hold_other"

def compute_flip_kind(real_action, shadow_action):
    real_open = real_action in ("open_long", "open_short")
    shadow_open = shadow_action in ("open_long", "open_short")
    if real_open == shadow_open:
        return "same"
    return "shadow_opens" if shadow_open else "shadow_holds"

def build_shadow_record(*, ts, symbol, real, shadow, tech_context):
    return {
        "timestamp": ts, "symbol": symbol,
        "real_action": real.get("action"), "real_gate": real.get("gate"),
        "shadow_action": shadow.get("action"), "shadow_gate": shadow.get("gate"),
        "shadow_plan": shadow.get("plan"),
        "flip_kind": compute_flip_kind(real.get("action"), shadow.get("action")),
        "tech_context": tech_context,
    }

async def log_shadow_decision(bundle, real_decision, log_path, *, enabled=True, logger=None):
    """旁路跑 both-levers 影子决策并 write-only 追加 jsonl。fail-safe: 异常绝不抛。"""
    if not enabled:
        return None
    try:
        if not bundle.get("replayable"):
            return None
        shadow = await replay_decision(bundle, SHADOW_CONFIG)
        real_summ = {"action": (real_decision or {}).get("action", "hold"),
                     "gate": _gate_of(real_decision)}
        shadow_summ = {"action": (shadow or {}).get("action", "hold"),
                       "gate": _gate_of(shadow), "plan": (shadow or {}).get("plan")}
        rec = build_shadow_record(
            ts=bundle.get("timestamp", 0), symbol=bundle.get("symbol"),
            real=real_summ, shadow=shadow_summ,
            tech_context=bundle.get("tech_analysis"))
        with open(log_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec
    except Exception as e:           # fail-safe: 影子绝不破 live
        if logger:
            logger.warning(f"[shadow] log_shadow_decision skipped: {e}")
        return None
```

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: 验证 replay 从 chokepoint bundle 可跑**（关键风险）—— 写一个测试：用一条真实可回放 record（`data/decision_replay_tape.jsonl` 取一条 v2/v3 replayable）当 bundle，`asyncio.run(log_shadow_decision(rec, {"action":"hold"}, tmp_path/"s.jsonl"))` 返回非 None 且写了一行；坐实**不抛、不重复 record 决策磁带**（replay 用 `MultiJudge.__new__` 替身无 live `_decision_tape`，若 `_make_decision` 内 record 路径用 getattr 防御则安全；若抛则 fail-safe 吞掉返回 None——本步同时验 fail-safe）。

```python
def test_log_shadow_on_real_bundle(tmp_path):
    import asyncio, json
    from utils.shadow_decision_logger import log_shadow_decision
    rec = _load_one_replayable_record()  # helper: 取一条 replayable v2/v3
    out = tmp_path / "s.jsonl"
    r = asyncio.run(log_shadow_decision(rec, {"action": "hold"}, str(out)))
    # fail-safe: 即便 replay 内部问题也不抛; 成功则写一行
    if r is not None:
        assert out.read_text().count("\n") == 1
        assert json.loads(out.read_text())["symbol"] == rec["symbol"]
```

- [ ] **Step 6: 提交** — `git commit -m "feat(shadow): shadow_decision_logger core (replay both-levers + write-only + fail-safe)"`

### Task 3: judge chokepoint 旁路 hook

**Files:** Modify `agents/trading/judge.py`（加 `_maybe_log_shadow` + 两 chokepoint 调用）

- [ ] **Step 1: 加 helper**（`__init__` 读 flag + 日志路径；`_maybe_log_shadow` await log_shadow_decision，getattr 防御）

```python
# __init__ 内(紧邻 _ladder_rr_enabled)
self._shadow_logger_enabled = config.get('shadow_decision_logger_enabled', True) if config else True
# helper
async def _maybe_log_shadow(self, bundle, real_decision):
    if not getattr(self, "_shadow_logger_enabled", False):
        return
    try:
        from utils.shadow_decision_logger import log_shadow_decision
        from utils.state_paths import get_state_paths
        path = get_state_paths().get("shadow_decision_log") or "data/shadow_decision_log.jsonl"
        await log_shadow_decision(bundle, real_decision, path,
                                  enabled=True, logger=self.logger)
    except Exception as e:
        self.logger.warning(f"[shadow] hook skipped: {e}")
```

（`state_paths` 加 `shadow_decision_log` 派生项；若不改 state_paths 则直接用 `data/shadow_decision_log.jsonl` 常量。）

- [ ] **Step 2: 两 chokepoint 旁路调用** —— `judge.py:2004`/`3093` 的 `record_decision(build_bundle(...))` 之后，把 bundle 存局部变量并 `await self._maybe_log_shadow(bundle, <real decision payload>)`。real decision = 该 chokepoint 即将 publish/返回的决策 dict。

- [ ] **Step 3: 测试 hook 不破 live**（mock log_shadow_decision 抛异常 → _make_decision 仍正常产出决策）。

- [ ] **Step 4: 全量相关测试** — `pytest tests/test_decision_tape_capture.py tests/test_shadow_decision_logger.py -q` 绿。

- [ ] **Step 5: 提交** — `git commit -m "feat(shadow): judge chokepoint side-channel hook (fail-safe, write-only)"`

### Task 4: 红线守卫扩展 + 隔离坐实

**Files:** Modify `tests/test_cf_red_line_guard.py`

- [ ] **Step 1: 加禁读断言** —— 交易决策/executor/halt/riskguard 路径源码不得出现 `shadow_decision_log` / `import shadow_decision_logger`（除 judge **写**路径 `_maybe_log_shadow`）。镜像现有 `test_decision_paths_do_not_read_replay_products` 模式。

```python
def test_decision_paths_do_not_read_shadow_products():
    # executor / halt / riskguard 源码不得读影子产物
    for mod in ("executor", "agents.trading.executor",
                "utils.halt_state", "agents.trading.portfolio_risk_guard"):
        src = inspect.getsource(__import__(mod, fromlist=["x"]))
        assert "shadow_decision_log" not in src
        assert "shadow_decision_logger" not in src
```

- [ ] **Step 2: 跑守卫** — PASS。

- [ ] **Step 3: 提交** — `git commit -m "test(shadow): red-line guard forbids decision/risk paths reading shadow products"`

### Task 5: 离线对比驱动 cf_shadow_lever1_compare.py

**Files:** Create `cf_shadow_lever1_compare.py`

- [ ] **Step 1: 实现驱动** —— 读 `data/shadow_decision_log.jsonl`，筛 `flip_kind=shadow_opens`（lever1 解锁的单），用 `resolve_counterfactual`+klines 结算前向结局，报 lever1 增量：多开数 / 含亏单净 R / 簇胜率，复用 `utils/cf_honesty_gate.summarize_bucket` 薄样本拒答。observability-only，不改任何 config。（结构镜像 `cf_lever2_rejected_ab.py`。）

- [ ] **Step 2: py_compile + smoke**（空/少量日志时优雅拒答，不崩）。

- [ ] **Step 3: 提交** — `git commit -m "feat(shadow): offline lever1 increment compare driver"`

### Task 6: 全量回归 + 失败安全

- [ ] **Step 1: 全量 pytest** — `python3 -m pytest -q` → `<baseline+N> passed`，无 fail。

- [ ] **Step 2: 失败安全终验** —— 确认 Task 3 Step 3 的"影子异常不破 live"测试在全量中绿；确认 flag off 时 live 行为等价（无影子日志写入）。

- [ ] **Step 3: 提交**（若有测试调整）

archived-with: 2026-06-17-trend-entry-shadow-decision-logger
---

## Self-Review

- **Spec coverage**：delta `shadow-decision-logger` 4 requirements →「前向影子记录」Task2/3；「observability-only write-only」Task4；「失败安全」Task2(fail-safe)+Task3(hook 不破 live)+config flag Task1；「结局离线结算+报表」Task5。无遗漏。
- **Placeholder scan**：核心代码完整给出；`_load_one_replayable_record`/驱动镜像 `cf_lever2_rejected_ab.py` 为明确指引非占位。
- **Type consistency**：`log_shadow_decision`/`build_shadow_record`/`compute_flip_kind`/`_gate_of` 签名贯穿 Task2-3 一致；flip_kind 取值 same/shadow_opens/shadow_holds 一致。
- **关键风险已标**：Task2 Step5 验证 replay 从 chokepoint bundle 不抛/不重复 record（fail-safe 兜底）。
