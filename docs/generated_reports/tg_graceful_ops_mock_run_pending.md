# TG Graceful Ops 人工 TG 验收记录 (待执行)

**状态**: PENDING — 等 verify 阶段把 worktree 合到 live 部署点 + OS 重启后人工 TG 验证

## 计划验收命令链

按用户在 Telegram 中真实发送以下命令,记录 input/output:

1. `/halts` — 期望: 显示当前 per-symbol halt 列表(可能为空)
2. (注入测试 halt) — 在 dev/admin shell 执行 `executor._halt_symbol("TEST-USDT-SWAP", reason="manual_test")` 模拟一个 halt
3. `/halts` — 期望: 显示 1 个 halt(TEST-USDT-SWAP, reason=manual_test, halted=Xs ago)
4. `/resume_symbol TEST` — 期望: 回 "🔄 已发送" + 接收到 "✅ TEST-USDT-SWAP per-symbol halt 已解除"
5. `/halts` — 期望: 显示 "✅ 无 per-symbol halt"
6. `/status` — 期望: 含 Agents / Bus DLQ / Per-symbol halt 三行
7. `/pnl XLM 0.42` — 期望: "❌ 未找到 symbol=XLM-USDT-SWAP 的活跃 pending external_close"(当前无 pending)
8. (如有 pending) `/pnl <SYMBOL> <PNL>` — 期望写 correction + 回显 supersede 信息
9. (多候选场景) `/pnl_id <event_id> <PNL>` — 期望 event_id 精确匹配写 correction

## 验收记录

待真实 TG 验证后填入 input/output 截图/日志。
