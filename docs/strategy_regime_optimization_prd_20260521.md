# 策略 Regime 优化产品需求文档

日期：2026-05-21  
关联文档：`docs/strategy_optimization_plan_20260521.md`  
需求主题：在不显著增加系统复杂度的前提下，修复 long/short 表现分化、低 R:R long 被误杀、bullish 市场 short 过度亏损的问题。

## 1. 背景

用户回溯过去约 18 小时代表性信号后发现：

- 整体假设胜率约 43.8%。
- long 假设胜率约 66.7%。
- short 假设胜率约 14.3%。
- 部分 long 信号虽然 R:R 低于 1.5，但后续走出了足够收益。
- short 信号在偏多市场中几乎全错。

上一版优化方案提出了：方向分层 EV、market regime、动态 R:R、short guard、CounterfactualLedger、低 R:R 持仓管理。用户进一步指出这些方案会引入新风险：regime 抖动、参数爆炸、short 错过早期反转、ledger 幸存者偏差、低 R:R 占用 slot、分批止盈执行复杂。

本 PRD 的核心原则是：**先用最小 live 改动验证主要假设，所有复杂机制先观察、后交易、可回滚。**

## 2. 产品目标

1. 在 bullish 市场中减少低质量 short live 开仓。
2. 在明确 bullish long 场景中，允许少量低 R:R 机会进入 live 或 paper 验证。
3. 防止 regime 边界抖动导致同一信号忽放忽拒。
4. 避免新增大量参数导致过拟合和系统不可解释。
5. 保留 short 早期反转探测能力，避免系统在顶部完全失明。
6. 建立被拒 plan 的反事实账本，但明确它只诊断“被拒信号”，不代表全市场机会。
7. 首阶段不做价格触发的分批止盈，避免执行层竞态风险。

## 3. 本期范围

### 3.1 Phase 1 必做

- Regime 判定加 hysteresis。
- 首版 live 只开放两个策略开关：
  - `RR_FLOOR_LONG_BULLISH`
  - `SHORT_REGIME_GUARD_ENABLED`
- bullish regime 下 short 强过滤。
- bullish regime 下保留一个小仓 `probe_short` 早期反转通道。
- 低 R:R long 与普通主槽位分层，避免挤掉高 R:R 信号。
- CounterfactualLedger 记录被拒但已有 plan 的信号。
- 所有新行为写入 attribution，支持复盘。

### 3.2 Phase 1 不做

- 不做价格触发分批止盈。
- 不引入 15 个以上 live 参数。
- 不把 rejected ledger 结果直接用于自动调参。
- 不扩大 `MAX_TRADE_AMOUNT`。
- 不提高 `MAX_CONCURRENT_POSITIONS`。
- 不让 LLM 绕过 regime / EV / R:R / 15m / 风控。

### 3.3 Phase 2 候选

只有 Phase 1 连续 paper/testnet 验收通过后，才考虑：

- side/regime 独立 Bayesian EV。
- 低 R:R long 的 TP1/保本止损。
- 更细的 short reversal 模型。
- CounterfactualLedger 覆盖 near-miss hold 样本。

## 4. 核心设计

### RQ-REG-01：Regime Hysteresis

系统必须维护稳定的 `effective_regime`，不能直接使用单次计算结果作为交易门槛。

新增状态：

```json
{
  "effective_regime": "bullish",
  "candidate_regime": "mixed",
  "candidate_count": 1,
  "confidence": 72,
  "last_changed_at": 1770000000,
  "basis": {}
}
```

切换规则：

- `mixed -> bullish`：需要连续 2 次判定为 bullish，且 confidence >= 65。
- `bullish -> mixed`：需要连续 2 次判定为 mixed，或 1 次 bearish 且 confidence >= 80。
- `bullish -> bearish`：需要连续 3 次 bearish，或出现全市场 critical risk event。
- `bearish -> mixed/bullish` 同理。
- 每次切换后进入最小停留期，默认 30 分钟；停留期内只允许 critical downgrade。

交易决策使用 `effective_regime`，并把它固化到 `plan.attribution.entry_regime`。后续 PositionAnalyst 不得因为短时间 regime 变化立即否定刚开仓的低 R:R long。

PositionAnalyst 规则：

- 持仓打开后 60 分钟内，regime 从 bullish 抖到 mixed 不触发减仓。
- regime 连续 2 次与持仓方向反向，且 15m 同时反向，才进入减仓评估。
- entry_regime 与 current_regime 必须同时出现在 review 日志里。

### RQ-REG-02：首版参数约束

Phase 1 live 只允许新增或改变两个策略交易参数：

```dotenv
RR_FLOOR_LONG_BULLISH=1.30
SHORT_REGIME_GUARD_ENABLED=true
```

其他参数只允许作为观察项或固定默认值：

```dotenv
REGIME_HYSTERESIS_ENABLED=true
COUNTERFACTUAL_LEDGER_ENABLED=true
LOW_RR_SLOT_ENABLED=true
PROBE_SHORT_ENABLED=true
```

这些开关可以控制功能启停，但不得在 Phase 1 中做网格寻优。

配置原则：

- 不允许一次性上线 10+ 可调交易参数。
- 每个阶段最多新增 2 个会改变 live 交易结果的参数。
- 参数必须进入启动 banner 和日志。
- 参数默认值必须保守。
- 参数变更必须能通过 `.env` 回滚。

### RQ-REG-03：Short Regime Guard

当 `effective_regime=bullish` 时，普通 short 默认禁止 live 开仓。

普通 short 放行条件必须满足：

```text
score <= -70
AND htf_bearish_votes >= 2
AND tf_15m_confirm_short = true
AND effective_rr >= 1.8
AND expected_value >= 0
```

注意：此处不要求 daily 必须 bearish，因为 daily 太滞后；但如果 daily 仍 bullish，普通 short 必须进一步降级为 `probe_short` 或 shadow。

被 guard 拦截的 short：

- 不进入 live。
- 写 CounterfactualLedger。
- 可进入 PaperExecutor 或 shadow tracker。
- attribution 中写入 `blocked_by=short_regime_guard`。

### RQ-REG-04：Probe Short 早期反转通道

为避免 bullish regime 中完全错过顶部反转，系统必须保留小仓试探 short 通道。

`probe_short` 触发条件：

必须满足以下市场级条件之一：

```text
1. BTC 或 ETH 4h RSI 从 >=75 下穿 70，且当前 4h 为放量阴线；
2. active_symbols 中 60% 以上 15m bias 从 bullish 转 neutral/bearish；
3. 市场 TopN 24h breadth 仍 bullish，但最近 2h breadth 明显恶化；
4. 资金费率极端偏多 + crowd 极端拥挤 + 15m 跌破关键均线。
```

同时满足标的级条件：

```text
score <= -50
tf_15m_confirm_short = true
effective_rr >= 1.3
liquidity_score > 0
```

执行限制：

- 保证金 = `MAX_TRADE_AMOUNT * 0.30`。
- 杠杆上限 = 3x。
- 同一时间最多 1 个 `probe_short`。
- `probe_short` 不得加仓。
- 最长持仓 2 小时，未达到 0.5R 且 15m 不继续走弱则退出。
- probe 亏损 2 次后，bullish regime 中 probe short 冷却 24 小时。

### RQ-REG-05：低 R:R Long 槽位分层

低 R:R long 不得与主策略槽位完全等价竞争。

定义：

```text
low_rr_long = action=open_long
              AND effective_regime=bullish
              AND effective_rr >= RR_FLOOR_LONG_BULLISH
              AND effective_rr < 1.5
```

slot 规则：

- 主槽位仍由 `MAX_CONCURRENT_POSITIONS` 控制，默认 3。
- 低 R:R long 使用 `LOW_RR_EXTRA_SLOT=1` 的附加观察槽。
- 附加槽只允许 long，不允许 short。
- 如果主槽位未满，低 R:R long 仍要经过 Ranking；如果主槽位已满，只能使用附加槽。
- 高 R:R 候选永远优先于低 R:R 候选。

Ranking 修改：

- `effective_rr >= 1.5` 的候选正常排名。
- `1.3 <= effective_rr < 1.5` 的候选打 `low_rr_penalty`。
- `rank_score` 不得因为 EV 单项过高而超过高 R:R + 高 HTF 共振候选。

仓位限制：

- Phase 1 不做分批止盈。
- 低 R:R long 保证金上限 = `MAX_TRADE_AMOUNT * 0.5`。
- 杠杆上限 = 5x 或 10x，首版建议 5x。
- 一旦 15m 转反向或 1h setup 失效，PositionAnalyst 可提前退出。

### RQ-REG-06：CounterfactualLedger 边界

CounterfactualLedger 只解决一个问题：**被 Judge 质量门、R:R、EV、short guard 拒绝但已经形成 plan 的信号，后续如果假设入场会怎样。**

必须记录：

- `rejected_plan_created`
- `shadow_tp`
- `shadow_sl`
- `shadow_expired`
- `shadow_invalidated`

不要求记录所有 hold 标的，也不要求诊断研判层漏选。文档和报表必须明确声明：

```text
CounterfactualLedger only measures rejected planned signals.
It does not estimate the opportunity cost of symbols never planned by Judge.
```

禁止行为：

- 禁止直接用 18 小时 ledger 结果自动改 live 参数。
- 禁止把 shadow 胜率当作真实成交胜率。
- 禁止用 ledger 结果绕过 data_quality、15m、熔断和执行风控。

### RQ-REG-07：Phase 1 不做分批止盈

Phase 1 中低 R:R 入场只通过仓位缩放和杠杆上限控制风险。

不新增：

- 价格触发 TP1 监控循环。
- TP1 后自动取消并重设 OKX SL 条件单。
- 多级 TP 条件单矩阵。

如果已有 PositionAnalyst 触发减仓，可以继续使用现有 reduce_position 路径，但不能把它包装成价格触发分批止盈。

## 5. 数据与消息契约

### 5.1 Regime Snapshot

发布或附加到 `tech_analysis` / `trade_decision`：

```json
{
  "effective_regime": "bullish",
  "raw_regime": "mixed",
  "confidence": 72,
  "hysteresis_state": {
    "candidate_regime": "mixed",
    "candidate_count": 1,
    "min_hold_remaining_sec": 900
  }
}
```

### 5.2 Trade Decision Attribution

新增字段：

```json
{
  "entry_regime": "bullish",
  "raw_regime": "mixed",
  "regime_confidence": 72,
  "rr_policy": "long_bullish_low_rr",
  "slot_type": "main|low_rr_extra|probe_short",
  "blocked_by": "short_regime_guard",
  "is_probe": false,
  "is_low_rr": true
}
```

### 5.3 Rejected Signal Event

```json
{
  "event_type": "rejected_plan_created",
  "symbol": "ONDO-USDT",
  "side": "long",
  "effective_regime": "bullish",
  "score": 53,
  "confidence": 40,
  "effective_rr": 1.35,
  "reject_reason": "confidence<60",
  "entry_price": 0.3811,
  "stop_loss": 0.3611,
  "take_profit": [0.4111],
  "created_at": 1770000000
}
```

## 6. 非功能需求

### 6.1 可解释性

每一笔被放行、被拒绝、被降级为 probe、进入 low_rr_extra_slot 的信号，都必须能解释：

- 使用哪个 effective regime。
- raw regime 是否不同。
- 触发了哪条 R:R policy。
- 是否被 hysteresis 影响。
- 是否占用主 slot。
- 是否进入 CounterfactualLedger。

### 6.2 可回滚

所有新增行为必须可通过环境变量关闭：

```dotenv
REGIME_HYSTERESIS_ENABLED=false
SHORT_REGIME_GUARD_ENABLED=false
PROBE_SHORT_ENABLED=false
LOW_RR_SLOT_ENABLED=false
COUNTERFACTUAL_LEDGER_ENABLED=false
```

关闭后系统行为应回到现有 Judge 规则。

### 6.3 安全性

- 熔断、对账失败、data_quality degraded、15m required failure 仍是硬闸。
- probe_short 不得绕过风险预算。
- low_rr_extra_slot 不得突破 Daily Hard Stop。
- LLM 不得直接改变 regime。

## 7. 阶段计划

### Phase 1A：观察层

- 实现 Regime hysteresis，但只记录不改变交易。
- 实现 CounterfactualLedger。
- 输出 rejected plan shadow 报表。

### Phase 1B：Short Guard

- 启用 `SHORT_REGIME_GUARD_ENABLED=true`。
- bullish regime 下普通 short 进入 shadow。
- 启用 probe_short 小仓通道，但默认 paper/testnet 验证。

### Phase 1C：低 R:R Long 小幅放行

- 启用 `RR_FLOOR_LONG_BULLISH=1.30`。
- 启用 low_rr_extra_slot。
- 低 R:R long 保证金 <= 50%，杠杆 <= 5x。

每个阶段至少运行 48 小时 paper/testnet，且不得同时改变两个以上 live 交易参数。

## 8. 风险与缓解

| 风险 | 缓解 |
---|---|
| Regime 抖动 | hysteresis、最小停留期、entry_regime 固化 |
| 参数过拟合 | Phase 1 只动两个 live 参数；禁止大网格直接上线 |
| Short 错过反转 | probe_short 小仓早期反转通道 |
| Ledger 幸存者偏差 | 文档声明范围；不用于上游选币诊断 |
| 低 R:R 收益小占槽 | low_rr_extra_slot + Ranking penalty |
| 分批止盈复杂 | Phase 1 不做价格触发分批止盈 |
| 系统难调试 | 每个新分支必须 attribution 可解释 |

## 9. 上线门槛

Phase 1C 进入小额 live 前必须满足：

- Phase 1A shadow ledger 连续记录不少于 48 小时。
- Phase 1B paper/testnet 中 short guard 未导致重大漏风控。
- low R:R long 在 paper/testnet 中至少 20 个 shadow/live 样本，PF >= 1.2。
- probe_short 样本若少于 10，不得扩大仓位。
- 默认全量回归通过。
- 文档与 `.env.example`、runbook 同步。

