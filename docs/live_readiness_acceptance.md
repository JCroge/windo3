# Live 准入验收文档

更新日期：2026-05-27  
关联 PRD：`docs/live_readiness_prd.md`

## 1. 验收结论规则

| 结论 | 条件 |
|---|---|
| PASS | 所有 P1 验收项通过，P2 无阻断，OKX 真实 testnet 通过 |
| CONDITIONAL PASS | 仅 P2 存在非阻断遗留，且有明确 owner 和回归保护 |
| FAIL | 任一 P1 验收项失败，或 OKX testnet 未执行/失败且无合理豁免 |

当前状态：PASS。自动化 P1 验收已通过，Phase 2 配置已接入，非 open `execution_result` 契约已完成回归；OKX posMode 执行兼容代码已上线，OKX 真实 testnet T0-T9 语义验收 2026-05-27 完成（7 PASS / 3 SKIP，详见 `docs/generated_reports/OKX执行语义testnet验收报告_20260527_150518.md`）。**Live 扩容前置阻断已解除**，下一步小额 24h 灰度观察 segmented metrics。

## 2. 验收前置条件

- 禁止使用 production key 执行 testnet 验收。
- `.env` 不得提交仓库。
- 本地测试不依赖真实交易所凭证。
- 真实 OKX testnet 验收必须使用 sandbox/testnet 环境。

## 3. 自动化验收命令

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
python3 - <<'PY'
from utils.config_loader import load_config
cfg = load_config(strict_live_check=False)
required = [
    'phase2_signal_confidence_split_enabled',
    'phase2_momentum_probe_long_enabled',
    'phase2_trend_saturation_enabled',
    'phase2_bucketed_ev_enabled',
]
missing = [k for k in required if k not in cfg]
assert not missing, missing
print({k: cfg[k] for k in required})
PY
python3 -m pytest -q
python3 verify_okx_testnet_semantics.py
```

通过标准：

- 编译无错误。
- Phase 2 四个配置 key 均存在。
- 全量 pytest 无失败；2026-05-24 结果为 `493 passed / 4 deselected / 1 warning`。
- OKX mock 8 case 全部 PASS。

## 4. 功能验收项

| ID | 优先级 | 验收项 | 验收方法 | 通过标准 |
|---|---|---|---|---|
| AC-01 | P1 | 所有 `execution_result` 发布点使用统一契约 | `rg -n "publish\\(\"execution_result\"" agents/trading/executor.py` 并结合测试 | 除统一 helper 内部外，不存在手写缺字段 payload |
| AC-02 | P1 | 风控强平事件可追踪 | 构造 `emergency_close` / `flash_move` / `position_danger` 风控 alert | payload 含 `schema_version`、`source=risk_alert`、`correlation_id`、`reason`、`result.entry_request_id` |
| AC-03 | P1 | 全平事件可追踪 | 触发 `_close_all_positions()` | 每个 symbol 均发布独立 `execution_result.v2`，`source=close_all` |
| AC-04 | P1 | 同步发现新持仓可追踪 | mock `get_newly_synced()` 返回新持仓 | payload 含 `source=sync`、`used_plan=false`、`correlation_id` |
| AC-05 | P1 | 外部 SL/TP 平仓可追踪 | mock `get_removed_symbols()` / removed position data | payload 含 `source=external_close`、`status=closed_externally`、entry attribution 不丢失 |
| AC-06 | P1 | 本地兜底止损可追踪 | mock `check_stop_loss_take_profit()` 返回 SL/TP/price failure | payload 含 `source=local_stop`、`reason`、`correlation_id` |
| AC-07 | P1 | partial TP reduce 可追踪 | mock `partial_tp_1` / `partial_tp_2` | payload 含 `source=partial_tp`、`status=risk_reduced`、`reduce_pct` |
| AC-08 | P1 | Reviewer 兼容新旧 payload | 构造新旧两类 `execution_result` 消息调用 Reviewer | trade record 保留 `entry_request_id`、`exit_request_id`、`source` 或兼容空值 |
| AC-09 | P1 | OKX 真实 testnet 8 case 通过 | 使用 testnet key 执行验收脚本或等价 runner | 8 case 有 raw response、normalized result、final state，报告写入 docs |
| AC-10 | P1 | live 准入门控清晰 | 检查 `docs/to-do-list.md` | 未完成 OKX testnet 时，文档明确“不允许 live 扩容” |
| AC-11 | P1 | Phase 2 配置 key 存在 | `load_config(strict_live_check=False)` | 四个 `phase2_*` key 均存在且为 bool |
| AC-12 | P1 | Phase 2 环境变量可覆盖 | 临时设置 `PHASE2_SIGNAL_CONFIDENCE_SPLIT_ENABLED=true`、`PHASE2_MOMENTUM_PROBE_LONG_ENABLED=true` | `load_config()` 读到 True，`MultiJudge` 初始化后对应私有开关为 True |
| AC-13 | P1 | 启动 banner 可观测 | 调用 `format_banner(cfg)` | banner 展示 Phase 2 confidence split、momentum probe long、trend saturation、bucketed EV 状态 |
| AC-14 | P1 | LLM hold 不再压死强规则信号 | 构造 rule_signal + HTF aligned + LLM hold 场景 | `execution_confidence >= 60`，`position_scale < 1`，不是 quality gate `confidence<60` |
| AC-15 | P1 | RSI 70-85 强趋势可走 probe_long | 构造 RSI=75、strong bullish、HTF bullish、无 bearish divergence | 生成 `slot_type=probe_long`，小仓位，3x 上限，走 `_gate_and_publish_open()` |
| AC-16 | P2 | 向后兼容 | 跑全量 pytest 和现有 Reviewer/RiskGuard 测试 | 现有消费者无失败，无字段删除导致的 KeyError |
| AC-17 | P2 | 文档收敛 | 检查 docs 入口 | 需求、验收、待办互相引用，不新增过期临时报告 |

## 5. OKX Testnet 手工验收表

| Case | 操作 | 记录项 | 通过标准 |
|---|---|---|---|
| 1 | market open + attached TP/SL | order raw、algo raw、normalized result、final position | 成功开仓，TP/SL 条件单状态可解释 |
| 2 | limit open timeout | create raw、cancel raw、normalized result | 超时后订单取消，系统输出 expired/rejected |
| 3 | insufficient balance | error raw、normalized result | 输出 `rejected/insufficient_balance` |
| 4 | min amount | error raw、normalized result | 输出 `rejected/min_amount` |
| 5 | posMode-aware close/reduce | close/reduce raw、posMode、final position | 平仓/减仓不反向开仓，`posSide`/`reduceOnly` 符合当前 OKX `posMode` |
| 6 | move SL | old SL state、new SL state | 旧 SL 取消或失效，新 SL 唯一有效 |
| 7 | close 后条件单状态 | close raw、algo final state | 无残留危险 TP/SL 条件单 |
| 8 | duplicate clOrdId | repeated raw、normalized result | 重复请求有 rejected 终态，不产生重复仓位 |

## 6. Go/No-Go

| 条件 | Go 标准 |
|---|---|
| 自动化测试 | `python3 -m pytest -q` 全部通过 |
| mock 验收 | 8/8 PASS |
| testnet 验收 | 8/8 PASS，或失败项明确为非生产相关且有记录 |
| 执行契约 | 所有 `execution_result` 发布点符合 v2 契约 |
| Phase 2 配置 | 四个 phase2 key 存在且 paper/testnet 可明确启用 |
| 文档 | `docs/to-do-list.md` 无除 OKX testnet 以外的 P1 BLOCKED |

自动化、mock 和文档项不足以恢复 live。只有 OKX posMode 执行兼容完成、目标账户模式 smoke test 通过，并且 OKX 真实 testnet 通过后，才允许进入 live 扩容评审。
