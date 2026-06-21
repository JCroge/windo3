---
change: regime-aware-long-entry-guard
design-doc: docs/superpowers/specs/2026-06-21-regime-aware-long-entry-guard-design.md
base-ref: 5765fc00da620310c30b7a22539234071f270e95
---

# Regime-Aware Long Entry Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让多单过热位置门按市场体制选阈值——choppy/mixed/bearish 收紧 range_pos 阈值（0.82→0.55）并复用现有 deferred_pullback_overheat 路径，bullish 维持 0.82，体制不可得或总开关关闭时回退兼容。

**Architecture:** 在 `_check_entry_position_policy` 内部用 `self._regime_manager.snapshot()['effective_regime']` 取体制（与相邻 `_apply_regime_policy` 同源），经纯函数 helper `_resolve_long_range_thresholds` 解析阈值。新增 3 个 config 键走既有 four-segment 模式。归因记录所用体制与阈值。不改 `_compute_score`、regime 分类、出场、空单。

**Tech Stack:** Python, pytest, `utils/config_loader.py` 四段式配置（HARD_LIMITS / DEFAULTS / YAML bool coercion / ENV）。

---

## File Structure

- `utils/config_loader.py` — 新增 3 个配置键（1 bool + 2 float）到四段。
- `config.yaml` — `risk` 段写入 3 个键（带注释）。
- `agents/trading/judge.py` — `__init__` 读取 3 个键；新增 `_resolve_long_range_thresholds`；`_check_entry_position_policy` 取体制 + 用解析阈值 + 写归因 metrics；overheat 归因点标记 `long_overheat_v2_regime`。
- `test_long_entry_position_guard.py` — `_make_judge` 增默认属性；新增体制感知用例。
- `tests/test_config_loader.py`（若存在则追加，否则在 `test_long_entry_position_guard.py` 内加 config 段）— 校验新键默认/范围。

---

## Task 1: Config 三键接入（four-segment）

**Files:**
- Modify: `utils/config_loader.py`（HARD_LIMITS ~L60-65 区、DEFAULTS ~L166-173 区、YAML coercion ~L246、ENV ~L321 区）
- Modify: `config.yaml`（`risk:` 段）
- Test: `test_long_entry_position_guard.py`（新增 `TestRegimeAwareConfig`）

- [ ] **Step 1: 写失败测试 — 默认值与范围**

在 `test_long_entry_position_guard.py` 末尾追加：

```python
class TestRegimeAwareConfig:
    def test_defaults_present(self):
        from utils.config_loader import DEFAULTS
        assert DEFAULTS['long_live_regime_aware_range_enabled'] is True
        assert DEFAULTS['long_live_max_range_pos_choppy'] == 0.55
        assert DEFAULTS['long_live_daily_gain_range_pos_choppy'] == 0.50

    def test_hard_limits_present(self):
        from utils.config_loader import HARD_LIMITS
        assert HARD_LIMITS['long_live_max_range_pos_choppy'] == (0.0, 1.0)
        assert HARD_LIMITS['long_live_daily_gain_range_pos_choppy'] == (0.0, 1.0)

    def test_env_bool_override(self, monkeypatch):
        monkeypatch.setenv('LONG_LIVE_REGIME_AWARE_RANGE_ENABLED', 'false')
        from utils.config_loader import _read_env_overrides
        out = _read_env_overrides()
        assert out['long_live_regime_aware_range_enabled'] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_long_entry_position_guard.py::TestRegimeAwareConfig -q`
Expected: FAIL（KeyError / 键不存在）

- [ ] **Step 3: HARD_LIMITS 增两 float 键**

`utils/config_loader.py` 在 `long_live_daily_gain_range_pos: (0.0, 1.0),`（L63）后插入：

```python
    "long_live_max_range_pos_choppy": (0.0, 1.0),
    "long_live_daily_gain_range_pos_choppy": (0.0, 1.0),
```

- [ ] **Step 4: DEFAULTS 增三键**

在 `long_live_daily_gain_range_pos: 0.75,`（L170）后插入：

```python
    "long_live_regime_aware_range_enabled": True,
    "long_live_max_range_pos_choppy": 0.55,
    "long_live_daily_gain_range_pos_choppy": 0.50,
```

- [ ] **Step 5: YAML bool coercion 增总开关**

在 `rotation_close_held_enabled` coercion 块（L250-251）后插入：

```python
    if 'long_live_regime_aware_range_enabled' in risk:
        out['long_live_regime_aware_range_enabled'] = _to_bool(risk['long_live_regime_aware_range_enabled'])
```

- [ ] **Step 6: ENV override 增总开关**

在 ENV 映射表（`LONG_LIVE_POSITION_GUARD_ENABLED` 行附近 L321）后插入：

```python
        "LONG_LIVE_REGIME_AWARE_RANGE_ENABLED": ("long_live_regime_aware_range_enabled", _to_bool),
```

- [ ] **Step 7: config.yaml risk 段写键**

`config.yaml` `risk:` 段追加（缩进对齐既有键）：

```yaml
  # 体制感知多单位置门（regime-aware-long-entry-guard）
  long_live_regime_aware_range_enabled: true   # 总开关；false=所有体制用默认0.82/0.75（回退旧行为）
  long_live_max_range_pos_choppy: 0.55         # choppy/mixed/bearish 收紧的 range_pos 阈值
  long_live_daily_gain_range_pos_choppy: 0.50  # 同上体制下 daily_gain 二级门 range_pos
```

- [ ] **Step 8: 运行确认通过**

Run: `python3 -m pytest test_long_entry_position_guard.py::TestRegimeAwareConfig -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add utils/config_loader.py config.yaml test_long_entry_position_guard.py
git commit -m "feat(risk): add regime-aware long range-pos config keys (four-segment)"
```

---

## Task 2: `_resolve_long_range_thresholds` helper（纯函数）

**Files:**
- Modify: `agents/trading/judge.py`（`__init__` 读取 + 新增 helper，置于 `_check_entry_position_policy` 之前）
- Test: `test_long_entry_position_guard.py`（`TestResolveThresholds`）

- [ ] **Step 1: 写失败测试 — 阈值映射**

```python
class TestResolveThresholds:
    def _judge(self, **kw):
        j = _make_judge(**kw)
        j._long_live_regime_aware_range_enabled = kw.get('_long_live_regime_aware_range_enabled', True)
        j._long_live_max_range_pos_choppy = 0.55
        j._long_live_daily_gain_range_pos_choppy = 0.50
        return j

    def test_bullish_uses_default(self):
        j = self._judge()
        assert j._resolve_long_range_thresholds('bullish') == (0.82, 0.75)

    def test_choppy_mixed_bearish_tighten(self):
        j = self._judge()
        for r in ('choppy', 'mixed', 'bearish'):
            assert j._resolve_long_range_thresholds(r) == (0.55, 0.50)

    def test_none_and_unknown_fallback(self):
        j = self._judge()
        assert j._resolve_long_range_thresholds(None) == (0.82, 0.75)
        assert j._resolve_long_range_thresholds('weird') == (0.82, 0.75)

    def test_toggle_off_forces_default(self):
        j = self._judge(_long_live_regime_aware_range_enabled=False)
        assert j._resolve_long_range_thresholds('choppy') == (0.82, 0.75)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_long_entry_position_guard.py::TestResolveThresholds -q`
Expected: FAIL（`_resolve_long_range_thresholds` 不存在）

- [ ] **Step 3: `__init__` 读取三键**

`agents/trading/judge.py` 在 `self._long_live_overheat_disable_chase = ...`（L215）后插入：

```python
        self._long_live_regime_aware_range_enabled = config.get('long_live_regime_aware_range_enabled', True) if config else True
        self._long_live_max_range_pos_choppy = config.get('long_live_max_range_pos_choppy', 0.55) if config else 0.55
        self._long_live_daily_gain_range_pos_choppy = config.get('long_live_daily_gain_range_pos_choppy', 0.50) if config else 0.50
```

- [ ] **Step 4: 新增 helper**

在 `def _check_entry_position_policy`（L2825）正上方插入：

```python
    def _resolve_long_range_thresholds(self, eff_regime):
        """按有效体制解析多单 (max_range, daily_gain_range_pos) 阈值。

        bullish / None / 未知 / 总开关关闭 → 默认 (0.82, 0.75)；
        choppy / mixed / bearish → 收紧 (0.55, 0.50)（可配置）。
        与相邻 _apply_regime_policy 用同一 snapshot 体制源，主/deferred 路径共用。
        """
        default = (self._long_live_max_range_pos, self._long_live_daily_gain_range_pos)
        if not getattr(self, '_long_live_regime_aware_range_enabled', True):
            return default
        if eff_regime in ('choppy', 'mixed', 'bearish'):
            return (
                getattr(self, '_long_live_max_range_pos_choppy', 0.55),
                getattr(self, '_long_live_daily_gain_range_pos_choppy', 0.50),
            )
        return default
```

- [ ] **Step 5: 运行确认通过**

Run: `python3 -m pytest test_long_entry_position_guard.py::TestResolveThresholds -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agents/trading/judge.py test_long_entry_position_guard.py
git commit -m "feat(judge): add _resolve_long_range_thresholds regime threshold helper"
```

---

## Task 3: 接入位置门 + 归因 metrics

**Files:**
- Modify: `agents/trading/judge.py`（`_check_entry_position_policy` overheat 块 L2866-2872）
- Test: `test_long_entry_position_guard.py`（`TestRegimeAwareGuard`）

- [ ] **Step 1: 写失败测试 — 体制条件化过热判定**

```python
class TestRegimeAwareGuard:
    def _judge(self, regime, enabled=True):
        j = _make_judge()
        j._long_live_regime_aware_range_enabled = enabled
        j._long_live_max_range_pos_choppy = 0.55
        j._long_live_daily_gain_range_pos_choppy = 0.50

        class _R:
            def snapshot(self_inner):
                return {'effective_regime': regime, 'raw_regime': regime, 'confidence': 60}
        j._regime_manager = _R()
        return j

    def _check(self, j):
        return j._check_entry_position_policy(
            'X', 'open_long', _make_plan(), _make_tech(range_pos=0.66), 50.0, context='main')

    def test_choppy_066_overheats(self):
        r = self._check(self._judge('choppy'))
        assert r['allowed'] is False
        assert r['entry_position_status'] == 'overheated'

    def test_mixed_066_overheats(self):
        assert self._check(self._judge('mixed'))['allowed'] is False

    def test_bearish_066_overheats(self):
        assert self._check(self._judge('bearish'))['allowed'] is False

    def test_bullish_066_passes(self):
        r = self._check(self._judge('bullish'))
        assert r['allowed'] is True
        assert r['entry_position_status'] == 'normal'

    def test_toggle_off_066_passes_in_choppy(self):
        r = self._check(self._judge('choppy', enabled=False))
        assert r['allowed'] is True

    def test_metrics_record_regime_and_threshold(self):
        r = self._check(self._judge('choppy'))
        assert r['metrics']['entry_regime_used'] == 'choppy'
        assert r['metrics']['entry_range_pos_threshold'] == 0.55
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_long_entry_position_guard.py::TestRegimeAwareGuard -q`
Expected: FAIL（仍用固定 0.82，0.66 在 choppy 不触发；metrics 无新键）

- [ ] **Step 3: 改 overheat 块取体制 + 解析阈值 + 写 metrics**

`agents/trading/judge.py` 将 overheat 块开头（L2866-2872）：

```python
        # Long overheat guard
        if (is_long and self._long_live_position_guard_enabled
                and not plan.get('is_probe')):
            max_range = self._long_live_max_range_pos
            max_pre = self._long_live_max_pre_move
            max_daily = self._long_live_max_daily_gain
            daily_gain_range_pos = self._long_live_daily_gain_range_pos
```

替换为：

```python
        # Long overheat guard
        if (is_long and self._long_live_position_guard_enabled
                and not plan.get('is_probe')):
            try:
                eff_regime = self._regime_manager.snapshot().get('effective_regime')
            except Exception:
                eff_regime = None
            max_range, daily_gain_range_pos = self._resolve_long_range_thresholds(eff_regime)
            max_pre = self._long_live_max_pre_move
            max_daily = self._long_live_max_daily_gain
            result['metrics']['entry_regime_used'] = eff_regime
            result['metrics']['entry_range_pos_threshold'] = round(max_range, 4)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest test_long_entry_position_guard.py::TestRegimeAwareGuard -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/judge.py test_long_entry_position_guard.py
git commit -m "feat(judge): regime-aware range threshold in _check_entry_position_policy + metrics"
```

---

## Task 4: 归因 policy 版本标记 + 透传字段

**Files:**
- Modify: `agents/trading/judge.py`（overheat 归因点 L1624、L1654 设 `entry_position_policy`；附近透传 metrics 新字段到 attr）
- Test: `test_long_entry_position_guard.py`（`TestAttributionV2`）

- [ ] **Step 1: 写失败测试 — 归因标记**

```python
class TestAttributionV2:
    def test_policy_tag_is_v2_on_overheat(self):
        import re
        src = open('agents/trading/judge.py', encoding='utf-8').read()
        # overheat 归因点应升级为 v2_regime（至少在 1620-1660 区段出现）
        seg = src[src.index("deferred_pullback_overheat"):]
        assert 'long_overheat_v2_regime' in src
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_long_entry_position_guard.py::TestAttributionV2 -q`
Expected: FAIL

- [ ] **Step 3: 升级 overheat 归因点标记 + 透传字段**

`agents/trading/judge.py` 将 L1624 与 L1654 两处：

```python
                            attr['entry_position_policy'] = 'long_overheat_v1'
```

改为：

```python
                            attr['entry_position_policy'] = 'long_overheat_v2_regime'
                            attr['entry_regime_used'] = pos_policy['metrics'].get('entry_regime_used')
                            attr['entry_range_pos_threshold'] = pos_policy['metrics'].get('entry_range_pos_threshold')
```

（注意两处缩进各自对齐其上下文；L2424 / L3226 的默认值标记保持 `long_overheat_v1` 不动——那是未过 overheat 路径的 fallback 默认。）

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest test_long_entry_position_guard.py::TestAttributionV2 -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/judge.py test_long_entry_position_guard.py
git commit -m "feat(judge): tag overheat attribution long_overheat_v2_regime + regime/threshold fields"
```

---

## Task 5: 全量回归

**Files:** 无新增，验证既有用例不回归。

- [ ] **Step 1: 跑本能力测试**

Run: `python3 -m pytest test_long_entry_position_guard.py -q`
Expected: 全 PASS（既有 bullish 用例因 bullish→0.82 不变而保持绿）

- [ ] **Step 2: 跑全量 CI 回归**

Run: `python3 -m pytest -q`
Expected: 全 PASS（既有 1010 passed 基线不下降；如有 `entry_position_policy=='long_overheat_v1'` 断言的旧用例命中 overheat 路径而失败，按本计划语义改断言为 `long_overheat_v2_regime`，并在 commit 注明）

- [ ] **Step 3: Commit（如有断言调整）**

```bash
git add -A
git commit -m "test: align long-overheat policy-tag assertions with v2_regime"
```

---

## Self-Review

- **Spec coverage**：体制感知阈值（Task 2/3）✓；体制不可得回退（Task 2 test_none_and_unknown_fallback）✓；总开关（Task 2/3 toggle 测试）✓；可配置（Task 1）✓；归因记录体制+阈值（Task 3/4）✓；不影响空单（既有 short guard 测试 + Task 5 回归）✓。
- **Placeholder scan**：无 TBD/TODO；每步含实代码或实命令。
- **Type consistency**：helper 返回 `(max_range, daily_gain_range_pos)` 二元组贯穿 Task 2→3；metrics 键 `entry_regime_used` / `entry_range_pos_threshold` 在 Task 3 写入、Task 4 透传，命名一致。
