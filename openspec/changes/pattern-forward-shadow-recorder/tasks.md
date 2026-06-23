# Tasks: pattern-forward-shadow-recorder

## 1. 记录器
- [x] 1.1 新建 `pattern_forward_shadow.py`:`--record`,读 klines.db 各 symbol 最新已闭合 bar,检测 Bearish Engulfing 且 context==low|down(复用 cf_pattern_edge_discovery)
- [x] 1.2 命中→ATR SL1.5/TP3.0/10日 would-be 信号,write-only 追加 jsonl,幂等键 (symbol,detect_date)
- [x] 1.3 防前视:只用已闭合 bar(bars[-1]);数据缺失 fail-safe(单测覆盖)。smoke:当前检出 5 个 live 信号、再跑+0 幂等

## 2. 结算器
- [x] 2.1 `--settle`:≥10 日未结算项经 resolve_counterfactual 算净 R 回写
- [x] 2.2 滚动报告 + cf_honesty_gate 诚实门

## 3. 红线守卫 + 测试
- [x] 3.1 红线守卫扩展 forbidden += pattern_forward_shadow(PASS)
- [x] 3.2 单测 tests/test_pattern_forward_shadow.py:命中/上下文/幂等/防前视/结算 5 passed
- [x] 3.3 全量 pytest 1415 passed / 1 预存正交 fail(零新回归)

## 4. 调度文档 + 收尾
- [x] 4.1 README 加每日 cron 注记(--record / 定期 --settle)
- [x] 4.2 record smoke 写入 5 live 信号 + 幂等;诚实汇报(前向样本须数周成熟)
