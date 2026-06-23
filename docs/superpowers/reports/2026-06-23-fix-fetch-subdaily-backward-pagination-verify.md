# Verification Report: fix-fetch-subdaily-backward-pagination

**Date**: 2026-06-23 | **verify_mode**: full(7 文件含 openspec 脚手架;实际代码仅 2 文件)| hotfix

## Summary
| 检查 | 结果 |
|---|---|
| tasks 全勾 | 5/5 ✓ |
| 实现符合 design.md | ✓(历史起点正向分页 + 窗口 interval 感知) |
| 根因消除 | ✓(旧 `since=None`+向未来翻 模式已移除) |
| 编译 + 测试 | ✓(2 文件编译;形态库+红线守卫 26 passed) |
| proposal 目标 | ✓(4h 回填 ~1.85年/116061根;4h 确认完成) |
| 安全 | ✓(无密钥;observability-only 未接 live) |
| 无 delta spec | hotfix 无 spec 级变更 |

**无 CRITICAL。Ready for archive。**

## 修复验证
- `fetch_symbol` 改从 `now - max_bars×interval_ms` 历史起点正向分页 + open_time 去重。4h:30000→**116061 根**(每币 4000 根起 2024-08-25,~1.85 年)。日线行为不变(复现原 2 候选)。
- `cf_pattern_edge_discovery.py`:加 `--interval` + 窗口 interval 感知(天→bar,4h 时窗 ×6 与日线对齐)。

## 4h 确认结果(本 hotfix 的实质 payoff)
- **Bearish Engulfing | 低位·跌势 = 确认**:日线 +0.326R(n135 actionable),4h 时间对齐 +0.208R(n293 actionable,同号正)。
- **Evening Star | 中位·涨势 = 否决**:4h 翻 −0.106R。
- 方法学:4h 原生上下文(bar 计窗=3.3天)误判翻号;时间对齐(20天窗)才正确确认 → 跨周期确认须时窗可比。

## 零回归
日线复现原 2 候选;本 change 测试 26 passed。全量套件的 1 预存正交 fail(test_no_unclassified_missing_snapshot_keys)与本 hotfix 无关(未碰 decision_replay)。
