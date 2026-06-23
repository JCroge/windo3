# Proposal: step-regime-choppy-range-pos-063

## Why

已上线能力 `regime-aware-long-entry-guard` 的生产阈值 `risk.long_live_max_range_pos_choppy` 当前缓进起步值为 **0.70**（代码默认/目标 0.55）。2026-06-23 用真实数据做了 0.70(live) vs 0.55(目标) 两臂实证对比（决策磁带 ⟕ trade_history 按 request_id join，证据归档于 agent memory `regime-threshold-070-vs-055-comparison`）：

- **入场位置**：174 条受影响体制多单决策中，range_pos ∈ (0.55, 0.70] 的"差异带"占 **44.8%**，中位 range_pos=0.550。一步收到 0.55 会把 ~45% 的 choppy 多单从立即开改成等回调——**行为剧变**。
- **PnL（样本薄）**：差异带 11 笔 join 中 n=5、净 −4.55U、胜率 20%（4/5 亏，含 HYPE 顶部追多 −3.23）。方向上支持收紧，但 **n=5 不过项目诚实门 n≥30**，不足以支撑一步到 0.55；且 ≤0.55 的"好位置"多单同样在亏，说明位置门只治标。

折中：先**中间步进到 0.63**，拦住最热那档（HYPE/SUI rp≈0.66–0.70），少动幅度，让影子臂继续累积差异带样本到 n≥30 后再决定是否收到 0.55。

## What Changes

- `config.yaml` 中 `risk.long_live_max_range_pos_choppy`：**0.70 → 0.63**。
- 不动 `long_live_daily_gain_range_pos_choppy`（0.50，已在目标）。
- 不动总开关 `long_live_regime_aware_range_enabled`（保持 true）。

## Scope

- **In**：`config.yaml` 单值修改（已存在的键）。
- **Out**：无代码改动、无 config_loader 改动（键已接四段式）、无 delta spec（不改 `regime-aware-long-entry-guard` 能力的验收场景，仅缓进值步进）、不碰 daily_gain 二级门、不碰其他体制（bullish 仍 0.82）。

## Rollback

改回 `0.70`，或总开关置 `false` 回退旧固定 0.82 行为。生效需重启 live 交易进程。

## Impact

- 生效需**重启 live 交易进程**（Judge 实例化时读配置）。
- 重启后用归因字段 `entry_regime_used` / `entry_range_pos_threshold`（应见 0.63）核对生效，并继续按差异带样本观察 PF。
