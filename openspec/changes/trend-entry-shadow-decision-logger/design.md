# Design (high-level): trend-entry-shadow-decision-logger

> 高层方向。深度技术设计 + 5 个待定决策由 comet-design（brainstorming）产出 Design Doc 后定稿。

## 核心思路（待 brainstorming 确认）

复用现成隔离机器，最小新增面：

- **hook 点**：live 决策磁带 chokepoint（`judge.py:2004` accept / `3093` reject），此处已 `build_bundle(tech, llm_inline, state_snapshot)`。
- **影子 = 同 bundle 再 replay 一次 flags-on**：`utils/decision_replay.py::replay_decision(bundle, {path_evidence_aligned_enabled: True, ladder_rr_enabled: True})` → 得影子决策。replay 天生隔离（mock 外部 await、用缓存 llm、捕获 publish 不进真实 bus、用 `MultiJudge.__new__` 不动 live 实例）。
- **记录**：新 observability 产物（如 `data/shadow_decision_log.jsonl`）写 `{ts, symbol, real_action+gate, shadow_action+gate, flip_kind, tech_context, plan, 结局锚}`。
- **结局锚**：影子开仓的前向结局复用 `resolve_counterfactual` + klines（与 rejected 流同口径）或 shadow-forward 结算。

## 隔离红线（observability-only write-only）

与 CF 产物 / provenance / agent-health 同性质：
- 影子决策**绝不** publish 真实 bus、**绝不**下单、**绝不** mutate live Judge / portfolio / cooldown / daily-stop 状态。
- 影子产物**严禁**任何 gate/rank/veto/halt/daily-stop 读取（红线守卫 `tests/test_cf_red_line_guard.py` 扩展禁读断言）。
- live 写影子日志允许（与 Judge 写决策磁带同性质）；禁的是决策/风控路径**读**。

## 待 brainstorming 定的设计决策

1. 影子跑法：复用 `replay_decision` 前向 vs 抽共享纯决策函数双调（性能/耦合权衡）。
2. hook 落点：决策磁带 chokepoint 内联 vs 独立 hook（避免拖慢 live 决策）。
3. 结局锚结算口径与节流。
4. 性能与失败安全（影子异常绝不破 live 决策——`getattr`/try 防御）。
5. 对比报表（驱动脚本形态，复用 cf_honesty_gate 诚实门）。

## 数据流（live 不变）

`tech_analysis → Judge._make_decision`（live 决策，照常 publish）`→ [chokepoint] 旁路:同 bundle replay flags-on → shadow_decision_log`（write-only）。live 链路零结构改动，影子是纯旁路。

## 风险与回滚

- 影子路径异常**必须** fail-safe 不影响 live 决策（防御性 getattr/try，缺则跳过本次影子记录）。
- 可经 config flag（如 `shadow_decision_logger_enabled`）整体关闭。
