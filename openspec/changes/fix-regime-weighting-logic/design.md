## Context

2026-07-01 部署的体制分类改进（BTC/ETH anchor 权重）设计思路正确，但实现中存在5个逻辑缺陷：

**当前状态**：
- `utils/market_regime.py::_compute_raw_regime()` 使用 BTC_WEIGHT=2.0, ETH_WEIGHT=1.5
- 加权逻辑在 line 211 存在 Python 布尔判断 bug
- neutral_pct 使用原始计数（line 218），未参与加权
- BULLISH/BEARISH 阈值 0.5，CHOPPY neutral_pct 阈值 0.6
- BTC/ETH bias 使用 higher_tf_bias 或 daily_bias（日线级别）

**影响**：
- 实际运行数据显示 choppy 占比 93%，bullish 仅 1.5%
- 体制空仓硬门过度拦截，开仓机会不足
- 7月2-3日验证显示权重效果被削弱

**约束**：
- 必须保持 hysteresis 平滑机制不变
- 不能破坏现有的 RegimeManager API
- 必须向后兼容持久化的 regime_state.json

## Goals / Non-Goals

**Goals:**
- 修复加权逻辑的数学 bug，让 neutral bias 不错误增加分母
- 让 neutral_pct 参与加权计算，避免 CHOPPY 判断绕过权重系统
- 优化阈值配置，让权重改进发挥预期效果
- 提升体制判断准确性，增加开仓机会但不过度激进

**Non-Goals:**
- 不改变 hysteresis 机制和状态持久化
- 不引入新的权重参数或体制类型
- 不修改 RegimeManager 的公共 API
- P2（4h bias）作为可选后续优化，不在本次修复范围

## Decisions

### D1: 修复 weighted_total 计算逻辑

**当前问题** (line 211):
```python
weighted_total = total + (BTC_WEIGHT if btc_bias else 0) + (ETH_WEIGHT if eth_bias else 0)
```

当 `btc_bias='neutral'` 时，`if btc_bias` 为 True，错误地增加分母。

**决策**：显式检查 bias 值
```python
weighted_total = total + (BTC_WEIGHT if btc_bias in ['bullish', 'bearish'] else 0) + \
                        (ETH_WEIGHT if eth_bias in ['bullish', 'bearish'] else 0)
```

**替代方案**：
- 方案A：使用 `btc_bias == 'bullish' or btc_bias == 'bearish'`（冗长但明确）
- 方案B：使用 `btc_bias and btc_bias != 'neutral'`（简洁但不如 `in` 清晰）

选择 `in ['bullish', 'bearish']` 因为最清晰表达意图。

### D2: neutral_pct 加权计算

**当前问题** (line 218):
```python
neutral_pct = neutral_count / total  # neutral 不加权
```

CHOPPY 判断（line 240）使用未加权的 neutral_pct，绕过了权重系统。

**决策**：让 neutral 也参与加权

**方案A（推荐）**：neutral bias 权重归给 neutral
```python
anchor_neutral_weight = 0
if btc_bias == 'neutral':
    anchor_neutral_weight += BTC_WEIGHT
if eth_bias == 'neutral':
    anchor_neutral_weight += ETH_WEIGHT

weighted_neutral = neutral_count + anchor_neutral_weight
weighted_total = total + anchor_bullish_weight + anchor_bearish_weight + anchor_neutral_weight
neutral_pct = weighted_neutral / weighted_total
```

**方案B**：只修改判断逻辑，不改加权
```python
# 保持 neutral_pct = neutral_count / total
# 但在 CHOPPY 判断时使用更高阈值补偿
```

选择方案A，因为：
- 数学上更一致（所有方向都加权）
- 让 CHOPPY 判断与 BULLISH/BEARISH 在同一权重基准下
- neutral bias 的 BTC/ETH 应该支持 neutral 占比，符合直觉

### D3: 阈值优化

**决策**：
- BULLISH/BEARISH 阈值：0.5 → **0.45**
- CHOPPY neutral_pct 阈值：0.6 → **0.70**

**理由**：
- 降低 BULLISH 阈值让权重改进发挥作用（BTC 2.0 权重约等于 6% 的 boost）
- 提高 CHOPPY 阈值减少误判（当前 60% 过于宽松）
- 数学模拟显示这两个值可以平衡趋势识别和震荡过滤

**替代方案**：
- 方案A：BULLISH 0.42, CHOPPY 0.75（更激进）
- 方案B：BULLISH 0.48, CHOPPY 0.65（更保守）
- 方案C：BULLISH 0.45, CHOPPY 0.70（平衡）

选择方案C作为首次修复的平衡点，后续可根据实际效果微调。

### D4: 环境变量配置化（可选）

**决策**：暂不引入环境变量

**理由**：
- 本次是 bug 修复，不是功能增强
- 阈值优化基于数学分析，不需要频繁调整
- 如果后续需要 A/B 测试，再引入配置

### D5: P2（4h bias）处理

**决策**：作为独立后续优化，不在本次修复

**理由**：
- 4h bias 涉及 tech_analyst 的输出字段变更
- 需要单独的验证和回测
- 当前修复 P0+P1 已经能显著改善效果
- 滞后性问题可以通过监控 bias 一致性来缓解

## Risks / Trade-offs

### R1: 开仓量激增风险

**风险**：修复后 bullish 占比可能从 1.5% 跳到 15-25%，短期内开仓量激增。

**缓解**：
- 体制空仓硬门仍然生效（需要方向论据）
- R:R floor、EV gate、slot 限制仍然约束开仓
- 修复后先观察 24-48h，评估胜率和 PnL 质量
- 如果开仓质量下降，可以微调阈值（0.45 → 0.48）

### R2: 市场震荡期误判

**风险**：如果市场确实处于震荡期，降低 BULLISH 阈值可能产生误判。

**缓解**：
- hysteresis 机制仍然平滑切换（需要 2 次确认）
- CHOPPY 阈值提高到 0.70，更严格过滤震荡
- 方向论据（aligned / path_evidence）作为二次验证
- 最坏情况：多开几单后被 SL 止损，损失有限

### R3: neutral bias 加权的直觉性

**风险**：neutral bias 归给 neutral 权重可能不符合某些场景的直觉。

**缓解**：
- neutral bias 通常意味着"没有明确方向"，给 neutral 加权合理
- 如果 BTC/ETH 都是 neutral，说明市场确实中性，应该支持 CHOPPY 判断
- 实际效果需要通过监控验证，如有问题可以回退到方案B

### R4: 阈值硬编码

**Trade-off**：阈值硬编码不如配置化灵活，但简化了修复范围。

**缓解**：
- 阈值基于数学模拟选择，有理论支撑
- 如果后续需要调整，改动成本低（单行修改）
- 可以在验证后的后续迭代中引入配置

## Migration Plan

**部署步骤**：
1. 修复代码（P0-1, P0-2, P1-1, P1-2）
2. 运行测试套件确认无回归（pytest 452 passed）
3. OS 层重启 live 实盘进程（新代码才生效）
4. 监控 24-48h：
   - 观察 `diagnostic_regime_classification.json` 的体制分布变化
   - 统计开仓决策数量和通过率
   - 评估胜率和 PnL 质量

**回滚策略**：
- 如果开仓质量显著下降（胜率 <30% 或连续负期望）
- 可以快速回滚：`git revert <commit>` + 重启
- 或者微调阈值：BULLISH 0.45 → 0.48, CHOPPY 0.70 → 0.65

**验证标准**：
- choppy 占比 <80%（目标 60-70%）
- bullish 占比 >10%（目标 15-25%）
- 开仓量增加 50%+
- 胜率维持 >40%（48h 观察窗口）

## Open Questions

无。设计已明确，可直接实施。
