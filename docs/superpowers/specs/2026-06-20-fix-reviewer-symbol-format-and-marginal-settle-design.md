---
comet_change: fix-reviewer-symbol-format-and-marginal-settle
role: technical-design
canonical_spec: openspec
---

# Reviewer symbol 格式根治 + 边缘单从权威源结算（技术设计）

> 需求事实源为 OpenSpec delta spec `openspec/changes/fix-reviewer-symbol-format-and-marginal-settle/specs/reviewer-canonical-symbol/spec.md`。本文档只描述 HOW。

## 1. 根因（已实测）

```
上游某 close 路径 leak BASE-USDT-SWAP (违反 CLAUDE.md "跨 Agent 用 BASE-USDT" 约定)
  → reviewer.py:112/151/216 `symbol = msg.get('symbol') or payload.get('symbol')` 不归一
    → trade_record['symbol'] + "[复盘] 记录交易" 日志格式混乱(BASE-USDT / -SWAP 并存)
      → track_marginal60 grep 精确字符串配对 fills(全BASE-USDT)↔PnL 失败
        → ETH +0.86 / UNI −1.97 / XRP −0.58 实际有 PnL 却未结算(8 单)
```

`utils/symbol.py::to_internal()` 是 canonical 归一 helper（`SOL-USDT-SWAP`/`SOL/USDT:USDT`/`SOL-USDT` → `SOL-USDT`，幂等），文档明确"所有 agent state dict 的 key 都应该用这个函数处理"——reviewer 未用，违反约定。

## 2. 方案：消费侧收口归一 + 跟踪器读权威源

### 2.1 reviewer 入口 `to_internal` 收口（① 根治 live 数据 bug）

3 处 symbol 取值点（均已核为 record-field/log-only，无 symbol-keyed 查找）：

| 行 | 上下文 | symbol 用途 | 归一安全性 |
|---|---|---|---|
| ~112 | risk_reduced trade_record | `trade_record['symbol']` | ✅ 仅记录字段 |
| ~151 | 主 close trade_record + `记录交易` 日志 | `trade_record['symbol']` + 日志 | ✅ 仅记录字段/日志 |
| ~216 | `_apply_pnl_resolution` | 仅 warning 日志；upsert 按 `entry_request_id`/`position_id`（reviewer.py:244-245） | ✅ 匹配键非 symbol |

改法（每处）：

```python
from utils.symbol import to_internal   # 模块顶部
...
symbol = to_internal(msg.get('symbol') or payload.get('symbol'))
```

**None fail-safe**：`to_internal` 内部 `base_of` 对 None/空/无法解析返回原值不抛（现有实现）；外层 `or` 兜底保留。归一幂等，已是 `BASE-USDT` 不变。

### 2.2 track_marginal60 读 lifecycle（② 权威结算源）

```
fill (judge "开仓成功"): symbol+ts → to_internal(symbol)
lifecycle (live_position_lifecycle.json): 每条 {symbol, side, opened_at, status, total_realized_pnl, reconcile_status}
                                          → to_internal(symbol)
join: symbol + side + |opened_at − fill_ts| ≤ TOL(300s) → total_realized_pnl
      status 未平 / total_realized_pnl None → "未结算"
```

- 结算源从 grep `agent_reviewer_*.log` 的 `记录交易` 改为读 `data/live_position_lifecycle.json`。
- **fill/tier 仍从 judge 日志取**（tier=置信度，lifecycle 没有）；只把"已实现 PnL"的来源换成 lifecycle。
- **容差窗 TOL=300s**：fill 日志时点 vs lifecycle.opened_at 落库时点差几秒~分钟。
- **同 symbol 多 fill 去重消费**：lifecycle 记录按 opened_at 排序，配对后用游标/已用集合标记，避免一条 lifecycle 被多个 fill 重复结算（同 `cf_lever2_rejected_ab` 的 `used_pnl` 思路）。

## 3. 数据流

```
开仓: judge "开仓成功" 日志 ──fill(symbol,ts,tier)──┐
                                                    ├─ to_internal 归一 ─ join(symbol+side+ts容差) ─→ 结算 PnL
平仓: executor → lifecycle.json (total_realized_pnl)─┘                                                  │
                                                                                              分桶(边缘60/信念70) + 汇总
```

## 4. 边界条件

| 情形 | 处理 |
|---|---|
| payload symbol = None | `to_internal(None)` 返回原值不抛；`or` 兜底 |
| lifecycle 无 opened_at 窗内匹配 | 该 fill "未结算"（真未平 / 无 lifecycle 记录） |
| lifecycle total_realized_pnl None/pending | "未结算"，不伪造 |
| 同 symbol 多 fill | 按时序配对、已用 lifecycle 记录不复用 |
| lifecycle 文件缺失 | tracker fail-safe（空结算，仍输出 fill 列表标未结算） |

## 5. 安全 / 回归

- reviewer 是 live 路径：归一只统一 `trade_record['symbol']` 格式，**不改任何匹配键**（pnl_resolution upsert 按 request_id/position_id）。segmented metrics / 分桶 keys 现在变一致（更正确）。
- 跑 reviewer 既有测试回归（`test_*reviewer*` / pnl_resolution / segmented metrics）。
- track_marginal60 是 observability-only（纯读 + 打印），不碰 live。

## 6. 测试策略

`tests/`（新增）：
1. reviewer 归一：构造 execution_result payload symbol=`XRP-USDT-SWAP` → `trade_record['symbol']==XRP-USDT`；`XRP-USDT` 幂等；None fail-safe 不抛。
2. tracker 从 lifecycle settle：构造 fill(`ETH-USDT`@ts) + lifecycle 记录(`ETH-USDT-SWAP`, opened_at≈ts, total_realized_pnl=+0.86, matched) → join 成功结算 +0.86；构造 pending(total_realized_pnl=None) → "未结算"；窗外 → "未结算"。
3. reviewer 既有测试不回归。
4. 真跑 `python3 scripts/track_marginal60.py`：原未结算的 ETH/UNI/XRP 现已结算，XLM 用权威 −10.09（非 −7.76）。

全量回归零退化。

## 7. 红线 / 非目标

- 不回填历史 `trade_history.json`（红线"不改 data/ 用户数据"，① 仅前向）。
- 不逐个修上游 leak 的 publisher（消费侧 reviewer 入口 + tracker 读时双归一已对上游鲁棒；仅记录"观察到上游 -SWAP leak"供后续可选根治）。
- 不改 close path / executor / realized_pnl_resolver（PnL 来源不动，只改 reviewer 落记格式 + tracker 读源）。
