# 审计整改验收文档

更新日期：2026-05-23  
关联 PRD：`docs/audit_remediation_prd.md`  
关联审计报告：`docs/generated_reports/系统性审计报告_20260523.md`  

## 1. 验收结论规则

| 结论 | 条件 |
|---|---|
| PASS | 所有 P0/P1 验收项通过，P2 无 live 阻断，OKX testnet 关键语义通过 |
| CONDITIONAL PASS | P0/P1 全过，仅 P2 遗留，且有 owner、风险说明和回归保护 |
| FAIL | 任一 P0 失败；或任一 P1 失败且无明确豁免；或 OKX testnet 未执行/失败且影响交易安全 |

当前状态：FAIL。原因：审计 P0 尚未修复，新增 P1 中 exchange sandbox、对账恢复、contractSize、依赖锁定和旧入口收敛尚未完成。

## 2. 验收前置条件

- 禁止使用 production key 执行 testnet 验收。
- `.env` 不得提交仓库。
- 默认自动化测试不得依赖真实交易所网络。
- OKX testnet 验收必须确认 `set_sandbox_mode(True)` 生效。
- 验收前不得同时运行旧 `main.py`、`live_trading.py` 和 `run_agents.py`。
- 验收前备份生产 `data/*.json`，状态迁移测试不得污染真实状态。

## 3. 自动化验收命令

基础编译：

```bash
python3 -m py_compile run_agents.py agents/*.py agents/research/*.py agents/trading/*.py utils/*.py core/*.py *.py
```

全量回归：

```bash
python3 -m pytest -q
```

重点定向回归建议：

```bash
python3 -m pytest -q \
  test_phase2_bucketed_ev.py \
  test_execution_result_contract.py \
  test_executor_terminal_result.py \
  test_request_id_flow.py \
  test_reconciliation.py \
  test_p1m_order_caps.py \
  test_llm_schema.py \
  test_p1k_message_bus.py \
  test_okx_support.py
```

配置和入口检查：

```bash
python3 - <<'PY'
from utils.config_loader import load_config, format_banner
cfg = load_config(strict_live_check=False)
for k in [
    "use_testnet",
    "max_trade_amount",
    "phase2_bucketed_ev_enabled",
    "phase2_signal_confidence_split_enabled",
]:
    assert k in cfg, k
print(format_banner(cfg))
PY

grep -n "python3 run_agents.py" start.sh
! grep -n "python3 main.py" start.sh
```

执行回参契约扫描：

```bash
rg -n 'publish\\("execution_result"' agents/trading/executor.py
```

通过标准：

- 编译无错误。
- 全量 pytest 无失败。
- `start.sh` 不再启动 `main.py`。
- 所有 execution_result 发布点要么在 helper 内部，要么调用 helper 生成 payload。

## 4. P0 验收项

| ID | 关联需求 | 验收项 | 方法 | 通过标准 |
|---|---|---|---|---|
| AC-P0-001 | FR-001 | `_build_plan(open_short)` 输出 short side | 直接调用 `_build_plan(tech, "open_short", ...)` | plan 含 `side="short"` |
| AC-P0-002 | FR-001 | short bucket 命中正确 | 构造 long/short 两组 bucket metrics | short plan 只使用 `short_*` 或 `side_short` |
| AC-P0-003 | FR-001 | legacy 缺 side 不静默落 long | 构造无 side plan | 走显式 fallback 或拒绝，日志可见，不误用 long bucket |
| AC-P0-004 | FR-002 | Telegram resume 不直接最终改 HaltState | mock Telegram `/resume` | Telegram 发请求或带 reconciliation result，不直接绕过 owner |
| AC-P0-005 | FR-002 | Executor 是恢复状态 owner | 发送 `system_command: resume` | Executor 调用 `confirm_resume/force_resume` 并更新本地 `_trading_halted` |
| AC-P0-006 | FR-002 | 对账失败保持 halted | mock exchange mismatch | `HaltState.halted=True`，`can_open_new=False` |
| AC-P0-007 | FR-002 | 状态文件损坏 fail-closed | 写入坏 `data/halt_state.json` | 启动后 halted，reason 可见，不允许新开仓 |
| AC-P0-008 | FR-003 | early reject 统一契约 | 构造 halted、low_confidence、balance_fail、risk_reject | 每个 payload 含 v2 字段全集 |
| AC-P0-009 | FR-003 | exception/None result 统一契约 | mock 底层 executor 抛异常和返回 None | status 为 `error/rejected`，含 `source/reason/timestamp/result` |
| AC-P0-010 | FR-003 | 下游兼容 | 将新旧 execution_result 喂给 Reviewer/RiskGuard/Telegram | 不抛 KeyError，核心字段被记录 |

P0 Go 标准：AC-P0-001 至 AC-P0-010 全部通过。

## 5. P1 验收项

| ID | 关联需求 | 验收项 | 方法 | 通过标准 |
|---|---|---|---|---|
| AC-P1-001 | FR-004 | exchange factory 设置 sandbox | mock ccxt exchange，`use_testnet=True` | 所有调用路径调用 `set_sandbox_mode(True)` |
| AC-P1-002 | FR-004 | Judge 不读 live 余额 | `use_testnet=True` 启动 Judge setup | 使用 sandbox exchange；测试证明不是 live client |
| AC-P1-003 | FR-004 | Telegram 对账使用 sandbox | mock `/reconcile` 或 `_run_reconciliation()` | factory 参数含 `use_testnet=True` |
| AC-P1-004 | FR-002 | 恢复对账包含 RiskGuard | 构造 `riskguard_state.json` 幽灵持仓 | resume 被 blocking issue 阻止 |
| AC-P1-005 | FR-002 | paper mismatch 不阻塞 | live 与 paper 方向不同，exchange/executor/riskguard 一致 | status 可 PASS，advisory issues 记录 paper mismatch |
| AC-P1-006 | FR-005 | orderbook depth 使用 contractSize | BTC contractSize=0.01、DOGE=1000 | notional 与手算一致 |
| AC-P1-007 | FR-005 | liquidation vol 使用 contractSize | mock liquidation orders | long/short vol_usd 与手算一致 |
| AC-P1-008 | FR-005 | slippage depth 使用 contractSize | mock ccxt orderbook + market metadata | depth_usdt 与合约面值一致 |
| AC-P1-009 | FR-006 | start.sh 入口正确 | 执行 grep 或 dry-run | 不再调用 `python3 main.py` |
| AC-P1-010 | FR-006 | 旧入口不会误用 | 执行 `python3 main.py` 或检查 main guard | 明确提示 deprecated/归档，不进入交易循环 |
| AC-P1-011 | FR-007 | Research LLM schema | LLM 返回缺字段/错类型 | schema 默认值生效，validation_errors 有记录 |
| AC-P1-012 | FR-007 | Censor/Tech/Critic schema | 构造坏 JSON 或越界字段 | 不破坏主链路，降级可见 |
| AC-P1-013 | FR-008 | critical event journal 落盘 | 发布 trade_decision/execution_result/system_command/risk_alert | JSONL 有记录，含 topic/msg_id/timestamp |
| AC-P1-014 | FR-008 | journal 可重放 | 用同 request_id 的 open/close 事件 | replay 输出完整生命周期 |
| AC-P1-015 | FR-009 | 依赖可复现 | 存在 lock file 并从 lock 安装 | CI/本地安装版本一致 |
| AC-P1-016 | FR-009 | ccxt 升级门控 | 修改 ccxt 版本触发验收流程 | 文档要求 OKX 语义验收，不能静默升级 |

P1 Go 标准：AC-P1-001 至 AC-P1-016 全部通过，或有项目负责人签字的非阻断豁免。

## 6. P2 验收项

| ID | 关联需求 | 验收项 | 方法 | 通过标准 |
|---|---|---|---|---|
| AC-P2-001 | FR-010 | `data_alert` 有消费者 | 连续采集失败 | Telegram/risk_alert/health 至少一处可见 |
| AC-P2-002 | FR-010 | MessageBus 记录无人订阅重要 topic | 发布无人订阅 `data_alert` | metrics 或 DLQ 可见 |
| AC-P2-003 | FR-010 | Telegram 发送不阻塞 command polling | mock 高频告警 + `/status` | 命令响应不被长时间阻塞 |
| AC-P2-004 | FR-010 | 日志有轮转或清理策略 | 检查 logger 配置/清理脚本 | 有 max size/days 或定时清理 |
| AC-P2-005 | FR-010 | LLM audit 有脱敏/保留策略 | 检查配置和写入内容 | 敏感字段不直接长期保留 |
| AC-P2-006 | FR-006 | 文档同步 | rg 旧入口和旧测试数 | 不再把 `main.py/live_trading.py` 作为生产入口 |

P2 不阻断小额 live 灰度，但必须在验收结论中列明剩余风险。

## 7. OKX Testnet 验收

### 7.1 环境要求

- `USE_TESTNET=true`
- 使用 OKX sandbox/testnet key。
- 验收脚本启动时打印 exchange sandbox 状态。
- 不允许 production key 出现在 testnet 环境。

### 7.2 手工验收表

| Case | 操作 | 必须记录 | 通过标准 |
|---|---|---|---|
| OKX-01 | market open + attached TP/SL | create order raw、attachAlgoOrds raw、position raw、execution_result | 成功开仓，TP/SL 条件单存在或语义可解释 |
| OKX-02 | limit open timeout | limit create raw、fetch_order raw、cancel raw | 超时后无残留挂单，系统输出 rejected/expired |
| OKX-03 | insufficient balance | error raw、normalized result | 不重试无限下单，输出 rejected/error 且 reason 清楚 |
| OKX-04 | min amount | precheck result、exchange error raw | 本地 precheck 能挡住或交易所错误被规范化 |
| OKX-05 | reduceOnly close | close raw、position after close | 不反向开仓，position 降低或归零 |
| OKX-06 | move SL / trailing | old algo state、new algo state | 旧保护单取消/失效，新保护单唯一有效，或明确使用本地兜底 |
| OKX-07 | add/reduce 后 SL/TP 生命周期 | before/after algo orders | 本地 positions 与交易所保护条件一致 |
| OKX-08 | duplicate clOrdId | 两次 create raw、final position | 不产生重复仓位，第二次有可解释终态 |
| OKX-09 | `/resume` reconciliation testnet | exchange positions、executor positions、riskguard positions | testnet 环境一致，mismatch 阻止恢复 |

OKX Go 标准：OKX-01 至 OKX-09 全部 PASS，或失败项明确为 testnet 限制且不影响 live 安全。

## 8. 状态文件迁移验收

| Case | 输入 | 通过标准 |
|---|---|---|
| STATE-01 | 正常 `halt_state.json` halted=true | 启动后仍 halted |
| STATE-02 | 损坏 `halt_state.json` | fail-closed，禁止新开仓 |
| STATE-03 | 正常 `risk_state.json` 含 daily_pnl | daily_pnl 保留或由 ledger 正确同步 |
| STATE-04 | 损坏 `risk_state.json` | 告警并进入 reconciliation required，不静默归零 |
| STATE-05 | `positions.json` 与 RiskGuard 不一致 | reconciliation blocking issue |
| STATE-06 | paper 与 live 不一致 | advisory issue，不阻塞恢复 |

## 9. 文档验收

必须同步以下文件：

- `README.md`
- `docs/runbook.md`
- `docs/development.md`
- `docs/architecture.md`
- `docs/handoff.md`
- `docs/待解决事项.md`
- `CLAUDE.md`

验收命令：

```bash
rg -n "python3 main.py|live_trading.py.*生产|373 passed|444 passed|184 passed|Phase 2.*<missing>" README.md docs CLAUDE.md
```

通过标准：

- 不再把 `main.py` 或 `live_trading.py` 描述为生产入口。
- 旧测试数只出现在历史记录上下文，不作为当前状态。
- `docs/待解决事项.md` 不再保留已修复但标为 blocked 的配置问题。
- 当前主入口明确为 `python3 run_agents.py`。

## 10. 最终 Go/No-Go 表

| 条件 | Go 标准 |
|---|---|
| P0 | 全部 AC-P0 通过 |
| P1 | 全部 AC-P1 通过，或仅有非阻断豁免 |
| 全量测试 | `python3 -m pytest -q` 无失败 |
| OKX testnet | OKX-01 至 OKX-09 通过 |
| 状态迁移 | STATE-01 至 STATE-06 通过 |
| 入口安全 | `start.sh` 和 docs 均指向 `run_agents.py` |
| live 安全 | `USE_TESTNET=true` 无路径读 live exchange |
| 文档 | 待解决事项无 P0/P1 BLOCKED |

结论规则：

- 任一 P0 失败：`FAIL`。
- 任一 live/testnet 边界失败：`FAIL`。
- 仅 P2 失败：`CONDITIONAL PASS`。
- 全部通过：`PASS`，允许进入“小额 live 灰度评审”，但不自动代表允许扩容。

## 11. 验收记录模板

```text
验收日期：
执行人：
代码版本/commit：
环境：local / paper / okx-testnet / live-small

自动化测试：
- py_compile：
- pytest：
- targeted tests：

P0 结果：
- AC-P0-001：
- ...

P1 结果：
- AC-P1-001：
- ...

OKX testnet：
- OKX-01：
- ...

剩余问题：
- ID：
- 严重度：
- 是否阻断：
- owner：
- 截止日期：

最终结论：PASS / CONDITIONAL PASS / FAIL
Go/No-Go：
```
