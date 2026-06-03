## 0. 依赖与配置

- [x] 0.1 `requirements.txt` / `requirements.lock` 新增 `freezegun==1.5.1` 测试依赖
- [x] 0.2 `paper_executor.py` 模块顶部新增常量 `DEFAULT_PAPER_LIMIT_TICK_STALENESS_SEC = 60`
- [x] 0.3 `__init__` 读取 `config['paper_limit_tick_staleness_sec']` 到 `self._tick_staleness_sec`，缺省走 default
- [x] 0.4 `utils/config_loader.py` `DEFAULTS` 字典新增 `"paper_limit_tick_staleness_sec": 60`；`_apply_env_overrides` ENV map 新增 `"PAPER_LIMIT_TICK_STALENESS_SEC": ("paper_limit_tick_staleness_sec", float)`；`.env.example` 在 paper 配置区段加注释（可选项，默认 60s）；`format_banner` 输出可选展示该字段
- [x] 0.5 `VALID_RANGES`（如适用）新增 `"paper_limit_tick_staleness_sec": (1.0, 600.0)` 边界校验

## 1. Paper Executor 限价撮合骨架

- [x] 1.1 在 `agents/trading/paper_executor.py` 增加 `self._pending_limits: Dict[str, dict]` 内存状态（key=symbol，value={created_at, deadline, side, action, plan, decision, entry_zone, last_tick_ts}）
- [x] 1.2 修改 `_open_paper`：检测 `plan.order_type == 'limit'` 且 `entry_zone` 有效时，写入 `_pending_limits[symbol]` 而非立即成交；写入前检查 `_pending_limits` / `_positions` 重复
- [x] 1.3 实现单一函数 `_wait_paper_limit_fill(symbol, tick_price)`：计算 `min(low) <= tick_price <= max(high)` 命中判定；命中则在 entry_zone 中点开仓，写 `entry_method='limit_filled'`，移出 `_pending_limits`；同时刷新 `last_tick_ts = time.time()`
- [x] 1.4 在 `on_message[price_tick]` 现有分支末尾对 `_pending_limits[symbol]` 调用 `_wait_paper_limit_fill`（仅当 symbol 有 pending）
- [x] 1.5 实现 `_scan_pending_limits()` 扫描所有 pending：超时（`now >= deadline`）走 timeout 分支；在 `tick()` 末尾调用（30s 周期）
- [x] 1.6 timeout 分支按决策树分流（见 design TD-5）：no_fallback=True → 拒单 + `risk_alert{type='paper_unfilled'}`；no_fallback=False + tick fresh → market 成交 + log；no_fallback=False + tick stale/None → `paper_unfilled_no_tick` 拒单

## 2. Paper 账本字段扩展

- [x] 2.1 `_open_paper` 立成交路径在 position 字典写入 `entry_method='market'`
- [x] 2.2 `_wait_paper_limit_fill` 命中路径写入 `entry_method='limit_filled'`
- [x] 2.3 timeout no_fallback 分支拒单记录写入 `_rejected_log` 含 `entry_method='limit_unfilled'`
- [x] 2.4 timeout fallback 分支写入 `entry_method='market'`（与立成交路径同字段）
- [x] 2.5 `_close_paper` 在 close 记录的 `paper_trades.jsonl` 行携带 `entry_method`（从 position 字典透传）
- [x] 2.6 `paper_positions.json` 持久化时确保 `entry_method` 字段被保存

## 3. Trade Decision 重复保护

- [x] 3.1 在 `_open_paper` 头部检查：若 `_pending_limits[symbol]` 已存在，记 info log 并 return（不开新单）
- [x] 3.2 在收到 `action='close'` 且 `_pending_limits[symbol]` 存在时，移除 pending 并记 info log
- [x] 3.3 `_open_paper` 持仓已存在分支保留原有跳过逻辑，新增对 pending 的相同保护

## 4. Pending Limits 不持久化

- [x] 4.1 paper_executor 启动时不读取/重建 `_pending_limits`（保持 in-memory only）
- [x] 4.2 优雅停机不持久化 `_pending_limits` 到磁盘（确认 save_state 路径不写入）
- [x] 4.3 在 design.md Open Question Q2 标注为已解决

## 5. Telegram critical_types 扩展

- [x] 5.1 `agents/trading/telegram_notifier.py:_handle_risk_alert` 的 `critical_types` 集合加入 `'pullback_unfilled'` 和 `'paper_unfilled'`
- [x] 5.2 `_handle_risk_alert` 按 `payload.source` 区分 paper/live，paper 用 `[模拟]` 前缀、live 用 `[实盘]` 前缀（或与现有命名一致）
- [x] 5.3 缺 source 字段时 fail-safe 默认 live 行为 + warning 日志
- [x] 5.4 消息体携带 `symbol / side / entry_zone / request_id / timeout_sec`

## 6. Live alert source 字段一致性

- [x] 6.1 检查 `executor.py:_enqueue_drift_alert('pullback_unfilled', ...)` 是否携带 `source` 字段；缺失则在 alert payload 构造点统一加入 `source='executor'`
- [x] 6.2 paper_executor 发布 `paper_unfilled` 时显式带 `source='paper_executor'`

## 7. Pullback 日志透传到 agent_executor.log

- [x] 7.1 `agents/trading/executor.py` 处理 drift alert 时在 agent logger 写一行 `[Pullback] {symbol} {side} 限价未成交（live）`，使 `agent_executor_*.log` 可见
- [x] 7.2 不删除 root `executor.py:2492` 原有日志（保持单点真相）

## 8. 单元测试

- [x] 8.1 新建 `tests/test_paper_limit_fill.py` 文件骨架，参考 `tests/test_pullback_atr_policy.py` 风格；引入 `from freezegun import freeze_time`
- [x] 8.2 case: limit plan 进入 `_pending_limits` 不立即成交（覆盖 D1 + Req1 Scenario 1）
- [x] 8.3 case: market plan 维持立成交，`entry_method='market'`（覆盖 Req1 Scenario 2）
- [x] 8.4 case: limit plan + 缺 entry_zone 走 fail-safe market 成交（覆盖 Req1 Scenario 3）
- [x] 8.5 case: tick 价进入 entry_zone 触发 fill at 中点 + `entry_method='limit_filled'`（覆盖 Req2 Scenario 1）
- [x] 8.6 case: tick 瞬时穿越 entry_zone 仍判定成交（覆盖 Req2 Scenario 2）
- [x] 8.7 case: tick 全程在 entry_zone 外，timeout no_fallback=True → `paper_unfilled` + `risk_alert` 发布（覆盖 Req2 Scenario 3 + Req3 Scenario 1）；用 `freeze_time` + `frozen.tick(seconds=1801)` 推进时间
- [x] 8.8 case: timeout no_fallback=False + 有 fresh tick → market 成交（覆盖 Req3 Scenario 2）
- [x] 8.9 case: timeout no_fallback=False + 无 tick → 拒单 `paper_unfilled_no_tick`（覆盖 Req3 Scenario 3 + 新 Req7 Scenario 1）
- [x] 8.10 case: timeout no_fallback=False + 老 tick (>staleness 阈值) → 拒单 `paper_unfilled_no_tick`（覆盖新 Req7 Scenario 1）
- [x] 8.11 case: 自定义 `paper_limit_tick_staleness_sec=120` 配置生效（覆盖新 Req7 Scenario 3）
- [x] 8.12 case: pending 期间收到第二个 open_short 被跳过（覆盖 Req5 Scenario 1）
- [x] 8.13 case: pending 期间收到 close 移除 pending（覆盖 Req5 Scenario 2）
- [x] 8.14 case: 重启后 `_pending_limits` 为空（覆盖 Req6 Scenario 1）
- [x] 8.15 case: `_save_state` 写入的 paper_positions.json 不含 pending_limits 字段（覆盖 Req6 Scenario 2）
- [x] 8.16 case: cleanup loop 在 30s 内处理 deadline 到达的 pending（覆盖新 Req8 Scenario 1）
- [x] 8.17 case: legacy paper_trades.jsonl 行无 `entry_method` 时下游 fail-safe 默认 market（覆盖 Req4 Scenario 4）
- [x] 8.18 新增 `tests/test_telegram_pullback_alerts.py`：`pullback_unfilled` 和 `paper_unfilled` 都触发 TG send，缺 source 走 fail-safe（覆盖 risk-alert-routing spec 全部 Scenario）
- [x] 8.19 case: paper 与 live 同时触发未成交，TG 收到两条独立消息且前缀区分

## 9. 回归与基线

- [x] 9.1 跑 `python3 -m pytest -q tests/test_pullback_atr_policy.py tests/test_limit_no_fallback.py` 确保不回归
- [x] 9.2 跑 `python3 -m pytest -q tests/test_paper_executor*.py` 确保 paper 现有用例不破
- [x] 9.3 跑全量 `python3 -m pytest -q`，预期基线从 954 升到 980+ (新增 ~26 case)
- [x] 9.4 跑 `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .` 确认无语法/导入错误
- [x] 9.5 验证 `pip install -r requirements.lock` 能复现含 freezegun 的环境

## 10. 文档与状态

- [x] 10.1 更新 `docs/to-do-list.md`：把"Paper 结果独立复盘"调整为已部分推进（entry_method 字段已铺垫），新增 follow-up 项（双轨模拟 / ma_aligned 触发面 / timeout 数值调参）
- [x] 10.2 更新 `CLAUDE.md` 当前事实段，记录新基线 + paper limit 撮合契约入口 `_wait_paper_limit_fill`
- [x] 10.3 在 `docs/superpowers/specs/` 创建对应 design doc 链接（comet-design 阶段产出）
- [x] 10.4 准备 verification report 路径：`docs/audit_remediation_pullback_entry_paper_parity_acceptance.md` (verify 阶段产出)
