## 1. P0: 修复加权逻辑的数学 bug

- [x] 1.1 修复 `weighted_total` 计算逻辑（line 211），使用 `in ['bullish', 'bearish']` 显式检查
- [x] 1.2 添加 `anchor_neutral_weight` 变量，当 BTC/ETH bias 为 'neutral' 时累加权重
- [x] 1.3 计算 `weighted_neutral = neutral_count + anchor_neutral_weight`
- [x] 1.4 更新 `weighted_total` 包含所有三个方向的 anchor 权重
- [x] 1.5 更新 `neutral_pct = weighted_neutral / weighted_total` 使用加权计算

## 2. P1: 优化阈值配置

- [x] 2.1 降低 BULLISH 阈值从 0.5 到 0.45（line 231）
- [x] 2.2 降低 BEARISH 阈值从 0.5 到 0.45（line 234）
- [x] 2.3 提高 CHOPPY 的 neutral_pct 阈值从 0.6 到 0.70（line 240）

## 3. 更新 basis 字段输出

- [x] 3.1 在 `basis` 字典中添加 `anchor_neutral_weight` 字段
- [x] 3.2 在 `basis` 字典中添加 `weighted_neutral` 字段（用于调试）
- [x] 3.3 更新 `neutral_pct` 在 basis 中的值（现在是加权的）

## 4. 测试验证

- [x] 4.1 运行完整测试套件确认无回归（`pytest -q`，预期 452 passed）
- [x] 4.2 手动测试场景：BTC bias='neutral' 时 weighted_total 不应错误增加
- [x] 4.3 手动测试场景：neutral_pct 应该反映加权计算结果
- [x] 4.4 手动测试场景：bullish_pct=0.45 应该触发 BULLISH 判断
- [x] 4.5 手动测试场景：neutral_pct=0.69 不应触发 CHOPPY，0.70 应该触发

## 5. 部署和监控

- [x] 5.1 提交代码改动（message 体现修复的 5 个问题）
- [x] 5.2 OS 层重启 live 实盘进程（使新代码生效）
- [ ] 5.3 监控 24-48h：记录 `diagnostic_regime_classification.json` 的体制分布变化
- [ ] 5.4 监控 24-48h：统计开仓决策数量和通过率变化
- [ ] 5.5 监控 24-48h：评估胜率和 PnL 质量，确认无显著下降
