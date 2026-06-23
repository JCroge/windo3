---
change: restore-llm-rsi-veto-power
design-doc: docs/superpowers/specs/2026-06-23-restore-llm-rsi-veto-power-design.md
base-ref: e13d91fe093b8ce5bbd2443f02798f6d90005df3
---

# 反转合流否决 (restore-llm-rsi-veto-power) Implementation Plan

> **For agentic workers:** build_mode=direct (用户选 TDD 直接实现)。Steps 用 checkbox 跟踪。

**Goal:** 当一笔开仓候选同时遇到「LLM 看反向」+「RSI 背离与开仓方向相反」双信号合流时，把它路由到等回调（deferred）而非立即开仓，恢复独立反转信号的否决权。

**Architecture:** judge.py 新增单一纯函数 `_reversal_confluence_veto(action, llm_action, tech)` 作唯一判定；主路径与 deferred 再分发点共用它；触发时复用现有 `deferred_entry` 机制（新 entry_type `deferred_reversal_veto` + 小幅回调目标价）；config 四段式开关可回退；不动 scoring。

**Tech Stack:** Python 3.9, pytest, 现有 judge.py / config_loader.py / event_backtest.py。

---

## 文件结构

- Modify: `utils/config_loader.py` — 新增 2 个 config 键的四段式接入（HARD_LIMITS/DEFAULTS/yaml/env）
- Modify: `agents/trading/judge.py` — `__init__` 读配置；新增 helper；主路径插入；deferred 再分发点插入；归因字段
- Modify: `config.yaml` — 新增 risk 键（开关，缓进 default 见 Task 8）
- Create: `test_reversal_confluence_veto.py` — 单测
- 验证: `event_backtest.py`（已有，跑 pre/post）

---

## Task 1: config_loader 四段式接入两个键

**Files:**
- Modify: `utils/config_loader.py`

- [ ] **Step 1: 写失败测试**

```python
# test_reversal_confluence_veto.py
from utils.config_loader import DEFAULTS, HARD_LIMITS

def test_reversal_veto_config_defaults():
    assert DEFAULTS['llm_rsi_reversal_veto_enabled'] is True
    assert DEFAULTS['reversal_veto_min_llm_confidence'] == 0
    assert HARD_LIMITS['reversal_veto_min_llm_confidence'] == (0, 100)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_reversal_confluence_veto.py::test_reversal_veto_config_defaults -v`
Expected: FAIL (KeyError)

- [ ] **Step 3: 实现**

`utils/config_loader.py` HARD_LIMITS 字典内（约 L64 附近）加：
```python
    "reversal_veto_min_llm_confidence": (0, 100),
```
DEFAULTS 字典内（约 L174 附近，紧随 regime 键）加：
```python
    "llm_rsi_reversal_veto_enabled": True,
    "reversal_veto_min_llm_confidence": 0,
```
yaml 覆盖块内（约 L260 后）加：
```python
    if 'llm_rsi_reversal_veto_enabled' in risk:
        out['llm_rsi_reversal_veto_enabled'] = _to_bool(risk['llm_rsi_reversal_veto_enabled'])
    if 'reversal_veto_min_llm_confidence' in risk:
        out['reversal_veto_min_llm_confidence'] = float(risk['reversal_veto_min_llm_confidence'])
```
env map 内（约 L334 后）加：
```python
        "LLM_RSI_REVERSAL_VETO_ENABLED": ("llm_rsi_reversal_veto_enabled", _to_bool),
        "REVERSAL_VETO_MIN_LLM_CONFIDENCE": ("reversal_veto_min_llm_confidence", float),
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest test_reversal_confluence_veto.py::test_reversal_veto_config_defaults -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add utils/config_loader.py test_reversal_confluence_veto.py
git commit -m "feat(reversal-veto): config 四段式接入 enabled + min_llm_confidence"
```

---

## Task 2: 单点收口 helper `_reversal_confluence_veto`

**Files:**
- Modify: `agents/trading/judge.py` (`__init__` 约 L216 后；helper 新增于 `_check_entry_position_policy` 附近 L2851 前)
- Test: `test_reversal_confluence_veto.py`

- [ ] **Step 1: 写失败测试**

```python
import types
from agents.trading.judge import MultiJudge

def _judge(enabled=True, min_conf=0):
    j = MultiJudge.__new__(MultiJudge)
    j._reversal_veto_enabled = enabled
    j._reversal_veto_min_llm_confidence = min_conf
    return j

def _tech(div):
    return {'momentum': {'rsi_divergence': div}}

def test_veto_confluence_long():
    j = _judge()
    assert j._reversal_confluence_veto('open_long', 'open_short', _tech('bearish_div')) == 'reversal_confluence'

def test_veto_confluence_short():
    j = _judge()
    assert j._reversal_confluence_veto('open_short', 'open_long', _tech('bullish_div')) == 'reversal_confluence'

def test_veto_only_llm_no_div():
    j = _judge()
    assert j._reversal_confluence_veto('open_long', 'open_short', _tech(None)) is None

def test_veto_only_div_no_llm():
    j = _judge()
    assert j._reversal_confluence_veto('open_long', 'hold', _tech('bearish_div')) is None

def test_veto_disabled():
    j = _judge(enabled=False)
    assert j._reversal_confluence_veto('open_long', 'open_short', _tech('bearish_div')) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_reversal_confluence_veto.py -k veto -v`
Expected: FAIL (AttributeError: _reversal_confluence_veto)

- [ ] **Step 3: 实现**

`agents/trading/judge.py` `__init__`（约 L217 后）加：
```python
        self._reversal_veto_enabled = config.get('llm_rsi_reversal_veto_enabled', True) if config else True
        self._reversal_veto_min_llm_confidence = config.get('reversal_veto_min_llm_confidence', 0) if config else 0
```
新增 helper（放在 `_check_entry_position_policy` 定义前，约 L2850）：
```python
    def _reversal_confluence_veto(self, action: str, llm_action: str, tech: dict,
                                  llm_confidence: float = 100.0) -> str | None:
        """反转合流否决判定。单一实现，所有开仓终点共用。
        返回 'reversal_confluence'=触发, None=不触发。不读 scoring。"""
        if not getattr(self, '_reversal_veto_enabled', True):
            return None
        dir_long = (action == 'open_long')
        dir_short = (action == 'open_short')
        if not (dir_long or dir_short):
            return None
        llm_counter = (
            llm_action in ('open_long', 'open_short')
            and llm_action != action
            and llm_confidence >= getattr(self, '_reversal_veto_min_llm_confidence', 0)
        )
        rsi_div = ((tech or {}).get('momentum', {}) or {}).get('rsi_divergence')
        rsi_against = (
            (dir_long and rsi_div == 'bearish_div')
            or (dir_short and rsi_div == 'bullish_div')
        )
        return 'reversal_confluence' if (llm_counter and rsi_against) else None
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest test_reversal_confluence_veto.py -k veto -v`
Expected: 5 PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/judge.py test_reversal_confluence_veto.py
git commit -m "feat(reversal-veto): 单点收口 helper _reversal_confluence_veto"
```

---

## Task 3: 共享 defer 路由 helper `_route_reversal_veto_defer`

**Files:**
- Modify: `agents/trading/judge.py`
- Test: `test_reversal_confluence_veto.py`

说明：复用现有 `deferred_entry` 机制（参 judge.py:1597-1647 overheat 构造），抽出单一路由函数，避免第二份内联实现。回调目标价用小幅 fraction（多单向下、空单向上），fraction 复用现有 pullback 配置或常量 0.005。

- [ ] **Step 1: 写失败测试**

```python
def test_route_defer_sets_state_and_decision():
    j = _judge()
    j._get_state = lambda s: j.__dict__.setdefault('_st', {})
    dec = j._route_reversal_veto_defer('BTC-USDT', 'open_long', price=100.0, score=40.0,
                                       tech=_tech('bearish_div'), llm_action='open_short')
    assert dec['action'] == 'hold'
    attr = dec['attribution']
    assert attr['reversal_veto_triggered'] is True
    assert attr['reversal_veto_deferred_dir'] == 'open_long'
    assert attr['reversal_veto_rsi_div'] == 'bearish_div'
    assert attr['reversal_veto_llm_action'] == 'open_short'
    st = j._st['deferred_entry']
    assert st['entry_type'] == 'deferred_reversal_veto'
    assert st['target_price'] < 100.0  # 多单等回调向下
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_reversal_confluence_veto.py::test_route_defer_sets_state_and_decision -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`agents/trading/judge.py` 新增（helper 旁）：
```python
    def _route_reversal_veto_defer(self, symbol: str, action: str, price: float,
                                   score: float, tech: dict, llm_action: str) -> dict:
        """反转合流否决 → 路由到 deferred_reversal_veto（复用 deferred_entry 机制）。
        返回 hold decision（含归因）。单一构造，杜绝第二份内联实现。"""
        frac = 0.005
        target = price * (1 - frac) if action == 'open_long' else price * (1 + frac)
        state = self._get_state(symbol)
        state['deferred_entry'] = {
            'action': action, 'signal_price': price, 'signal_score': score,
            'target_price': target, 'created_at': time.time(),
            'entry_type': 'deferred_reversal_veto',
            'timeout_hours': getattr(self, '_long_live_pullback_timeout_hours', 4),
            'expiry_bars': 999, 'chase_eligible': False,
            'highest_since': price, 'lowest_since': price,
        }
        rsi_div = ((tech or {}).get('momentum', {}) or {}).get('rsi_divergence')
        attr = self._rejection_attribution(action, None, 'reversal_confluence', tech=tech)
        attr['reversal_veto_triggered'] = True
        attr['reversal_veto_llm_action'] = llm_action
        attr['reversal_veto_rsi_div'] = rsi_div
        attr['reversal_veto_deferred_dir'] = action
        attr['deferred_target_price'] = target
        attr['deferred_reason'] = 'reversal_confluence'
        self.logger.warning(
            f"[Judge] {symbol} reversal confluence veto: {action} llm={llm_action} "
            f"rsi_div={rsi_div} -> deferred_reversal_veto target={target:.6f}"
        )
        return {
            "symbol": symbol, "timestamp": time.time(),
            "action": "hold", "confidence": 0, "plan": None, "size_pct": 0,
            "reasoning": f"反转合流否决: LLM={llm_action}+RSI背离{rsi_div}, 等待回调至{target:.6f}",
            "key_factors": ["reversal_confluence_veto"],
            "risk_warnings": ["reversal_confluence"],
            "attribution": attr,
        }
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest test_reversal_confluence_veto.py::test_route_defer_sets_state_and_decision -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/judge.py test_reversal_confluence_veto.py
git commit -m "feat(reversal-veto): 共享 defer 路由 _route_reversal_veto_defer + 归因"
```

---

## Task 4: 主路径插入 veto

**Files:**
- Modify: `agents/trading/judge.py` (主路径，约 L1310 之后、`_open_quality_rejection` L1312 之前)

- [ ] **Step 1: 写失败测试（集成，主路径触发 defer）**

```python
import asyncio
def test_main_path_veto_routes_defer(monkeypatch):
    # 构造最小 judge，mock _ask_llm 返回反向，tech 带 bearish_div + rule_signal long
    # 断言 publish 的 decision.attribution.reversal_veto_triggered True 且 entry_type deferred_reversal_veto
    ...  # 见实现时按现有 test_long_entry_position_guard.py 的 fixture 范式补全
```

> 注：集成测试 fixture 较重，实现时参照 `test_long_entry_position_guard.py` 的 MultiJudge 构造与 publish 捕获范式补全完整可运行代码（不留占位）。

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

在 judge.py 主路径 L1310（强冲突缩仓 warning）之后、L1312（`if final_action in ('open_long','open_short'): reject_reason = self._open_quality_rejection(...)`）之前插入：
```python
                _veto = self._reversal_confluence_veto(
                    final_action, llm_action, tech, llm_confidence=final_conf
                )
                if _veto:
                    self._record_rejected_plan(symbol, final_action, plan, score, final_conf, _veto)
                    decision = self._route_reversal_veto_defer(
                        symbol, final_action, price, score, tech, llm_action
                    )
                    await self.publish("trade_decision", decision, symbol=symbol)
                    return
```

- [ ] **Step 4: 运行确认通过**

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(reversal-veto): 主路径插入合流否决 -> deferred_reversal_veto"
```

---

## Task 5: deferred 再分发点覆盖核定（红线：单点收口）

**Files:**
- Modify: `agents/trading/judge.py` (deferred 再分发点：约 L805/L934/L1055 区域 + deferred_entry 重入 L746+)

- [ ] **Step 1: 核定** 逐一检查三条 deferred 再分发点（deferred_15m_confirmation / deferred_pullback / deferred_chase）在最终发 open 前是否可得 llm_action（经 `self._symbol_llm_cache.get(symbol)`）。

- [ ] **Step 2:** 对每个可得 llm 的再分发点，在其发 open 前调用同一 helper：
```python
                _veto = self._reversal_confluence_veto(
                    <action>, (self._symbol_llm_cache.get(symbol) or {}).get('action', 'hold'),
                    tech
                )
                if _veto:
                    decision = self._route_reversal_veto_defer(symbol, <action>, <price>, <score>, tech,
                        (self._symbol_llm_cache.get(symbol) or {}).get('action', 'hold'))
                    await self.publish("trade_decision", decision, symbol=symbol); return
```

- [ ] **Step 3:** 若某再分发点无 llm 上下文，**在代码注释 + design doc/delta spec 显式记录边界**（不写第二份判定），并在 tasks 勾选该核定结论。

- [ ] **Step 4: 测试** deferred 路径 parity（同输入主路径与 deferred 同判定）。

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(reversal-veto): deferred 再分发点共用 helper + 覆盖边界记录(红线)"
```

---

## Task 6: 放行路径写 reversal_veto_triggered=false

**Files:**
- Modify: `agents/trading/judge.py` (开仓放行的 attribution 构造处)
- Test: `test_reversal_confluence_veto.py`

- [ ] **Step 1: 写失败测试** 放行决策 attribution 含 `reversal_veto_triggered=false`。
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现** 在放行 attribution 构造处补 `attribution['reversal_veto_triggered'] = False`（单点：放行 attribution 的统一构造函数内）。
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: 提交** `git commit -am "feat(reversal-veto): 放行路径写 reversal_veto_triggered=false 归因"`

---

## Task 7: config.yaml 落开关 + banner

**Files:**
- Modify: `config.yaml`, `utils/config_loader.py` (banner)

- [ ] **Step 1:** `config.yaml` risk 段加（缓进 default 见 Task 8，先写开关存在）：
```yaml
  # 反转合流否决（restore-llm-rsi-veto-power）
  llm_rsi_reversal_veto_enabled: true
  reversal_veto_min_llm_confidence: 0
```
- [ ] **Step 2:** banner（config_loader 约 L515 区域）加一行显示开关状态。
- [ ] **Step 3:** 测试 config_loader 读出值正确。
- [ ] **Step 4: 提交** `git commit -am "feat(reversal-veto): config.yaml 开关 + banner"`

---

## Task 8: event_backtest 验证（CLAUDE.md 红线）

**Files:**
- 使用: `event_backtest.py`
- Create: `docs/superpowers/reports/2026-06-23-restore-llm-rsi-veto-power-backtest.md`

- [ ] **Step 1:** 跑 baseline（`llm_rsi_reversal_veto_enabled=false`）event_backtest，记录被 veto-命中样本子集（用归因 reversal_veto_triggered 在 on 臂识别同集）。
- [ ] **Step 2:** 跑 on 臂（enabled=true）。
- [ ] **Step 3:** 对比被 veto 样本集 PnL/胜率；核 (1) 净 PnL 不变差 (2) 全量无新回归 (3) 触发率低区间。
- [ ] **Step 4:** 报告落盘。结果决定上线 default：正向且触发率低 → default true；否则改 false 先影子观察（改 config.yaml + DEFAULTS）。
- [ ] **Step 5: 提交** `git commit -am "test(reversal-veto): event_backtest pre/post 报告 + 定 default"`

---

## Task 9: 全量回归 + 收尾

- [ ] **Step 1:** `python3 -m pytest test_reversal_confluence_veto.py -q` 全绿。
- [ ] **Step 2:** 跑相关既有套件不回归：`python3 -m pytest test_long_entry_position_guard.py test_short_main_path_risk_guard.py test_judge_close_cause.py -q`（按实际存在的 judge 套件）。
- [ ] **Step 3:** 勾选 tasks.md 全部；guard build --apply。

---

## Self-Review

- **Spec 覆盖**：delta spec 5 Requirement → Task 2/3(合流判定+defer)、Task 4/5(主+deferred收口)、Task 7/1(开关)、Task 3/6(归因)、Task 2/8(不改scoring由设计+backtest无回归保证)。✅
- **Placeholder**：Task 4 Step 1 集成测试标注"按现有 fixture 范式补全"——实现时必须写出完整可运行代码，不得留桩。⚠️ 执行时注意。
- **类型一致**：helper 签名 `_reversal_confluence_veto(action, llm_action, tech, llm_confidence)` 与 `_route_reversal_veto_defer(symbol, action, price, score, tech, llm_action)` 在 Task 4/5 调用处一致。✅
