# OKX PosMode 执行兼容需求文档

更新日期：2026-05-25  
状态：P1，阻断 live 扩容  
关联验收：`docs/okx_posmode_execution_acceptance.md`

## 1. 背景

2026-05-25 实盘日志显示，NEAR-USDT-SWAP 在 `partial_tp_1`、本地兜底止盈/止损、平仓路径中连续出现 OKX 拒单：

- `51169`: Order failed because you don't have any positions in this direction for this contract to reduce or close.
- `51205`: Reduce Only is not available.

本地代码现状：

- `executor.py` 在开仓后独立 SL、`close_position()`、`place_stop_loss_order()`、`reduce_position()` 中直接硬编码 `reduceOnly=True`。
- `executor.py` 没有启动时读取 OKX `posMode`，也没有统一构造 `posSide`。
- 当前 `sync_positions()` 依赖 CCXT 统一字段，未把 OKX 原始 `posSide`、`pos`、`availPos` 作为执行参数依据持久化。

OKX 官方语义要点：

- `GET /api/v5/account/config` 返回当前账户配置，响应包含 `posMode`。
- `POST /api/v5/account/set-position-mode` 可设置 `posMode`，取值为 `long_short_mode` 或 `net_mode`。
- `POST /api/v5/trade/order` 的 `posSide` 在 net mode 默认是 `net`，在 long/short mode 必须传 `long` 或 `short`。
- `reduceOnly` 只适用于 margin 订单，以及 FUTURES/SWAP 在 net mode 下的订单；long/short mode 的合约平仓单天然具备 reduce-only 语义。
- `POST /api/v5/trade/order-algo` 用于条件单、OCO、trigger、trailing stop，不能沿用普通市价平仓单的 `reduceOnly` 参数假设。

官方参考：

- OKX Account Config: https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-account-configuration
- OKX Set Position Mode: https://app.okx.com/docs-v5/en/#trading-account-rest-api-set-position-mode
- OKX Place Order: https://app.okx.com/docs-v5/en/#order-book-trading-trade-post-place-order
- OKX Place Algo Order: https://app.okx.com/docs-v5/en/#order-book-trading-algo-trading-post-place-algo-order
- OKX Error Code: https://app.okx.com/docs-v5/en/#error-code-rest-api

## 2. 目标

1. 执行层启动时识别 OKX 当前 `posMode`，并以该模式构造所有开仓、加仓、减仓、平仓、保护单参数。
2. 消除业务路径中散落的 `reduceOnly`、`posSide` 手写逻辑。
3. 在减仓/平仓前以交易所真实仓位为准，限制数量不超过可平仓位，避免超量拒单或反向开仓。
4. 51169/51205 等拒单后必须做交易所状态复核，不能直接把本地仓位删除。
5. 输出完整 testnet 验收证据，证明 net mode 与 long/short mode 至少一个生产目标模式可安全运行。

## 3. 非目标

- 不在主交易循环中自动切换 OKX 账户持仓模式。
- 不把所有交易所抽象成统一持仓模式。本需求只覆盖 OKX SWAP/FUTURES。
- 不扩大 live 额度；本需求完成前 live 扩容仍为 NO-GO。

## 4. 技术路线

### 4.1 账户模式探测

在 `ContractExecutor` 初始化阶段增加 OKX 专用探测：

```python
raw = self.exchange.private_get_account_config()
self._okx_pos_mode = raw["data"][0]["posMode"]
```

要求：

- 仅 `exchange_id == "okx"` 时启用。
- 允许值只接受 `net_mode`、`long_short_mode`。
- live 环境读取失败必须 fail closed：禁止开新仓，只允许进入人工处理或只读状态。
- testnet/paper 可降级为配置指定值，但日志必须明确标记不是交易所真实返回。

### 4.2 交易所仓位模型

新增内部结构 `OKXPositionState`，由 `fetch_positions()` 归一化：

| 字段 | 来源 | 用途 |
|---|---|---|
| `symbol` | unified symbol / `instId` | 本地持仓 key |
| `side` | CCXT `side` 或 OKX `pos` 符号 | 内部方向：`long` / `short` |
| `pos_side` | OKX `info.posSide` | OKX 下单 `posSide` |
| `contracts` | `contracts` / `info.pos` abs | 当前总仓位 |
| `available_contracts` | `info.availPos` 优先，否则 `contracts` | 最大可平数量 |
| `entry_price` | `entryPrice` / `info.avgPx` | PnL 和风控 |
| `leverage` | `leverage` / `info.lever` | PnL 和限额 |

### 4.3 参数构造器

新增单一入口，业务代码禁止直接写 `reduceOnly` / `posSide`：

```python
_build_okx_open_params(side, clord_id=None, attach_algo=None) -> dict
_build_okx_close_params(position, amount, purpose) -> dict
_build_okx_algo_params(position, trigger, purpose) -> dict
```

参数策略：

| 场景 | `net_mode` | `long_short_mode` |
|---|---|---|
| 开仓 long | `side=buy`, `posSide=net`, 不传 `reduceOnly` | `side=buy`, `posSide=long`, 不传 `reduceOnly` |
| 开仓 short | `side=sell`, `posSide=net`, 不传 `reduceOnly` | `side=sell`, `posSide=short`, 不传 `reduceOnly` |
| 加仓 | 同开仓方向 | 同开仓方向 |
| 减 long / 平 long | `side=sell`, `posSide=net`, `reduceOnly=True`, `amount<=available_contracts` | `side=sell`, `posSide=long`, 不传 `reduceOnly`, `amount<=available_contracts` |
| 减 short / 平 short | `side=buy`, `posSide=net`, `reduceOnly=True`, `amount<=available_contracts` | `side=buy`, `posSide=short`, 不传 `reduceOnly`, `amount<=available_contracts` |
| 独立 SL/TP algo | `side` 为反向，`posSide=net`，不传 `reduceOnly` | `side` 为反向，`posSide` 为被保护仓位方向，不传 `reduceOnly` |
| attached TP/SL | `attachAlgoOrds` 跟随开仓单，不传 `reduceOnly` | `attachAlgoOrds` 跟随开仓单，不传 `reduceOnly` |

说明：

- `reduceOnly=False` 没有业务价值，默认不传，降低交易所参数歧义。
- net mode 平仓保留 `reduceOnly=True`，因为 OKX 官方说明该参数适用于 FUTURES/SWAP net mode。
- long/short mode 平仓不依赖 `reduceOnly`，只依赖正确的 `posSide`。
- 如果 testnet 证明 OKX/CCXT 某组合存在差异，以验收报告记录的 raw request/response 为准更新本表。

### 4.4 拒单与状态复核

对 51169、51205、51112、51333 等执行错误新增统一处理：

1. 记录 raw request、raw response、symbol、local position、exchange position。
2. 立即调用 `fetch_positions()` 和 open algo/order 查询。
3. 如果交易所已无仓位：清理本地持仓，输出 `external_closed` 或 `already_flat`。
4. 如果交易所仍有仓位：不得删除本地持仓；标记 symbol execution halt，通知 RiskGuard/Telegram。
5. 如果本地和交易所方向冲突：以交易所方向为准，但禁止继续自动减仓/平仓，等待人工确认或专门恢复流程。

## 5. 接口参数清单

### 5.1 GET /api/v5/account/config

用途：启动时读取账户持仓模式。

请求：

```http
GET /api/v5/account/config
```

关键响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | string | `0` 表示成功 |
| `data[0].posMode` | string | `net_mode` / `long_short_mode` |
| `data[0].acctLv` | string | 账户模式等级，辅助日志 |
| `data[0].perm` | string | API key 权限，必须包含 trade 才能下单 |

### 5.2 POST /api/v5/account/set-position-mode

用途：人工应急切换账户模式。主程序不得自动调用。

请求：

```json
{
  "posMode": "long_short_mode"
}
```

约束：

- 切换前必须无持仓、无普通挂单、无 algo 条件单。
- 切换后必须重新读取 `GET /api/v5/account/config` 确认。
- 切换后必须执行最小 size smoke test。

### 5.3 POST /api/v5/trade/order

用途：普通开仓、加仓、减仓、平仓。

核心参数：

| 参数 | 必填 | 本项目策略 |
|---|---|---|
| `instId` | 是 | 由 CCXT symbol 转 OKX instrument，如 `NEAR-USDT-SWAP` |
| `tdMode` | 是 | 由 CCXT default margin mode 或显式配置生成，默认 `cross` |
| `side` | 是 | `buy` / `sell` |
| `posSide` | 条件 | net mode 传 `net`；long/short mode 必传 `long` / `short` |
| `ordType` | 是 | `market` / `limit` |
| `sz` | 是 | 合约张数，必须按 `amount_to_precision()` 处理 |
| `px` | 条件 | limit 类订单必传 |
| `clOrdId` | 否 | 所有开仓、加仓、减仓、平仓都应传，便于幂等追踪 |
| `reduceOnly` | 否 | 只在 net mode 减仓/平仓传 `true` |
| `attachAlgoOrds` | 否 | 开仓附带 TP/SL 使用；不能和 reduce-only close 混用 |

### 5.4 POST /api/v5/trade/order-algo

用途：独立止损、移动止损、独立 TP/SL。

核心参数：

| 参数 | 必填 | 本项目策略 |
|---|---|---|
| `instId` | 是 | OKX instrument |
| `tdMode` | 是 | 与普通订单一致 |
| `side` | 是 | 被保护仓位的反向 |
| `posSide` | 条件 | net mode 传 `net`；long/short mode 传被保护仓位方向 |
| `ordType` | 是 | `conditional` / `trigger` / `move_order_stop` |
| `sz` | 条件 | 独立 SL/TP 传保护数量；如使用 `closeFraction` 需按 OKX 限制验证 |
| `slTriggerPx` | 条件 | 止损触发价 |
| `slOrdPx` | 条件 | `-1` 表示触发后市价 |
| `tpTriggerPx` | 条件 | 止盈触发价 |
| `tpOrdPx` | 条件 | `-1` 表示触发后市价 |
| `callbackRatio` / `callbackSpread` | 条件 | trailing stop 二选一 |

禁止：

- 独立 algo 保护单不得复用普通 close 的 `reduceOnly=True`。
- close/reduce 订单不得附加 TP/SL。

## 6. 影响范围

| 文件 | 影响 |
|---|---|
| `executor.py` | 增加 OKX posMode 探测、参数构造器、拒单复核 |
| `verify_okx_testnet_semantics.py` | 从 mock reduceOnly case 扩展为 posMode matrix |
| `test_executor_upgrade.py` | 增加参数构造单测 |
| `test_lifecycle_pnl.py` | 验证拒单后不错误删除本地仓位 |
| `docs/generated_reports/OKX执行语义testnet验收报告_20260522.md` | 更新真实 testnet raw response 和结论 |

## 7. Go/No-Go

Go 条件：

- 本需求的自动化测试通过。
- `docs/okx_posmode_execution_acceptance.md` 的 testnet 必测项通过。
- 日志中不再出现同一路径重复 51169/51205 而无状态复核。
- `docs/to-do-list.md` 中 OKX posMode 项关闭或降级为非阻断项。

No-Go 条件：

- 无法读取 live 账户 `posMode`。
- 仍存在业务代码直接硬编码 close/reduce 的 `reduceOnly` / `posSide`。
- testnet 未覆盖当前生产目标账户模式。
- 出现 51169/51205 后本地仓位被直接删除但交易所仍有仓位。
