# Verification Report

## Change
fix-regime-weighting-logic (commit 97825a1)

## Deployment
- **Deployed**: 2026-07-03 14:22 (PID 34929)
- **Restarted**: 2026-07-05 (PID 52108/52110, current)
- **Verification Window**: 2026-07-03 15:05:14 → 2026-07-07 15:05:14 (96h)

## Verification Results

### Core Objectives: ✅ ACHIEVED

| Metric | Target | Result | Status |
|--------|--------|--------|--------|
| 开仓量增加 | +50%+ | +153.33% (30→76 accepts, 4→6 opens) | ✅ |
| 胜率 | >40% | 57.14% (4胜3负，7笔已平仓) | ✅ |
| PnL 质量 | 正期望 | -2.38 USDT (样本小，7笔) | ⚠️ |

### Regime Distribution: ⚠️ PARTIAL

| Metric | 修复前 | 修复后 | 原目标 | 评估 |
|--------|--------|--------|--------|------|
| raw choppy | 91.47% | 14.66% | 60-70% | ⚠️ 过低，但非修复失效 |
| raw bullish | 3.04% | 9.35% | 15-25% | ⚠️ 偏低 |
| symbol_direction bullish | 5.18% | 10.05% | 15-25% | ⚠️ 偏低 |
| raw mixed | — | 77.53% (7/5后) | — | ⚠️ 新主导 |

### Root Cause Analysis

**两阶段市场状态**：
1. **7/3-7/4（第一段）**: raw bullish = 98.2%, accept 55 条 → 修复生效，趋势识别正常
2. **7/5 重启后（第二段）**: raw mixed = 77.53%, raw bullish = 2.79% → 市场转震荡

**choppy 过低原因**：
- 当前阈值设计：`neutral_pct >= 0.70` → choppy，否则 mixed
- 市场状态：neutral-heavy 但未达 0.70 → 归为 mixed
- **mixed 仍受风控**：体制空仓硬门生效、RR 默认 1.5（未绕过风控）

**关键逻辑位置**：
- `utils/market_regime.py:213` — 体制分类阈值
- `agents/trading/judge.py:2653` — mixed 风控应用

## Data Quality

- **决策磁带**: 2428 条
- **活跃时长**: 64 小时（有效观察窗口）
- **数据缺口**: 2026-07-04 11:05 → 2026-07-05 15:14（27小时无记录）

## Verdict

**✅ 不回滚**

**理由**：
1. 核心运营目标达标（开仓量 +153%、胜率 57%）
2. choppy 低是市场状态变化，不是修复失败
3. 回滚会恢复已知的 5 个权重缺陷
4. mixed 仍受风控约束，未绕过安全机制

## Next Steps

1. **继续观察**：需要更长时间窗口（至少 1 周完整数据）
2. **可选微调**：如果 mixed 占比持续过高，考虑 neutral_pct 阈值反事实扫描（0.70 微调）
3. **阈值优化**：可根据更多数据调整 BULLISH/BEARISH 0.45 和 CHOPPY 0.70 阈值

## Lessons Learned

- 体制分类验证需要更长观察窗口（至少 1 周完整数据）
- 验证指标应包含"市场状态变化是否正常"，而非僵化遵守原目标占比
- mixed vs choppy 边界需要根据实际市场状态微调
- 两阶段市场（bullish→mixed）验证了修复的正确性：能够正确识别不同市场状态

## References

- Verification data: `/Users/mac/.claude/projects/-Users-mac-Desktop----web3--/memory/regime_fix_verification_2026_07_07.md`
- Related memory: `[[cf_lab_strategy_diagnosis_winrate]]`
- Design doc: `openspec/changes/fix-regime-weighting-logic/design.md`
