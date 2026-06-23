# Verification Report: fix-replay-register-reversal-pseudo-keys

**Date**: 2026-06-23 | hotfix | full(6 文件含 openspec 脚手架;实际代码 1 文件 +4 行)

## Summary
| 检查 | 结果 |
|---|---|
| tasks 全勾 | 3/3 ✓ |
| 实现符合 design.md | ✓(_EPOCH_FALLBACK 加 4 键,纪元前默认 OFF/no-op) |
| 目标测试 | `test_no_unclassified_missing_snapshot_keys` PASS;decision_replay 全 12 passed |
| 全量回归 | **1416 passed / 0 failed**(预存 fail 收掉,零新回归) |
| 安全 | 纯纪元回退白名单登记(+4 行),不改判定逻辑/config/live 行为 |

**无 CRITICAL。Ready for archive。**

## 修复
`utils/decision_replay.py::_EPOCH_FALLBACK` 加:`llm_rsi_reversal_veto_enabled:False`(真翻转)、`reversal_veto_min_llm_confidence:0`(no-op)、`pseudo_resonance_downweight_enabled:False`(真翻转)、`ma_bloc_cap:50`(no-op)。同 521dad5 维护模式。

## 影响
收掉自 daily-pattern-edge-lab 起一直挂着的预存正交 fail。全套件首次全绿。不改任何 live 决策/回放判定(enabled=False 旧纪元本不触判定分支)。
