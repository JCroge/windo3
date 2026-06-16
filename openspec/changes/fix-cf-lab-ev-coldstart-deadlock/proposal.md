## Why

反事实策略实验室 L1-L4 全建成、磁带也累积充足（v2 修复后 573 条、klines_1s 真 1s 粒度且完全覆盖窗口），但 `cf_direction_recommendation.py` 兑现时**全程 `cf_open=0 / div=0 / delta=0 / no_actionable_direction`**，实验室对任何单旋钮都给不出方向。多轮证伪后定位到真根因：**L3b 序列组合模拟存在 EV-gate 冷启动死锁**——`utils/sequential_perturbation.py::run_arm` 经 `_inject_cf_state` 把 CF 组合自身的**冷 EV 状态**（`p_win=40% bayesian_prior`）灌进记录再 `replay_decision`，使真实 Judge 的 EV gate 算出负 EV 直接拒开仓。死锁链：开仓需正 EV → EV 靠累计 CF 胜率 → 没单开成 → 胜率不累计 → EV 永远冷 → 永不开仓。

关键矛盾：这些单 live 当时撞的是 `rr_below_floor`（说明 live 的 EV gate **是过的**），但 CF-sim 注入的冷 EV 比现实更悲观，把本该过的 EV gate 拦死。于是无论怎么扫 R:R 地板，上游 EV gate 先把所有候选灭掉。

> **已证伪、勿写进 scope**：tape tech 捕获正确、L2 回放忠实（同一记录直接 `replay_decision({rr_floor_default:0.3})` → `open_long`；经 `_inject_cf_state` 后 → `hold (EV=-0.41<0.05 p_win=40%)`）。早先怀疑的"抓错 tech 快照 / hold 当 reject 的虚假保真"机制均不成立。

## What Changes

- **修复 EV 冷启动死锁（主）**：让 CF-sim 在 EV gate 处的状态贴近 live 决策时的真实 EV，使被扰动旋钮（如 R:R 地板）真正能影响开仓结果。具体修法（贴 live EV / 用录制 EV / 调整 `_seed_cf_prior` 暖启动 / CF-sim EV gate 改读录制 EV）留 design 阶段 brainstorm 定夺。
- **保真改 gate-level 比对**：`baseline_fidelity` 当前把 `live=reject` 与 `CF-sim=hold(换 EV gate 拦)` 都归"非-accept"类即算复现，对"换了个 gate 拦"是盲的 → 改为 `reject_reason` / 实际触达 gate 一致才算复现，使 EV-gate 误拦能被保真指标暴露而非掩盖。
- **驱动按 v2 过滤**：`cf_direction_recommendation.py::load_records` 全量喂入含 932 条 stale `replayable=true` 的 v1 旧空记录 → 改为按 `schema v2 AND tech 非空` 过滤，不盲信写入时已固化的 stale `replayable` 标志。
- 全程 **observability-only / write-only**，红线守卫（`tests/test_cf_red_line_guard.py`）维持：禁止任何 gate/rank/veto/halt/daily-stop 读取 CF 产物。

## Capabilities

### New Capabilities
<!-- 无新增能力；本 change 修正既有 L3b/L4 能力的需求级行为 -->

### Modified Capabilities
- `counterfactual-portfolio-sim`: CF 组合在 EV gate 判定处使用的 EV/胜率状态须避免冷启动死锁——不得因初始先验比 live 决策时更悲观而系统性拦死本该通过 EV gate 的候选。
- `sequential-perturbation-driver`: `_inject_cf_state` 注入的 CF 状态须与 live 决策时的真实 EV 一致性可控，不得引入比现实更悲观的 EV 而使被扰动旋钮失效；`run_arm` 在两臂均开仓为 0 时须可与"旋钮无效"区分。
- `perturbation-delta-report`: `baseline_fidelity` 须按 gate-level（`reject_reason` / 触达 gate）比对而非 accept-vs-非accept 类，确保"换 gate 拦"被计为不复现并反映到 untrustworthy / fidelity_note。
- `replay-report-driver`: 报告驱动须按 `schema v2 AND tech 非空`过滤可回放记录，不盲信 stale `replayable` 标志。

## Impact

- 代码：`utils/sequential_perturbation.py`、`utils/cf_portfolio.py`、`utils/knob_sweep.py`、`cf_direction_recommendation.py`，及对应测试（`tests/` 下 cf/perturbation/portfolio/sweep 相关 + 红线守卫）。
- 行为：仅影响反事实实验室的回放/扫描产物质量；**不触及交易决策路径、不改 live Judge 逻辑、不改 choppy R:R 地板 1.50、不新增 LLM 旋钮扰动**。
- 风险红线：CLAUDE.md 反事实回放产物 observability-only write-only 约束不变；无需 `event_backtest` 同构（不改策略公式）。
