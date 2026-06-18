# 验证报告: fix-cf-lab-fidelity-epoch-resolution

- 日期: 2026-06-18
- 验证模式: full
- base-ref: fc42e576b89502c839c403a857a705ae67ec7f3e
- Design Doc: docs/superpowers/specs/2026-06-18-fix-cf-lab-fidelity-epoch-resolution-design.md
- delta spec: openspec/changes/fix-cf-lab-fidelity-epoch-resolution/specs/deterministic-replay-harness/spec.md (MODIFIED)

## Summary

| 维度 | 状态 |
|------|------|
| Completeness | 15/15 tasks `[x]`，1 capability，3 需求全实现 |
| Correctness  | 8/8 spec 场景全覆盖，实现与需求一致 |
| Coherence    | 符合 design.md（四层合并）+ Design Doc，残余根因超额修复 |

**结论：0 CRITICAL，0 WARNING。Ready for archive。**

## 保真度恢复实绩

| 指标 | 修前 | 修后 |
|------|------|------|
| gate 严格保真（诊断）| 0.732（失败）| **0.969** |
| accept/reject 二元保真（硬门）| — | **0.996** |

两个原失败测试 `test_production_baseline_restores_fidelity` / `test_sequential_baseline_fidelity_restored` 均 failed→passed。

## Completeness

- tasks.md：15/15 `[x]`，含残余调查任务（已升级为快修）。
- 3 需求均有实现：
  - R1 回放有效 config 与 live 生产一致 → `utils/decision_replay.py:99-105`（`_resolve_effective_config` 四层合并）+ `:122`（replay_decision 调用）
  - R2 accept/reject 二元保真为主判据 → 两测试 `ar_fid >= 0.95` 硬断言 + gate 保真 `print` 诊断
  - R3 纪元兜底表防静默漂移守卫 → `tests/test_decision_replay.py` 两守卫测试

## Correctness — spec 场景 → 测试映射（8/8）

| spec 场景 | 实现 | 测试 |
|---|---|---|
| R1 优先用录制 config_snapshot | 四层合并 snapshot 在 fallback 之上 | `test_snapshot_overrides_epoch_fallback` |
| R1 缺键用录制纪元默认 fallback | `_EPOCH_FALLBACK` 在 production 之上 | `test_epoch_fallback_for_missing_keys` |
| R1 扰动 override 不被纪元解析覆盖 | perturbation 最顶层 | `test_perturbation_overrides_all` |
| R1 纪元解析恢复 baseline 保真 | replay_decision(r, None) 逐记录 | `test_production_baseline_restores_fidelity`（gate 0.969 诊断）|
| R2 accept/reject 二元保真作硬门 | `assert ar_fid >= 0.95` | 两 baseline 测试（实测 0.996/0.985）|
| R2 gate 严格保真降为诊断 | `print([diag] ...)` 无断言 | 两 baseline 测试 |
| R3 缺键必须被显式分类 | `missing ⊆ _EPOCH_FALLBACK ∪ _GATE_IRRELEVANT` | `test_no_unclassified_missing_snapshot_keys` |
| R3 纪元兜底键不悬空 | `_EPOCH_FALLBACK ⊆ DEFAULTS` | `test_epoch_fallback_keys_exist_in_defaults` |

额外（残余快修）：`test_install_config_flags_restores_ev_winrate_gate`。

本 change 测试：27 passed。

## Coherence

- **design.md / Design Doc**：四层合并 `production_base < _EPOCH_FALLBACK < config_snapshot < 扰动override`、accept/reject 主指标、守卫表——实现逐条吻合。
- **残余调查产出**：Design Doc 第 6 节定为"逐记录追 EV 内部，快修 or follow-up"。实际逐记录追到根因（`_install_config_flags` 白名单漏还原 `_ev_winrate_gate_enabled`/`_ev_neutral_p_win`，ev_gate `getattr` 默认 True 强制门开），属**快修**路径，已在本 change 内修复（与历史 symbol-state/tech-tape 捕获三修同类）。设计预案的两条路径之一被采纳，无漂移。
- **范式复用**：`_EPOCH_FALLBACK` no-op 条目有注释澄清；守卫测试沿用 `_load_v2_v3`。
- **审查**：CF-T1（引擎四层合并）经 spec 合规 + code quality 双阶段审查 APPROVED（含注释澄清修复）；CF-T3/CF-T4 spec 合规审查通过。

## 全量回归（零退化）

- 全量：1314 passed / 8 failed / 4 deselected（169s）。
- 8 失败为既有 flaky：round2（probe_long_dispatcher / request_id_position）全量 asyncio event-loop 污染，**隔离单跑 8/8 PASS**，与本 change 无关（rotation change 回归亦见同批）。
- 净改善：原 2 个 CF 保真度测试 failed→passed（rotation 回归 10 失败 → 本次 8 失败）。

## 安全检查

- 无硬编码密钥；CF lab 全程 observability-only 离线 write-only，红线守卫禁生产链路 import；无 live 行为变更，不需重启交易进程。

## 价值

CF lab 真·恢复可信（gate 0.969 / accept-reject 0.996）——非靠老记录稀释，而是连根拔除残余（白名单捕获缺口）。下游 `cf_direction_recommendation.py` 等方向推荐工具结论恢复可信赖。[[cf-lab-fidelity-degraded]] 记录的问题已闭环。
