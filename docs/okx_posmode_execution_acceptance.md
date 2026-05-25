# OKX PosMode 执行兼容验收文档

更新日期：2026-05-25  
关联需求：`docs/okx_posmode_execution_prd.md`  
状态：待实现、待 testnet 验收

## 1. 验收目标

证明 OKX 执行层在真实账户 `posMode` 下可以安全完成：

- 开仓和 attached TP/SL。
- 独立 SL/TP algo。
- partial TP 减仓。
- RiskGuard / 本地兜底全平。
- 拒单后的交易所状态复核。
- close 后无危险残留条件单。

验收结论必须基于 raw request、raw response、normalized result、final position/order state，不能只基于 mock。

## 2. 前置条件

| 条件 | 标准 |
|---|---|
| API 环境 | OKX demo trading / testnet key，可读写 SWAP |
| 账户状态 | 测试前无目标 symbol 持仓、无普通挂单、无 algo 挂单 |
| 交易对 | 使用高流动性、最小下单成本低的 USDT-SWAP |
| 风险限额 | 单 case 名义本金不超过测试账户权益 1%，杠杆不超过 5x，除非专门验证杠杆 |
| 日志 | 保存 raw request、raw response、final positions、open orders、algo orders |
| 本地状态 | 测试前备份 `data/positions.json`、`data/trade_history.json` |

## 3. 自动化验收

### AC-A1 参数构造矩阵

新增或更新单测，覆盖：

| Case | 输入 | 期望 |
|---|---|---|
| net open long | `posMode=net_mode`, `side=long` | `side=buy`, `posSide=net`, 无 `reduceOnly` |
| net close long | long position | `side=sell`, `posSide=net`, `reduceOnly=True` |
| net reduce short | short position, pct=0.5 | `side=buy`, `posSide=net`, `reduceOnly=True`, amount 不超过可平 |
| long_short open long | `posMode=long_short_mode`, `side=long` | `side=buy`, `posSide=long`, 无 `reduceOnly` |
| long_short close long | long position | `side=sell`, `posSide=long`, 无 `reduceOnly` |
| long_short close short | short position | `side=buy`, `posSide=short`, 无 `reduceOnly` |
| standalone SL | 任意模式 | 反向 `side`，正确 `posSide`，无 `reduceOnly` |
| attached TP/SL | 任意模式 | `attachAlgoOrds` 存在，主单无 `reduceOnly` |

通过标准：

- 没有业务路径直接写 close/reduce `params={'reduceOnly': True}`。
- 参数构造器是唯一来源。
- 非 OKX 交易所不受影响。

### AC-A2 拒单复核

Mock 51169、51205、51112：

| 场景 | 通过标准 |
|---|---|
| 拒单后交易所无仓位 | 本地清理，输出 `already_flat` / `external_closed`，不重复提交平仓 |
| 拒单后交易所仍有仓位 | 本地持仓保留，symbol halt，输出告警，不继续无限兜底 |
| 本地方向与交易所方向冲突 | 以交易所状态为准记录，但禁止自动继续执行 |

### AC-A3 回归测试

必须通过：

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
python3 -m pytest -q
python3 verify_okx_testnet_semantics.py
```

`verify_okx_testnet_semantics.py` 可以保留 mock 模式，但必须新增 posMode 参数矩阵。

## 4. OKX Testnet 验收矩阵

### Case T0: Account Config

操作：

```http
GET /api/v5/account/config
```

记录：

- raw response。
- `data[0].posMode`。
- 本地缓存的 `executor._okx_pos_mode`。

通过标准：

- `posMode` 为 `net_mode` 或 `long_short_mode`。
- live/testnet 下读取失败时，执行器不允许开新仓。

### Case T1: Market Open + Attached TP/SL

操作：

- 按当前账户 `posMode` 开最小 size long。
- 主单附带 `attachAlgoOrds`，包含 `slTriggerPx/slOrdPx=-1` 和 `tpTriggerPx/tpOrdPx=-1`。

记录：

- create order raw request/response。
- final position。
- attached algo 状态。

通过标准：

- 主单成交或进入明确终态。
- final position 方向正确。
- attached algo 可查询，或 OKX 返回可解释的拒绝且本地兜底开启。
- 主单无 `reduceOnly=True`。

### Case T2: Net Mode Partial Reduce

适用：当前账户为 `net_mode`。

操作：

- 对 T1 仓位执行 50% partial reduce。
- request 必须包含 `posSide=net`、`reduceOnly=True`。

通过标准：

- OKX 不返回 51169/51205。
- final contracts 约等于原仓位 50%，误差只允许来自交易所精度。
- 未反向开仓。
- 本地 `position.amount` 与交易所一致。

### Case T3: Net Mode Full Close

适用：当前账户为 `net_mode`。

操作：

- 对剩余仓位执行 full close。
- request 必须包含 `posSide=net`、`reduceOnly=True`。

通过标准：

- final position 为 0。
- open orders / algo orders 无危险残留。
- 本地持仓清理。
- PnL 来源优先使用成交/ledger，不使用未标注的估算值冒充真实成交。

### Case T4: Long/Short Mode Smoke

适用：只有在账户可安全切换且无持仓/挂单时执行。

操作：

```json
{
  "posMode": "long_short_mode"
}
```

随后：

- 开 long：`side=buy`, `posSide=long`。
- 减 long：`side=sell`, `posSide=long`, 不传 `reduceOnly`。
- 平 long：`side=sell`, `posSide=long`, 不传 `reduceOnly`。

通过标准：

- 不出现 `51000` 缺 `posSide`。
- 不出现 51169/51205。
- final position 为 0。
- 切换前后均有 `GET /api/v5/account/config` 证据。

### Case T5: Standalone SL Algo

操作：

- 开最小 size 仓位。
- 使用独立 algo 方式挂 SL。

请求要求：

| 模式 | 必须参数 |
|---|---|
| net mode | `side` 为反向，`posSide=net`，`ordType=conditional` 或 `trigger`，`slTriggerPx` / `slOrdPx=-1`，无 `reduceOnly` |
| long/short mode | `side` 为反向，`posSide` 为仓位方向，`ordType=conditional` 或 `trigger`，`slTriggerPx` / `slOrdPx=-1`，无 `reduceOnly` |

通过标准：

- OKX 不返回 `51205 Reduce Only is not available`。
- algo order 可查询、可取消。
- 本地记录 `sl_order_id` 或明确标记交易所保护单未挂成功并启用本地兜底。

### Case T6: Move SL

操作：

- 对 T5 仓位移动 SL。

通过标准：

- 旧 SL 被取消、失效或被 amend。
- 新 SL 唯一有效。
- 本地 `stop_loss` 与交易所保护单状态一致。

### Case T7: Reject Reconciliation

操作：

- 使用 mock 或 testnet 控制方式制造 51169/51205。

通过标准：

- 执行器立即 fetch positions。
- 如果交易所仍有仓位，不删除本地仓位。
- 触发 symbol halt 或至少进入不可重复提交状态。
- Telegram/RiskGuard 可看到失败状态。

### Case T8: Duplicate clOrdId

操作：

- 对同一 symbol 和 action 重复提交相同 `clOrdId`。

通过标准：

- 第二次请求不会产生重复仓位。
- normalized result 为明确 rejected/duplicate/idempotent。
- 本地幂等窗口可清理，不阻塞后续合法反向交易。

### Case T9: Close 后条件单状态

操作：

- 开仓并挂 attached 或 standalone TP/SL。
- 全平。
- 查询 open orders / algo orders / positions。

通过标准：

- final position 为 0。
- 无会反向开仓的残留 TP/SL。
- 如果 OKX 保留历史 algo 状态，状态必须是 canceled / triggered / finished 等不可执行状态。

## 5. 验收报告模板

每个 case 必须按以下字段记录：

| 字段 | 要求 |
|---|---|
| `case_id` | 如 `T2` |
| `executed_at` | ISO 时间 |
| `okx_pos_mode` | `net_mode` / `long_short_mode` |
| `symbol` | OKX instrument |
| `local_request` | 本地调用参数，JSON |
| `raw_response` | OKX 原始响应，JSON 或原始错误 |
| `normalized_result` | `execution_result.v2` 或验收脚本归一化结果 |
| `final_position` | 验收后 `fetch_positions()` 结果 |
| `final_open_orders` | 验收后 open orders |
| `final_algo_orders` | 验收后 algo orders |
| `result` | `PASS` / `FAIL` |
| `notes` | 失败原因、人工动作、残余风险 |

验收报告落地到：

`docs/generated_reports/OKX执行语义testnet验收报告_20260522.md`

## 6. Go/No-Go

| 条件 | Go 标准 |
|---|---|
| 自动化参数矩阵 | AC-A1 全部通过 |
| 拒单复核 | AC-A2 全部通过 |
| 全量回归 | `pytest -q` 通过 |
| testnet 当前模式 | T0、T1、T5、T6、T7、T8、T9 通过 |
| net mode | 如果生产账户保持 net mode，T2、T3 必须通过 |
| long/short mode | 如果生产账户切 long/short mode，T4 必须通过 |
| 日志 | 无重复 51169/51205 无限重试 |
| 文档 | `docs/to-do-list.md`、本验收文档、testnet 报告一致 |

任一必测项失败：

- live 扩容 NO-GO。
- 自动开新仓 NO-GO。
- 只允许人工确认后的最小化恢复动作。

## 7. 回滚与应急

如果上线后再次出现 51169/51205：

1. 立即暂停自动开仓。
2. 查询 OKX 当前 positions、open orders、algo orders。
3. 如果交易所仍有仓位，人工用 OKX UI 或经 testnet 验证过的 close 参数平仓。
4. 不允许通过删除 `data/positions.json` 掩盖交易所真实仓位。
5. 记录 raw response，补充到 testnet 验收报告的失败案例中。
