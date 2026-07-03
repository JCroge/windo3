## Why

体制分类改进（2026-07-01 BTC/ETH anchor 权重）的设计思路正确，但实现中存在5个逻辑缺陷导致效果被严重削弱。当前系统 choppy 占比高达 93%，bullish 仅 1.5%，导致体制空仓硬门过度拦截开仓机会。修复这些缺陷可以让权重系统发挥预期效果，提升趋势识别能力，增加开仓机会而不过度激进。

## What Changes

- **P0-1**: 修复 `weighted_total` 计算bug，当 `btc_bias='neutral'` 时不应增加分母
- **P0-2**: 让 `neutral_pct` 使用加权计算，避免 CHOPPY 判断绕过权重系统
- **P1-1**: 降低 BULLISH/BEARISH 阈值从 0.5 到 0.45，让权重改进发挥作用
- **P1-2**: 提高 CHOPPY 的 neutral_pct 阈值从 0.6 到 0.70，减少误判
- **P2**: 评估使用 4h bias 替代 daily bias 以减少滞后性（可选）

预期效果：
- choppy 占比从 93% 降到 60-70%
- bullish 占比从 1.5% 提升到 15-25%
- 开仓机会显著增加但不过度激进

## Capabilities

### New Capabilities

无新功能

### Modified Capabilities

- `market-regime-classification`: 修复加权逻辑缺陷，优化阈值配置，提升体制判断准确性

## Impact

**受影响代码**:
- `utils/market_regime.py`: `_compute_raw_regime()` 方法的加权计算和判断逻辑

**受影响系统**:
- Judge 的体制空仓硬门（`_classify_regime_flat_gate`）
- 开仓决策的体制感知逻辑
- 持仓管理的体制切换响应

**数据影响**:
- `data/diagnostic_regime_classification.json` 的统计分布将显著变化
- 决策磁带中的体制判断记录将更准确

**风险**:
- 开仓量可能短期激增，需要监控胜率和 PnL 质量
- 如果市场确实处于震荡期，可能会产生更多 mixed 判断（这是正确的）
