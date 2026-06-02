# Proposal: Entry Drift Hybrid Policy

## Why

5/30 XLM 实盘案例：Judge 在 03:48:08 看到 price=0.2179 算出 plan（entry≈0.2179, SL=0.2125, TP=[0.2312,...], R:R=2.19, lev=10x）；03:48:18 plan SELECTED；03:48:19 限价单挂出，但 executor 入口 `fetch_ticker` 已是 0.2337，limit_price 被"偏离 2% 重新校准"逻辑改写为 0.2334；30 秒等不到成交，03:48:50 fallback 市价 @ 0.2336（较 plan entry 涨 7.2%）；03:48:53 partial_tp_1 立即触发 — 因为 `position.take_profit_levels[0]=0.2312 < entry=0.2336`，**仓刚开就被 TP1 扫掉一半**。整笔 XLM 行情走了 0.2179 → 0.2401（10.2%），10x 杠杆本应是大盈利，结果只赚 +0.78 USDT，剩 25% 残仓还进 EarlyReview 收紧路径微亏收场。

根因有三层：
1. **Plan stale 容忍度过宽**：plan.entry 与 executor 实时价漂移 7.2%，系统只把 `limit_price` 校准了，没有任何"该不该入场"的复检；既有的 fallback 7.2% abandon 检查（`executor.py:2259`）用的 `current_price` 是函数入参（已被 `open_position_with_plan` line 1974 重新 fetch_ticker 覆盖），从设计上就检测不到 plan 与现价的 stale 距离。
2. **Plan 字段里没有 `entry_ref` / `sl_pct` / `tp_pct`**：Judge `_build_plan` 把 price 当入参用完即丢，下游想做"按比例重算 SL/TP"也没有锚点。
3. **partial_tp_1 双源真相**：position 同时落 `take_profit`（被 line 1991-1997 机械修正成 entry+3%）和 `take_profit_levels`（plan 原始 TP 列表），partial_tp_1 判定走后者，方向修正完全是空的；快行情里 entry 漂过 plan TP1 就开仓即触发。

R:R Floor Policy 上次的教训说明：跨路径的判定函数必须单一真相源，不能在调用点重写 if/else（详见 `docs/rr_floor_policy_prd.md`）。本次 drift 重算也必须按同一原则。

## What Changes

### Plan 字段扩展（Judge 侧）

- `agents/trading/judge.py:_build_plan` 新增 3 个字段：`entry_ref`（Judge 决策时点的 price 入参）、`sl_pct`（`|sl - entry_ref| / entry_ref`）、`tp_pct`（list，对应 take_profit 各档位的 `|tp - entry_ref| / entry_ref`）。
- 不破坏既有 `entry_zone` / `stop_loss` / `take_profit` 字段语义，只是补元信息让 executor 可以"按比例平移"。
- `event_backtest.py` / Reviewer 路径同步识别新字段；旧 plan 缺字段时 executor 回退到现行行为（fail-safe，不破坏历史回放）。

### Hybrid 阶梯 Drift Gate（Executor 侧）

新增单一函数 `executor._classify_entry_drift(plan, live_price) -> DriftDecision`，返回：
- `accept`：drift ≤ 0.5%，原计划照走。
- `recalc_small`：0.5% < drift ≤ 2%，调 `_recompute_plan_for_drift()` 按 `sl_pct/tp_pct` 同比例平移到新 entry，R:R 复检 floor 沿用 plan.attribution 里 Judge 决策时的 floor。
- `recalc_medium`：2% < drift ≤ 5%，同上重算，但 R:R floor 加成 +0.20。
- `abandon`：drift > 5%，直接 reject，发 `execution_result.v2 status=rejected reason=drift_too_large`。
- `recalc_fail`：重算后 R:R 不过 floor，reject，reason=`drift_rr_floor_fail`。

drift 测量基准始终是 `plan.entry_ref`（Judge 决策时点），不是 `entry_zone` 中点也不是上次 gate 的 live_price，避免分段累加漏放。

### 双 Gate 执行时机

- **Gate 1**（`open_position_with_plan` 入口，line 1974 之后）：拿到 live_price 立即跑 drift gate；reject/abandon 路径直接 return None，不再下任何单。
- **Gate 2**（`_execute_limit_order` fallback 市价前，line 2257 之后）：基准依然是 `plan.entry_ref`（不是 Gate 1 的 live_price），保证 30 秒内累计漂移依然能触发 abandon。
- 现有 `executor.py:2203` 的"限价单偏离 2% 重新校准" 与 line 2259 的 fallback 0.5% 检查全部由新 drift gate 取代；删除冗余路径。

### 删除机械 TP 方向修正

- `executor.py:1991-1997` 那段 `tp_first = current_price * 1.03`（long）/ `* 0.97`（short）的机械修正全删 — drift gate 通过后 TP1 在 entry 一侧成立性由重算函数保证；不通过的会在 gate 阶段就 reject，不会走到这里。
- `executor.py:1983-1988` 的 SL 方向修正同理：drift gate 通过后 SL 一定在 entry 正确一侧；保留代码但加 invariant 断言（违反则 fail-closed，不再"修正"）。

### partial_tp_1 双源真相统一

- 落库时 `position.take_profit == position.take_profit_levels[0]`，由单一赋值点保证（**BREAKING** 内部 invariant：违反触发 fail-closed 关停 symbol）。
- 重算路径的新 levels 同时写到两个字段。
- 加单测覆盖 invariant。

### Attribution & 可观测性

- `trade_decision.v2.attribution` / `execution_result.v2.attribution` 加字段 `drift_decision ∈ {accept, recalc_small, recalc_medium, abandon, recalc_fail}` 与 `drift_pct`。
- Reviewer 后续可以切片"重算入场"vs"原计划入场"的胜率差（不在本 change 范围，仅打开数据通道）。
- 新增 `risk_alert` type：`entry_drift_abandoned` / `entry_drift_rr_fail`，进 critical_types 通过 Telegram 通知。

## Capabilities

### New Capabilities

- `entry-drift-policy`: Plan 与 Executor 之间的 entry 漂移仲裁单一真相源 — drift band 划分、按比例重算、R:R 复检、双 gate 时机、execution_result.v2 reason 枚举。

### Modified Capabilities

无。Judge plan 字段是新增（向后兼容），不变更现有 spec-level 行为契约。

## Impact

**代码**：
- `agents/trading/judge.py:_build_plan`（+3 字段）
- `executor.py:open_position_with_plan`（line 1974 之后插 Gate 1，删 line 1983-1997 修正，添 invariant）
- `executor.py:_execute_limit_order`（删 line 2203-2205 校准、line 2259-2262 fallback 0.5%，line 2257 之后插 Gate 2）
- `executor.py` 新增 `_classify_entry_drift` / `_recompute_plan_for_drift` / `_apply_recomputed_plan_to_position`
- `agents/trading/executor.py`（攒 `risk_alert.type` 新枚举进 critical_types）
- `event_backtest.py`（plan 字段兼容）

**契约**：
- `trade_decision.v2.attribution` / `execution_result.v2.attribution` 新增 `drift_decision` / `drift_pct`
- `execution_result.v2` 新 reason 枚举：`drift_too_large` / `drift_rr_floor_fail`
- plan 新字段 `entry_ref` / `sl_pct` / `tp_pct`（向后兼容，缺失时 executor fail-safe 回退）

**测试**：
- 新增 `tests/test_entry_drift_hybrid_policy.py`（覆盖 5/30 XLM 7.2% abandon、small band recalc pass/fail、medium band floor 加成、Gate 2 基准 entry_ref 不分段累加、partial_tp_1 双源 invariant）
- baseline 921 → 预计 +20–30 case
- `event_backtest.py` 历史 plan 回放兼容性回归（旧 plan 无新字段不破坏）

**风险**：
- plan.entry_ref 新增字段必须 Judge 与 executor 同步落地，否则 executor 拿不到锚点会全部走 fail-safe 旧路径（行为退化但不破坏）。
- 阶梯阈值（0.5% / 2% / 5%）是初始值，需要 Reviewer 切片观察后续调参；本 change 把阈值集中为 `executor.py` 常量便于后续调整，不进 yaml 配置（避免运行期改阈值绕过单测）。
- Telegram 新 risk_alert 文案与现有 critical_types 列表同步，避免静默吞噬。

**红线遵循**：
- 单一真相源：所有 drift 判定走 `_classify_entry_drift`，主路径与 fallback 路径共用，不在调用点重写。
- close/reduce 不受影响：本 change 只动 open 路径。
- 状态文件命名空间无影响。
- LLM 不参与 drift 判定（纯规则）。
