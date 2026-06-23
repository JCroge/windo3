# 验证报告: restore-llm-rsi-veto-power（反转合流否决）

- 日期：2026-06-23
- change：restore-llm-rsi-veto-power
- 结论：**实现正确，但真实数据证明当前 0% 触发 → 以默认 OFF 潜伏护栏合并**

## 验证方法（红线适配）

CLAUDE.md 红线要求策略改动经 event_backtest 验证。但经核查：**`event_backtest.py`（EventBacktest）走 RobustStrategy MA 信号，不调用 `MultiJudge._make_decision`/`_ask_llm`**，触达不到本 veto 所在的 LLM-Judge 决策路径——event_backtest 对本 change 不适用。

改用更保真的口径：**真实决策磁带** `data/decision_replay_tape.jsonl`（含真实 `llm_output_inline` 与真实 `tech_analysis.momentum.rsi_divergence`），直接判定 veto 触发条件（与 `utils/decision_replay.py` 跑真实 `_make_decision` 同源的输入）。

## 关键发现

样本：187 笔真实 `accept`-open 决策。

| 信号 | 计数 | 占比 |
|---|---|---|
| `llm_relation` = hold | 155 | 82.9% |
| `llm_relation` = agree | 32 | 17.1% |
| `llm_relation` = **reverse** | **0** | **0%** |
| RSI 背离与开仓方向相反 | 71（37 bearish_div@long + 34 bullish_div@short 口径合计，方向匹配后参与） | — |

- **veto（LLM 反向开仓 AND RSI 背离反向）→ 0/187 触发**。根因：**线上 LLM 从不用"反向开仓"表达反对，一律说 `hold`**。已批准设计中"LLM 明确看反=反向开仓建议"的假设在真实数据上不成立。
- 若把 LLM 侧改用 `hold` → 触发 28/187（15%），但 `hold` 占 155/187 近乎默认值，合流**退化为≈纯 RSI 背离门**（即设计阶段明确未选的"先只解锁 RSI 背离"），且其 PnL 证据 join 上仅 n=1（TRUMP −0.37U），不足以支撑。

## 决策

**以默认 OFF 潜伏护栏合并**（用户拍板）：
- `llm_rsi_reversal_veto_enabled` 默认 `false`（DEFAULTS / config.yaml / judge fallback / helper 一致）→ **不改任何线上决策行为**，红线"上 live"不触发（本 change 不改变 live 决策）。
- 实现正确、单点收口、单测齐全（14 passed），机制就位：**若未来 LLM 行为产出可执行的反向判断，置 `true` 即生效**；启用前须以 CF 回放（`utils/decision_replay.py`）做 pre/post PnL 验证。
- 更深结论：**"恢复 LLM 否决权"前提部分落空**——rule_signal ±35 锁死方向（病根1），LLM 无独立反转料可依，只能 hold/agree。真正的杠杆在病根1（降 ±35 主导 + 引入独立信号源）。本发现记入下一 change。

## 测试

- `test_reversal_confluence_veto.py`：14 passed（config 默认 off、helper 合流判定、defer 路由+归因、主路径接线、deferred 边界、放行归因、banner）。
- 回归：`test_long_entry_position_guard.py` / `tests/test_short_main_path_risk_guard.py` / `test_ev_gate.py` / `test_judge_close_cause.py` / `test_judge_15m_filter.py` / `test_config_clamp_fallback.py` 合计 150 passed，零回归。

## 启用前置（留档）

启用（置 true）前须：(1) 攒更多磁带确认 LLM 是否产出 reverse 判断（或决定改用 hold 口径）；(2) CF 回放跑被 veto 样本 pre/post PnL，过通过标准（净 PnL 不变差 + 触发率低区间）。
