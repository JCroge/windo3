# Live 准入产品需求文档

更新日期：2026-05-22  
关联待办：`docs/to-do-list.md`

## 1. 背景

第三次系统审计后，核心 open 决策链路已通过回归测试，`request_id`、probe slot gate、ranking logger、bucket EV 等问题已关闭。代码更新后，非 open `execution_result` 契约也已完成 helper 化和定向测试。当前阻断 live 扩容的剩余问题集中在两类：

1. OKX 真实 testnet 执行语义尚未验收。
2. Phase 2 解决“过保守不开仓”的能力尚未接入运行配置，paper/testnet 直接启动时仍可能未启用 confidence split 和 momentum probe long。

本 PRD 目标不是优化策略收益，而是把系统从“本地/paper/mock 可验证”推进到“允许评审小额 live 灰度”的工程准入状态。

## 2. 产品目标

- 保持所有执行结果事件具备统一可追踪契约，Reviewer、RiskGuard、运维日志可以稳定还原一次交易生命周期。
- OKX testnet 真实验证覆盖关键下单语义，避免 mock 通过但真实交易所参数、错误码、条件单行为不一致。
- Phase 2 开仓解冻能力可通过配置明确启用，避免 LLM `hold` 将强规则信号压到 60 以下，避免 RSI 70-85 强趋势只进入等待回调。
- 建立清晰 live 准入门槛：testnet 未通过或执行事件不可追踪时，不允许 live 扩容。

## 3. 非目标

- 不调整交易策略、评分、LLM 提示词、仓位算法和风控阈值。
- 不新增交易所。
- 不做前端或 Telegram 功能扩展。
- 不以 mock/paper 结果替代真实 testnet 验收。

## 4. 用户与使用场景

| 角色 | 诉求 |
|---|---|
| 开发者 | 清楚知道哪些执行分支需要迁移到统一契约，如何写测试 |
| 运维者 | 能从日志和报告判断是否可以进入 live 灰度 |
| Reviewer/RiskGuard | 能稳定消费 `execution_result`，不因分支差异丢失 source、request/correlation ID |
| 项目负责人 | 有明确 Go/No-Go 标准，避免凭感觉扩容 |

## 5. 功能需求

### FR-01 统一 execution_result 发布契约

状态：已实现，后续作为回归约束保留。

在 `agents/trading/executor.py` 引入统一发布 helper，所有 `execution_result` 事件至少包含：

```json
{
  "schema_version": "execution_result.v2",
  "status": "executed | rejected | force_closed | risk_reduced | closed_externally | expired",
  "action": "open_long | open_short | close | reduce",
  "symbol": "BTC-USDT-SWAP",
  "source": "executor_open | risk_alert | close_all | sync | external_close | local_stop | partial_tp | okx_testnet",
  "request_id": "来自 trade_decision 或 position，可为空字符串但不得缺字段",
  "correlation_id": "无 request_id 时生成，用于串联非决策触发事件",
  "reason": "触发原因",
  "result": {},
  "timestamp": 1770000000.0
}
```

要求：

- 不删除旧消费者依赖的字段。
- open 主路径保持现有行为。
- 非 open 路径新增 `source` 和 `correlation_id`，并尽量从 position 透传 `entry_request_id`。

### FR-02 迁移非 open 执行分支

状态：已实现，后续作为回归约束保留。

必须迁移以下分支：

| 分支 | 当前风险 | 目标 source |
|---|---|---|
| `emergency_close` / `flash_move` / `position_danger` 等风控强平 | payload 缺 schema 和追踪 ID | `risk_alert` |
| `_close_all_positions()` 全平 | 多标的并发强平难串联 | `close_all` |
| `_notify_synced_positions()` 同步发现新持仓 | 无 request/correlation ID | `sync` |
| `_notify_removed_positions()` 外部 SL/TP 平仓 | 外部成交难与 entry 对齐 | `external_close` |
| `_check_all_positions()` 本地兜底止损/价格失败 | 兜底事件格式漂移 | `local_stop` |
| partial TP reduce | reduce 事件缺统一契约 | `partial_tp` |

### FR-03 Reviewer 兼容与追踪

Reviewer 应继续兼容旧 payload，同时优先读取：

- `result.entry_request_id`
- `result.exit_request_id`
- 顶层 `request_id`
- 顶层 `correlation_id`
- 顶层 `source`

验收时必须证明 Reviewer 不会因为新增字段或旧字段缺失而丢记录。

### FR-04 OKX testnet 真实验收

基于 `verify_okx_testnet_semantics.py` 的 8 个 case，增加或配置真实 OKX testnet 执行模式。每个 case 必须记录：

- raw response 摘要
- normalized `execution_result`
- final position/order/algo order state
- 是否通过
- 失败原因和是否阻断 live

8 个 case：

1. market open + attached TP/SL
2. limit open timeout
3. insufficient balance
4. min amount
5. posMode-aware close/reduce
6. move SL
7. close 后条件单状态
8. duplicate clOrdId / idempotency

### FR-05 Phase 2 开仓解冻配置闭环

在 `utils/config_loader.py` 中补齐以下默认值和环境变量映射：

| 配置 key | 环境变量 | 建议默认 |
|---|---|---|
| `phase2_signal_confidence_split_enabled` | `PHASE2_SIGNAL_CONFIDENCE_SPLIT_ENABLED` | `true`（paper/testnet），live 灰度前必须显式确认 |
| `phase2_momentum_probe_long_enabled` | `PHASE2_MOMENTUM_PROBE_LONG_ENABLED` | `true`（paper/testnet），live 灰度前必须显式确认 |
| `phase2_trend_saturation_enabled` | `PHASE2_TREND_SATURATION_ENABLED` | `true` |
| `phase2_bucketed_ev_enabled` | `PHASE2_BUCKETED_EV_ENABLED` | `true` |

要求：

- `load_config(strict_live_check=False)` 必须返回四个 key，不能是 `<missing>`。
- 启动 banner 必须展示 Phase 2 开关状态。
- `docs/runbook.md` 必须说明 paper/testnet 建议启用的开关组合。
- 不降低 `MIN_CONFIDENCE=60`，通过 `execution_confidence` 和 `position_scale` 分离解决过保守问题。
- `probe_long` 仍必须受最大并发、流动性、HTF bullish、无 bearish divergence 约束。

### FR-06 live 准入门控

live 扩容评审必须同时满足：

- 全量测试通过。
- OKX mock 8 case 通过。
- OKX 真实 testnet 8 case 通过或有明确非阻断解释。
- 所有 `execution_result` 发布点使用统一契约。
- Phase 2 四个运行配置 key 存在，paper/testnet 观察中确认不再出现“配置缺失导致功能关闭”。
- `docs/to-do-list.md` 中除 OKX testnet 外无 P1 BLOCKED。

## 6. 非功能需求

- 安全：testnet/sandbox key 与 production key 不得混用，不得写入仓库。
- 可观测：所有执行事件必须有 `source`，无自然 `request_id` 时必须有 `correlation_id`；启动日志必须展示 Phase 2 开关状态。
- 兼容：新增字段不得破坏 Reviewer、RiskGuard、Telegram、测试用例。
- 可回归：新增测试应可在无真实交易所凭证时跑完；真实 testnet 验收可单独手动执行。

## 7. 开发路径

1. 契约设计：定义 helper 参数、默认字段、source 枚举和 correlation ID 生成规则。
2. 单测先行：新增非 open 分支契约测试，锁定字段要求和 Reviewer 兼容行为。
3. Phase 2 配置：补默认值、环境变量映射、banner、runbook 和配置单测。
4. OKX testnet：给验收脚本增加真实 testnet 模式或单独 testnet runner，确保 sandbox/testnet 在 exchange 初始化后立即启用。
5. 回归与文档：跑全量测试、mock 验收、真实 testnet 验收，并更新 `docs/to-do-list.md` 与 testnet 报告。

## 8. 交付物

- 统一 `execution_result` helper 及迁移后的调用点。
- 非 open 分支契约测试。
- Reviewer 兼容测试。
- Phase 2 配置加载和 banner 测试。
- OKX testnet 验收记录。
- 更新后的 `docs/to-do-list.md`。
