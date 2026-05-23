# 回撤基准修正验收文档

更新日期：2026-05-23  
关联 PRD：`docs/drawdown_baseline_prd.md`

## 1. 验收结论规则

| 结论 | 条件 |
|---|---|
| PASS | P1 项全部通过，旧状态兼容、启动基准、cap 口径、close 绕过和 paper 隔离均验证 |
| CONDITIONAL PASS | P1 全通过，P2 有非阻断增强项未完成 |
| FAIL | 任一 P1 失败，或 live 仍会把外部转出误判为回撤 |

当前状态：已实现，PASS。14 项自动化验收测试全部通过（test_drawdown_baseline.py）。

## 2. 验收前置条件

- 不使用真实下单接口做自动化验收。
- OKX 真实余额查询只能用于只读核对，不作为自动化测试必需条件。
- 测试必须隔离 `data/`，不得污染生产 `data/risk_state.json`。
- PaperExecutor 验收不得访问交易所。

## 3. 自动化验收命令

```bash
python3 -m pytest -q test_risk_manager.py test_logical_account_split.py test_paper_live_isolation.py test_paper_executor.py test_executor_terminal_result.py
python3 -m pytest -q
```

建议新增测试文件：

```bash
python3 -m pytest -q test_drawdown_baseline.py
```

## 4. 验收矩阵

| ID | 优先级 | 验收项 | 构造条件 | 通过标准 |
|---|---|---|---|---|
| AC-01 | P1 | 旧 peak 不误杀启动 | 旧 `risk_state.json` 写入 `peak_balance=6268.64`，启动余额 `4864.46`，cap `300` | `initialize_session()` 后 `session_peak_equity=300`，`check_can_open()` 通过 |
| AC-02 | P1 | cap 进入 live 回撤口径 | 真实余额 `4864.46`，`EFFECTIVE_BALANCE_CAP=300` | `risk_equity=300`，不是 `4864.46` |
| AC-03 | P1 | 本轮回撤正常触发 | baseline `300`，当前风险权益 `239` | `drawdown=20.33%`，拒绝新开仓 |
| AC-04 | P1 | 本轮盈利更新 peak | baseline `300`，后续风险权益 `330` | `session_peak_equity=330` |
| AC-05 | P1 | 外部转出不影响重启后基准 | 第一次 peak `6268`，重启时余额 `4864` | 默认 `session_start` 模式重置本轮基准 |
| AC-06 | P1 | persisted_peak 兼容旧行为 | `DRAWDOWN_BASELINE_MODE=persisted_peak` | 继续使用持久化峰值，文档和日志明确这是兼容模式 |
| AC-07 | P1 | close 不被最大回撤挡住 | 当前 drawdown 超 20%，action=`close` | Executor 不调用新增风险检查，允许进入平仓逻辑 |
| AC-08 | P1 | reduce 不被最大回撤挡住 | 当前 drawdown 超 20%，action=`reduce` 或 PA 减仓 | 允许执行减仓 |
| AC-09 | P1 | open/add 被最大回撤挡住 | 当前 drawdown 超 20%，action=`open_long/open_short/add` | 拒绝新增风险，reason 包含 drawdown/risk_equity/peak |
| AC-10 | P1 | PaperExecutor 隔离 | mock exchange 若被调用则抛错 | PaperExecutor setup/open/close 不触发交易所和 `risk_state` 写入 |
| AC-11 | P1 | LiveLedger daily pnl 不混入 paper | 写入 paper 和 live 两类事件 | `daily_realized_pnl(exclude_paper=True)` 只统计 live |
| AC-12 | P1 | 启动日志可解释 | 启动 Executor | 日志含 `[RiskBaseline] real_total/free/cap/risk_equity/mode/peak` |
| AC-13 | P1 | 状态文件 v2 兼容 | 从旧 state 启动后保存 | 新 state 含 `schema_version=risk_state.v2`，旧 `peak_balance` 保留到 `legacy_peak_balance` |
| AC-14 | P2 | 运行中外部转账提示 | 运行中 total 大幅变化且无 live ledger 盈亏 | 记录 warning，不作为本阶段阻断 |

## 5. 关键测试样例

### Case 1：用户转出资金后重启

输入：

```json
{
  "old_state": {
    "peak_balance": 6268.638370790319,
    "daily_pnl": 0.0,
    "last_reset_date": "2026-05-21"
  },
  "real_total_balance": 4864.4597523186985,
  "effective_balance_cap": 300.0,
  "max_drawdown_pct": 20.0
}
```

期望：

```json
{
  "risk_equity": 300.0,
  "session_baseline_equity": 300.0,
  "session_peak_equity": 300.0,
  "can_open": true
}
```

不得出现：

```text
已达最大回撤限制 20.0%
```

### Case 2：本轮真实回撤触发

输入：

```json
{
  "session_peak_equity": 300.0,
  "current_risk_equity": 239.0,
  "max_drawdown_pct": 20.0
}
```

期望：

```text
open rejected: drawdown=20.33%
```

### Case 3：回撤状态下仍允许平仓

输入：

```json
{
  "drawdown_pct": 25.0,
  "action": "close",
  "symbol": "BTC-USDT"
}
```

期望：

- 不调用 `check_can_open()`。
- 发布或进入 `close_position()` 流程。
- 不返回 `已达最大回撤限制`。

## 6. 只读现场核对

可选命令，用于人工确认当前 live 资金口径：

```bash
python3 - <<'PY'
from dotenv import load_dotenv
load_dotenv('.env')
import os, ccxt
from utils.balance_adapter import BalanceAdapter
ex = ccxt.okx({
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET'),
    'password': os.getenv('OKX_PASSWORD'),
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'},
})
free, total = BalanceAdapter._parse(ex.fetch_balance())
print({'free': free, 'total': total})
PY
```

当前人工观测值：

```text
USDT_free=4864.4597523186985
USDT_total=4864.4597523186985
EFFECTIVE_BALANCE_CAP=300
```

live 风控目标口径：

```text
risk_equity = min(4864.4597523186985, 300) = 300
```

## 7. Go/No-Go

| 条件 | Go 标准 |
|---|---|
| 启动基准 | 默认 `session_start` 下不继承旧账户 peak |
| cap 口径 | live Executor、Judge、PaperExecutor 对 `EFFECTIVE_BALANCE_CAP` 解释一致 |
| 新增风险检查 | 只拦 open/add |
| 退出风险动作 | close/reduce/force close 不被最大回撤阻止 |
| 状态兼容 | 旧 `risk_state.json` 可读，新 v2 state 可写 |
| 测试 | 新增 `test_drawdown_baseline.py` 和相关回归通过 |

全部满足后，才允许恢复 live 小额验证；否则应保持 paper/testnet 或手动暂停 live executor。
