# Tasks: trend-entry-shadow-decision-logger

> 初始任务边界。影子跑法/隔离/结局锚/报表由 brainstorming 定后细化。

- [ ] 1. 设计定稿：brainstorming 拍板影子跑法（复用 replay_decision 前向 vs 共享纯函数）+ hook 落点 + 隔离红线 + 结局锚口径 + 性能/失败安全；产出 Design Doc + delta spec `shadow-decision-logger`。
- [ ] 2. 影子决策记录器：在 live 决策 chokepoint 旁路跑 both-levers on 影子决策（复用隔离机器），write-only 写 `shadow_decision_log.jsonl`（real vs shadow + tech_context + 结局锚）；影子异常 fail-safe 不破 live。
- [ ] 3. 隔离红线守卫：扩展 `tests/test_cf_red_line_guard.py` 禁交易决策/风控路径读影子产物；坐实影子绝不 publish 真实 bus / 不下单 / 不 mutate live 状态。
- [ ] 4. 结局锚结算 + 对比报表：影子开仓前向结局（resolve_counterfactual/klines）+ 一次性对比驱动（real lever2-only vs shadow both-levers：多开数/前向 R/lever1 增量），复用诚实门。
- [ ] 5. 全量回归 pytest 绿 + 失败安全测试（影子异常不影响 live 决策）。
- [ ] 6. 验证报告：隔离红线坐实 + 影子记录 sanity（产物 schema、tech_context 非空填了 lever1 数据墙）+ 性能影响。
