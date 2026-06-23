# Tasks: daily-pattern-edge-lab

## 1. 历史数据抓取
- [ ] 1.1 改造 `fetch_historical_klines.py`:加分页(while 循环按 since 翻页至无新数据)、多币列表、多周期参数
- [ ] 1.2 落 `data/klines.db`,沿用 `UNIQUE(symbol,interval,open_time)`,`INSERT OR IGNORE` 保证幂等
- [ ] 1.3 跑 ~50 币 × 1d(2.75 年)入库;4h 同步入库(锁为确认集,不进第一轮)
- [ ] 1.4 入库后自检:打印每币 interval 根数 + 起始日期 + 短史币标注

## 2. 形态库(预登记、固定阈值)
- [ ] 2.1 新建 `utils/candlestick_patterns.py`,实现 ~28 种标准形态识别器(单K/双K/三K,反转+延续+中性)
- [ ] 2.2 每形态返回 (名称, 预登记方向);阈值全部固定常量,无调参入口
- [ ] 2.3 形态库单测 `tests/test_candlestick_patterns.py`:对构造的已知形态序列断言识别正确

## 3. 边缘发现骨架
- [ ] 3.1 新建 `cf_pattern_edge_discovery.py`(镜像 `cf_oi_divergence_ab.py` 结构),载入 klines.db + 计算 ATR(14)
- [ ] 3.2 上下文条件化:range_pos(N 日区间位置)/ 趋势(价 vs MA)/ 前置移动,分桶
- [ ] 3.3 ATR 退出 + `resolve_counterfactual` 结算(SL/TP 优先 4h 解析否则日线 SL-first);簇去重
- [ ] 3.4 train(2023-24)/val(2025)/test(2026) 三分统计每(形态×上下文)桶
- [ ] 3.5 多重比较校正(Bonferroni/FDR)+ 复用 `cf_honesty_gate.summarize_bucket`
- [ ] 3.6 加权:`weight=max(0,OOS净R)`,三关全过才非零;输出 edge 报告(全桶 + 过关桶 + 权重)

## 4. 红线守卫
- [ ] 4.1 `tests/test_cf_red_line_guard.py` 加 `test_decision_paths_do_not_read_pattern_research`(判 judge/executor/risk_guard/reviewer/position_analyst 不 import 形态研究模块)
- [ ] 4.2 跑全量 pytest 确认无回归(基线 1359 passed)

## 5. 验收与汇报
- [ ] 5.1 跑骨架产出首版 edge 报告(日线主测)
- [ ] 5.2 诚实汇报:有无过三关的形态;若有 → 进入 4h 确认集解封;若无 → 干净证伪结论
- [ ] 5.3 结论写入项目记忆(更新 alpha-source-hunt-verdict 或新建条目)
