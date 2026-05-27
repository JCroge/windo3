# 分批止盈生命周期收敛产品需求文档

更新日期：2026-05-27  
状态：CLOSED（阶段 1+2+3 完成 + testnet T0-T9 PASS，2026-05-27）  
关联验收：`docs/partial_tp_lifecycle_acceptance.md`、`docs/generated_reports/OKX执行语义testnet验收报告_20260527_150518.md`  
关联问题：OKX attached TP 与本地 partial TP 双 owner、TP1 后本地兜底全平、减仓后保护单生命周期不一致

## 1. 背景

当前执行层同时存在两套止盈 owner：

1. 开仓时通过 OKX `attachAlgoOrds` 附带第一档 TP。
2. 本地 `check_stop_loss_take_profit()` / `_update_trailing()` 在价格到达 `take_profit_levels[0]` 时返回 `partial_tp_1`，外层 agent 调用 `reduce_position(symbol, 0.5)`。

这会导致 TP1 同价位发生竞态：OKX 侧 attached TP 可能按交易所默认语义平掉全部剩余仓位，本地 partial TP 又会尝试减仓 50%。谁先执行取决于本地轮询周期、OKX 撮合速度和网络延迟。

复核代码后还发现两个独立并发症：

- `position["take_profit"]` 开仓时等于 TP1。`partial_tp_1` 成功后，下一轮本地检查仍可能命中常规 `take_profit`，触发 `close_position()` 全平。
- `reduce_position()` 减仓前会撤旧 `sl_order_id`，但减仓后没有按剩余仓位重挂保护 SL；attached SL 路径又没有保存可撤改的 algo id。

该问题对做多/做空都成立，只是价格比较方向相反。

## 2. 产品目标

1. 每个仓位同一时间只有一个止盈 owner，避免交易所 TP 与本地 partial TP 重复平仓。
2. partial TP 状态机对 long/short 对称，TP1/TP2/TP3 行为明确且可测试。
3. 交易所侧只保留可追踪、可撤改、可重建的保护 SL。
4. 减仓、平仓、移动 SL、RiskGuard close、local stop 等退出动作按 symbol 串行执行。
5. 任何执行失败不得让本地状态提前进入“已减仓/已保护”。
6. 重启、同步、拒单和外部触发后可以判断是否存在未追踪的 OKX algo 残留。

## 3. 非目标

- 不在本阶段优化止盈比例、R 倍数、策略收益或 Judge 产出的 TP 档位。
- 不把 OKX split TP 作为首选实现。
- 不自动切换 OKX `posMode`。
- 不把 mock 结果等同于 OKX testnet/live 语义。
- 不重构整个执行器；本阶段只收敛退出生命周期和保护单 owner。

## 4. 决策

采用“本地 partial TP + 交易所可追踪保护 SL”的单一 owner 方案。

| 生命周期对象 | Owner | 说明 |
|---|---|---|
| TP1/TP2 分批减仓 | 本地 executor | 本地轮询触发，调用 reduce order |
| TP3 或尾仓退出 | 本地 executor | 可配置为最终全平或 trailing 管理 |
| 移动止损 | 本地 executor 决策，交易所执行保护单 | 本地计算新 SL，交易所 algo 只负责触发 |
| 交易所 TP algo | 禁用 | OKX 开仓不得再 attach TP |
| 交易所 SL algo | 允许，但必须可追踪 | 必须保存 `sl_algo_id` 或可用 client id 查询恢复 |

不选择 OKX `closeFraction` 作为主路径，原因：

- `closeFraction` / split TP 字段约束和 OKX 版本语义容易漂移，必须由 testnet 再确认。
- 本项目 TP2、TP3、trailing、RiskGuard 都已经是本地状态机，继续让交易所主管 TP1 会形成混合 owner。
- attached TP 在本地轮询命中同一价位时天然存在竞态，事后撤单无法消除。

## 5. 功能需求

### FR-01 禁止 OKX 开仓附带 TP

OKX 开仓参数中不得生成 `tpTriggerPx` / `tpOrdPx`。

允许两种 SL 实现，优先级如下：

1. 首选：开仓 `attachAlgoOrds` 只附带 SL，并提供 `attachAlgoClOrdId`。成交后立即解析或查询并保存真实 `sl_algo_id`。
2. 备选：开仓成交后立即下独立 SL algo，并保存 `sl_algo_id`。

如果 live 环境中无法确认 SL algo id：

- 仓位必须标记 `protection_state=unknown`。
- 禁止继续 partial TP、加仓、自动移动 SL。
- 触发 symbol halt 和告警。
- 如 SL 完全未挂成功，必须尝试立即关闭新仓；关闭失败时进入人工接管状态。

### FR-02 统一持仓保护字段

`position` 字典需要新增或规范以下字段：

| 字段 | 说明 |
|---|---|
| `exit_owner` | 固定为 `local_partial_tp_exchange_sl`，用于后续兼容判断 |
| `take_profit_levels` | Judge 输出的完整 TP 列表 |
| `take_profit` | legacy scalar，仅无 `take_profit_levels` 时用于全平 TP |
| `tp_filled` | 已完成的 partial TP 档位，只有减仓订单成功后递增 |
| `tp_pending_action_id` | 正在执行的 TP 动作幂等键，防重复 |
| `sl_algo_id` | OKX 独立或 attached SL 的真实 algo id |
| `sl_algo_clord_id` | 可用于恢复查询的 client algo id |
| `sl_sync_state` | `active` / `pending` / `unknown` / `failed` |
| `protection_state` | `protected` / `local_fallback` / `unprotected` / `halted` |
| `last_exit_action_id` | 最近一次 reduce/close 的 client id |

`sl_order_id` 可继续保留兼容旧路径，但 OKX 新路径应迁移到 `sl_algo_id`。

### FR-03 修正本地 TP 状态机

当 `take_profit_levels` 存在且 `partial_tp_enabled=True`：

- TP1：价格到达 `take_profit_levels[0]`，减仓 50%。
- TP2：价格到达 `take_profit_levels[1]`，再减仓 25%。
- TP3：若存在 `take_profit_levels[2]`，可配置为全平剩余仓位；默认允许尾仓交给 trailing。
- TP1/TP2 成功后不得再因为 `position["take_profit"] == TP1` 触发常规全平。
- `tp_filled` 只能在 reduce order 成功、交易所仓位复核通过后更新。
- long 使用 `price >= tp_level`，short 使用 `price <= tp_level`。

当没有 `take_profit_levels` 时，保留 legacy scalar `take_profit` 全平逻辑。

### FR-04 partial TP 原子执行

新增内部方法，例如：

```python
execute_partial_take_profit(symbol, level, pct) -> dict | None
```

要求：

1. 获取 symbol exit lock。
2. 读取交易所真实仓位，确认方向和可平数量。
3. 计算 reduce amount，并按交易所精度处理。
4. 提交 reduce order。
5. 复核成交或最终仓位。
6. 成功后更新 `amount`、`amount_usdt`、`tp_filled`、`stop_loss`。
7. 更新交易所 SL 到剩余仓位和新触发价。
8. 保存本地仓位。
9. 发布 `execution_result.v2`。

失败处理：

- reduce 失败：不得更新 `tp_filled`，不得移动本地 SL。
- reduce 成功但 SL 更新失败：标记 `protection_state=local_fallback` 或 `halted`，并告警；live 可配置为立即关闭剩余仓位。
- 交易所返回 already flat：走外部平仓复核，不重复下单。

### FR-05 减仓后保护 SL 生命周期

减仓后必须确保剩余仓位有唯一有效 SL：

优先级：

1. 使用 OKX amend algo 原地修改 `sl_algo_id` 的触发价和数量。
2. 若 amend 不支持或失败，执行 cancel + recreate。
3. cancel + recreate 失败时，立即进入保护失败流程。

保护失败流程：

- `protection_state=unprotected` 或 `halted`。
- 发送高优先级告警。
- 暂停该 symbol 自动加仓、减仓、移动 SL。
- live 环境默认尝试关闭剩余仓位；关闭失败则人工接管。

不得在 reduce order 提交前无条件取消旧 SL。旧 SL 至少应保护到 reduce 成功确认之后，除非 testnet 证明该旧 SL 会在减仓瞬间造成更高风险，并有替代保护。

### FR-06 串行化所有退出动作

对每个 symbol 增加 exit lock，覆盖：

- `close_position()`
- `reduce_position()`
- `execute_partial_take_profit()`
- 本地 stop_loss / take_profit 全平
- RiskGuard 强平或减仓
- PositionAnalyst 调仓
- `price_fetch_failed` 强平
- 外部同步发现仓位已消失时的本地清理

同一 symbol 在 lock 持有期间收到新退出请求：

- 相同 action id：返回 idempotent。
- 不同 action：拒绝或排队，默认拒绝并记录 `exit_locked`。

### FR-07 重启和存量仓位迁移

启动或 `sync_positions()` 时必须处理存量 OKX algo：

1. 查询目标 symbol 的 pending algo orders。
2. 如果发现 TP algo 且本地 `exit_owner` 为 local partial TP，必须取消该 TP algo。
3. 如果存在未知 SL algo，尝试归属到本地仓位；无法归属时 symbol halt。
4. 如果本地有仓位但没有交易所 SL，按 live/testnet 策略补挂或 halt。
5. 如果交易所无仓位但有可执行 algo，取消残留 algo。

迁移期间不得开新仓。

### FR-08 可观测与审计

所有 partial TP、SL 更新、algo 迁移、保护失败事件必须记录：

- `symbol`
- `side`
- `tp_level`
- `tp_filled_before` / `tp_filled_after`
- `requested_pct`
- `requested_amount`
- `filled_amount`
- `remaining_amount`
- `sl_algo_id`
- `sl_sync_state`
- `protection_state`
- `entry_request_id`
- `exit_action_id`
- raw response 摘要

`execution_result.v2.source` 使用：

- `partial_tp`
- `local_stop`
- `risk_alert`
- `protective_sl_update`
- `algo_migration`
- `external_close`

## 6. 并发症、后果与缓解

| 并发症 | 直接后果 | 严重后果 | 缓解 |
|---|---|---|---|
| OKX attached TP 与本地 partial TP 同价触发 | TP1 后仓位被多平 | 剩余仓位被全吃，ledger/PnL 错乱，后续 close 拒单 | 禁用交易所 TP，TP 只由本地 owner 管理 |
| `position["take_profit"]` 停留在 TP1 | TP1 后下一轮本地全平 | partial TP 失效，误以为策略止盈正确 | 有 TP levels 时跳过 legacy scalar TP |
| `tp_filled` 在 reduce 前更新 | reduce 失败但本地进入 TP1 后状态 | trailing/SL 错位，真实仓位未减但保护按半仓计算 | reduce 成功和复核后再更新状态 |
| 减仓前撤 SL，减仓失败 | 仓位短时间无交易所保护 | 快速反向行情造成裸奔损失 | reduce 前保留旧 SL，成功后 amend/recreate |
| 减仓成功后旧 SL 数量过大 | SL 触发时交易所拒单或行为不确定 | 以为有保护但实际无效 | 减仓后必须唯一有效 SL，testnet 验证超量 SL 行为 |
| SL recreate 失败 | 剩余仓位无保护 | live 裸奔 | halt + 告警 + 默认尝试关闭剩余仓位 |
| 无 exit lock | local stop、risk alert、partial TP 并发执行 | 重复平仓、反向开仓、51169/51205、状态误删 | symbol exit lock + action id 幂等 |
| 交易所仓位查询延迟 | 本地误判 already flat 或仍有仓位 | 删除真实持仓或重复下单 | 拒单后复核至少两次，状态不确定时 halt |
| amount 精度和最小量 | reduce amount 为 0 或剩余 dust | TP 状态推进但没有实际成交 | 精度后再判断，dust 走 full close 或跳过 |
| 重启丢失 algo id | 无法撤改 SL 或 TP 残留 | 未知 algo 后续触发 | 保存 algo id/client id，启动扫描 pending algo |
| short 比较方向错误 | 做空 TP/SL 触发条件反 | 错误平仓或不止损 | long/short 参数化测试锁定比较方向 |

## 7. 技术路径

### 阶段 1：热修止血

1. OKX `_build_okx_attach_algo()` 不再生成 TP 字段。
2. `check_stop_loss_take_profit()` 在 `take_profit_levels` 存在时禁用 legacy `take_profit` 全平检查。
3. `tp_filled` 由 reduce 成功后更新，不能由 `_update_trailing()` 预写。
4. 对 `partial_tp_1/2` 调用路径增加 symbol exit lock。

### 阶段 2：保护单 owner 收敛

1. 引入 `sl_algo_id` / `sl_algo_clord_id`。
2. 实现 OKX pending algo 查询、归属、取消、amend/recreate。
3. partial TP 成功后同步剩余仓位 SL。
4. close/full stop 后清理本地可追踪 algo。

### 阶段 3：存量迁移和 testnet 验收

1. 启动时扫描并取消本地 partial TP 仓位上的 OKX TP algo。
2. 对无法归属的 algo 进入 symbol halt。
3. 完成 long/short mock 单测。
4. 完成 OKX testnet 最小仓位验收。
5. 更新 runbook 和 live Go/No-Go 状态。

## 8. 留意事项

- 不要用“触发 partial TP 前撤 OKX TP”作为修复核心；触发前后本身就是竞态窗口。
- 不要在未保存 algo id 的情况下继续使用 attached TP/SL。
- 不要让 `_update_trailing()` 既做状态 mutation 又返回待执行动作；建议拆成“计算候选动作”和“执行成功后提交状态”。
- `amount_usdt *= (1 - pct)` 只是近似，partial reduce 后应按实际 `filled_amount / old_amount` 或 ledger 成交回写。
- OKX `cancel_order()` 与 algo cancel/amend 可能不是同一个 API 语义，必须 testnet 验证。
- close 后必须确认普通 open orders 和 algo orders 都无可执行残留。
- live 环境下任何 `protection_state != protected` 都应阻断新开仓和加仓。
- 文档中的 OKX 字段约束必须以执行当日官方文档和 testnet raw response 为准复核。

## 9. Go/No-Go

Go 条件：

- `docs/partial_tp_lifecycle_acceptance.md` 自动化和 OKX testnet 必测项通过。
- long/short TP1 后不会被本地 legacy TP 全平。
- OKX 新开仓无交易所 TP algo。
- partial TP 成功后剩余仓位有唯一有效 SL。
- exit lock 覆盖所有同 symbol reduce/close 路径。
- 启动迁移能发现并处理存量 TP algo。

No-Go 条件：

- 仍存在不可追踪 OKX TP algo。
- TP1 后下一轮轮询会返回 `take_profit` 全平。
- 减仓成功后 SL 更新失败但系统继续自动交易。
- 拒单后本地直接删除仍存在的交易所仓位。
