# 回撤基准修正产品需求文档

更新日期：2026-05-23  
关联问题：外部转出资金被误判为账户回撤，导致 live Executor 拒绝执行。

## 1. 背景

当前 `RiskManager` 使用 `data/risk_state.json` 中持久化的 `peak_balance` 作为最大回撤基准。用户手动从 OKX 账户转出资金后，真实账户余额下降，但这不是交易亏损。系统仍按旧峰值计算回撤，导致 `check_can_trade()` 返回“已达最大回撤限制 20%”，新开仓和平仓指令都可能被错误拒绝。

现有配置里 `EFFECTIVE_BALANCE_CAP=300` 已经用于 Judge 仓位预算和 PaperExecutor 初始影子权益，但 live Executor 的回撤检查仍使用真实账户总余额与历史峰值，不使用逻辑账户基准。

## 2. 产品目标

- 最大回撤基准应以“本次系统启动时可参与交易的资金”为准，而不是跨外部转账继承历史账户峰值。
- `EFFECTIVE_BALANCE_CAP` 应进入 live 风控基准计算，确保 live、Judge、paper 的资金口径一致。
- 外部转入/转出不应被当作策略盈亏。
- 回撤风控只限制新增风险：开仓、加仓；不得阻止减仓、平仓、强平和对账修正。
- PaperExecutor 继续保持纯影子账户，不查询交易所、不真实下单、不影响 live 风控状态。

## 3. 非目标

- 不改变交易策略评分、R:R 门槛、EV 门、LLM 提示词。
- 不把 paper 盈亏计入 live `daily_pnl` 或 live drawdown。
- 不实现完整资金流水归因系统；本阶段只要求启动时基准正确，运行中外部转账可作为后续增强。
- 不自动删除历史交易记录。

## 4. 当前问题路径

| 路径 | 当前行为 | 问题 |
|---|---|---|
| `risk_manager.py` | `_load_state()` 读取历史 `peak_balance`，`check_can_trade(balance)` 用 `(peak-balance)/peak` 算回撤 | 外部转出会被误判为交易亏损 |
| `agents/trading/executor.py` | `_get_balance()` 读取 OKX total，再传给 `risk_manager.check_can_trade()` | 未应用 `EFFECTIVE_BALANCE_CAP`，且 close 也先过开仓风控 |
| `executor.py` | `ContractExecutor` 初始化 `RiskManager(state_file='data/risk_state.json')` | 未提供本次启动基准初始化 |
| `utils/config_loader.py` | 已有 `effective_balance_cap` | 配置未传入 `RiskManager` 的回撤基准计算 |
| `agents/trading/judge.py` | `_calc_risk_budget()` 使用 `min(real_balance, effective_balance_cap)` | Judge 口径正确，但 Executor 风控未对齐 |
| `agents/trading/paper_executor.py` | 使用 `effective_balance_cap` 初始化影子账户 | 影子账户独立，不应影响 live |

## 5. 目标资金口径

### 5.1 定义

| 名称 | 含义 |
|---|---|
| `real_total_balance` | 交易所返回的真实 USDT total |
| `real_free_balance` | 交易所返回的真实 USDT free |
| `effective_balance_cap` | 逻辑账户上限，来自 `EFFECTIVE_BALANCE_CAP` |
| `risk_equity` | live 风控参与计算的权益，`min(real_total_balance, effective_balance_cap)`；cap 为空时等于 `real_total_balance` |
| `session_baseline_equity` | 本次系统启动时扫描得到的 `risk_equity` |
| `session_peak_equity` | 本次系统运行期间的 `risk_equity` 峰值 |
| `drawdown_pct` | `(session_peak_equity - risk_equity) / session_peak_equity * 100` |

### 5.2 示例

当前真实账户：

- OKX USDT total/free：`4864.46`
- `EFFECTIVE_BALANCE_CAP=300`
- 本次 live 风控 `risk_equity=300`
- 本次启动基准 `session_baseline_equity=300`
- 正常主仓单笔保证金：`min(300 * 10%, 30)=30`
- 主仓最大并发 3 个，主路径最多占用约 `90U` 保证金

历史 `peak_balance=6268.64` 不应继续影响本次启动后的回撤判断。

## 6. 功能需求

### FR-01 启动时初始化回撤基准

`MultiExecutor` / `ContractExecutor` 初始化完成后必须读取一次真实账户余额，并初始化本次风控基准。

建议接口：

```python
RiskManager.initialize_session(
    real_total_balance: float,
    effective_balance_cap: float | None,
    baseline_mode: str = "session_start",
) -> None
```

行为：

- 计算 `risk_equity = min(real_total_balance, effective_balance_cap)`，cap 为空时用真实余额。
- 当 `baseline_mode="session_start"` 时，`session_baseline_equity` 和 `session_peak_equity` 设为当前 `risk_equity`。
- 保存状态到 `data/risk_state.json`。
- 启动日志必须打印真实余额、cap、risk equity、baseline mode。

### FR-02 回撤检查使用 risk equity

`check_can_trade()` 不应直接用真实 total 与历史峰值比较。它应接收或内部计算 `risk_equity`。

建议接口：

```python
RiskManager.check_can_open(
    real_total_balance: float,
    effective_balance_cap: float | None = None,
) -> tuple[bool, str]
```

要求：

- 使用 `risk_equity` 更新 `session_peak_equity`。
- 用 `session_peak_equity` 计算回撤。
- 余额不足判断仍需检查真实 `free` 是否足够覆盖计划保证金。
- 返回 reason 中应包含关键数值，便于日志判断：`risk_equity`、`session_peak_equity`、`drawdown_pct`。

### FR-03 close/reduce 绕过新增风险风控

`agents/trading/executor.py` 处理 `trade_decision` 时，只有以下动作需要经过最大回撤和每日亏损的“新增风险”检查：

- `open_long`
- `open_short`
- PositionAnalyst 触发的加仓

以下动作不得被最大回撤风控阻止：

- `close`
- `reduce`
- `emergency_close`
- `flash_move`
- `position_danger`
- `_close_all_positions()`
- 对账发现外部平仓后的同步事件

### FR-04 状态文件结构升级

`data/risk_state.json` 应保留旧字段并新增字段。旧字段用于兼容，不再作为默认回撤基准。

目标结构：

```json
{
  "schema_version": "risk_state.v2",
  "baseline_mode": "session_start",
  "session_started_at": 1770000000.0,
  "real_total_at_start": 4864.4597,
  "effective_balance_cap": 300.0,
  "session_baseline_equity": 300.0,
  "session_peak_equity": 300.0,
  "current_risk_equity": 300.0,
  "daily_pnl": 0.0,
  "last_reset_date": "2026-05-23",
  "legacy_peak_balance": 6268.6383
}
```

兼容要求：

- 读取旧 `risk_state.json` 时不得崩溃。
- 旧 `peak_balance` 可迁移到 `legacy_peak_balance`。
- 如果新字段缺失，启动时必须重新初始化 session baseline。

### FR-05 配置项

新增或明确以下配置：

| 配置 key | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `drawdown_baseline_mode` | `DRAWDOWN_BASELINE_MODE` | `session_start` | 默认按本次启动资金为回撤基准 |
| `effective_balance_cap` | `EFFECTIVE_BALANCE_CAP` | 当前已有 | live 风控、Judge、paper 统一使用 |
| `reset_risk_baseline_on_start` | `RESET_RISK_BASELINE_ON_START` | `true` | 启动时重置本轮回撤基准 |

允许值：

- `session_start`：默认。启动时读取账户余额并重置本轮基准。
- `persisted_peak`：兼容旧行为，仅手动审计场景使用。

### FR-06 启动日志与可观测性

启动时必须在 `orchestrator` 或 `executor` 日志打印：

```text
[RiskBaseline] real_total=4864.46 free=4864.46 cap=300.00 risk_equity=300.00 mode=session_start peak=300.00
```

当发生风控拒绝时，日志应包含：

```text
[RiskManager] open rejected: drawdown=21.0% risk_equity=237.0 peak=300.0 mode=session_start
```

### FR-07 PaperExecutor 隔离

PaperExecutor 必须保持以下约束：

- 不调用 `exchange.fetch_balance()`。
- 不调用任何下单接口。
- 不读写 `data/risk_state.json`。
- 只读写 `data/paper_equity.json`、`data/paper_positions.json`、`data/paper_trades.jsonl`。

## 7. 接口与路径清单

| 模块 | 需要调整的接口/函数 | 目标行为 |
|---|---|---|
| `utils/config_loader.py` | `DEFAULTS`、`_read_env_overrides()`、`format_banner()` | 增加 baseline 配置和启动展示 |
| `risk_manager.py` | `__init__()` | 接收 cap/baseline mode，兼容旧 state |
| `risk_manager.py` | `initialize_session()` | 启动时设置本轮风险基准 |
| `risk_manager.py` | `check_can_open()` | 用 `risk_equity/session_peak_equity` 判断新增风险 |
| `risk_manager.py` | `check_can_trade()` | 保留兼容，可委托到 `check_can_open()` 或标记 deprecated |
| `executor.py` | `ContractExecutor.__init__()` | 初始化 BalanceAdapter 后调用 `initialize_session()` |
| `agents/trading/executor.py` | `_execute_decision()` | open/add 才调用新增风险检查，close/reduce 绕过 |
| `agents/trading/executor.py` | `_get_balance()` | 继续返回真实 total，但不得直接作为 drawdown 基准 |
| `agents/trading/paper_executor.py` | 无行为变更 | 验证不触达 live risk state |
| `utils/live_ledger.py` | `daily_realized_pnl()` | 继续排除 paper 事件 |

## 8. 风险与回滚

- 风险：启动即重置基准可能掩盖此前真实亏损。缓解：`daily_pnl` 仍从 LiveLedger 同步，连续亏损和每日亏损不重置。
- 风险：运行中外部转出仍可能造成风险权益下降。缓解：本阶段文档明确只修启动基准；后续可增加资金流水归因。
- 回滚：设置 `DRAWDOWN_BASELINE_MODE=persisted_peak` 可恢复旧行为。

## 9. 完成标准

- 旧 `risk_state.json` 中 `peak_balance=6268.64` 且当前真实余额 `4864.46`、cap `300` 时，启动后新开仓不因历史 peak 被拒。
- 如果本轮 `risk_equity` 从 `300` 跌到 `239`，应触发 20% 最大回撤。
- close/reduce 在任何回撤状态下都不被新增风险风控拦截。
- PaperExecutor 不影响 live 风控。
