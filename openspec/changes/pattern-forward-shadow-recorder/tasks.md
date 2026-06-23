# Tasks: pattern-forward-shadow-recorder

## 1. 记录器
- [ ] 1.1 新建 `pattern_forward_shadow.py`:`--record` 子命令,拉/读最新日线(复用 fetch 或直接读 klines.db),对每 symbol 最新已闭合 bar 检测 `Bearish Engulfing` 且 `cf_pattern_edge_discovery.context==low|down`
- [ ] 1.2 命中→构造 would-be 信号(entry=收盘,ATR via cf_pattern_edge_discovery.atr,SL 1.5×/TP 3.0×/10日),write-only 追加 `data/pattern_forward_shadow.jsonl`,幂等键 (symbol,detect_date_utc)
- [ ] 1.3 防前视:只用已闭合 bar(bars[-1] 为已收盘日);网络/数据缺失 fail-safe 跳过不崩

## 2. 结算器
- [ ] 2.1 `--settle` 子命令:读 jsonl,对 detect_date ≤ now-10d 且 settled:false 的,拉后续日线经 `resolve_counterfactual` 算净 R,回写 settled/net_r/outcome
- [ ] 2.2 滚动报告:n/胜率/均净R + `cf_honesty_gate.summarize_bucket` 诚实门(薄样本拒答)

## 3. 红线守卫 + 测试
- [ ] 3.1 `tests/test_cf_red_line_guard.py` 守卫扩展:决策/风控路径禁 import `pattern_forward_shadow`
- [ ] 3.2 单测 `tests/test_pattern_forward_shadow.py`:构造命中/不命中/幂等/防前视 + 结算回写,用合成日线(不依赖网络)
- [ ] 3.3 全量 pytest 无新回归

## 4. 调度文档 + 收尾
- [ ] 4.1 README/runbook 注记每日 cron:`python3 pattern_forward_shadow.py --record`(UTC 收盘后)+ 定期 `--settle`
- [ ] 4.2 record-only smoke:对现有 klines.db 跑 `--record` 验证写入 + 幂等;诚实汇报(前向样本需数周,当前仅起步)
