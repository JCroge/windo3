## Why

第五次系统性审计（`docs/generated_reports/系统性审计报告_20260610_第五次.md`）确认两条同源的短单 gate 缺陷，均经一手代码核对：

- **P1-02（CONFIRMED 0.97）**：`Judge._classify_short_entry_risk`（`agents/trading/judge.py:2692-2694`）用 `float(a or b or default)` 取关键指标。当 `position_in_24h_range == 0.0`（价格恰在 24h 锅底，做空最危险的"追空底部"场景）时，`0.0` 是 falsy → 被合并成默认 `0.5` → `range_position_too_low` gate 失效，系统在 24h 最低点放行做空。`pre_12h_return_pct`（2693）同模式。
- **P1-03（P1 红线，名实不符）**：`Judge._apply_regime_policy`（`agents/trading/judge.py:2914-2950`）内联了短单结构 gate 的**第二份完整实现**，没有委托给被 CLAUDE.md 红线指定为"单一收口"的 `_classify_short_entry_risk`；且 `position_in_24h_range` 缺失默认值在三处发散（`_classify_short_entry_risk`→0.5、`_apply_regime_policy`→1.0、`_check_entry_position_policy`→0.5）。当前阈值下未触发实际分歧，但属脆性约定：改一处阈值语义忘了另一处即发散。CLAUDE.md 红线明文"不能在 `_apply_regime_policy` 调用点重写 daily_bias/range_pos/pre_move/RSI 判定"，此红线当前不成立。

讽刺点：被红线指定为"单一真相源"的 `_classify_short_entry_risk` 恰恰是唯一带 bug 的实现；`_apply_regime_policy` 用 `.get(k, 1.0)` 反而正确处理 0.0；`event_backtest._check_entry_with_regime`（`event_backtest.py:396-441`）的第三份短单 gate 用 `float(row.get(..., 0.5))` 且 row 永不为 None，也正确处理 0.0。故 live 主路径是唯一偏离回测的点——P1-02 修复让 live 向既有正确的回测对齐。

## What Changes

- **P1-02（核心修复）**：`_classify_short_entry_risk` 的指标提取从 `or`-falsy 合并改为显式 None 哨兵合并——区分"present 的 0.0"与"absent"，present 的 `0.0` 必须原样保留进 gate 判定。覆盖 `position_in_24h_range`、`pre_12h_return_pct`（及同模式 `rsi`）。引入极小的 `_coalesce_float(*vals, default)` helper 作为统一合并入口。
- **P1-03（红线归位）**：`_apply_regime_policy` 短单结构段改为 **delegate 到 `_classify_short_entry_risk`**，删除第二份内联实现，统一缺失默认值；**保留 probe 路由外壳**——当 delegate 返回 `daily_bearish_required` 时由外壳决定 `probe_short` 路由或拒单，其它结构性 reason 直接透传拒单。`_apply_short_gate_attribution` 四字段（`short_gate_version/short_gate_decision/short_gate_reason/llm_short_reversal_risk`）在 accept/reject 两路径继续写入。
- **范围内的兄弟 `or`-falsy 点**：`_check_entry_position_policy`（`judge.py:2761`，long overheat gate，真实 gate）一并改用 `_coalesce_float`，消除同类 latent bug 并统一默认值；纯 attribution 写点（`judge.py:2359`）作为 cosmetic 一并改用同 helper 保持一致。
- **测试**：新增 `range_position_24h=0.0` 短单回归（锅底必须 `range_position_too_low` 拒单）；`_apply_regime_policy` delegate 后与 `_classify_short_entry_risk` 同结果的 parity 用例；probe 路由外壳在 delegate 后仍生效的用例；既有 `tests/test_short_main_path_risk_guard.py` 14 case 必须保持全绿。
- **同构核对**：`event_backtest.py` 短单 gate 已正确处理 0.0 且为单份实现，P1-02 是让 live 对齐回测、P1-03 是 live 侧两份合一——回测无需改动，在 tasks/design 记录此结论以满足 CLAUDE.md 红线。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `short-main-path-risk-guard`：强化 "Route-Consistent Short Risk Gate" 需求——(a) 短单 gate 必须把 `position_in_24h_range=0.0`（真实 24h 锅底）当作 present 的 0.0 评估，不得合并为中性默认；(b) `_apply_regime_policy` 必须委托 `_classify_short_entry_risk` 作为唯一短单结构 gate 实现，缺失指标默认值必须跨所有调用方一致，禁止第二份内联实现。

## Impact

- **代码**：
  - `agents/trading/judge.py`：新增 `_coalesce_float` helper；`_classify_short_entry_risk`（2692-2694）改哨兵合并；`_apply_regime_policy`（2914-2950）短单段改 delegate + 保留 probe 外壳；`_check_entry_position_policy`（2761）与 attribution 写点（2359）改用 helper。
- **测试**：扩展 `tests/test_short_main_path_risk_guard.py`（range_pos=0.0 回归 + delegate parity + probe 外壳）。
- **不影响**：`RSI <= 30` 三处硬阈值（`judge.py:853/978/1404`）独立保留不动；probe 路由语义不变；LLM 反转风险只收紧不单独 veto；`event_backtest.py` 决策路径（已正确，单份实现）。
- **风险红线**：修改 Judge 决策路径，必须保持短单 gate 单点收口红线（本 change 正是让该红线名实相符）、`_apply_short_gate_attribution` 四字段不回归；基线当前 `1066 passed`，变更后须全绿。
