---
change: trend-entry-rr-fidelity
design-doc: docs/superpowers/specs/2026-06-17-trend-entry-rr-fidelity-design.md
base-ref: 582a0639aa52c0e64ae9dd013123308b7d5a42e8
---

# Trend-Entry R:R Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让入场 gate 不再把干净趋势挡在门外——① 给干净趋势授予趋势对齐 R:R 地板,② 让 effective_rr 口径对齐 executor 真实阶梯离场。

**Architecture:** 两处独立改动均在 `agents/trading/judge.py`,各自 config 开关(默认关),互不依赖。① 在 `_select_rr_floor` 的 long_aligned 判定加「客观路径证据」OR 分支(仅用 `tech.entry_context` 的入场前字段);② 抽出 `_compute_ladder_rr` 纯函数,在 `_build_plan` 按开关替换 effective_rr。全部用 TDD,改完用 `event_backtest` 四臂全样本 A/B 背书。

**Tech Stack:** Python 3.9, pytest, pandas(event_backtest)。

---

## 文件结构

- 修改:`agents/trading/judge.py`
  - `__init__`:新增 4 个 config 字段(两开关 + 阈值)
  - `_select_rr_floor`(2531-2550):long_aligned 判定加 OR 客观证据分支
  - `_build_plan`(3427-3433):effective_rr 按开关走 `_compute_ladder_rr`
  - 新增纯函数 `_compute_ladder_rr(...)`
- 修改:`agents/trading/tech_analyst.py` — 无需改(`entry_context` 已含所需字段)
- 测试:`test_rr_floor_policy.py`(①)、新增 `test_ladder_weighted_rr.py`(②)
- A/B:`event_backtest.py` 已建模阶梯,新增四臂对照脚本 `cf_rr_fidelity_ab.py`(repo 根,observability-only)

---

## Task 1: 新增 config 开关与阈值

**Files:**
- Modify: `agents/trading/judge.py` (在 `__init__` 的 rr_floor 字段附近,约 162-167 行后)

- [ ] **Step 1: 加 config 字段**

在 `agents/trading/judge.py` `__init__` 中 `self._low_rr_long_aligned_enabled = ...` 那一行之后插入:

```python
        # trend-entry-rr-fidelity 杠杆① P1:客观路径证据授对齐地板(默认关,灰度)
        self._path_evidence_aligned_enabled = config.get('path_evidence_aligned_enabled', False) if config else False
        self._path_evidence_min_pre12h_return = config.get('path_evidence_min_pre12h_return', 0.03) if config else 0.03
        self._path_evidence_max_range_pos = config.get('path_evidence_max_range_pos', 0.92) if config else 0.92
        self._path_evidence_min_strength = config.get('path_evidence_min_strength', 60) if config else 60
        # trend-entry-rr-fidelity 杠杆② v1:阶梯加权 effective_rr(默认关,灰度)
        self._ladder_rr_enabled = config.get('ladder_rr_enabled', False) if config else False
```

- [ ] **Step 2: 运行现有 rr_floor 测试确认无回归**

Run: `python3 -m pytest test_rr_floor_policy.py -q`
Expected: PASS(新增字段不影响既有行为)

- [ ] **Step 3: Commit**

```bash
git add agents/trading/judge.py
git commit -m "feat(rr-fidelity): add config flags for path-evidence floor and ladder rr (default off)"
```

---

## Task 2: 杠杆① — _select_rr_floor 客观路径证据 OR 分支

**Files:**
- Modify: `agents/trading/judge.py:2531-2550`(long_aligned 判定块)
- Test: `test_rr_floor_policy.py`

- [ ] **Step 1: 写失败测试**

在 `test_rr_floor_policy.py` 末尾追加(复用文件内 `_make_judge`;注意该 helper 默认未设新字段,测试里直接补设):

```python
def _make_judge_path(regime='choppy', overrides=None):
    j = _make_judge(regime=regime, config_overrides=overrides)
    # 新字段在 _make_judge 中未设,显式补设以反映 Task 1 行为
    j._path_evidence_aligned_enabled = (overrides or {}).get('path_evidence_aligned_enabled', True)
    j._path_evidence_min_pre12h_return = 0.03
    j._path_evidence_max_range_pos = 0.92
    j._path_evidence_min_strength = 60
    return j


def _clean_trend_tech():
    """choppy regime 下的干净 long 趋势:bias 漏报(neutral),但路径证据明确。"""
    return {
        'trend': {'direction': 'bullish', 'strength': 70,
                  'higher_tf_bias': 'neutral', 'daily_bias': 'neutral'},
        'entry_timing': {'tf_15m_block_long': False},
        'entry_context': {'pre_12h_return_pct': 0.08, 'position_in_24h_range': 0.6,
                          'prev_daily_return_pct': 0.05},
    }


def test_path_evidence_grants_aligned_floor():
    j = _make_judge_path(regime='choppy')
    min_rr, policy, reason = j._select_rr_floor('open_long', {}, _clean_trend_tech(), score=60)
    assert min_rr == 1.30
    assert policy == 'long_aligned_path_evidence'


def test_path_evidence_real_choppy_not_granted():
    """方向反复/回撤大:pre_12h_return 为负 → 不授对齐地板。"""
    j = _make_judge_path(regime='choppy')
    tech = _clean_trend_tech()
    tech['entry_context']['pre_12h_return_pct'] = -0.02
    min_rr, policy, reason = j._select_rr_floor('open_long', {}, tech, score=60)
    assert min_rr == 1.50
    assert policy == 'default'


def test_path_evidence_overheated_not_granted():
    """追高(range_pos 过高)→ 不授对齐地板。"""
    j = _make_judge_path(regime='choppy')
    tech = _clean_trend_tech()
    tech['entry_context']['position_in_24h_range'] = 0.97
    min_rr, policy, reason = j._select_rr_floor('open_long', {}, tech, score=60)
    assert min_rr == 1.50


def test_path_evidence_switch_off_keeps_default():
    j = _make_judge_path(regime='choppy', overrides={'path_evidence_aligned_enabled': False})
    min_rr, policy, reason = j._select_rr_floor('open_long', {}, _clean_trend_tech(), score=60)
    assert min_rr == 1.50
    assert policy == 'default'
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_rr_floor_policy.py -k path_evidence -q`
Expected: FAIL(当前授 default 1.50,policy='default')

- [ ] **Step 3: 实现 OR 分支**

在 `agents/trading/judge.py` 的 long_aligned 块(2540-2550)里,把 `aligned = (...)` 与其后的 `if aligned:` 替换为:

```python
            aligned = (sym_dir == 'bullish'
                       and (htf_bias == 'bullish' or daily_bias == 'bullish')
                       and not block_long
                       and abs(score) >= min_deferred_score)
            # trend-entry-rr-fidelity 杠杆① P1:bias 漏报时用入场前客观路径证据补判
            path_evidence = False
            if (not aligned
                    and getattr(self, '_path_evidence_aligned_enabled', False)
                    and not block_long
                    and abs(score) >= min_deferred_score):
                ectx = (tech or {}).get('entry_context', {}) or {}
                strength = trend.get('strength', 0)
                pre12h = ectx.get('pre_12h_return_pct', 0.0)
                range_pos = ectx.get('position_in_24h_range', 0.5)
                path_evidence = (sym_dir == 'bullish'
                                 and strength >= getattr(self, '_path_evidence_min_strength', 60)
                                 and pre12h >= getattr(self, '_path_evidence_min_pre12h_return', 0.03)
                                 and range_pos <= getattr(self, '_path_evidence_max_range_pos', 0.92))
            if aligned:
                return (
                    rr_floor_long_aligned,
                    'long_aligned_low_rr',
                    f'long_aligned:regime={eff_regime},'
                    f'sym_trend={sym_dir},htf={htf_bias},daily={daily_bias}',
                )
            if path_evidence:
                return (
                    rr_floor_long_aligned,
                    'long_aligned_path_evidence',
                    f'long_aligned_path:regime={eff_regime},'
                    f'strength={trend.get("strength")},pre12h={ectx.get("pre_12h_return_pct")},'
                    f'range_pos={ectx.get("position_in_24h_range")}',
                )
```

- [ ] **Step 4: 运行确认通过 + 全文件无回归**

Run: `python3 -m pytest test_rr_floor_policy.py -q`
Expected: PASS(含新 4 个 path_evidence 测试 + 既有用例)

- [ ] **Step 5: 勾选 tasks.md 1.x 并 Commit**

```bash
git add agents/trading/judge.py test_rr_floor_policy.py openspec/changes/trend-entry-rr-fidelity/tasks.md
git commit -m "feat(rr-fidelity): grant aligned floor to clean trends via pre-entry path evidence (lever 1)"
```

---

## Task 3: 杠杆② — 阶梯加权 effective_rr 纯函数

**Files:**
- Modify: `agents/trading/judge.py`(新增 `_compute_ladder_rr`,改 `_build_plan` effective_rr)
- Test: `test_ladder_weighted_rr.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `test_ladder_weighted_rr.py`:

```python
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from agents.trading.judge import MultiJudge


def _judge():
    j = MultiJudge.__new__(MultiJudge)
    return j


def test_ladder_ge_tp1_when_all_positive():
    """阶梯加权 ≥ 仅TP1 口径(各档正贡献)。"""
    j = _judge()
    # tp_dists 三档, notional/gross_loss/cost 简单数
    ladder = j._compute_ladder_rr(
        tp_dists=[0.023, 0.045, 0.068], sl_dist=0.0145,
        notional=1000.0, gross_loss=14.5, total_cost=3.0)
    tp1_only = (1000.0 * 0.023 - 3.0) / (14.5 + 3.0)
    assert ladder >= round(tp1_only, 2)


def test_ladder_far_tier_low_prob_no_inflation():
    """远档到达概率低(0.5/0.25),不应把 effective_rr 抬到几何满额。"""
    j = _judge()
    ladder = j._compute_ladder_rr(
        tp_dists=[0.023, 0.045, 0.068], sl_dist=0.0145,
        notional=1000.0, gross_loss=14.5, total_cost=3.0)
    # 几何满额(各档P=1, 剩余记TP3):明显高于折扣后
    full = (1000.0 * (0.5*0.023 + 0.25*0.045 + 0.25*0.068) - 3.0) / (14.5 + 3.0)
    assert ladder < round(full, 2)


def test_ladder_remainder_conservative():
    """剩余 25% 记 +1R 锁利(=sl_dist),不记最远档。"""
    j = _judge()
    # 远档极大时,保守口径使结果远低于"剩余记最远档"
    ladder = j._compute_ladder_rr(
        tp_dists=[0.02, 0.04, 0.30], sl_dist=0.02,
        notional=1000.0, gross_loss=20.0, total_cost=2.0)
    optimistic = (1000.0 * (0.5*0.02 + 0.25*0.04 + 0.25*0.30) - 2.0) / (20.0 + 2.0)
    assert ladder < round(optimistic, 2)


def test_ladder_missing_tiers_normalized():
    """只有 1 档时退化为该档(权重归一),不报错。"""
    j = _judge()
    ladder = j._compute_ladder_rr(
        tp_dists=[0.03], sl_dist=0.02,
        notional=1000.0, gross_loss=20.0, total_cost=2.0)
    assert ladder > 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_ladder_weighted_rr.py -q`
Expected: FAIL with "AttributeError: ... _compute_ladder_rr"

- [ ] **Step 3: 实现 `_compute_ladder_rr`**

在 `agents/trading/judge.py` 的 `_build_plan` 定义之前插入纯函数:

```python
    # trend-entry-rr-fidelity 杠杆② v1:阶梯离场比例加权 effective_rr(Option B,无概率折扣)
    _LADDER_WEIGHTS = (0.50, 0.25, 0.25)      # 对齐 executor 50/25/25 真实离场比例

    def _compute_ladder_rr(self, tp_dists, sl_dist, notional, gross_loss, total_cost):
        """按真实阶梯离场比例加权的 effective_rr(与旧口径同"目标达成"假设)。

        - tp_dists: 各 TP 档距离(占比),升序;不足 3 档则权重归一到现有档。
        - 剩余 trailing 档(第3档)的盈利距离保守封顶 min(tp_dist3, sl_dist),即至多记 +1R 锁利。
        - 不施加 P(reach tier) 概率折扣:旧 TP1-only 口径本就隐含 TP1 必达,只对新口径缩分子
          而不缩阶梯化后降低的风险分母会反向压低 R:R(v2 才做相干的概率+风险口径)。
        """
        if not tp_dists or sl_dist <= 0:
            return 1.0
        weights = list(self._LADDER_WEIGHTS[:len(tp_dists)])
        wsum = sum(weights)
        if wsum <= 0:
            return 1.0
        weights = [w / wsum for w in weights]   # 缺档归一化
        exp_profit = 0.0
        for i, dist in enumerate(tp_dists):
            d = dist
            if i == 2:  # 剩余 trailing 档:保守 +1R 锁利上限
                d = min(dist, sl_dist)
            exp_profit += weights[i] * (notional * d)
        denom = gross_loss + total_cost
        if denom <= 0:
            return 1.0
        return round((exp_profit - total_cost) / denom, 2)
```

**注**:测试 `test_ladder_ge_tp1_when_all_positive` 的基线必须是**真实旧口径** `(notional*tp_dists[0] - cost)/(gross_loss+cost)`(满仓 TP1),断言 ladder ≥ 它。这是不注水/不反向的守卫,不得改基线来凑过。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest test_ladder_weighted_rr.py -q`
Expected: PASS(4 测试)

- [ ] **Step 5: Commit**

```bash
git add agents/trading/judge.py test_ladder_weighted_rr.py
git commit -m "feat(rr-fidelity): add ladder-weighted effective_rr pure fn with conservative priors (lever 2)"
```

---

## Task 4: 把阶梯口径接入 _build_plan(按开关)

**Files:**
- Modify: `agents/trading/judge.py:3427-3433`(effective_rr 计算)与 return dict

- [ ] **Step 1: 写失败测试(开关切换行为)**

在 `test_ladder_weighted_rr.py` 追加:

```python
def _plan_inputs():
    # 直接验证开关分支:用最小 stub 调 _build_plan 太重,改测 _effective_rr_for_plan 包装
    return dict(tp_dists=[0.023, 0.045, 0.068], sl_dist=0.0145,
                notional=1000.0, gross_loss=14.5, total_cost=3.0)


def test_effective_rr_switch_off_uses_tp1():
    j = _judge()
    j._ladder_rr_enabled = False
    val = j._effective_rr_for_plan(**_plan_inputs())
    tp1_only = round((1000.0 * 0.023 - 3.0) / (14.5 + 3.0), 2)
    assert val == tp1_only


def test_effective_rr_switch_on_uses_ladder():
    j = _judge()
    j._ladder_rr_enabled = True
    val = j._effective_rr_for_plan(**_plan_inputs())
    assert val == j._compute_ladder_rr(**_plan_inputs())
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_ladder_weighted_rr.py -k effective_rr_switch -q`
Expected: FAIL with "AttributeError: ... _effective_rr_for_plan"

- [ ] **Step 3: 加包装函数 + 接入 _build_plan**

在 `_compute_ladder_rr` 之后加包装:

```python
    def _effective_rr_for_plan(self, tp_dists, sl_dist, notional, gross_loss, total_cost):
        """按 ladder_rr_enabled 开关返回 effective_rr;关闭时为旧 TP1-only 口径。"""
        denom = gross_loss + total_cost
        if getattr(self, '_ladder_rr_enabled', False):
            return self._compute_ladder_rr(tp_dists, sl_dist, notional, gross_loss, total_cost)
        tp1 = tp_dists[0] if tp_dists else sl_dist
        return round((notional * tp1 - total_cost) / denom, 2) if denom > 0 else 1.0
```

在 `_build_plan` 中,把:

```python
        effective_rr = round((gross_profit - total_cost) / (gross_loss + total_cost), 2) if (gross_loss + total_cost) > 0 else 1.0
```

替换为:

```python
        tp_dists = [abs(tp - price) / price for tp in take_profit] if take_profit else [sl_dist]
        effective_rr_tp1 = round((gross_profit - total_cost) / (gross_loss + total_cost), 2) if (gross_loss + total_cost) > 0 else 1.0
        effective_rr = self._effective_rr_for_plan(tp_dists, sl_dist, notional, gross_loss, total_cost)
        effective_rr_ladder = self._compute_ladder_rr(tp_dists, sl_dist, notional, gross_loss, total_cost)
```

并在 `_build_plan` 的 return dict 里,`"effective_risk_reward_ratio": effective_rr,` 之后加可观测字段:

```python
            "effective_rr_tp1": effective_rr_tp1,
            "effective_rr_ladder": effective_rr_ladder,
            "ladder_rr_enabled": bool(getattr(self, '_ladder_rr_enabled', False)),
            "ladder_weights": list(self._LADDER_WEIGHTS),
```

- [ ] **Step 4: 运行确认通过 + judge 相关回归**

Run: `python3 -m pytest test_ladder_weighted_rr.py test_rr_floor_policy.py test_risk_budget.py -q`
Expected: PASS

- [ ] **Step 5: 勾选 tasks.md 2.x 并 Commit**

```bash
git add agents/trading/judge.py test_ladder_weighted_rr.py openspec/changes/trend-entry-rr-fidelity/tasks.md
git commit -m "feat(rr-fidelity): wire ladder rr into _build_plan behind switch with observability fields"
```

---

## Task 5: 四臂全样本 A/B(event_backtest)

**Files:**
- Create: `cf_rr_fidelity_ab.py`(repo 根,observability-only)
- 复用:`event_backtest.py`(已建模 50%@TP1 + trailing)

- [ ] **Step 1: 确认 event_backtest 入口签名**

Run: `python3 -c "import event_backtest, inspect; print([m for m in dir(event_backtest) if not m.startswith('__')][:20])"`
Expected: 打印模块成员(确认类名/run 入口,供脚本调用)

- [ ] **Step 2: 写四臂 A/B 脚本**

新建 `cf_rr_fidelity_ab.py`,对同一历史数据集跑四臂(baseline / 仅① / 仅② / ①+②),各臂用不同 config 开关组合实例化回测,产出净 PnL/胜率/MDD。脚本骨架(按 Step 1 的真实签名补全数据加载):

```python
"""trend-entry-rr-fidelity 四臂全样本 A/B。observability-only,输出严禁交易决策读取。"""
import json
from event_backtest import EventBacktest  # 按 Step 1 实际类名调整

ARMS = {
    'baseline':       dict(path_evidence_aligned_enabled=False, ladder_rr_enabled=False),
    'lever1_only':    dict(path_evidence_aligned_enabled=True,  ladder_rr_enabled=False),
    'lever2_only':    dict(path_evidence_aligned_enabled=False, ladder_rr_enabled=True),
    'lever1_plus_2':  dict(path_evidence_aligned_enabled=True,  ladder_rr_enabled=True),
}

def run_arm(name, flags, df, symbol):
    bt = EventBacktest(**flags)          # 按真实构造函数传 flags/config
    res = bt.run(df, symbol=symbol)
    return {
        'arm': name,
        'net_pnl': res.get('net_pnl'),
        'win_rate': res.get('win_rate'),
        'max_drawdown': res.get('max_drawdown'),
        'trades': res.get('num_trades'),
    }

def main():
    # TODO(Step 3): 载入全样本(含亏单)数据集 df + symbol 列表
    rows = []
    # for df, symbol in load_full_sample():
    #     for name, flags in ARMS.items():
    #         rows.append(run_arm(name, flags, df, symbol))
    print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))

if __name__ == '__main__':
    main()
```

- [ ] **Step 3: 接全样本数据并跑**

按 `test_event_backtest_real_data.py` 的数据加载方式补全 `load_full_sample()`(全样本含亏单,不只趋势赢家),运行:

Run: `python3 cf_rr_fidelity_ab.py`
Expected: 打印四臂 net_pnl / win_rate / max_drawdown / trades

- [ ] **Step 4: 记录背书结论**

把四臂结果写入 `openspec/changes/trend-entry-rr-fidelity/specs/`同级的 `ab_result.md`(change 目录下),给出背书判断:净 PnL 改善且胜率不显著下降则 PASS。

```bash
git add cf_rr_fidelity_ab.py openspec/changes/trend-entry-rr-fidelity/ab_result.md
git commit -m "test(rr-fidelity): four-arm full-sample A/B harness + result record"
```

---

## Task 6: 全量回归 + 收尾

- [ ] **Step 1: 全量测试零回退**

Run: `python3 -m pytest -q`
Expected: PASS,数量 ≥ 1270 基线(新增 path_evidence + ladder 测试净增)

- [ ] **Step 2: 勾选 tasks.md 剩余项 + 登记拆出 change**

确认 tasks.md 4.3 已登记:① P2 bias 上游根治、② v2 频率校准。

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore(rr-fidelity): finalize lever1+lever2, register P2/v2 follow-up changes"
```

---

## Self-Review 记录

- **Spec 覆盖**:`trend-aligned-rr-floor`(授对齐地板/真choppy不误授/禁前视/灰度/可观测)→ Task 2;`ladder-weighted-rr`(阶梯加权/保守剩余/保守先验/可观测/全样本A/B/灰度)→ Task 3+4+5。
- **禁前视**:Task 2 证据仅取 `tech.entry_context`(决策时点产出,无未来 bar)→ 满足 scenario「客观证据禁前视」。
- **不注水**:Task 3 概率 [1.0,0.5,0.25] + 剩余 +1R 上限,测试 `test_ladder_far_tier_low_prob_no_inflation` / `test_ladder_remainder_conservative` 守门。
- **开关默认关**:Task 1 全 False;Task 2/4 开关关闭回退测试。
- **类型一致**:`_compute_ladder_rr` / `_effective_rr_for_plan` 签名在 Task 3/4 一致。
