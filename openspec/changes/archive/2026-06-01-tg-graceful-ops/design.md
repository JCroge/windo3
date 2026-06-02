## Context

Telegram 是本系统**唯一的人工运维入口**。当 root executor 触发 per-symbol halt（如 `_halt_symbol(reason='sl_replace_failed')`）时，会同时写全局 `halt_state.json`+触发 in-memory `_halted_symbols[symbol]`。运维通过 `/resume` 命令只能解全局 halt，per-symbol 残留在内存里直到进程重启——5/30 XLM 案例正是这个 bug 的真实暴露：8 小时静默拒单。

同期 `docs/to-do-list.md` 还有两个未闭环的 TG 优化（`/pnl` 行 58 + `/status` agent health 行 64），跟本次 bug 同属"运维可见性 + 控制"主题，本 change 一起闭环。

参考：
- `executor.py:900-915`：`_halt_symbol` / `is_symbol_halted` 实现
- `agents/trading/executor.py:376-407`：`_handle_resume` 现有逻辑
- `agents/trading/telegram_notifier.py:368-394`：命令分发
- `utils/live_ledger.py:392-491`：`apply_pnl_resolution` / `find_pending_external_closes` 已有 API

## Goals / Non-Goals

**Goals:**
- 修复 `/resume` 不清 per-symbol halt 残留 bug（彻底闭环 5/30 XLM 类问题）
- 提供 TG 端可见性：`/halts` 列锁的 symbol；`/status` 输出 per-symbol halt
- 提供 TG 端控制：`/resume_symbol <SYMBOL>` 单 symbol 解锁
- 落地 `/pnl <SYMBOL> <NET_PNL>` 手动 PnL correction（todo 58）
- `/status` 增加 agent health 轻量信号（todo 64 轻量）
- 三个新 capability 都有完整 test 覆盖；无回归

**Non-Goals:**
- 不引入 agent heartbeat / loop alive 重构（这是另一个 change）
- 不改 `_halted_symbols` 数据结构（保持 in-memory dict，仅暴露读/清 API）
- 不持久化 per-symbol halt 到磁盘（重启清零是合理行为，避免重启后旧 symbol 永远锁住）
- 不改 `LiveLedger.apply_pnl_resolution` 内部契约（只加 TG 入口）
- 不改全局 halt 语义（HaltState 的 halt/resume/force_resume 不动）
- 不在 TG 端实现 `/halt_symbol`（手动 halt 单 symbol）—— root executor 不需要这个能力

## Decisions

### Decision 1：`_halted_symbols` 通过公开方法清理，agent 不直接动私有字段

**问题**：agent 层 `_handle_resume` 怎么清 `_halted_symbols`？三个选项：

- A：agent 直接 `self.executor._halted_symbols.clear()` —— 私有字段穿透，封装破坏
- B：root executor 暴露 `clear_symbol_halt(symbol=None)` 公开方法，agent 调用
- C：root executor 监听 bus 上的 `system_command{cmd=resume}` —— 但 root 不是 agent，不在 bus 上

**选择 B**。`clear_symbol_halt(symbol=None)` 语义明确：传 symbol 清一个，不传清全部。新增配套 `get_halted_symbols() -> dict`（返回快照副本，不暴露内部字典引用）。

```python
# executor.py (root)
def clear_symbol_halt(self, symbol: Optional[str] = None) -> int:
    """清除 per-symbol halt 残留。

    Args:
        symbol: 指定 symbol 仅清该项；None 清全部。
    Returns:
        清掉的项数（用于审计日志）。
    """
    halted = getattr(self, '_halted_symbols', {})
    if symbol is None:
        n = len(halted)
        halted.clear()
        return n
    if symbol in halted:
        del halted[symbol]
        return 1
    return 0

def get_halted_symbols(self) -> Dict[str, dict]:
    """返回 _halted_symbols 的浅拷贝快照。"""
    return dict(getattr(self, '_halted_symbols', {}))
```

### Decision 2：`/resume` 路径修复——HaltState 解全局成功后调用 `clear_symbol_halt(None)`

**问题**：什么时机清？

`_handle_resume` 当前三种成功分支都设置 `self._trading_halted = False`：
1. payload 已带 `reconciliation_result.matched`（line 380-384）
2. 本地 reconciler 跑完无 blocking issues（line 386-395）
3. 无 reconciler，直接恢复（line 405-407）

三种都是"全局 halt 已 confirm 解除"的语义点。**在每个分支后都调用 `self.executor.clear_symbol_halt(None)`**。

`force_resume` 路径（agent 层 line 87-89）同样清理：force 是用户主动绕过对账，per-symbol halt 也是用户的责任，一并清掉符合"force"语义。

写入操作记 audit log：`[Resume] cleared N per-symbol halts: [XLM, ...]`。

### Decision 3：`/halts` 输出 + `/resume_symbol` 解析

**`/halts` 输出格式**（无参数）：

```
🔒 Per-symbol halt: 1 个
• XLM-USDT-SWAP
  reason: sl_replace_failed
  halted: 8h32m ago
```

无 symbol 被锁时输出 `✅ 无 per-symbol halt`。

**`/resume_symbol <SYMBOL>` 流程**：

1. 解析 symbol（容忍带 `-USDT` / `-SWAP` / `-USDT-SWAP` 后缀，全部归一化到 root executor 的 `_normalize_symbol` 调用结果）
2. 调用 `executor.clear_symbol_halt(normalized_symbol)`
3. 返回值为 0 → 回 `ℹ️ <SYMBOL> 没有被 halt`；返回 1 → 回 `✅ <SYMBOL> per-symbol halt 已解除`
4. **不动全局 halt_state.json**——这是关键，全局 halt 由独立 `/resume` 管

权限对等：`/resume_symbol` 同 `/resume`，仅 chat_id 等于 `_chat_id` 时执行。

### Decision 4：`/pnl` 候选必须恰好 1 条才执行

**问题**：用户在 TG 输入 `/pnl XLM 0.42`，怎么找到要 correct 的 pending 事件？

`LiveLedger.find_pending_external_closes()` 返回所有未 supersede 的 pending。需要按 symbol 过滤后**只接受恰好 1 条**：

- 0 条 → `❌ 没有找到 <SYMBOL> 的 pending external close`
- 1 条 → 写 correction，回 `✅ 已写 PnL correction: <SYMBOL> +0.42 USDT (supersedes EID...)`
- ≥2 条 → `⚠️ 有 N 条 pending，请用 /pnl_id <event_id> <pnl> 指定具体哪一条`（当前 change 只实现 `/pnl`，多候选场景 fail-fast；`/pnl_id` 留作后续）

correction event 用 `source='manual_tg_review'`（与 5/29 ALGO correction 一致），含 `pnl_pending_reason=''`。

**幂等**：`apply_pnl_resolution` 现有契约——按 `position_id + close_match_key + sorted(order_ids)` 去重。manual correction 的 `order_ids` 通常为空，但 `position_id + close_match_key` 已经唯一定位 pending；重复提交相同 net_pnl 不写新 event（apply_pnl_resolution 会返回 existing correction）。需补 test 验证。

**参数解析**：

```
/pnl SYMBOL NET_PNL [reason...]
       ↓       ↓        ↓
       str   float   optional rest joined
```

`NET_PNL` 必须可解析为 float（含正负号），否则拒绝。

### Decision 5：`/status` 增强字段

**问题**：要展示什么 agent health 信号？

不引入 heartbeat 重构（non-goal）。从已有数据中读：

```
📊 系统状态
运行: 23.5h
持仓: 0个
熔断: 否
对账: matched
─ Agents: 17 注册 / 17 任务存活 / 0 任务异常
─ Bus DLQ: 0 条
─ Per-symbol halt: 1 个 (XLM)         ← F-TG-002 输出
今日交易: 5 笔
今日PnL: +1.27 USDT
```

数据来源：
- `Agents 注册数` = `Orchestrator._research_agents + _trading_agents` 长度
- `任务存活 / 异常` = `Orchestrator._tasks` 中 done() 但 exception() 非 None 的数量
- `Bus DLQ` = `MessageBus._dlq` 长度（如有 attribute；查现状）
- `Per-symbol halt` = `len(executor.get_halted_symbols())`

**问题**：TelegramNotifier agent 层是否能拿到 Orchestrator 引用？现状 TG agent 不持有 orchestrator——可以通过 `system_command{cmd=health_query}` 让 Orchestrator publish 一次性 `health_snapshot` event 回 bus，TG 订阅。但这是异步且引入新事件。

**简化选择**：直接读文件 + 模块单例。

- `Agents 注册数` / `任务存活` —— 让 Orchestrator 把统计写入 `data/agent_health.json`（每 30s 刷一次），TG 读文件
- `Bus DLQ` —— `MessageBus.get_instance()._dlq` 直接读模块单例（TG agent 已经在用 bus）
- `Per-symbol halt` —— TG agent 已经持有 root executor 引用？查现状

读现状：TG agent 是不是有 executor 引用？

实际现状：`TelegramNotifier` 在 `agents/trading/telegram_notifier.py`，**不持有** root executor 引用（不像 `MultiExecutor` agent）。要拿 `_halted_symbols`，需要：

- 选项 A：让 Orchestrator 在初始化时把 `executor` 引用注入 TG agent（明确依赖）
- 选项 B：通过 bus 异步查询（`/halts` 命令 publish `query_halts`，root 通过 MultiExecutor agent 响应）
- 选项 C：从 `data/agent_health.json` 读（让 MultiExecutor agent 周期性 dump per-symbol halt 到磁盘）

**选 C**：在已经设计 health.json 的轨道上多写一项 `halted_symbols`。MultiExecutor agent 持有 `self.executor`，每 30s 写一次。TG 读文件即可。优势：不引入新依赖、统一可观测性 surface、跨进程可读（重启后 file 仍在）。劣势：30s 延迟（可接受，运维场景非毫秒级）。

**写入入口**：MultiExecutor agent 的 tick 循环（如有；否则在 `_run_reconciliation` 同周期）写 `data/<ns_>agent_health.json`，schema：

```json
{
  "ts": 1780150000.0,
  "agents_registered": 17,
  "tasks_alive": 17,
  "tasks_failed": 0,
  "halted_symbols": {
    "XLM-USDT-SWAP": {"reason": "sl_replace_failed", "halted_at": 1780085000.0}
  },
  "bus_dlq_size": 0
}
```

### Decision 6：测试策略

| 文件 | 关键 case |
|---|---|
| `test_tg_symbol_halt_control.py` | clear_symbol_halt(None) / clear_symbol_halt(XLM) / get_halted_symbols / `_handle_resume` 三分支都清 / force_resume 清 / `/halts` 输出 / `/resume_symbol` 解析（带后缀 + 不带后缀）/ `/resume_symbol` 不存在 symbol 返回友好消息 |
| `test_tg_pnl_correction.py` | `/pnl` 解析 / 0 候选 reject / 1 候选写 correction / 2 候选 reject / 幂等（重复提交不累计）/ NET_PNL 解析失败 |
| `test_tg_status_enhancement.py` | `/status` 输出含 "Agents" 行 / 含 "Per-symbol halt" 行 / agent_health.json 缺失时 fallback / health.json 写入 schema |

预期新增 case ≥ 18。

## Risks / Trade-offs

- **agent_health.json 30s 延迟** → Mitigation：`/halts` 命令直接走 bus 查询而不走文件（实时），`/status` 走文件（容忍延迟）
- **agent_health.json 路径与 namespace** → Mitigation：使用 `utils.state_paths.get_state_paths()` 派生（live=`data/agent_health.json`, testnet=`data/testnet_agent_health.json`）
- **多候选 pending 时 `/pnl` fail-fast** → Mitigation：`/pnl_id` 留 todo；error message 引导用户后续命令
- **`_halted_symbols` 不持久化** → 已是 non-goal；重启清零是 by design（避免历史 symbol 永久被锁）

## Migration Plan

按 capability 顺序：

1. F-TG-001（resume bug 修复）：风险最低，先落
2. F-TG-002（/halts /resume_symbol /status per-symbol halt）：依赖 F-TG-001 的 `clear_symbol_halt` API
3. F-TG-003（/pnl）：独立，可与 F-TG-002 并行
4. F-TG-004（/status agent health）：依赖 agent_health.json 写入路径

每个 capability 一次或多次 commit，message 带 `[TG-OPS]` 前缀。

回滚：每项独立 commit，回滚单个不影响其余。新 TG 命令对老用户是加法，不会破坏现有 `/status` / `/resume` 行为。

## Open Questions

- 是否在 `/halts` 命令里也展示 force_resume 后清掉的 audit 历史？**结论**：否，超出 scope；`logs/agent_executor_*.log` 已经有清理日志
- agent_health.json 是否要进 reviewer / Judge 决策路径？**结论**：否，仅运维可见性用途
- 多候选 pending `/pnl` 的 `/pnl_id` 命令是否本 change 一起做？**结论**：不做；当前真实场景多候选罕见，留 todo
