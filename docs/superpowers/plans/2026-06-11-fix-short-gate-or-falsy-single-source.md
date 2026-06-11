---
change: fix-short-gate-or-falsy-single-source
design-doc: docs/superpowers/specs/2026-06-11-fix-short-gate-or-falsy-single-source-design.md
base-ref: 79795b84929ea8367947d83d44ed9cc71d0d65fc
archived-with: 2026-06-11-fix-short-gate-or-falsy-single-source
---

# 短单 gate `or`-falsy 修复 + 单点收口归位 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `_classify_short_entry_risk` 的 `or`-falsy bug（24h 锅底 `range_pos=0.0` 被当 0.5 → 放行追空底部），并让 `_apply_regime_policy` 委托该 canonical 函数，消除第二份内联实现与默认值发散。

**Architecture:** 引入 `_coalesce_float(*vals, default)` 哨兵合并 helper（区分 present 0.0 与 absent None），应用到三处 `or`-falsy 取值点（短单 gate / long overheat gate / attribution 写点）。`_apply_regime_policy` 的 side-aware 短单结构段改为调用 `_classify_short_entry_risk`，保留 probe 路由外壳。attribution 由 caller 持有（callers 各自调 canonical + `_apply_short_gate_attribution`），delegate 不触碰。

**Tech Stack:** Python 3, pytest, MagicMock；`agents/trading/judge.py` 单文件 + `tests/test_short_main_path_risk_guard.py`。

archived-with: 2026-06-11-fix-short-gate-or-falsy-single-source
---

## File Structure

- `agents/trading/judge.py` — 唯一代码改点：
  - 新增 `_coalesce_float` helper（类内方法）
  - `_classify_short_entry_risk:2692-2694` → helper（P1-02 核心）
  - `_check_entry_position_policy:2761` → helper（long overheat gate）
  - attribution 写点 `judge.py:2359` → helper（cosmetic 一致性）
  - `_apply_regime_policy` 短单结构段（2897-2950）→ delegate 到 `_classify_short_entry_risk`
- `tests/test_short_main_path_risk_guard.py` — P1-02 锅底回归 + P1-03 delegate parity / probe 外壳 / 默认值一致性。

archived-with: 2026-06-11-fix-short-gate-or-falsy-single-source
---

## Task 1: `_coalesce_float` helper + P1-02 短单 gate 修复

**Files:**
- Modify: `agents/trading/judge.py`（新增 `_coalesce_float`；改 2692-2694）
- Test: `tests/test_short_main_path_risk_guard.py`

- [ ] **Step 1: 写失败测试 — 锅底 0.0 必须拒单**

在 `tests/test_short_main_path_risk_guard.py` 的 `TestClassifyShortEntryRisk` 类内追加（复用文件内 `_make_judge` / `_good_tech` / `_good_plan`）：

```python
    def test_range_pos_zero_is_rejected_not_coalesced(self):
        """P1-02: present range_pos=0.0 (24h 锅底) 必须 range_position_too_low，不得退化成 0.5。"""
        judge = _make_judge()
        tech = _good_tech()
        tech['short_context']['position_in_24h_range'] = 0.0   # 价格在 24h 最低点
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), tech, 60.0
        )
        assert result['allowed'] is False
        assert result['reason'] == 'range_position_too_low'
        assert result['metrics']['range_position_24h'] == 0.0

    def test_pre_move_zero_present_preserved(self):
        """P1-02: present pre_12h=0.0 保留为 0.0（0.0 <= max_pre_move(-0.01) 为 False → 不因 pre_move 拒）。"""
        judge = _make_judge()
        tech = _good_tech()
        tech['short_context']['pre_12h_return_pct'] = 0.0
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), tech, 60.0
        )
        # 0.0 not <= -0.01 → pre_move gate 不触发；其它指标良好 → pass
        assert result['allowed'] is True
        assert result['metrics']['pre_12h_return_pct'] == 0.0

    def test_absent_range_uses_default(self):
        """absent（无 key）时回退默认 0.5（>=0.45 → 不因 range 拒）。"""
        judge = _make_judge()
        tech = _good_tech()
        tech['short_context'].pop('position_in_24h_range', None)
        # entry_context 也无该 key
        result = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', _good_plan(), tech, 60.0
        )
        assert result['metrics']['range_position_24h'] == 0.5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_short_main_path_risk_guard.py::TestClassifyShortEntryRisk::test_range_pos_zero_is_rejected_not_coalesced -v`
Expected: FAIL — 当前 `float(0.0 or ... or 0.5) == 0.5` → 不拒单，`allowed` 为 True。

- [ ] **Step 3: 实现 — 新增 `_coalesce_float` helper**

在 `_classify_short_entry_risk` 定义之前（约 judge.py:2620 上方）新增类内方法：

```python
    def _coalesce_float(self, *vals, default: float) -> float:
        """Return first non-None value as float; only an absent (None) value
        falls back to default. Unlike `a or b or default`, a present 0.0 is
        preserved (not treated as falsy)."""
        for v in vals:
            if v is not None:
                return float(v)
        return float(default)
```

- [ ] **Step 4: 实现 — 改 `_classify_short_entry_risk:2692-2694`**

把：

```python
        range_pos = float(short_ctx.get('position_in_24h_range') or entry_ctx.get('position_in_24h_range') or 0.5)
        pre_move = float(short_ctx.get('pre_12h_return_pct') or entry_ctx.get('pre_12h_return_pct') or 0.0)
        rsi_val = float(indicators.get('rsi') or momentum.get('rsi') or 50)
```

替换为：

```python
        range_pos = self._coalesce_float(
            short_ctx.get('position_in_24h_range'),
            entry_ctx.get('position_in_24h_range'), default=0.5)
        pre_move = self._coalesce_float(
            short_ctx.get('pre_12h_return_pct'),
            entry_ctx.get('pre_12h_return_pct'), default=0.0)
        rsi_val = self._coalesce_float(
            indicators.get('rsi'), momentum.get('rsi'), default=50.0)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest tests/test_short_main_path_risk_guard.py::TestClassifyShortEntryRisk -v`
Expected: PASS（新增 3 + 既有 case 全绿）

- [ ] **Step 6: 提交**

```bash
git add agents/trading/judge.py tests/test_short_main_path_risk_guard.py
git commit -m "fix(judge): _classify_short_entry_risk 用哨兵合并替代 or-falsy，锅底 range_pos=0.0 正确拒空 (P1-02)"
```

archived-with: 2026-06-11-fix-short-gate-or-falsy-single-source
---

## Task 2: 兄弟 `or`-falsy 点改用 helper（2761 + 2359）

**Files:**
- Modify: `agents/trading/judge.py:2761`（`_check_entry_position_policy`）、`judge.py:2359`（attribution 写点）

- [ ] **Step 1: 改 `_check_entry_position_policy:2761`（long overheat gate）**

把：

```python
        range_pos = float(ctx.get('position_in_24h_range', 0.5) or 0.5)
```

替换为：

```python
        range_pos = self._coalesce_float(ctx.get('position_in_24h_range'), default=0.5)
```

- [ ] **Step 2: 改 attribution 写点 `judge.py:2359`（及紧邻的 pre_12h）**

把：

```python
            'entry_range_pos_24h': float((tech.get('entry_context')
                                          or tech.get('short_context')
                                          or {}).get('position_in_24h_range', 0.5) or 0.5),
            'entry_pre_12h_return_pct': float((tech.get('entry_context')
                                               or tech.get('short_context')
                                               or {}).get('pre_12h_return_pct', 0.0) or 0.0),
```

替换为（保留 entry_context/short_context 选择逻辑，只换内层 `or`-falsy）：

```python
            'entry_range_pos_24h': self._coalesce_float(
                (tech.get('entry_context') or tech.get('short_context')
                 or {}).get('position_in_24h_range'), default=0.5),
            'entry_pre_12h_return_pct': self._coalesce_float(
                (tech.get('entry_context') or tech.get('short_context')
                 or {}).get('pre_12h_return_pct'), default=0.0),
```

- [ ] **Step 3: 跑相关回归**

Run: `python3 -m pytest tests/test_short_main_path_risk_guard.py tests/test_long_entry_position_guard.py -q`
Expected: PASS（long overheat 与短单既有用例不回归）

> 若 `tests/test_long_entry_position_guard.py` 不在 `tests/` 下，先 `find . -name test_long_entry_position_guard.py -not -path '*pycache*'` 定位后替换路径。

- [ ] **Step 4: 提交**

```bash
git add agents/trading/judge.py
git commit -m "fix(judge): long overheat gate + attribution 写点统一改用 _coalesce_float，根除同类 or-falsy (P1-02 兄弟点)"
```

archived-with: 2026-06-11-fix-short-gate-or-falsy-single-source
---

## Task 3: P1-03 — `_apply_regime_policy` delegate 到 canonical

**Files:**
- Modify: `agents/trading/judge.py:2896-2950`（side-aware 短单结构段）
- Test: `tests/test_short_main_path_risk_guard.py`（新增 `TestApplyRegimePolicyDelegation`）

- [ ] **Step 1: 写失败测试 — delegate parity + probe 外壳 + 锅底拒单**

在 `tests/test_short_main_path_risk_guard.py` 末尾追加：

```python
class TestApplyRegimePolicyDelegation:
    """P1-03: _apply_regime_policy 短单段委托 _classify_short_entry_risk，保留 probe 外壳。"""

    def _make_regime_judge(self, config=None):
        judge = _make_judge(config)
        # 隔离 side-aware 结构段：effective_regime 非 bullish，跳过 short_regime_guard 前置块
        judge._regime_manager = MagicMock()
        judge._regime_manager.snapshot.return_value = {'effective_regime': 'bearish'}
        judge._record_rejected_plan = MagicMock()
        judge._route_to_probe = MagicMock()
        judge._can_route_probe_short = MagicMock(return_value=(False, 'not_eligible'))
        # RR floor 下游：给足够字段让 pass 路径不 KeyError
        judge._select_rr_floor = MagicMock(return_value=(1.2, 'default', 'default'))
        judge._low_rr_max_position_pct = 1.0
        judge._low_rr_max_leverage = 5
        return judge

    def test_regime_rejects_range_pos_zero(self):
        """锅底 0.0：delegate 后 _apply_regime_policy 返回 range_position_too_low。"""
        judge = self._make_regime_judge()
        tech = _good_tech()
        tech['short_context']['position_in_24h_range'] = 0.0
        plan = {'is_probe': False, 'risk_reward_ratio': 3.0,
                'effective_risk_reward_ratio': 3.0, 'size_usdt': 100.0, 'leverage': 5}
        reject = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, 60.0, tech)
        assert reject == 'range_position_too_low'

    def test_regime_matches_classify_reason(self):
        """parity：_apply_regime_policy 的拒单 reason 与直接调 _classify_short_entry_risk 一致。"""
        judge = self._make_regime_judge()
        tech = _good_tech()
        tech['short_context']['position_in_24h_range'] = 0.0
        plan = {'is_probe': False, 'risk_reward_ratio': 3.0,
                'effective_risk_reward_ratio': 3.0, 'size_usdt': 100.0, 'leverage': 5}
        classify = judge._classify_short_entry_risk(
            'BTC-USDT', 'open_short', dict(plan), tech, 60.0, llm_result=None)
        reject = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, 60.0, tech)
        assert reject == classify['reason']

    def test_daily_bearish_probe_shell_routes_not_rejects(self):
        """probe 外壳：daily_bias!=bearish 且 probe_ok=True → _route_to_probe，不返回拒单。"""
        judge = self._make_regime_judge()
        judge._can_route_probe_short = MagicMock(return_value=(True, 'eligible'))
        tech = _good_tech()
        tech['trend']['daily_bias'] = 'bullish'   # 触发 daily_bearish_required
        plan = {'is_probe': False, 'risk_reward_ratio': 3.0,
                'effective_risk_reward_ratio': 3.0, 'size_usdt': 100.0, 'leverage': 5}
        reject = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, 60.0, tech)
        assert reject is None                       # 路由 probe，不拒
        judge._route_to_probe.assert_called_once()

    def test_daily_bearish_probe_fail_rejects(self):
        """daily_bias!=bearish 且 probe 不合格 → 返回 daily_bearish_required。"""
        judge = self._make_regime_judge()  # _can_route_probe_short 默认 (False, ...)
        tech = _good_tech()
        tech['trend']['daily_bias'] = 'bullish'
        plan = {'is_probe': False, 'risk_reward_ratio': 3.0,
                'effective_risk_reward_ratio': 3.0, 'size_usdt': 100.0, 'leverage': 5}
        reject = judge._apply_regime_policy('BTC-USDT', 'open_short', plan, 60.0, tech)
        assert reject == 'daily_bearish_required'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_short_main_path_risk_guard.py::TestApplyRegimePolicyDelegation -v`
Expected: FAIL — `test_regime_rejects_range_pos_zero` 失败（当前内联 `short_ctx.get(..., 1.0)` 对 present 0.0 也返回 0.0 → 实际会拒？需确认）。

> 注意：当前内联实现用 `short_ctx.get('position_in_24h_range', 1.0)`，present 0.0 返回 0.0 → 会拒。故 `test_regime_rejects_range_pos_zero` 当前可能已 PASS。真正失败点是 `test_regime_matches_classify_reason`（delegate 前 regime 用 short_ctx-only、不读 entry_context，且若 short_ctx 缺 key 用默认 1.0，与 canonical 0.5 发散）与 probe 外壳行为是否经由 canonical 路径。**以实际运行为准**：若部分用例当前已 PASS，保留它们作为防回归断言；只要 `test_regime_matches_classify_reason` 在 delegate 前后语义被锁定即可。

- [ ] **Step 3: 实现 — 替换 side-aware 短单结构段（judge.py:2897-2950）**

把 `if not is_long and self._short_regime_guard_enabled and not plan.get('is_probe'):` 块体（即 2898-2950 的内联 gate）替换为 delegate：

```python
        if not is_long and self._short_regime_guard_enabled and not plan.get('is_probe'):
            entry_timing = tech.get('entry_timing', {})
            short_gate = self._classify_short_entry_risk(
                symbol, action, plan, tech, score, llm_result=None
            )
            if not short_gate['allowed']:
                reason = short_gate['reason']
                if reason == 'daily_bearish_required':
                    confirm_15m = entry_timing.get('tf_15m_confirm_short', False)
                    rr_val = plan.get('effective_risk_reward_ratio',
                                      plan.get('risk_reward_ratio', 0))
                    probe_ok, _ = self._can_route_probe_short(
                        symbol, score, confirm_15m, rr_val)
                    if probe_ok:
                        self._route_to_probe(plan, symbol)
                    else:
                        self._record_rejected_plan(
                            symbol, action, plan, score, 60, 'daily_bearish_required')
                        return 'daily_bearish_required'
                else:
                    self._record_rejected_plan(symbol, action, plan, score, 60, reason)
                    return reason
```

> 删除原 2898-2950 的内联 `daily_bias`/`range_pos`/`pre_move`/`rsi`/`score`/`htf` 判定。保留其后的 `# ── Dynamic R:R Floor ──` 段（2952 起）不动。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_short_main_path_risk_guard.py::TestApplyRegimePolicyDelegation -v`
Expected: PASS（4 case 全绿）

- [ ] **Step 5: 提交**

```bash
git add agents/trading/judge.py tests/test_short_main_path_risk_guard.py
git commit -m "refactor(judge): _apply_regime_policy 短单段 delegate 到 _classify_short_entry_risk，消除第二份实现+默认值发散 (P1-03)"
```

archived-with: 2026-06-11-fix-short-gate-or-falsy-single-source
---

## Task 4: 同构记录 + 全量回归 + compileall

**Files:**
- Modify: `openspec/changes/fix-short-gate-or-falsy-single-source/tasks.md`（勾选 + 同构理由）

- [ ] **Step 1: 同构核对 event_backtest**

Run: `grep -nE "position_in_24h_range|range_position_too_low|_classify_short_entry_risk|_apply_regime_policy" event_backtest.py`
Expected: 仅匹配回测自带的 `_check_entry_with_regime`（166-167 fillna + 379/398/407 `.get(..., 0.5)`），无 `_classify_short_entry_risk`/`_apply_regime_policy` 引用。在 tasks.md 标注「event_backtest 短单 gate 用 `.get(...,0.5)` 且 row 永不 None，已正确处理 0.0 且单份实现；P1-02 让 live 对齐回测、P1-03 是 live 两份合一，回测决策路径无需改动」。

- [ ] **Step 2: compileall**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q agents utils`
Expected: 无输出（通过）

- [ ] **Step 3: 全量回归**

Run: `python3 -m pytest -q`
Expected: `1066 + 7 = 1073 passed`（新增 3 + 4 用例；实际数以运行为准，须全绿无 fail）

- [ ] **Step 4: 勾选 change tasks.md 并提交**

把 `tasks.md` 已完成项 `- [ ]` → `- [x]`（CLAUDE.md/to-do 收尾留待 verify/archive）。

```bash
git add openspec/changes/fix-short-gate-or-falsy-single-source/tasks.md
git commit -m "docs(tasks): 勾选短单 gate 修复实现项 + 记录 event_backtest 同构结论"
```

archived-with: 2026-06-11-fix-short-gate-or-falsy-single-source
---

## Self-Review

**Spec coverage：**
- delta「price-at-24h-low 不合并」→ Task 1（test_range_pos_zero_is_rejected_not_coalesced）✅
- delta「absent 用统一默认」→ Task 1（test_absent_range_uses_default）+ Task 3（parity）✅
- delta「regime delegates，保留 probe 外壳」→ Task 3（4 case）✅
- delta「attribution preserved」→ 由 caller 持有（813/936/1058/1536/1700），delegate 不触碰 → 全量回归覆盖 ✅
- 同构红线 → Task 4 Step 1 ✅

**Placeholder scan：** 无 TBD/TODO；Task 3 Step 2 的「以实际运行为准」是真实的 TDD 红灯校验（部分防回归断言当前可能已绿），非 placeholder。

**Type consistency：** `_coalesce_float(self, *vals, default)` 签名在四处调用一致；`_classify_short_entry_risk` 返回 dict（`allowed`/`reason`/`metrics`）；`_apply_regime_policy` 返回 reason 字符串或 None；parity 断言 `regime_reject == classify['reason']` 类型对齐。
