# 分批止盈生命周期收敛验收文档

更新日期：2026-05-27  
关联需求：`docs/partial_tp_lifecycle_prd.md`  
状态：待实现、待 mock/testnet 验收

## 1. 验收目标

证明分批止盈生命周期已收敛为单一 owner：

- OKX 不再挂交易所 TP algo。
- 本地 partial TP 对 long/short 均只按预期减仓，不会在 TP1 后全平。
- partial TP 成功后剩余仓位有唯一有效的交易所 SL。
- reduce/close/local stop/risk alert 不会并发处理同一 symbol。
- 重启后能发现并处理存量 TP/SL algo。
- 失败场景不会提前推进 `tp_filled` 或误删本地仓位。

## 2. 前置条件

| 条件 | 标准 |
|---|---|
| 测试环境 | mock 单测必须无真实凭证可运行；testnet 使用 OKX demo trading |
| 账户状态 | testnet case 前目标 symbol 无持仓、无普通挂单、无 pending algo |
| 交易对 | 使用高流动性、最小下单成本低的 USDT-SWAP |
| 风险 | 单 case 名义本金不超过测试账户权益 1%，杠杆不超过 5x |
| 日志 | 保存 raw request、raw response、position snapshot、algo snapshot |
| 本地状态 | 测试前备份 `data/positions.json` 和 ledger 文件 |
| 时间 | 记录执行时间、OKX `posMode`、代码 commit hash |

## 3. 自动化验收

### AC-A1 OKX 开仓不附带 TP

输入：

- `exchange_id=okx`
- `stop_loss` 有值
- `take_profit=[tp1, tp2, tp3]`

通过标准：

- create order params 中 `attachAlgoOrds` 不包含 `tpTriggerPx` / `tpOrdPx`。
- 若附带 SL，则只包含 `slTriggerPx` / `slOrdPx` 和可追踪 client id。
- position 中保存 `take_profit_levels`，但不保存任何 `tp_algo_id`。
- 非 OKX 路径保持原有兼容行为，除非该路径也启用本地 partial TP。

### AC-A2 TP1 后不触发 legacy 全平

Long case：

1. position: `side=long`, `entry=100`, `take_profit_levels=[110, 120, 130]`, `take_profit=110`, `tp_filled=0`。
2. price=110，检查返回 `partial_tp_1`。
3. 模拟 partial TP 成功，`tp_filled=1`。
4. 下一轮 price=111。

通过标准：

- 第 2 步只触发 50% reduce，不调用 `close_position()`。
- 第 4 步不得返回 `take_profit`。
- `tp_filled` 保持 1，尾仓继续由 TP2/trailing 管理。

Short case 同理：

- `entry=100`, `take_profit_levels=[90, 80, 70]`, `take_profit=90`。
- price=90 触发 `partial_tp_1`。
- price=89 不得触发 legacy `take_profit` 全平。

### AC-A3 `tp_filled` 只在 reduce 成功后更新

Mock `create_order()` 抛错或返回拒单。

通过标准：

- `tp_filled` 保持原值。
- `amount` / `amount_usdt` 不变。
- `stop_loss` 不移动或不持久化移动。
- `execution_result` 为 rejected/error，source=`partial_tp`。
- 下一轮仍允许重新评估同一 TP，但必须受幂等窗口限制。

### AC-A4 partial TP 成功后 SL 唯一有效

输入：

- position 有 `sl_algo_id=old-sl`。
- TP1 reduce 成功，剩余仓位为原 50%。

通过标准：

- old SL 被 amend 到新数量和新触发价，或 old SL 被取消且 new SL 创建成功。
- 最终 pending algo 中只有一个有效 SL。
- position 中 `sl_algo_id` 指向最终有效 SL。
- `protection_state=protected`。
- `sl_sync_state=active`。

### AC-A5 SL 更新失败进入保护失败流程

模拟：

- reduce order 成功。
- amend/cancel/recreate SL 失败。

通过标准：

- position 不得显示 `protection_state=protected`。
- symbol 被 halt 或进入 `local_fallback`，按配置决定是否立即关闭剩余仓位。
- 发送告警或发布 `execution_result`，reason 包含 `protective_sl_update_failed`。
- 后续 add/reduce/move SL 请求被拒绝，直到人工恢复或对账通过。

### AC-A6 同 symbol 退出动作串行

并发触发：

- `partial_tp_1`
- `risk_alert` reduce
- `local_stop` close

通过标准：

- 同一时刻最多一个 create close/reduce order 发出。
- 其他动作返回 `exit_locked` / idempotent，不直接下单。
- 最终本地 position 与交易所仓位一致。
- ledger 不出现同一仓位重复 close/reduce 记录。

### AC-A7 存量 TP algo 迁移

Mock pending algo 中存在：

- 一个 TP algo。
- 一个 SL algo。
- 本地 position `exit_owner=local_partial_tp_exchange_sl`。

通过标准：

- TP algo 被取消。
- SL algo 能归属则保存为 `sl_algo_id`。
- 无法归属 SL 时 symbol halt。
- 迁移完成前不允许该 symbol 新开仓或 partial TP。

### AC-A8 close 后清理 algo

全平路径执行后：

通过标准：

- 本地 position 删除或标记 closed。
- 该 symbol 无 pending TP algo。
- 该 symbol 无可执行 SL algo。
- 如果 OKX 返回历史 algo，状态必须是 canceled/triggered/finished 等不可执行终态。

### AC-A9 精度、dust 和最小数量

覆盖：

- reduce amount 精度后为 0。
- partial TP 后剩余量低于最小交易量。
- available contracts 小于本地计算 reduce amount。

通过标准：

- amount 为 0 时不推进 `tp_filled`。
- dust 按配置 full close 或标记 closed，不留下无法管理尾仓。
- reduce amount 不超过 `available_contracts`。
- 本地 `amount_usdt` 按实际 reduce 比例更新。

### AC-A10 long/short 方向回归

参数化测试覆盖：

| side | TP1 条件 | TP2 条件 | SL 不利方向 |
|---|---|---|---|
| long | `price >= tp1` | `price >= tp2` | `price <= stop_loss` |
| short | `price <= tp1` | `price <= tp2` | `price >= stop_loss` |

通过标准：

- 任一比较方向写反，测试失败。

## 4. 推荐自动化命令

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
python3 -m pytest -q test_okx_posmode_executor.py
python3 -m pytest -q test_execution_result_contract.py
python3 -m pytest -q test_executor_upgrade.py
python3 -m pytest -q
```

新增测试建议：

- `test_partial_tp_lifecycle.py`
- `test_okx_algo_lifecycle.py`
- `test_exit_lock.py`

## 5. OKX Testnet 验收矩阵

### T0 清场与账户快照

操作：

- 查询 `fetch_positions()`。
- 查询普通 open orders。
- 查询 pending algo orders。
- 记录 OKX `posMode`。

通过标准：

- 目标 symbol 无仓位、无普通挂单、无 pending algo。
- 如存在残留，必须先取消并记录。

### T1 Long 开仓只挂 SL，不挂 TP

操作：

- 以最小 size 开 long。
- plan 包含三档 TP 和 SL。

记录：

- create order raw request/response。
- pending algo orders。
- 本地 position。

通过标准：

- OKX pending algo 中无 TP algo。
- 有且只有一个有效 SL algo，或明确记录 SL 未挂成功并立即进入失败流程。
- 本地 position 保存 `take_profit_levels`、`sl_algo_id` 或可恢复 client id。

### T2 Long TP1 本地减仓 50%

操作：

- 不依赖真实价格触发，可在测试脚本中直接调用 partial TP 执行方法。
- 对 T1 仓位执行 TP1 50% reduce。

通过标准：

- OKX 只收到 reduce order，不存在 TP algo 触发。
- final position contracts 约等于原仓位 50%。
- `tp_filled=1`。
- pending algo 只有一个 SL，数量/保护语义与剩余仓位一致。
- 下一轮本地价格仍高于 TP1 时不触发 full close。

### T3 Long TP2 再减仓 25%

操作：

- 对剩余仓位执行 TP2 reduce。

通过标准：

- final position contracts 约等于原始仓位 25%。
- `tp_filled=2`。
- SL 更新到 TP2 后规则要求的新触发价。
- 无 TP algo 残留。

### T4 Long 尾仓 close 清理

操作：

- 对剩余仓位执行 full close。

通过标准：

- final position 为 0。
- pending algo orders 为 0，或均为不可执行终态。
- 本地 position 清理。

### T5 Short 对称路径

重复 T1-T4，但方向为 short。

通过标准：

- open short、reduce short、close short 的 `side` / `posSide` 符合 OKX 当前 `posMode`。
- TP 比较方向为 `price <= tp_level`。
- 不出现 51169/51205。
- 无 TP algo 残留。

### T6 SL amend 或 recreate 语义

操作：

- 开仓并挂 SL。
- partial TP 后更新 SL。
- 主动查询 old/new algo 状态。

通过标准：

- 如果使用 amend：同一个 `sl_algo_id` 的触发价/数量更新成功。
- 如果使用 cancel + recreate：old SL 不可执行，new SL 唯一有效。
- 任一 API 路径失败时，本地进入保护失败流程。

### T7 重启恢复

操作：

- 开仓后停止进程。
- 保留仓位和 SL algo。
- 重启执行器并运行 sync/migration。

通过标准：

- 本地能恢复 `sl_algo_id` 或通过 client id 重新归属。
- 不创建第二个 SL。
- 不误取消有效 SL。
- 如果发现 TP algo，必须取消或 halt。

### T8 拒单复核

操作：

- 制造 reduce/close 51169 或 51205。
- 立即查询交易所仓位和 algo。

通过标准：

- 交易所仍有仓位时，本地不删除 position。
- 交易所已无仓位时，本地走 external close/already flat 清理。
- 状态不确定时 symbol halt。
- 不重复提交无限 close/reduce。

### T9 保护失败 live 策略演练

操作：

- 在 testnet 模拟 SL 更新失败。

通过标准：

- live 配置下默认尝试关闭剩余仓位，或明确配置为 halt-only。
- 无论哪种策略，都必须有告警和可追踪事件。
- 不允许继续自动加仓或 partial TP。

## 6. 验收报告模板

每个 case 记录：

| 字段 | 要求 |
|---|---|
| `case_id` | 如 `T2` |
| `executed_at` | ISO 时间 |
| `commit` | git commit hash |
| `okx_pos_mode` | `net_mode` / `long_short_mode` |
| `symbol` | OKX instrument |
| `side` | long / short |
| `local_position_before` | 本地仓位快照 |
| `local_request` | 本地调用参数，JSON |
| `raw_request` | 交易所请求摘要 |
| `raw_response` | OKX 原始响应或错误 |
| `final_position` | `fetch_positions()` 结果 |
| `final_open_orders` | 普通 open orders |
| `final_algo_orders` | pending/history algo orders |
| `local_position_after` | 本地仓位快照 |
| `execution_result` | 发布事件 |
| `passed` | true / false |
| `failure_reason` | 失败原因 |

## 7. Go/No-Go

Go：

- AC-A1 至 AC-A10 全部通过。
- T0 至 T8 至少在 OKX testnet 通过；T9 至少完成演练。
- long/short 均无交易所 TP algo。
- partial TP 后本地不会 legacy full close。
- 减仓后 SL 唯一有效。
- close 后无可执行 algo 残留。

No-Go：

- 任一新仓仍产生 OKX TP algo。
- TP1 后下一轮本地返回 `take_profit`。
- partial TP reduce 失败但 `tp_filled` 增加。
- reduce 成功后 `protection_state=protected` 与交易所 pending algo 不一致。
- exit lock 缺失导致同 symbol 并发下出两笔 close/reduce。
- 重启后无法识别或处理存量 TP algo。
