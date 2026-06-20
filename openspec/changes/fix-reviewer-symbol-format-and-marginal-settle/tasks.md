## 1. reviewer 入口 symbol 归一（根治）

- [ ] 1.1 `agents/trading/reviewer.py` import `from utils.symbol import to_internal`
- [ ] 1.2 3 处 symbol 取值点（~112/151/216 `symbol = msg.get('symbol') or payload.get('symbol')`）之后套 `symbol = to_internal(symbol)`（None fail-safe）
- [ ] 1.3 确认 `trade_record['symbol']` 与 `[复盘] 记录交易` 日志均用归一后 symbol
- [ ] 1.4 确认 `_apply_pnl_resolution` upsert 按 request_id/position_id（不依赖 symbol 格式）不回归

## 2. track_marginal60 结算源改读 lifecycle

- [ ] 2.1 `scripts/track_marginal60.py` 新增读 `data/live_position_lifecycle.json`（fail-safe 文件缺失）
- [ ] 2.2 fill（judge 开仓成功）symbol 经 `to_internal` 归一；lifecycle 记录 symbol 亦归一
- [ ] 2.3 settle：按 symbol + side + `opened_at≈fill_ts`（容差窗 ±300s）join，取 `total_realized_pnl`；`status` 未平/`total_realized_pnl` 缺失 → "未结算"
- [ ] 2.4 移除/替换原 grep `agent_reviewer_*.log` 的 `记录交易` 结算逻辑（fill/tier 仍从 judge 日志取）
- [ ] 2.5 真跑 `python3 scripts/track_marginal60.py` 确认原未结算的 ETH/UNI/XRP 现已结算、XLM 用权威 −10.09

## 3. 测试

- [ ] 3.1 单测：reviewer symbol 归一——构造 payload symbol=`XRP-USDT-SWAP` → trade_record['symbol']==`XRP-USDT`；`XRP-USDT` 幂等；None fail-safe
- [ ] 3.2 单测：tracker 从 lifecycle settle——构造 fill（`ETH-USDT`）+ lifecycle（`ETH-USDT-SWAP`,total_realized_pnl）→ join 成功结算；pending/缺失 → 未结算
- [ ] 3.3 reviewer 既有测试不回归（segmented metrics / trade_history / pnl_resolution upsert）
- [ ] 3.4 main() 登记新用例，全量回归零退化

## 4. 文档

- [ ] 4.1 更新 CLAUDE.md（reviewer symbol 归一约定 / track_marginal60 读 lifecycle）
- [ ] 4.2 comet-design 产出 Superpowers Design Doc
