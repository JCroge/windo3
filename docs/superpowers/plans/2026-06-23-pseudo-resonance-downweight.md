---
change: pseudo-resonance-downweight
design-doc: docs/superpowers/specs/2026-06-23-pseudo-resonance-downweight-design.md
base-ref: b8b498ea0c6acfa1d84edcba985cddb5245e2168
archived-with: 2026-06-23-pseudo-resonance-downweight
---

# 伪共振降权 (病根1a) Implementation Plan

> build_mode=direct (TDD)。Steps 用 checkbox。

**Goal:** `_compute_score` 把 rule/trend/htf 三段同源 MA 贡献合成「MA 趋势块」同向封顶，逼独立信号说话；config 可调可回退。

**Architecture:** 在 `_compute_score` 内把三段散落贡献重构成"各算 component → 合成 bloc → 同向封顶 → 一次加到 score"；独立信号/保护层不动。开关 + cap 走 config 四段式。CF 回放定 cap 默认值。

**Tech Stack:** Python 3.9, pytest, judge.py, config_loader.py, utils/decision_replay.py。

archived-with: 2026-06-23-pseudo-resonance-downweight
---

## Task 1: config 四段式（开关 + cap）

**Files:** `utils/config_loader.py`, `test_pseudo_resonance_downweight.py`

- [ ] **Step 1: 失败测试**
```python
from utils.config_loader import DEFAULTS, HARD_LIMITS
def test_config_defaults():
    assert DEFAULTS['pseudo_resonance_downweight_enabled'] is False  # 默认OFF保守起步
    assert DEFAULTS['ma_bloc_cap'] == 50  # 缓进起步(目标45,据CF回放收)
    assert HARD_LIMITS['ma_bloc_cap'] == (0, 100)
```
- [ ] **Step 2:** `pytest test_pseudo_resonance_downweight.py::test_config_defaults -v` → FAIL
- [ ] **Step 3:** config_loader 四处加（仿 reversal_veto 模式）：HARD_LIMITS `"ma_bloc_cap": (0,100)`；DEFAULTS `"pseudo_resonance_downweight_enabled": False, "ma_bloc_cap": 50`；yaml 块 `pseudo_resonance_downweight_enabled`(_to_bool)/`ma_bloc_cap`(float)；env `PSEUDO_RESONANCE_DOWNWEIGHT_ENABLED`/`MA_BLOC_CAP`。
- [ ] **Step 4:** PASS
- [ ] **Step 5:** commit `feat(pseudo-resonance): config 四段式 enabled + ma_bloc_cap`

## Task 2: 重构 _compute_score MA 块封顶

**Files:** `agents/trading/judge.py`(`__init__` 读config; `_compute_score` 3403), `test_pseudo_resonance_downweight.py`

- [ ] **Step 1: 失败测试**（helper 化封顶数学，便于单测）
```python
from agents.trading.judge import MultiJudge
def _j(enabled=True, cap=50):
    j=MultiJudge.__new__(MultiJudge); j._pseudo_resonance_downweight_enabled=enabled; j._ma_bloc_cap=cap; return j
def test_bloc_cap_same_dir():
    j=_j(cap=45)
    assert j._cap_ma_bloc(35+18+10) == 45     # 同向超cap削
def test_bloc_under_cap():
    j=_j(cap=45); assert j._cap_ma_bloc(30) == 30
def test_bloc_internal_offset():
    j=_j(cap=45); assert j._cap_ma_bloc(35-10) == 25  # 内部反向抵消后未超
def test_bloc_disabled_passthrough():
    j=_j(enabled=False, cap=45); assert j._cap_ma_bloc(63) == 63  # 关闭=线性不封顶
def test_bloc_negative():
    j=_j(cap=45); assert j._cap_ma_bloc(-63) == -45
```
- [ ] **Step 2:** FAIL
- [ ] **Step 3:** judge.py `__init__` 加：
```python
        self._pseudo_resonance_downweight_enabled = config.get('pseudo_resonance_downweight_enabled', False) if config else False
        self._ma_bloc_cap = config.get('ma_bloc_cap', 50) if config else 50
```
新增 helper：
```python
    def _cap_ma_bloc(self, ma_bloc_raw: float) -> float:
        """MA 趋势块同向封顶。关闭时线性透传（回退旧行为）。"""
        if not getattr(self, '_pseudo_resonance_downweight_enabled', False):
            return ma_bloc_raw
        cap = getattr(self, '_ma_bloc_cap', 50)
        if ma_bloc_raw == 0:
            return 0.0
        import math
        return math.copysign(min(abs(ma_bloc_raw), cap), ma_bloc_raw)
```
重构 `_compute_score`：把 §0 rule、§1 trend、§7 htf 三段从"直接 score+="改为累加到局部 `ma_bloc_raw`，最后 `score += self._cap_ma_bloc(ma_bloc_raw)`。**保留各 component 现有条件逻辑（trend 的 RSI×0.3 等）不变，仅改汇总方式**。独立信号(§2 RSI背离/§3 OI/§4鲸鱼/§5散户/§6 taker)与保护层(§极端cap/§4h)仍直接作用于 score。
- [ ] **Step 4:** PASS（含既有 judge 套件回归）
- [ ] **Step 5:** commit `feat(pseudo-resonance): _compute_score MA块同向封顶 + _cap_ma_bloc`

## Task 3: 归因字段

**Files:** `agents/trading/judge.py`, test

- [ ] **Step 1: 失败测试** 归因含 `ma_bloc_capped`/`ma_bloc_contribution`/`independent_contribution`。
- [ ] **Step 2:** FAIL
- [ ] **Step 3:** `_compute_score` 可选返回 bloc 诊断（或经 self 暂存 `_last_score_breakdown`），在 `_build_attribution` 写入三字段。最小侵入：`_compute_score` 末尾把 breakdown 存 `self._last_score_breakdown = {...}`；`_build_attribution` 读取写入（无则默认）。
- [ ] **Step 4:** PASS
- [ ] **Step 5:** commit `feat(pseudo-resonance): MA块/独立信号贡献 + capped 归因`

## Task 4: config.yaml + banner

- [ ] config.yaml risk 段加 `pseudo_resonance_downweight_enabled: false` + `ma_bloc_cap: 50`（缓进，注释）。banner 加一行。commit。

## Task 5: 单测补全 + 回归

- [ ] `pytest test_pseudo_resonance_downweight.py -q` 全绿；回归 `test_long_entry_position_guard.py` / `tests/test_short_main_path_risk_guard.py` / `test_ev_gate.py` / `test_judge_15m_filter.py` / `test_reversal_confluence_veto.py` 零回归。commit。

## Task 6: CF 回放验证（红线）

**Files:** 复用/补 `utils/decision_replay.py` 驱动；report `docs/superpowers/reports/2026-06-23-pseudo-resonance-downweight-backtest.md`

- [ ] **Step 1:** 跑真实磁带回放，off vs on（cap=45/50），重算 `_make_decision`→`_compute_score`，统计 ma_bloc_capped 决策的 accept→reject/defer 翻转集 + 方向。
- [ ] **Step 2:** 翻转集 PnL 分布（join trade_history，同病根3/regime 口径）。
- [ ] **Step 3:** 核 (1) 翻转方向合理(砍纯MA无佐证追势单) (2) 翻转集 PnL 不变差 (3) 未触发cap决策不变(全量无回归)。
- [ ] **Step 4:** 报告落盘；据结果定 cap 默认值与上线缓进（default off→影子→on，或保守 cap）。commit。

## Task 7: 据 CF 定 default

- [ ] 据 Task 6 调 `ma_bloc_cap` 默认 + enabled 上线策略（保守起步）。commit。

## Self-Review
- Spec 3 Requirement → Task 2(封顶)、Task 1/4(开关可配)、Task 3(归因)。✅
- 类型：`_cap_ma_bloc(ma_bloc_raw)` 签名 Task 2 一致。✅
- 红线：CF 回放（非 event_backtest）；不碰独立信号/保护层。✅
