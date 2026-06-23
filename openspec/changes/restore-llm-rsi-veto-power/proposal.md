# Proposal: restore-llm-rsi-veto-power

## Why

策略诊断（agent memory `strategy-no-directional-edge-diagnosis`）的**病根3**：规则信号不可否决，反转预警被自我压制。现行代码已核实（judge.py，行号 2026-06-23 实测）：

- **rule_signal 锁方向**：`_compute_score`（judge.py:3316）触发时强制 ±35 主导分数，docstring（judge.py:3319）自称"回测验证83%胜率"——但 live 实测仅 27%，典型过拟合。
- **LLM 不能否决**：判断逻辑（judge.py:1251-1310）注释明写"rule_signal 触发时 LLM 只能降低仓位，不能阻止入场"。LLM 看反 → 只缩仓 60%；LLM hold → 只缩仓 30%。诊断实测 `llm_relation=agree` 4/4 方向全反 = LLM 的"同意"无意义，而它的"反对"又被剥夺否决权。
- **RSI 背离被压制**：背离计分（judge.py:3381-3400）在 HTF 对齐且 RSI 非极端时，把背离分压到 ≤15——压住了唯一的独立反转预警。

净效果：当一笔追势开仓其实买在反转点上时，**没有任何独立反转信号能拦住它**，照样发出 → 放大亏损（病根诊断 + HYPE/marginal60 实证）。

## What Changes

新增**反转合流否决（reversal confluence veto）**：当一笔开仓即将发出，且**同时**满足两个相互独立的反转信号——

1. **LLM 明确看反向**（llm_action 为开仓且方向与待开方向相反，即现有 judge.py:1295-1310「强冲突」分支已识别的条件）；
2. **RSI 背离与开仓方向相反**（待开多单遇 `bearish_div`，待开空单遇 `bullish_div`）——

则把这笔开仓**改路由到已有的 `deferred_pullback`**（等回调再评估），而非立即开仓。

只有**两者共振**才触发（合流，最保守，最小化误杀）。读 `rsi_divergence` 原始布尔信号，**不动打分权重**，因此不与病根1 纠缠。

## Scope

**In**：
- judge.py 新增反转合流检测 + 触发 defer 路由（挂在 LLM 强冲突分支旁 / L1 质量门层，rule_signal 绕不过）。
- 归因字段（observability）：记录是否触发、两路信号取值、被 defer 的方向，供 Reviewer/backtest 切分。
- config 开关 + 阈值（走 config_loader，可回退），因现状打分权重 100% 硬编码。
- event_backtest 验证（**CLAUDE.md 红线**）。
- 单元测试覆盖合流触发/不触发/单信号不触发/defer 路由。

**Out（非目标）**：
- 不下调 rule_signal ±35 强权重（→ 病根1 另起 change）。
- 不改打分各分量权重、不引入独立信号源（→ 病根1）。
- 不动 RSI 背离的 ≤15 分数压制本身（那是 scoring，归病根1；本 change 只用背离的原始布尔信号做 veto 输入）。
- 不碰 RSI≤30 空单硬门（judge.py:890/1015/1443）。
- 不碰出场、体制分类、槽位逻辑。

## Rollback

config 开关关闭即回退旧行为（LLM/RSI 仍只缩仓）；阈值可调。生效需重启 live 交易进程。

## Impact / Red Line

- **策略改动红线**：event_backtest 走 MA 策略、触达不到 LLM-Judge 的 veto 路径，故改用真实磁带（CF 回放口径）验证。
- **实况（见 verify 报告）**：真实磁带 187 笔 accept 中 LLM `llm_relation` 从无 reverse（只 hold/agree）→ veto 当前 **0% 触发**。
- **决定：默认 OFF 潜伏护栏合并**——不改任何线上决策（红线"上 live"不触发）；机制正确就位，启用前须 CF 回放 PnL 验证。
- 更深结论：真正杠杆在病根1（rule_signal ±35 锁方向使 LLM 无从否决），下一 change 处理。
