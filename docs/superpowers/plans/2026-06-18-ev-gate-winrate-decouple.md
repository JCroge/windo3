---
change: ev-gate-winrate-decouple
design-doc: docs/superpowers/specs/2026-06-18-ev-gate-winrate-decouple-design.md
base-ref: b6519db6f9137dcf5c980bc9d2da93ace94d7a3b
---

# 剔除开仓门胜率因子 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 config 开关 `ev_winrate_gate_enabled`（默认 True 保持现状），关闭后让 EV 开仓门不再用实际胜率拦开仓，但保留 EV 经济门。

**Architecture:** 在 `MultiJudge` 的 EV 门链路三处解耦实际胜率（`_get_p_win` 短路用固定 p_win、`_check_expected_value` 跳过胜率硬阈值与分桶覆盖），保留 EV 阈值门；配置经 config_loader 四段式注入。

**Tech Stack:** Python 3.9, pytest（print 风格单测 + `main()` 登记）, PyYAML config。

---

## 文件结构

- `agents/trading/judge.py` — `MultiJudge.__init__`（line ~88）新增两字段；`_get_p_win`（line 3619）短路；`_check_expected_value`（line 3637）两处前置开关条件。
- `utils/config_loader.py` — RISK_DEFAULTS / RANGE_VALIDATORS / env_map / `_load_yaml` / banner 五处接入两键。
- `config.yaml` — risk 节点新增两键。
- `test_ev_gate.py` — 新增 4 个用例 + 在 `main()` 登记。

---

### Task 1: 配置层接入两个新键（config_loader + config.yaml）

**Files:**
- Modify: `utils/config_loader.py`（RANGE_VALIDATORS ~line33、RISK_DEFAULTS ~line108、env_map ~line259、`_load_yaml` ~line232、banner ~line481）
- Modify: `config.yaml`（risk 节点）
- Test: 命令行 `load_config()` 验证

- [ ] **Step 1: RISK_DEFAULTS 加默认值**（`utils/config_loader.py`，在 `"ev_strong_signal_threshold": 70,` 附近）

```python
    "ev_winrate_gate_enabled": True,
    "ev_neutral_p_win": 0.55,
```

- [ ] **Step 2: RANGE_VALIDATORS 加 p_win 范围**（在 `"ev_strong_signal_threshold": (30, 100),` 附近）

```python
    "ev_neutral_p_win": (0.0, 1.0),
```

- [ ] **Step 3: env_map 加两键**（在 `"EV_STRONG_SIGNAL_THRESHOLD": (...)` 附近）

```python
        "EV_WINRATE_GATE_ENABLED": ("ev_winrate_gate_enabled", _to_bool),
        "EV_NEUTRAL_P_WIN": ("ev_neutral_p_win", float),
```

- [ ] **Step 4: `_load_yaml` risk 节点映射**（在 `return out` 之前）

```python
    if 'ev_winrate_gate_enabled' in risk:
        out['ev_winrate_gate_enabled'] = _to_bool(risk['ev_winrate_gate_enabled'])
    if 'ev_neutral_p_win' in risk:
        out['ev_neutral_p_win'] = float(risk['ev_neutral_p_win'])
```

- [ ] **Step 5: banner 加展示行**（在 EV 相关展示附近，format_banner 内）

```python
        f"  EV 胜率门:             {'开启' if cfg.get('ev_winrate_gate_enabled', True) else '关闭'} (neutral_p_win={cfg.get('ev_neutral_p_win', 0.55)})",
```

- [ ] **Step 6: config.yaml risk 节点加两键**（紧随 `consecutive_loss_limit` 之后）

```yaml
  ev_winrate_gate_enabled: false  # 关闭后开仓门不用实际胜率(胜率25%不拦)，EV门仍按R:R/成本
  ev_neutral_p_win: 0.55          # 关闭胜率门时 EV 公式使用的固定中性胜率
```

- [ ] **Step 7: 验证配置生效**

Run: `python3 -c "from utils.config_loader import load_config; c=load_config(strict_live_check=False); print(c.get('ev_winrate_gate_enabled'), c.get('ev_neutral_p_win'))"`
Expected: `False 0.55`

- [ ] **Step 8: 验证越界报错**

Run: `python3 -c "from utils.config_loader import _validate_ranges" 2>/dev/null; python3 -c "from utils.config_loader import _load_yaml; import tempfile,os; f=tempfile.NamedTemporaryFile('w',suffix='.yaml',delete=False); f.write('risk:\n  ev_neutral_p_win: 0.55\n'); f.close(); print(_load_yaml(f.name)); os.unlink(f.name)"`
Expected: 打印 `{'ev_neutral_p_win': 0.55}`（确认 yaml 映射；越界校验在 load_config 主流程，Task 验证留到回归）

- [ ] **Step 9: Commit**

```bash
git add utils/config_loader.py config.yaml
git commit -m "feat(ev-gate): config 接入 ev_winrate_gate_enabled / ev_neutral_p_win"
```

---

### Task 2: Judge 构造函数新增两字段

**Files:**
- Modify: `agents/trading/judge.py`（`MultiJudge.__init__`，line ~88，`_ev_strong_signal_threshold` 之后）

- [ ] **Step 1: 加字段**

```python
        # EV 胜率门开关：关闭后 EV 公式用固定中性胜率，开仓门不再受实际胜率影响
        self._ev_winrate_gate_enabled = config.get('ev_winrate_gate_enabled', True) if config else True
        self._ev_neutral_p_win = config.get('ev_neutral_p_win', 0.55) if config else 0.55
```

- [ ] **Step 2: 冒烟验证字段存在**

Run: `python3 -c "from agents.trading.judge import MultiJudge; j=MultiJudge(config={'exchange':'okx','max_trade_amount':10,'ev_winrate_gate_enabled':False}); print(j._ev_winrate_gate_enabled, j._ev_neutral_p_win)"`
Expected: `False 0.55`

- [ ] **Step 3: Commit**

```bash
git add agents/trading/judge.py
git commit -m "feat(ev-gate): Judge 构造新增 ev_winrate_gate_enabled / ev_neutral_p_win"
```

---

### Task 3: `_get_p_win` 关闭时短路 + 测试

**Files:**
- Modify: `agents/trading/judge.py`（`_get_p_win`，line 3619 函数体顶部）
- Test: `test_ev_gate.py`

- [ ] **Step 1: 写失败测试**（`test_ev_gate.py` 末尾、`main()` 之前）

```python
def test_p_win_fixed_when_gate_disabled():
    """关闭胜率门 → _get_p_win 返回固定中性胜率，不读实际胜率"""
    from agents.trading.judge import MultiJudge
    j = MultiJudge(config={'exchange': 'okx', 'max_trade_amount': 10,
                           'ev_winrate_gate_enabled': False, 'ev_neutral_p_win': 0.55})
    j._available_balance = 100.0
    j._recent_win_rate = 0.25      # 实际胜率很低
    j._total_completed_trades = 30  # 样本充足
    p_win, source = j._get_p_win()
    assert source == 'fixed', f"应 fixed，实际 {source}"
    assert abs(p_win - 0.55) < 1e-6, f"应=0.55，实际 {p_win}"
    print("  ✅ Case 11: 关闭胜率门 → p_win=0.55 (fixed)")
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest test_ev_gate.py::test_p_win_fixed_when_gate_disabled -v`
Expected: FAIL（source 当前为 'rolling'）

- [ ] **Step 3: 实现短路**（`_get_p_win` 文档串之后、`if (self._recent_win_rate is not None ...` 之前）

```python
        # 胜率门关闭：用固定中性胜率，切断实际胜率对 EV 的影响
        if not self._ev_winrate_gate_enabled:
            return float(self._ev_neutral_p_win), "fixed"
```

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest test_ev_gate.py::test_p_win_fixed_when_gate_disabled -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/judge.py test_ev_gate.py
git commit -m "feat(ev-gate): _get_p_win 关闭时返回固定中性胜率"
```

---

### Task 4: `_check_expected_value` 跳过胜率硬阈值与分桶 + 测试

**Files:**
- Modify: `agents/trading/judge.py`（`_check_expected_value`：分桶块 line ~3651、硬阈值 line ~3699）
- Test: `test_ev_gate.py`

- [ ] **Step 1: 写失败测试（低胜率放行 + 经济门仍拦）**（`main()` 之前）

```python
def test_ev_gate_disabled_allows_low_winrate():
    """关闭胜率门 → 胜率25% + score<70 + 正 EV 计划应放行"""
    from agents.trading.judge import MultiJudge
    j = MultiJudge(config={'exchange': 'okx', 'max_trade_amount': 10,
                           'ev_winrate_gate_enabled': False, 'ev_neutral_p_win': 0.55})
    j._available_balance = 100.0
    j._recent_win_rate = 0.25
    j._total_completed_trades = 30
    plan = {
        'expected_value': 0.80,      # 上游已用固定 p_win 算出的正 EV
        'p_win_used': 0.55,
        'p_win_source': 'fixed',
        'net_profit_usdt': 3.0,
        'net_loss_usdt': 2.0,
    }
    assert j._check_expected_value('BTC-USDT', plan, score=50.0) is True, \
        "关闭胜率门后低胜率不应拦截"
    print("  ✅ Case 12: 关闭胜率门 → 胜率25% 放行")


def test_ev_gate_disabled_still_blocks_bad_economics():
    """关闭胜率门 → R:R 极差(负 EV)且非强信号 仍被经济门拦"""
    from agents.trading.judge import MultiJudge
    j = MultiJudge(config={'exchange': 'okx', 'max_trade_amount': 10,
                           'ev_winrate_gate_enabled': False, 'ev_neutral_p_win': 0.55})
    j._available_balance = 100.0
    j._recent_win_rate = 0.25
    j._total_completed_trades = 30
    plan = {
        'expected_value': -0.50,     # 经济上亏损期望
        'p_win_used': 0.55,
        'p_win_source': 'fixed',
        'net_profit_usdt': 1.0,
        'net_loss_usdt': 2.0,
    }
    assert j._check_expected_value('BTC-USDT', plan, score=50.0) is False, \
        "经济门应继续拦截负 EV"
    print("  ✅ Case 13: 关闭胜率门 → 负 EV 仍被经济门拦")
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest test_ev_gate.py::test_ev_gate_disabled_allows_low_winrate -v`
Expected: FAIL（当前硬阈值用 `_recent_win_rate=0.25<0.4` 且 score<70 强拒）

- [ ] **Step 3: 分桶块前置开关条件**（`_check_expected_value` 内，`if getattr(self, '_bucketed_ev_enabled', False):`）

改为：
```python
        if self._ev_winrate_gate_enabled and getattr(self, '_bucketed_ev_enabled', False):
```

- [ ] **Step 4: 胜率硬阈值前置开关条件**（`if (effective_win_rate < 0.4 and abs(score) < self._ev_strong_signal_threshold):`）

改为：
```python
        if (self._ev_winrate_gate_enabled and effective_win_rate < 0.4
                and abs(score) < self._ev_strong_signal_threshold):
```

- [ ] **Step 5: 运行两个新测试验证通过**

Run: `python3 -m pytest test_ev_gate.py::test_ev_gate_disabled_allows_low_winrate test_ev_gate.py::test_ev_gate_disabled_still_blocks_bad_economics -v`
Expected: PASS（前者放行、后者仍拦）

- [ ] **Step 6: Commit**

```bash
git add agents/trading/judge.py test_ev_gate.py
git commit -m "feat(ev-gate): 关闭开关时跳过胜率硬阈值与分桶覆盖，保留经济门"
```

---

### Task 5: 在 main() 登记新用例 + 全量回归

**Files:**
- Modify: `test_ev_gate.py`（`main()`）

- [ ] **Step 1: main() 登记 4 个新用例**（在 `test_strategy_review_message_updates_state()` 之后）

```python
    test_p_win_fixed_when_gate_disabled()
    test_ev_gate_disabled_allows_low_winrate()
    test_ev_gate_disabled_still_blocks_bad_economics()
```
并把 `print("\n... ✅ 全部 10 个测试通过")` 的 10 改为 13。

- [ ] **Step 2: 默认配置回归（开关默认 True，行为不变）**

Run: `python3 -m pytest test_ev_gate.py test_phase2_bucketed_ev.py test_phase2_confidence_split.py -q`
Expected: 全部 PASS（含 3 个新用例）

- [ ] **Step 3: 端到端配置验证**

Run: `python3 -c "from utils.config_loader import load_config; c=load_config(strict_live_check=False); print(c.get('ev_winrate_gate_enabled'), c.get('ev_neutral_p_win'))"`
Expected: `False 0.55`

- [ ] **Step 4: Commit**

```bash
git add test_ev_gate.py
git commit -m "test(ev-gate): main() 登记新用例，全量回归通过"
```

---

## Self-Review

- **Spec coverage**：delta spec `open-gate-ev` 四场景 → Task3(p_win fixed)、Task4(低胜率放行 + 经济门拦)、Task1(配置三级注入)；开关默认 True 现状不变 → Task5 默认回归覆盖。无遗漏。
- **Placeholder scan**：无 TBD/TODO；每步含实际代码与命令。
- **Type consistency**：字段名 `_ev_winrate_gate_enabled` / `_ev_neutral_p_win`、配置键 `ev_winrate_gate_enabled` / `ev_neutral_p_win`、p_win source `"fixed"` 全程一致；类名统一 `MultiJudge`。

## 验证（端到端，对应 tasks.md Task 7）
1. `python3 -m pytest test_ev_gate.py test_phase2_bucketed_ev.py test_phase2_confidence_split.py -q` 全过。
2. `load_config()` 读到 `ev_winrate_gate_enabled=False`、`ev_neutral_p_win=0.55`。
3. `git diff --stat b6519db...HEAD` 仅 judge.py / config_loader.py / config.yaml / test_ev_gate.py（+ openspec/docs 元数据）。
