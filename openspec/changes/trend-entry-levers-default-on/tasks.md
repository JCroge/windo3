# Tasks: trend-entry-levers-default-on

> 范围定为 **lever2-only**（brainstorming）；验证主证据 = rejected 流 A/B + tier 定价 + paper 前向（event_backtest 对 Judge 级口径改动已知失真，仅作非回归）。

- [x] 1. 设计定稿：brainstorming 定范围 lever2-only + 验证方法栈 + env 逃生阀；产出 Design Doc + delta spec `ladder-weighted-rr`「默认启用」。
- [x] 2. config 默认开：`config_loader.DEFAULTS` 加 `ladder_rr_enabled: True` + env `LADDER_RR_ENABLED`（逃生阀）；`judge.py:174` + `decision_replay.py:196` 兜底对齐 True。（布尔 flag 不入 HARD_LIMITS。）
- [x] 3. 风控链确认：lever2 抬高 effective_rr 让趋势单过**正常 1.50 地板**作全尺寸正常单，**不触发** `low_rr_policies`（那是 lever1 授 <1.5 地板的路径，本 change lever1 默认关）；lever2 对非趋势单的影响由全量回归覆盖（无意外放开）。
- [x] 4. event_backtest 非回归（红线合规）：跑通 smoke（4 trades，exit 0），结构上不读 `ladder_rr_enabled`（自有 `_build_plan`）→ 翻 flag 零影响；已知失真记入 Design Doc，主验证证据指向 rejected 流 A/B + tier 定价。
- [x] 5. 全量回归 pytest **1288 passed**（1285+3 新）；修 3 个 config-parity/capture 保真守卫（pin 翻转前磁带纪元 ladder=off），Design Doc 记录回放保真副作用。
- [x] 6. 验证证据已齐（见 comet-verify 验证报告）：lever2 rejected 流 A/B **+0.181 R/簇**（77 簇/52.5%胜率/保守 TP1 含亏单）+ tier 定价 P(TP2)=68% + event_backtest 非回归 + 全量绿 + env 逃生阀回滚。
