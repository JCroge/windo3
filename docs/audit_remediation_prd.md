# 审计整改产品需求文档

更新日期：2026-05-24  
关联审计报告：`docs/generated_reports/系统性审计报告_20260524.md`  
目标阶段：从“本地/paper/mock 可继续迭代”推进到“testnet 稳定、小额 live 灰度可评审”  

## 1. 背景

2026-05-23 系统性审计确认：当前项目已经形成 Research/Trading 两层多 Agent 架构，工程底座可以继续推进。2026-05-24 复核结果为 `493 passed / 4 deselected / 1 warning`，FR-001~FR-010 的自动化整改已完成；当前阻断 live 扩容的关键风险收敛为 OKX 真实 testnet 语义验收未执行，以及少量 P1/P2 契约和观测收敛项。

本 PRD 将审计项 F-001 至 F-026 转换为可排期的产品需求与技术实施路径。目标不是新增策略收益功能，而是提高系统在真实交易环境下的可控性、可恢复性、可追踪性和可验收性。

## 2. 产品目标

1. 消除 P0 阻断项，确保 open 决策、熔断恢复和执行回参在 live 前具备确定性。
2. 统一交易所 client 创建与 sandbox/live 语义，避免 testnet 与 live 数据混用。
3. 确保所有关键交易事件可追踪、可复盘、可重放，降低消息丢失和字段漂移风险。
4. 修正会影响信号质量或风控判断的关键数据换算问题。
5. 收敛旧入口、旧文档和旧配置，避免运维跑错系统。
6. 建立清晰 Go/No-Go 准入标准：未满足 P0/P1 验收前，不允许扩大 live 仓位或并发。

## 3. 非目标

- 不优化策略参数、收益曲线、LLM prompt 风格或交易频率。
- 不新增交易所。
- 不重构为分布式系统。
- 不引入重型工作流引擎替代当前单进程 Agent 架构。
- 不把 paper/mock 结果等同于 OKX testnet 或 live 语义验证。

## 4. 用户与场景

| 角色 | 核心诉求 |
|---|---|
| 项目负责人 | 明确哪些问题阻断 live，哪些可以延后，避免凭感觉扩容。 |
| 开发者 | 能按工作包逐项修复，有明确代码位置、测试和验收标准。 |
| 运维者 | 只需执行一个正确入口，能判断当前是 testnet 还是 live。 |
| Reviewer/RiskGuard/Judge | 能稳定消费统一契约，不因缺字段或错字段产生错误复盘。 |
| Telegram 操作者 | `/halt`、`/resume`、`/reconcile` 的含义清晰，恢复交易前状态一致。 |

## 5. 优先级定义

| 优先级 | 定义 | Go/No-Go 影响 |
|---|---|---|
| P0 | 直接影响错误开仓、错误恢复、执行回参不一致的阻断项 | 未完成不得扩大 live；建议仅 paper/mock/testnet |
| P1 | 影响 testnet/live 安全边界、数据质量、可复现性和运维正确性 | testnet 稳定前必须完成 |
| P2 | 影响长期可维护性、观测体验和交接质量 | 不阻断小额灰度，但必须纳入后续迭代 |

## 6. 功能需求

### FR-001 Bucketed EV side 修复

关联审计项：F-001  
优先级：P0  

需求：

- `Judge._build_plan()` 必须在所有 open plan 中写入 `side`，取值只能是 `long` 或 `short`。
- Bucketed EV 查桶时不得默认把缺失 side 当成 long。
- open_short 主链路、deferred short、probe short、PA add short 都必须能正确进入 short bucket 或 side_short fallback。

技术实现路径：

1. 在 `agents/trading/judge.py::_build_plan()` 中基于 `action` 计算 `side` 并写入返回 dict。
2. 调整 `_get_bucketed_ev_info()`：
   - 优先读取 `plan["side"]`。
   - 缺失时允许从 `plan["action"]` 或调用方传入 action 推断。
   - 不再用 `entry_type` 中是否含 `long` 作为主要 side 来源。
3. 确保 deferred/probe 路径复用或补齐 `plan["side"]`。
4. 新增测试：
   - `_build_plan(open_short)` 返回 `side=short`。
   - bucket metrics 同时存在 `long_*` 与 `short_*` 时，short plan 必须命中 short。
   - 无 side 的 legacy plan 进入保守 fallback，不得静默用 long bucket。

### FR-002 熔断与恢复状态单一所有权

关联审计项：F-002、F-016、F-017、F-018  
优先级：P0/P1  

需求：

- `HaltState` 的最终状态迁移必须只有一个 owner。
- `/resume` 必须在对账通过后才能恢复；对账失败或状态文件损坏时 fail-closed。
- paper/live mismatch 默认是 advisory，不应阻塞恢复，除非配置明确要求。
- RiskGuard 状态必须纳入恢复前对账。

推荐技术方案：

1. 新增 `utils/reconciliation_service.py`：
   - 统一读取 `positions.json`、`riskguard_state.json`、`paper_positions.json`。
   - 通过统一 exchange factory 查询真实交易所持仓。
   - 返回结构区分 `blocking_issues` 与 `advisory_issues`。
2. 调整 `utils/position_reconciler.py`：
   - `paper_live_mismatch` 放入 advisory。
   - `exchange_query_failed`、`missing_in_executor`、`missing_in_exchange`、`missing_in_risk_guard` 放入 blocking。
   - `status` 只由 blocking issues 决定。
3. 调整 `TelegramNotifier`：
   - `/resume` 只发恢复请求或调用 reconciliation_service 获取结果。
   - 不直接把 `HaltState` 改成最终恢复状态。
4. 调整 `MultiExecutor.on_message(system_command)`：
   - `halt`：写入 `_trading_halted=True` 和 `HaltState.halt()`。
   - `resume`：校验 payload 中的 reconciliation result，或自身调用 reconciliation_service。
   - 通过后调用 `HaltState.confirm_resume()`，失败则保持 halted。
   - `force_resume` 必须显式 payload 标记，并写入 `HaltState.force_resume()`。
5. 调整 `utils/halt_state.py` 和 `risk_manager.py`：
   - 状态文件读取失败时记录错误。
   - 对 `HaltState`，读取失败应进入 halted 状态，reason=`state_load_failed`。
   - 对 `RiskManager`，读取失败应触发 reconciliation required 或至少发出 critical warning，不得无声归零。

### FR-003 execution_result.v2 全路径统一

关联审计项：F-003  
优先级：P0  

需求：

所有 `execution_result` 发布点必须使用统一 helper，字段全集固定：

```json
{
  "schema_version": "execution_result.v2",
  "status": "executed|rejected|error|force_closed|risk_reduced|closed_externally|expired",
  "action": "open_long|open_short|close|add|reduce",
  "symbol": "BTC-USDT",
  "source": "executor_open|risk_alert|close_all|sync|external_close|local_stop|partial_tp|executor_reject",
  "request_id": "",
  "correlation_id": "",
  "reason": "",
  "result": {},
  "timestamp": 1770000000.0
}
```

技术实现路径：

1. 扩展 `MultiExecutor._build_execution_result()`：
   - `source` 必填，缺失时填 `unknown` 并 warning。
   - `result` 必须为 dict。
   - 对无 request_id 的事件生成 correlation_id。
2. 替换 `agents/trading/executor.py` 内所有 inline `await self.publish("execution_result", {...})`。
3. 对 early return 分支统一 source：
   - halted / reconciliation_pending / low_confidence / balance_fetch_failed / risk_reject / position_exists / open_cooldown / add_cooldown / exception / none_result。
4. Reviewer、RiskGuard、Telegram 保持兼容，不删除旧字段。
5. 新增参数化测试覆盖所有终态。

### FR-004 统一 exchange factory 与 testnet/live 边界

关联审计项：F-015  
优先级：P1  

需求：

- 所有创建 ccxt exchange 的路径必须使用同一个 factory。
- `use_testnet=true` 时必须设置 sandbox。
- Telegram 对账、Judge 余额、MarketScanner、DataCollector、Executor 必须一致。

技术实现路径：

1. 新增 `utils/exchange_factory.py`：
   - `create_exchange(config, exchange_id=None, require_private=False, purpose="")`
   - 统一设置 `enableRateLimit=True`、`options.defaultType=swap`。
   - OKX/Binance 凭证从 env/config 读取。
   - `use_testnet` 为 true 时调用 `set_sandbox_mode(True)`。
   - 可选 `load_markets`。
2. 替换以下位置：
   - `executor.py::ContractExecutor.__init__`
   - `agents/trading/judge.py::setup`
   - `agents/trading/multi_data_collector.py::setup`
   - `agents/research/market_scanner.py::setup`
   - `agents/trading/telegram_notifier.py::_run_reconciliation`
3. 新增测试：
   - config `use_testnet=True` 时 factory 调用 sandbox。
   - Telegram reconciliation 使用同一个 factory。
   - live 缺凭证仍由 config loader fail-fast。

### FR-005 OKX SWAP notional 换算修正

关联审计项：F-019  
优先级：P1  

需求：

- 任何 OKX SWAP orderbook/liquidation/trade size 转 USD notional 时必须考虑 `contractSize`。
- liquidity_score、depth、slippage、liquidation intensity 的数量级必须可信。

技术实现路径：

1. 在 `MultiDataCollector` 中缓存 market metadata：
   - `contract_size = market["contractSize"] or 1`
   - `to_notional(symbol, price, contracts) = price * contracts * contract_size`
2. 修复：
   - `_fetch_orderbook()` 的 `bid_depth_usd` / `ask_depth_usd`。
   - `_fetch_liquidations()` 的 `vol_usd`。
   - 必要时 `_fetch_big_trades()` 的 big trade volume 口径。
3. 修复 `ContractExecutor._check_slippage()`：
   - ccxt orderbook amount 如为合约张数，按 `price * amount * contract_size` 计算深度。
4. 新增 BTC 与 DOGE 类不同 contractSize 的测试，避免只在 contractSize=1 时通过。

### FR-006 旧入口与文档收敛

关联审计项：F-014、F-024  
优先级：P1/P2  

需求：

- 仓库只能有一个生产主入口：`run_agents.py`。
- `start.sh` 不得启动旧套利系统。
- 旧 `main.py`、`live_trading.py`、旧 docs 必须明确归档或移出主路径。

技术实现路径：

1. 修改 `start.sh`：
   - 文案改为“多 Agent 交易系统”。
   - 执行 `python3 run_agents.py`。
2. `main.py`：
   - 加强 deprecated 标识，或移到 `archive/`。
   - 若保留，运行时默认退出并提示 `run_agents.py`。
3. 文档同步：
   - README、runbook、development、architecture、handoff、CLAUDE、待解决事项。
   - 当前测试基线统一为 `493 passed / 4 deselected / 1 warning`（2026-05-24），除非重新测试更新。

### FR-007 LLM schema 全覆盖

关联审计项：F-005  
优先级：P1  

需求：

- ResearchSynthesizer、Censor、TechAnalyst、BehavioralCritic 的 LLM JSON 输出必须 schema 化。
- schema validation error 必须进入 LLM audit 和日志。

技术实现路径：

1. 在 `agents/llm_client.py` 增加：
   - `SYNTHESIS_SCHEMA`
   - `FINAL_DECISION_SCHEMA`
   - `CENSOR_SCHEMA`
   - `TECH_ANALYSIS_SCHEMA`
   - `BEHAVIORAL_CRITIC_SCHEMA`
2. 各调用处传入 schema。
3. 对 list item 的字段做最小校验：缺失 symbol/confidence/action 时剔除或默认。
4. 新增 bad JSON、缺字段、类型错、越界值测试。

### FR-008 critical event journal

关联审计项：F-006  
优先级：P1  

需求：

- 关键交易消息必须可追踪到本地 append-only journal。
- 进程崩溃后能重放最近交易生命周期。

技术实现路径：

1. 新增 `utils/event_journal.py`：
   - `append_event(topic, message)`
   - JSONL append，字段含 `topic/msg_id/timestamp/payload_hash/payload`。
   - 写失败只告警，不阻塞交易。
2. 在 MessageBus 或 BaseAgent publish 中对以下 topics 落盘：
   - `trade_decision`
   - `execution_result`
   - `daily_hard_stop_triggered`
   - `system_command`
   - `risk_alert`
3. 新增 replay 工具：
   - 按 `request_id/correlation_id/symbol` 聚合交易生命周期。

### FR-009 依赖锁定与升级验收

关联审计项：F-021  
优先级：P1  

需求：

- 生产环境依赖必须可复现。
- ccxt 升级必须触发 OKX 语义验收。

技术实现路径：

1. 使用 `pip-tools` 或 `uv` 生成 lock 文件。
2. CI/本地安装优先使用 lock。
3. 新增 `docs/dependency_upgrade_runbook.md`，规定升级 ccxt/openai/aiohttp 的验证命令。

### FR-010 可观测与运维补强

关联审计项：F-013、F-022、F-023、F-026  
优先级：P2  

需求：

- MessageBus、Agent health、LLM degraded、data degraded、DLQ、queue backlog 能被 `/status` 或 health 报告看到。
- 日志和 LLM audit 有轮转、保留和脱敏策略。
- `data_alert` 有消费者。

技术实现路径：

1. MessageBus 记录无人订阅的重要 topic。
2. `data_alert` 统一转为 `risk_alert` 或让 Telegram/RiskGuard 订阅。
3. Telegram 发送改后台队列，命令轮询不被告警发送阻塞。
4. logger 使用 RotatingFileHandler 或按天清理。
5. LLM audit 增加 `LLM_AUDIT_RETENTION_DAYS` 与敏感字段脱敏。

## 7. 工作包拆分

### Milestone 0：准备与冻结

目标：避免继续引入新策略复杂度。

- 冻结策略参数改动。
- 建立整改分支。
- 确认当前测试基线。
- 确认 `.env` 不入库，真实凭证不用于自动测试。

### Milestone 1：P0 阻断修复

范围：

- FR-001 Bucketed EV side。
- FR-002 熔断恢复所有权。
- FR-003 execution_result.v2 全路径统一。

完成标准：

- 新增 P0 定向测试全部通过。
- 全量 pytest 通过。
- 审计报告 P0 可关闭。

### Milestone 2：testnet 安全边界

范围：

- FR-004 exchange factory。
- FR-005 OKX notional 换算。
- FR-006 start.sh/旧入口收敛。
- FR-009 依赖锁定。

完成标准：

- sandbox factory 测试通过。
- contractSize 换算测试通过。
- `./start.sh` 不会进入旧套利系统。
- lock file 可复现安装。

### Milestone 3：LLM 与事件可追踪

范围：

- FR-007 LLM schema。
- FR-008 event journal。
- Reviewer/Replay 工具补强。

完成标准：

- schema bad-case 测试通过。
- 关键 topic journal 落盘。
- 能按 request_id 重放一笔 open-close 生命周期。

### Milestone 4：观测、文档和 live 灰度评审

范围：

- FR-010 可观测补强。
- docs sync。
- OKX testnet 手工验收。

完成标准：

- 验收文档全部 P0/P1 通过。
- OKX testnet 报告落盘。
- Go/No-Go 结论明确。

## 8. 交付物

| 交付物 | 类型 | 说明 |
|---|---|---|
| P0 修复代码 | code | Judge side、HaltState owner、execution_result helper 全路径 |
| exchange factory | code | 统一 sandbox/live 和凭证处理 |
| contractSize notional 修正 | code | 修复深度、爆仓、滑点相关计算 |
| LLM schemas | code | 覆盖 research/censor/tech/critic |
| event journal | code/tool | 关键消息 JSONL + replay |
| 依赖 lock | ops | 固定生产依赖 |
| OKX testnet 验收报告 | docs/generated_reports | 真实交易所语义结果 |
| 文档同步 | docs | 入口、配置、状态、测试基线统一 |

## 9. 风险与取舍

| 风险 | 说明 | 处理 |
|---|---|---|
| 重构范围过大 | 一次性引入 exchange factory/event journal/schema 可能触动较多模块 | 按 Milestone 分批，P0 先闭环 |
| testnet 与 live 仍有差异 | OKX testnet 不能完全代表 live 流动性和撮合 | 小额 live 灰度仍需单独审批 |
| fail-closed 降低可用性 | 状态文件损坏时可能阻止交易 | 交易系统优先安全，不以可用性换风险 |
| paper mismatch 不阻塞可能漏风险 | paper/live 方向不同不一定危险，但可能提示逻辑差异 | advisory 告警保留，恢复门只看 live/RiskGuard/Executor/Exchange |

## 10. Go/No-Go 规则

允许进入“小额 live 灰度评审”的最低标准：

- P0 全部修复并通过验收。
- P1 中 exchange factory、contractSize、start.sh、依赖锁定、对账恢复全部通过。
- 全量 pytest 通过。
- OKX mock 和真实 testnet 验收通过或有明确非阻断豁免。
- `docs/to-do-list.md` 无除 OKX testnet 外的 P0/P1 BLOCKED。

不允许扩大 live 的情况：

- 任一 P0 未修复。
- `USE_TESTNET=true` 时仍有路径读取 live exchange。
- `/resume` 不能证明对账通过。
- `execution_result.v2` 仍有缺字段分支。
- OKX TP/SL 生命周期未通过 testnet 或小额验证。
