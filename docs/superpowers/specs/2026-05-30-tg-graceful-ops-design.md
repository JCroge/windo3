---
comet_change: tg-graceful-ops
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-01-tg-graceful-ops
status: final
---

# TG Graceful Ops — Technical Design

## 1. 设计目标与范围

让 Telegram 成为系统真正的"优雅"运维入口：命令准确执行下去、不留残留状态、可见可控。本 change 闭环 4 个工作流：

- **F-TG-001**：修复 `/resume` 不清 root executor `_halted_symbols` 残留 bug（5/30 XLM 案例）
- **F-TG-002**：新增 `/halts` `/resume_symbol` 命令；`/status` 显示 per-symbol halt
- **F-TG-003**：新增 `/pnl` `/pnl_id` 手动 PnL correction 命令（todo-list 行 58）
- **F-TG-004**：`/status` 增强 agent health 轻量信号（todo-list 行 64 轻量版）

**Canonical spec**：本次需求与验收契约以 OpenSpec change `tg-graceful-ops` 为准（见 `openspec/changes/tg-graceful-ops/specs/{tg-symbol-halt-control,tg-pnl-correction,tg-status-enhancement}/spec.md`）。本设计文档只描述实现方案、技术选型、风险与测试策略，不重复定义需求。

**关键技术决策**（brainstorm 已确认）：
- F-TG-001：`/resume` 与 `/force_resume` 都清 per-symbol halt；`/force_resume` 额外打 audit warning + TG 回显
- F-TG-002：`/halts`（读）走 file 直读，`/resume_symbol`（写）走 bus system_command，TG agent 不持有 root executor 引用
- F-TG-003：`/pnl` 和 `/pnl_id` 一起做（不留 todo），共用 `_resolve_pending_for_pnl_correction(filter_fn)` helper
- F-TG-004：`agent_health.json` 由 **Orchestrator 独写**；MultiExecutor 周期 publish `halts_snapshot` 事件供 Orchestrator 订阅缓存

## 2. F-TG-001 Resume 清 per-symbol halt

### 2.1 root executor 公开 API

`executor.py` (root) 新增两个公开方法（位置：在 `_halt_symbol` / `is_symbol_halted` 附近，line ~900-915）：

```python
def clear_symbol_halt(self, symbol: Optional[str] = None) -> int:
    """清除 per-symbol halt 残留。

    Args:
        symbol: 指定 symbol 仅清该项；None 清全部。
    Returns:
        清掉的项数（用于审计日志）。
    """
    halted = getattr(self, '_halted_symbols', None)
    if not halted:
        return 0
    if symbol is None:
        n = len(halted)
        cleared_keys = list(halted.keys())
        halted.clear()
        if n > 0:
            self.logger.info(
                f"[ClearSymbolHalt] cleared {n} per-symbol halt(s): {cleared_keys}"
            )
        return n
    if symbol in halted:
        reason = halted[symbol].get('reason', '')
        del halted[symbol]
        self.logger.info(
            f"[ClearSymbolHalt] cleared {symbol} (reason={reason})"
        )
        return 1
    return 0

def get_halted_symbols(self) -> Dict[str, dict]:
    """返回 _halted_symbols 的浅拷贝快照（顶层 dict 拷贝；value dict 引用复用，调用方不应修改）。"""
    return dict(getattr(self, '_halted_symbols', {}))
```

### 2.2 Agent 层 `_handle_resume` 与 force_resume 改造

`agents/trading/executor.py:_handle_resume` 三条成功分支末尾各加 `self.executor.clear_symbol_halt(None)`：

```python
async def _handle_resume(self, source: str, payload: dict):
    reconciliation_result = payload.get('reconciliation_result')

    if reconciliation_result and reconciliation_result.get('status') == 'matched':
        self._halt_state.confirm_resume(resume_by=source, reconcile_ok=True)
        self._trading_halted = False
        self.executor.clear_symbol_halt(None)  # F-TG-001
        self.logger.info(f"[解除熔断] 通过{source}触发，对账通过")
        return

    if self._reconciler:
        try:
            result = self._reconciler.reconcile(
                executor_positions=self.executor.positions
            )
            blocking = result.get('blocking_issues', [])
            if not blocking:
                self._halt_state.confirm_resume(resume_by=source, reconcile_ok=True)
                self._trading_halted = False
                self.executor.clear_symbol_halt(None)  # F-TG-001
                self.logger.info(f"[解除熔断] 通过{source}触发，本地对账通过")
            else:
                # 对账失败:不清,halt 维持
                self._halt_state.confirm_resume(resume_by=source, reconcile_ok=False)
                self.logger.warning(
                    f"[熔断维持] 对账失败: {len(blocking)}个阻断问题 — {blocking}"
                )
        except Exception as e:
            self._halt_state.confirm_resume(resume_by=source, reconcile_ok=False)
            self.logger.error(f"[熔断维持] 对账异常: {e}")
    else:
        self._halt_state.confirm_resume(resume_by=source, reconcile_ok=True)
        self._trading_halted = False
        self.executor.clear_symbol_halt(None)  # F-TG-001
        self.logger.info(f"[解除熔断] 通过{source}触发（无reconciler，直接恢复）")
```

`force_resume` 路径（agent 层 line 86-89）特殊处理 + audit warning：

```python
elif cmd == 'force_resume':
    self._halt_state.force_resume(resume_by=source)
    self._trading_halted = False
    # F-TG-001: force_resume 同样清 per-symbol halt,但额外打 audit warning
    halted_snapshot = self.executor.get_halted_symbols()
    cleared_n = self.executor.clear_symbol_halt(None)
    if cleared_n > 0:
        symbols_with_reason = [
            f"{sym} ({info.get('reason', '?')})"
            for sym, info in halted_snapshot.items()
        ]
        self.logger.warning(
            f"[强制解除熔断 audit] {source} 同时清除 {cleared_n} 个 "
            f"per-symbol halt: {symbols_with_reason} — 请确认根因已排除"
        )
        # 同时发回 Telegram(via system_command response 或 risk_alert)
        await self.publish('risk_alert', {
            'type': 'force_resume_cleared_symbol_halts',
            'cleared_symbols': symbols_with_reason,
            'source': source,
        })
    self.logger.warning(f"[强制解除熔断] 通过{source}触发，跳过对账")
```

TelegramNotifier 订阅 `risk_alert.type='force_resume_cleared_symbol_halts'`，在 `_handle_risk_alert` 加分支输出回显：

```python
elif alert_type == 'force_resume_cleared_symbol_halts':
    cleared = payload.get('cleared_symbols', [])
    text = (
        f"⚠️ /force_resume 同时清除了 {len(cleared)} 个 per-symbol halt:\n"
        + "\n".join(f"  • {s}" for s in cleared)
        + "\n\n请确认根因已排除"
    )
    await self._send_message(text)
```

注意：现有 `_handle_risk_alert` 的 `critical_types` allowlist 需要加 `force_resume_cleared_symbol_halts`。

### 2.3 边界条件

- `_halted_symbols` 不存在（attribute 还没初始化）→ `getattr(..., None)` 返回 None，函数早返回 0
- 多线程：root executor `_halted_symbols` 是 dict，Python GIL 保护单步 `del` / `clear`；race 最坏情况是刚清就被新 SL 失败重新加上，正确行为
- 文档同步：`executor.py` 新方法的 docstring 含使用示例

## 3. F-TG-002 `/halts` `/resume_symbol` `/status` per-symbol halt

### 3.1 `/halts` 走 file 直读

读 `data/<ns_>agent_health.json` 的 `halted_symbols` 字段（30s 延迟可接受，运维场景）：

```python
async def _cmd_halts(self):
    health = self._read_agent_health() or {}
    halts = health.get('halted_symbols', {})

    if not halts:
        await self._send_message("✅ 无 per-symbol halt")
        return

    lines = [f"🔒 Per-symbol halt: {len(halts)} 个"]
    now = time.time()
    for sym, info in halts.items():
        reason = info.get('reason', '?')
        halted_at = info.get('halted_at', 0)
        elapsed = now - halted_at if halted_at else 0
        lines.append(f"• {sym}")
        lines.append(f"  reason: {reason}")
        lines.append(f"  halted: {self._format_elapsed(elapsed)} ago")
    await self._send_message("\n".join(lines))

def _read_agent_health(self) -> Optional[dict]:
    """读 agent_health.json,失败返回 None(让上游降级)。"""
    try:
        from utils.state_paths import get_state_paths
        path = get_state_paths().agent_health  # F-TG-004 新加字段
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return None

@staticmethod
def _format_elapsed(seconds: float) -> str:
    """格式化经过时间为人类可读 '2h15m' / '45s'。"""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h{minutes % 60}m"
```

### 3.2 `/resume_symbol` 走 bus system_command

TG 端：

```python
async def _cmd_resume_symbol(self, args: list):
    if not args:
        await self._send_message("用法: /resume_symbol <SYMBOL>")
        return

    raw = args[0].strip().upper()
    # 简单归一化:容忍带后缀,统一加 -USDT-SWAP
    if not raw.endswith('-SWAP'):
        if not raw.endswith('-USDT'):
            symbol = f"{raw}-USDT-SWAP"
        else:
            symbol = f"{raw}-SWAP"
    else:
        symbol = raw

    await self.publish('system_command', {
        'command': 'resume_symbol',
        'symbol': symbol,
        'source': 'telegram',
    })
    await self._send_message(f"🔄 已发送 /resume_symbol {symbol} 请求")
```

⚠️ 注意：TG 端的归一化只是粗判，**真正的归一化必须在 root executor 端**通过 `_normalize_symbol` 完成（兼容 `BTC` / `BTC/USDT:USDT` / `BTC-USDT-SWAP` 等多种形态）。所以 TG 这里粗加 `-USDT-SWAP` 后缀，剩下交给 root executor 处理。

MultiExecutor agent 端（`on_message` system_command 分支扩展）：

```python
if msg['type'] == 'system_command':
    cmd = msg.get('payload', {}).get('command', '')
    source = msg.get('payload', {}).get('source', 'telegram')
    if cmd == 'halt':
        ...
    elif cmd == 'resume':
        ...
    elif cmd == 'force_resume':
        ...
    elif cmd == 'resume_symbol':
        symbol = msg.get('payload', {}).get('symbol', '').strip()
        if not symbol:
            return
        normalized = self.executor._normalize_symbol(symbol)
        cleared = self.executor.clear_symbol_halt(normalized)
        if cleared > 0:
            await self.publish('risk_alert', {
                'type': 'symbol_halt_cleared',
                'symbol': normalized,
                'source': source,
            })
            self.logger.info(f"[ResumeSymbol] {source} 解除 {normalized} per-symbol halt")
        else:
            await self.publish('risk_alert', {
                'type': 'symbol_halt_not_found',
                'symbol': normalized,
                'source': source,
            })
    return
```

TG 订阅 `risk_alert.type ∈ {symbol_halt_cleared, symbol_halt_not_found}`，回显结果（同 `force_resume_cleared_symbol_halts` 模式）。

### 3.3 `/status` 增加 Per-symbol halt 行

复用 `_read_agent_health()`：

```python
# 在 _cmd_status 末尾或合适位置插入
health = self._read_agent_health() or {}
halts = health.get('halted_symbols', {})
if not halts:
    text += "\n─ Per-symbol halt: 0"
else:
    short_list = list(halts.keys())[:5]
    suffix = f" …+{len(halts) - 5}" if len(halts) > 5 else ""
    text += f"\n─ Per-symbol halt: {len(halts)} ({', '.join(short_list)}{suffix})"
```

### 3.4 边界条件

- TG 收到 `/resume_symbol XLM` 但全局 halted=true：`/resume_symbol` MUST NOT 改全局；user 还要单独 `/resume`
- TG 收到 `/resume_symbol XLM` 但 `_halted_symbols` 为空：`clear_symbol_halt` 返回 0，TG 收到 `symbol_halt_not_found` 回显友好消息
- agent_health.json 缺失：`_read_agent_health()` 返回 None；`/halts` / `/status` 都降级为"health 文件缺失"

## 4. F-TG-003 `/pnl` `/pnl_id`

### 4.1 共用 helper

```python
def _resolve_pending_for_pnl_correction(
    self,
    filter_fn,  # callable(event_dict) -> bool
    label: str,  # 用于错误消息("symbol=XLM" / "event_id=abc-123")
) -> dict:
    """共享候选解析。返回 {status, candidates, error_msg}。"""
    if not self._ledger:
        return {"status": "error", "error_msg": "ledger 未初始化"}

    try:
        all_pending = self._ledger.find_pending_external_closes()
    except Exception as e:
        return {"status": "error", "error_msg": f"查询 pending 失败: {e}"}

    candidates = [ev for ev in all_pending if filter_fn(ev)]

    if len(candidates) == 0:
        return {"status": "not_found", "candidates": [],
                "error_msg": f"未找到 {label} 的活跃 pending external_close"}
    if len(candidates) > 1:
        return {"status": "multiple", "candidates": candidates,
                "error_msg": f"{label} 匹配 {len(candidates)} 条 pending,请用 /pnl_id 指定具体 event_id"}
    return {"status": "ok", "candidates": candidates}
```

### 4.2 `/pnl` 命令

```python
async def _cmd_pnl(self, args: list):
    if len(args) < 2:
        await self._send_message("用法: /pnl <SYMBOL> <NET_PNL_USDT> [reason]")
        return

    raw_sym = args[0]
    try:
        net_pnl = float(args[1])
    except ValueError:
        await self._send_message("用法: /pnl <SYMBOL> <NET_PNL_USDT> [reason]\nNET_PNL 必须是数字")
        return

    reason = " ".join(args[2:]) if len(args) > 2 else ""
    symbol = self._normalize_symbol_for_pnl(raw_sym)

    result = self._resolve_pending_for_pnl_correction(
        filter_fn=lambda ev: ev.get('symbol') == symbol,
        label=f"symbol={symbol}",
    )

    if result["status"] == "ok":
        await self._apply_pnl_correction(result["candidates"][0], net_pnl, reason)
    elif result["status"] == "multiple":
        eids = [ev.get('event_id', '?')[:8] for ev in result["candidates"]]
        await self._send_message(
            f"⚠️ {result['error_msg']}\n候选: {eids}\n用 /pnl_id <event_id> <NET_PNL>"
        )
    else:
        await self._send_message(f"❌ {result['error_msg']}")

async def _cmd_pnl_id(self, args: list):
    if len(args) < 2:
        await self._send_message("用法: /pnl_id <event_id> <NET_PNL_USDT> [reason]")
        return

    event_id = args[0]
    try:
        net_pnl = float(args[1])
    except ValueError:
        await self._send_message("用法: /pnl_id <event_id> <NET_PNL_USDT> [reason]\nNET_PNL 必须是数字")
        return

    reason = " ".join(args[2:]) if len(args) > 2 else ""

    result = self._resolve_pending_for_pnl_correction(
        filter_fn=lambda ev: ev.get('event_id') == event_id,
        label=f"event_id={event_id}",
    )

    if result["status"] == "ok":
        await self._apply_pnl_correction(result["candidates"][0], net_pnl, reason)
    else:
        # event_id 唯一,不可能 multiple,只可能 not_found 或 error
        await self._send_message(f"❌ {result['error_msg']}")
```

### 4.3 `_apply_pnl_correction`

```python
async def _apply_pnl_correction(self, pending_ev: dict, net_pnl: float, reason: str):
    """根据 pending event 写 manual correction。"""
    resolution = {
        "pnl_status": "final",
        "pnl_source": "manual_tg_review",
        "symbol": pending_ev.get('symbol', ''),
        "side": pending_ev.get('side', ''),
        "position_id": pending_ev.get('position_id', ''),
        "entry_request_id": pending_ev.get('entry_request_id', ''),
        "realized_pnl_net_usdt": net_pnl,
        "estimated_pnl": pending_ev.get('estimated_pnl', 0),
        "gross_close_pnl_usdt": net_pnl,  # manual:无 fills,直接用 net 当 gross
        "fee_usdt": 0.0,
        "funding_usdt": 0.0,
        "order_ids": [],
        "bill_ids": [],
        "match_confidence": 1.0,
        "warnings": ["manual_pnl_correction"],
        "close_match_key": pending_ev.get('close_match_key', ''),
        "close_cause": "manual_close",
        "final_close_cause": "manual_close",
        "is_strategy_stop": False,
        "close_evidence": {},
        "manual_correction_reason": reason or "tg_user_review",
        # 透传保护单字段(若 pending 含)
        "sl_algo_id": pending_ev.get('sl_algo_id', ''),
        "sl_algo_clord_id": pending_ev.get('sl_algo_clord_id', ''),
        "tp_algo_id": pending_ev.get('tp_algo_id', ''),
        "tp_algo_clord_id": pending_ev.get('tp_algo_clord_id', ''),
        "entry_attribution": pending_ev.get('entry_attribution', {}),
    }
    correction = self._ledger.apply_pnl_resolution(resolution)
    if correction:
        sym = pending_ev.get('symbol', '?')
        await self._send_message(
            f"✅ PnL correction 已写入\n"
            f"symbol: {sym}\n"
            f"net_pnl: {net_pnl:+.4f} USDT\n"
            f"supersedes: {pending_ev.get('event_id', '')[:8]}\n"
            f"new event: {correction.get('event_id', '')[:8]}"
        )
    else:
        await self._send_message(
            f"⚠️ apply_pnl_resolution 返回 None(可能已 superseded);未写新 correction"
        )
```

### 4.4 边界条件

- `apply_pnl_resolution` 内部去重：相同 `position_id + close_match_key + sorted(order_ids)` 命中已有 correction → 返回 existing；TG 用户得到友好回显（`new event` 与 existing event_id 一致）
- 跨 symbol 用 event_id：如果用户拿了 ETH 的 event_id 但写到 `/pnl_id`，filter_fn 按 event_id 命中 ETH 的 pending；这是合法行为（用户知道自己在做什么），不强制做 symbol 校验
- `_ledger` 在 TG agent 实例化时通过 `agents/trading/multi_executor.py` 共享创建——确认实际现状

## 5. F-TG-004 `/status` agent health（Orchestrator 独写）

### 5.1 状态路径配置

`utils/state_paths.py` 加 `agent_health: str` 字段：

```python
@dataclass(frozen=True)
class StatePaths:
    namespace: str
    positions: str
    risk_state: str
    riskguard_state: str
    halt_state: str
    live_order_events: str
    live_position_lifecycle: str
    agent_health: str  # F-TG-004 新加

    @classmethod
    def for_namespace(cls, namespace: Optional[str] = None) -> 'StatePaths':
        ns = _resolve_namespace(namespace)
        p = _prefix(ns)
        return cls(
            namespace=ns,
            positions=f'data/{p}positions.json',
            risk_state=f'data/{p}risk_state.json',
            riskguard_state=f'data/{p}riskguard_state.json',
            halt_state=f'data/{p}halt_state.json',
            live_order_events=f'data/{p}live_order_events.jsonl',
            live_position_lifecycle=f'data/{p}live_position_lifecycle.json',
            agent_health=f'data/{p}agent_health.json',  # F-TG-004
        )

    def as_banner_lines(self) -> list:
        ...
        # 加一行 agent_health
```

### 5.2 MultiExecutor publish halts_snapshot

`agents/trading/executor.py:MultiExecutor` 增加 publish helper 与 tick 调用：

```python
async def _publish_halts_snapshot(self):
    """F-TG-004: 周期性 publish halts_snapshot 供 Orchestrator 写 agent_health.json。"""
    try:
        halts = self.executor.get_halted_symbols()
        await self.publish('halts_snapshot', {
            'halted_symbols': halts,
            'ts': time.time(),
        })
    except Exception as e:
        self.logger.warning(f"[HaltsSnapshot] publish 失败: {e}")
```

调用位置：`_run_reconciliation` 末尾（已有 ≤30s 周期），或 `tick` 内独立计时器。

### 5.3 Orchestrator 订阅 + 周期写

`agents/orchestrator.py` 加 `agent_health` 写入：

```python
class Orchestrator:
    def __init__(self):
        ...
        self._latest_halts_snapshot: Dict[str, dict] = {}
        self._last_health_write = 0.0
        self._health_write_interval = 30.0  # 秒

    # 在 message bus 订阅注册时加 halts_snapshot
    def _setup_bus(self):
        self.bus.register('orchestrator', ['halts_snapshot'])

    async def _on_message(self, msg: dict):
        if msg['type'] == 'halts_snapshot':
            self._latest_halts_snapshot = msg.get('payload', {}).get('halted_symbols', {})

    async def _tick(self):
        """已有 tick 循环加 health.json 写入。"""
        now = time.time()
        if now - self._last_health_write >= self._health_write_interval:
            self._write_agent_health()
            self._last_health_write = now

    def _write_agent_health(self):
        try:
            from utils.state_paths import get_state_paths
            from utils.atomic_io import atomic_write_json
            from agents.message_bus import MessageBus

            tasks_alive = sum(1 for t in self._tasks if not t.done())
            tasks_failed = sum(
                1 for t in self._tasks
                if t.done() and t.exception() is not None
            )
            agents_registered = len(self._research_agents) + len(self._trading_agents)

            try:
                bus = MessageBus.get_instance()
                dlq_size = len(getattr(bus, '_dlq', []))
            except Exception:
                dlq_size = 0

            health = {
                'ts': time.time(),
                'agents_registered': agents_registered,
                'tasks_alive': tasks_alive,
                'tasks_failed': tasks_failed,
                'halted_symbols': self._latest_halts_snapshot,
                'bus_dlq_size': dlq_size,
            }
            path = get_state_paths().agent_health
            atomic_write_json(path, health)
        except Exception as e:
            self.logger.warning(f"[AgentHealth] 写入失败: {e}")
```

### 5.4 `/status` 增强字段

```python
async def _cmd_status(self):
    # ...现有代码...

    health = self._read_agent_health() or {}
    if health:
        agents_registered = health.get('agents_registered', '?')
        tasks_alive = health.get('tasks_alive', '?')
        tasks_failed = health.get('tasks_failed', 0)
        dlq = health.get('bus_dlq_size', 0)
        text += f"\n─ Agents: {agents_registered} 注册 / {tasks_alive} 任务存活 / {tasks_failed} 异常"
        text += f"\n─ Bus DLQ: {dlq}"

        halts = health.get('halted_symbols', {})
        if not halts:
            text += "\n─ Per-symbol halt: 0"
        else:
            short_list = list(halts.keys())[:5]
            suffix = f" …+{len(halts) - 5}" if len(halts) > 5 else ""
            text += f"\n─ Per-symbol halt: {len(halts)} ({', '.join(short_list)}{suffix})"
    else:
        text += "\n─ Health: ?（health 文件缺失）"
```

### 5.5 边界条件

- Orchestrator 启动后还没收到 `halts_snapshot` 事件：`_latest_halts_snapshot = {}`，写出的 health.halted_symbols 为空 dict（不阻塞）
- atomic_write_json 失败：`_write_agent_health` 内 try/except 吞掉，logger.warning，下个 tick 重试
- MultiExecutor 没启动：Orchestrator 仍写 health（halted_symbols=空），其他字段正常

## 6. 测试策略

### 6.1 测试矩阵

| 文件 | 关键 case |
|---|---|
| `test_tg_symbol_halt_control.py` | `clear_symbol_halt` 4 case + `get_halted_symbols` 浅拷贝；`_handle_resume` 三分支清；`force_resume` 清+audit warning+publish risk_alert；TG 订阅 force_resume_cleared_symbol_halts 回显；TG agent 不持有 root executor 引用（实例属性扫描）；`/halts` 输出 3 case；`/resume_symbol` via bus 4 case；`_format_elapsed` helper 单测 |
| `test_tg_pnl_correction.py` | helper `_resolve_pending_for_pnl_correction` 3 状态；`/pnl` 6 case；`/pnl_id` 4 case；幂等 2 case；reason 字段 2 case |
| `test_tg_status_enhancement.py` | `state_paths.agent_health` namespace 派生；MultiExecutor publish halts_snapshot；Orchestrator 订阅缓存；Orchestrator `_write_agent_health` schema；`_write_agent_health` 失败不阻塞；`/status` 含三新行 + halts 截断展示 + health 缺失降级 |

预期新增 case ≥ 35。

### 6.2 回归基线

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_tg_pycache python3 -m compileall -q .
python3 -m pytest -q  # 期望 860 → ≥ 895
python3 -m pytest -q -m network  # 仍 4 PASS
```

### 6.3 Mock TG run（人工验收）

启动后从本地 mock 一遍命令链：
1. `/halts` → 应回 "无 per-symbol halt"（前提：当前 _halted_symbols 为空）
2. 注入一个 mock per-symbol halt（人工调 root executor `_halt_symbol("TEST-USDT-SWAP", reason="manual_test")`）
3. `/halts` → 应列出 TEST-USDT-SWAP
4. `/resume_symbol TEST` → 应回 "✅ 已解除"
5. `/halts` → 应回 "无"
6. `/status` → 应含 Agents / Bus DLQ / Per-symbol halt 三行
7. `/pnl` 与 `/pnl_id` 在 mock pending 上验证写 correction

## 7. 风险与回滚

| 风险 | Mitigation |
|---|---|
| `_halted_symbols` 跨线程访问 race | dict ops GIL 原子；最坏情况是清后立即被新 SL 失败重新加，正确行为 |
| Orchestrator 写 health.json 失败 | try/except + logger.warning + 不阻塞 tick；TG 端读失败时降级文案 |
| TG 订阅 risk_alert 数量增加 | 新增 3 种 alert_type（symbol_halt_cleared / symbol_halt_not_found / force_resume_cleared_symbol_halts）；critical_types allowlist 加这 3 种 |
| `_resolve_pending_for_pnl_correction` 找不到 ledger | 显式 error 分支 + 友好回显 |
| `/pnl_id` 跨 symbol event_id 误用 | 接受用户责任；correction event 内 symbol 字段来自原 pending，不会写错 |

回滚：4 个 capability 各自独立 commit，回滚单个不影响其余；新命令对老用户是加法，不影响现有 `/status` `/resume` 行为。

## 8. 实施顺序与里程碑

按依赖关系（不是风险递增）：

1. **F-TG-001**（root API + agent resume 改造）：基础设施，其他都依赖
2. **F-TG-004**（state_paths + Orchestrator 写 health + MultiExecutor publish）：基础设施 #2
3. **F-TG-002**（TG `/halts` / `/resume_symbol` / `/status` 行）：依赖 F-TG-001 的 API 与 F-TG-004 的 health.json
4. **F-TG-003**（`/pnl` `/pnl_id`）：独立，可与上面并行
5. 全量回归 + 人工 mock TG run + 文档同步

## 9. Open Questions（已闭合）

- `/resume_symbol` 是否走 bus？→ **走**（agent 隔离）
- `force_resume` 是否清 per-symbol halt？→ **清 + audit warning + TG 回显**
- `/pnl_id` 是否本 change 一起做？→ **一起做**
- agent_health.json 由谁写？→ **Orchestrator 独写 + MultiExecutor publish halts_snapshot**
- `_halted_symbols` 是否持久化磁盘？→ **不**（重启清零 by design）
