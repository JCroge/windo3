# Tasks — fix-cf-lab-replay-config-parity

> 骨架，comet-design 定方案后细化。

## 设计（comet-design）
- [x] brainstorm 选定 config 基线方案（采用 C：生产基线 + 磁带录 config_snapshot）
- [x] 确认 perturbation 叠加语义（生产基线 < config_snapshot < perturbation 覆盖，只覆盖目标旋钮）
- [x] 产出 Design Doc + delta spec（decision-replay-tape / deterministic-replay-harness / sequential-perturbation-driver / replay-report-driver，validate 通过）

## 实现
- [x] replay_decision 用生产 config 基线（production_base_config = config_loader.DEFAULTS，record.config_snapshot 优先，扰动叠加其上）—— 单 chokepoint 覆盖 build_delta_report/run_arm/sweep_knob/driver
- [x] decision_tape 录 config_snapshot（schema v3）+ judge.py 两 chokepoint 捕获 self.config（write-only observability）
- [x] 旧 v2 记录 fallback production_base_config()

## 测试
- [x] 直接 L2 fidelity 用生产基线 = **0.914**（≥0.85，对照 config={} 的 0.365）—— config-parity 根因已修坐实
- [x] perturbation 叠加正确：扰动 rr_floor 不改 phase2 flag（test_perturbation_overlays_on_production_base_only_target）
- [x] 红线守卫 `tests/test_cf_red_line_guard.py` 维持（4 passed）
- [x] 全量 pytest 回归：1252 passed（基线 1247 +5，不回退）

## 验收
- [x] 重跑 cf_direction_recommendation.py：**config-parity 已修**（直接 L2 fidelity 0.34→0.914 坐实）。**但驱动 sequential 臂 baseline_fidelity=0.798 仍 <0.8 untrustworthy**——根因是 `_inject_cf_state` 把 `_symbol_state={}`/balance 等用 CF 重建替换录制快照引入的二级残差（~12pp），即本 change **明确非目标**的 CF 序列状态重建缺口（下一前沿）。本 change 范围（config parity）已达成且必要（是解开该残差的前置），但**实验室端到端仍未跨可信线**，需后续 change 修 CF-state 注入保真。
