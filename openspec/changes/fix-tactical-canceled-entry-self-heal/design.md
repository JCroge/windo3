## Context

生产 PUMP intent 在原价限价等待 900 秒后发起撤单。OKX 已将订单 `3805724946214244352` 标记为 `canceled`，`accFillSz=0`，账户仓位、普通挂单、conditional 和 OCO 均为空；但精确订单查询仍以 `sz-accFillSz` 计算 `remaining_qty=200`。恢复循环因此再次调用撤单，OKX 返回 51400，未捕获异常中断 tick，导致完整性 halt 和陈旧状态持续存在。

本地最小复现确认，相同的终态响应会被 `query_tactical_entry()` 返回为 canceled + 200 remainder，`cancel_tactical_entry()` 随后再次调用 `cancel_order()`。

## Goals / Non-Goals

**Goals:**
- 让明确终态的 entry 返回零可撤剩余量，同时保留真实 `filled_qty`。
- 让查询与撤单之间的终态竞态通过确定性 client-order id 回查收敛。
- 保持未知或无法证明的状态 fail closed，不用 51400 本身作为安全证明。
- 让现有 controller 自愈路径自动终结 PUMP episode 并清除对应完整性 halt。

**Non-Goals:**
- 不手工篡改 Tactical event ledger 或 governor 状态。
- 不改变 900 秒 TTL、0.10R、100U x 3、-15U/24h 或 Sidecar 策略。
- 不新增 API、配置、状态 schema 或宽泛的异常吞噬。

## Decisions

### D1: 订单终态决定可撤余量

`query_tactical_entry()` 在 exact OKX 和 CCXT fallback 两条读取路径上，先解析状态、原始大小和成交量，再将 `canceled/cancelled/closed/filled/rejected/expired` 的可撤 `remaining_qty` 规范化为零。部分成交量仍保留，由 controller 进入持仓和保护核对，不能被误当成零成交。

只在 `cancel_tactical_entry()` 特判 canceled 不足以修复根因，因为 controller 的重启恢复、限价到期和精确成交回查都消费统一 observation。

### D2: 撤单异常后必须重新取得交易所证明

`cancel_tactical_entry()` 捕获查询后撤单时的异常，但不直接宣告成功。它继续按确定性 `clOrdId` 回查：若订单已终态且余量为零，则返回 proven；若仍有余量、查询失败或身份不可见，则返回 unproven 并保留完整性 halt。这样覆盖 OKX 51400 竞态，同时不把网络错误或未知订单状态错误降级为安全。

### D3: 使用现有 controller 恢复与 proof 清除路径

不新增状态迁移或运维清理脚本。修复后的 exact observation 会进入 `_recover_exact_unfilled_entry_halt()`：先证明订单终态，再证明交易所无仓，最后以现有完整 proof 清除 halt。生产恢复由重启后的账本 replay 驱动。

## Risks / Trade-offs

- **[交易所终态状态集合变化]** OKX 或 CCXT 可能返回新的终态字符串。→ 仅采用代码库已使用的终态集合，并由明确测试锁定。
- **[撤单异常被隐藏]** 捕获所有撤单异常可能掩盖真实故障。→ 必须执行精确回查；未得到终态证明仍返回 unproven，并携带异常证据。
- **[部分成交的 canceled 订单]** 余量归零不能等同于无仓。→ 保留 `filled_qty`，让现有 filled recovery 校验实际仓位、数量和保护。

## Migration Plan

1. 测试先行复现已取消零成交订单和撤单 51400 竞态。
2. 实现最小修复并运行 Tactical V2 专项与完整回归。
3. 备份云端 `executor.py` 和相关运行状态，只同步经过验证的文件。
4. 仅重启 Main，保持 Sidecar PID 和 admission 状态不变。
5. 验证 PUMP 变为 `expired`、integrity halt 清除、状态恢复新鲜、OKX 仍为空仓空挂单，且新日志不再出现对应 51400 循环。

回滚为恢复备份文件并仅重启 Main；若任何订单、仓位或归属证明不明确，则保持 fail closed，不解除 halt。

## Open Questions

无。生产订单、仓位与挂单真相已通过只读 OKX API 取得。
