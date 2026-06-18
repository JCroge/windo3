# 验证报告: rotation-respect-position-hold

- 日期: 2026-06-18
- 验证模式: full
- base-ref: 1bbbc2471ee1d1a3d61d7dfb0b04c125036a3a9c
- Design Doc: docs/superpowers/specs/2026-06-18-rotation-respect-position-hold-design.md
- delta spec: openspec/changes/rotation-respect-position-hold/specs/symbol-rotation-position-guard/spec.md

## Summary

| 维度 | 状态 |
|------|------|
| Completeness | 18/18 tasks `[x]`，1 capability，3 需求全实现 |
| Correctness  | 8/8 spec 场景全覆盖（11 测试），实现与需求一致 |
| Coherence    | 符合 design.md（B-revised）+ Design Doc，复用既有范式 |

**结论：0 CRITICAL，0 WARNING。Ready for archive。**

## Completeness

- openspec status：4/4 artifacts complete（proposal/design/specs/tasks）。
- tasks.md：全部 `[x]`。
- 3 需求均有实现：
  - R1 轮换保留已持仓标的 → `agents/research/symbol_router.py:64-67`（B-revised else 分支）
  - R2 持仓查询 fail-safe → `symbol_router.py:109-126`（`_get_position_symbols`）
  - R3 行为开关 → `utils/config_loader.py`（四段式 116/247-248/275/477）+ `symbol_router.py:20`（`_close_held`）

## Correctness — spec 场景 → 测试映射（8/8）

| spec 场景 | 实现 | 测试 |
|---|---|---|
| R1-A 持仓标的被轮出但保留监控、不平 | else 分支 retained 合并 active、removed 排除 held | `test_held_symbol_retained_not_closed` / `test_retained_merged_into_active` |
| R1-B 无持仓标的维持原平仓 | removed=old-new-held + close 循环 | `test_unheld_symbol_still_closed` |
| R2 positions 文件缺失 | `os.path.exists` 守卫 → `[]` | `test_get_position_symbols_missing_file` |
| R2 positions 文件损坏 | `except Exception` → `[]` + warning | `test_get_position_symbols_corrupt_file` |
| R3 开关默认关闭（保护生效） | DEFAULTS `False` | `test_config_default_is_false` |
| R3 开关开启回退旧行为 | `if self._close_held:` 分支 | `test_close_held_true_reverts_old_behavior` |
| R3 启动 banner 展示状态 | `format_banner` 新增行 | `test_banner_shows_rotation_flag` |
| （额外）既持仓又重选不重复 | `retained = held - new` 去重 | `test_held_and_reselected_appears_once` |
| （额外）env / yaml 覆盖 | env_map + `_load_yaml` | `test_config_env_override_true` / `test_config_yaml_override_true` |

本 change 测试：11 passed。

## Coherence

- **design.md 高层决策**：采纳 B-revised（持仓标的保留 active 集），实现一致（`active_symbols = new + sorted(retained)`）。
- **Design Doc**：fail-safe 退化为旧强平、config risk: 节点四段式、监控链路一致性——实现与文档逐条吻合。
- **范式复用**：`_get_position_symbols` 与 `MultiDataCollector` 同款；config 开关与 `ev_winrate_gate_enabled` bool 范式一致（不进 HARD_LIMITS）。
- **代码审查**：Task 3 经 spec 合规 + code quality 双阶段审查 APPROVED；MINOR 项（retained 定序、去重测试）已修。

## 全量回归（零退化）

- 全量：1306 passed / 10 failed / 4 deselected（177s）。
- 10 失败经 base-ref 对照确认均为既有 flaky，与本 change 无关：
  - 8× round2（probe_long_dispatcher / request_id_position）：全量运行的测试间状态污染，**隔离单跑全 PASS**。
  - 2× CF 保真度（test_decision_replay / test_sequential_perturbation）：**base-ref 1bbbc24 即同样失败**（依赖录制磁带/数据态，fidelity 0.732<0.85）。
- 本 change 11 用例全绿。

## 安全检查

- 无硬编码密钥；新增 `_get_position_symbols` 为只读 fail-safe；config 仅新增 bool 开关默认关闭，实盘默认行为变更需重启生效，env `ROTATION_CLOSE_HELD_ENABLED=true` 可回滚。

## 旁注（独立于本 change）

CF 实验室保真度测试在 base-ref 已跌破阈值（0.732<0.85），疑似录制磁带/数据态随时间漂移导致 lab 再度 untrustworthy。建议单独跟进，不阻塞本 change。
