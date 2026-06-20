## 1. 分类：gate-toggle 两臂复盘 + baseline 自检

- [x] 1.1 `cf_ev_decouple_ab.py` 读 `data/decision_replay_tape.jsonl`，筛 `decision=accept` 且 `replayable`
- [x] 1.2 baseline 臂 `replay(record, {ev_winrate_gate_enabled:False})` 复现 live accept 自检，失真排除（复用 `_is_accept` 二元判定）
- [x] 1.3 反事实臂 `replay(record, {ev_winrate_gate_enabled:True})` 翻 reject(ev_gate) → 归 "解耦放行"，否则 "双门皆过"
- [x] 1.4 报失真排除条数（透明）

## 2. 簇去重 + 前向 CF 结算

- [x] 2.1 两桶各按 symbol+连续重复评估归一信号簇（同 `cf_lever2_rejected_ab` 簇逻辑）
- [x] 2.2 每簇代表用 `resolve_counterfactual`+`load_bars`(klines_1s→klines fallback) 结算前向 outcome（TP1 保守口径含亏单）
- [x] 2.3 算两桶净 R + delta；klines 无覆盖簇跳过并计数

## 3. 诚实门 + real PnL 交叉

- [x] 3.1 去重簇数经 `cf_honesty_gate.summarize_bucket` 诚实门，薄样本拒答
- [x] 3.2 解耦放行实际开仓单经 symbol+ts 模糊 join `live_position_lifecycle.json` 取真实 PnL 作次要 sanity 交叉，标注模糊 join/无 request_id、pending 不计

## 4. 报表

- [x] 4.1 输出：忠实/失真数、解耦放行簇数/双门皆过簇数、两桶净 R + delta、可结算/跳过簇数、real PnL 交叉、诚实门结论
- [x] 4.2 报表显式判据：解耦放行净 R << 双门皆过且 <0 → 提示解耦放行亏损单（非自动执行）

## 5. 测试

- [x] 5.1 单测：gate-toggle 分类（构造 accept 记录，gate-on→reject 归解耦放行 / gate-on→accept 归双门皆过）
- [x] 5.2 单测：baseline 自检失真排除（baseline 臂复盘≠live accept → 排除）
- [x] 5.3 单测：薄样本诚实门拒答
- [x] 5.4 红线守卫 `tests/test_cf_red_line_guard.py` 扩展：决策/风控路径禁 import/读 `cf_ev_decouple_ab` 产物
- [x] 5.5 main() 登记新用例，全量回归零退化

## 6. 真跑 + 文档

- [x] 6.1 真跑 `python3 cf_ev_decouple_ab.py`，记录结论（解耦放行前向期望 vs 双门皆过，诚实门是否拒答）入验证报告
- [x] 6.2 comet-design 产出 Superpowers Design Doc
