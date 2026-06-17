# Tasks: trend-entry-levers-default-on

> 初始任务边界。范围（lever2-only vs +lever1）与验证深度由 brainstorming 定后细化。

- [ ] 1. 设计定稿：brainstorming 拍板范围（lever2-only / +lever1）+ 验证方法栈 + 灰度策略；产出 Design Doc + delta spec。
- [ ] 2. config 默认开：`config_loader.DEFAULTS` 加 flag=True + HARD_LIMITS + env 覆盖（逃生阀）；`judge.py` 兜底对齐。
- [ ] 3. 风控链确认：测试坐实 lever1 授 <1.5 地板 long 经 `low_rr_policies` 缩仓/降杠杆/独立 slot 正确生效；lever2 ladder 口径对非趋势单无意外放开。
- [ ] 4. event_backtest 同构（红线合规）：跑 event_backtest 确认非崩溃/回归；记录其对 Judge 级口径改动的已知失真，主验证证据指向 rejected 流 A/B + tier 定价 + paper 前向。
- [ ] 5. 全量回归 pytest 绿 + 更新相关单测（默认值变更涉及的断言）。
- [ ] 6. 验证报告：汇总主证据栈 + event_backtest 结果 + 灰度/回滚（env 逃生阀）说明。
