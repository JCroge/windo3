# Proposal: 剔除开仓门的胜率因子

## Why

开仓决策的最后闸门是 `agents/trading/judge.py` 的 **EV 门**（`_check_expected_value`）。实测策略衰减时（近 20 笔胜率 25%、PF 0.64），这道门会因为**实际滚动胜率**把开仓拦死。运维诉求：胜率 25% 不应该直接决定能否开仓——开仓应由信号质量与单笔经济性（R:R/成本）决定，而非被近期实现胜率单点否决。

实际胜率通过**三条路径**进入 EV 门并拦截开仓：
1. **胜率硬阈值**（`judge.py:3699`）：`effective_win_rate < 0.4 且 |score| < 70 → 强拒`。
2. **压垮 EV**（`judge.py:3566`）：`EV = p_win × net_profit − (1−p_win) × net_loss`，`p_win` 取自实际滚动胜率（`_get_p_win`），25% 时 EV 近乎必为负，撞 `EV < ev_min_threshold(0.05) → 拒`。
3. **分桶覆盖**（`judge.py:3652-3693`）：分桶 win_rate 再次用实际胜率重算 EV。

只删硬阈值不够——必须同时切断 `p_win` 与实际胜率的耦合，否则 EV 仍被拖负。

## What

引入 config 开关 `ev_winrate_gate_enabled`（默认 `true` = 完全保持现状）。关闭后：
1. EV 公式改用**固定中性 p_win**（`ev_neutral_p_win`，默认 0.55），不再读实际滚动胜率。
2. 跳过胜率<40% 硬阈值（路径 ①）。
3. 跳过分桶 win_rate 覆盖（路径 ③）。
4. **保留** EV 阈值门（路径 c）：用固定 p_win 算出的 EV 继续拦 R:R/成本差的单——经济保护不丢。

config.yaml 设 `ev_winrate_gate_enabled: false` 落地运维诉求。

## Scope

- 改动集中在 `judge.py` 的 EV 门链路 + 配置加载（config_loader.py / config.yaml）+ 测试。
- 开关默认值保持 `true`，不改变任何未显式配置环境的现状行为。

## Non-goals / Out of scope

- 不动 `reviewer.py` 的 win_rate 计算 / decay 检测、`telegram_notifier.py` 展示、backtest/cf_* —— 属监控复盘，与开仓门无关。
- 不动 EV 阈值 `ev_min_threshold`、强信号豁免阈值、R:R 门、评分门 `_compute_score`。
- 不移除 EV 门本身（保留经济门），仅剔除「实际胜率」这一因子。
