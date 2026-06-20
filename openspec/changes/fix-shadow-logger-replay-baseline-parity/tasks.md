## 1. 影子记录器：两臂复盘 + 自检

- [x] 1.1 `utils/shadow_decision_logger.py` 加 `BASELINE_CONFIG={path_evidence_aligned_enabled:False, ladder_rr_enabled:True}`，与现有 `SHADOW_CONFIG` 并列
- [x] 1.2 `log_shadow_decision` 跑两条复盘臂：`baseline=replay(bundle, BASELINE_CONFIG)` + `shadow=replay(bundle, SHADOW_CONFIG)`
- [x] 1.3 `compute_flip_kind` 改基于 `baseline_action` vs `shadow_action`（替换原 `real_action` vs `shadow_action`）
- [x] 1.4 新增 baseline 自检：`baseline_mismatch = (baseline 复盘 accept/reject 类别 != live record accept/reject 类别)`；抽 `_is_accept(action)` helper
- [x] 1.5 `build_shadow_record` 新增字段 `baseline_action` / `baseline_gate` / `baseline_mismatch`，保留 `real_action`/`real_gate`（供自检追溯）
- [x] 1.6 fail-safe 不变：任一臂异常 → 跳过本次记录、返回 None、绝不抛

## 2. judge chokepoint 接线

- [x] 2.1 `agents/trading/judge.py` shadow hook 把 live record 的 accept/reject（real action）传入 `log_shadow_decision` 供自检（不改决策逻辑、防御性 getattr）
- [x] 2.2 确认 hook 仍在 publish 之后、fire-and-forget、异常 fail-safe（不回归 2026-06-17 契约）

## 3. 离线驱动

- [x] 3.1 `cf_shadow_lever1_compare.py` 筛 `flip_kind=shadow_opens` 时先剔除 `baseline_mismatch=True` 记录
- [x] 3.2 报表注明被排除的 `baseline_mismatch` 条数（透明，不静默丢弃）

## 4. 测试

- [x] 4.1 单测：baseline 复盘复现 live accept → `baseline_mismatch=False`，进增量
- [x] 4.2 单测：baseline 复盘背离 live（复盘 hold / live accept）→ `baseline_mismatch=True`，排除
- [x] 4.3 单测：两臂复盘相同 → `flip_kind=same`；shadow 开/baseline 不开 → `shadow_opens`
- [x] 4.4 单测：任一臂 `replay_decision` 抛异常 → fail-safe 跳过、live 不受影响
- [x] 4.5 红线守卫 `tests/test_cf_red_line_guard.py` 不回归（决策/风控路径禁读影子产物）
- [x] 4.6 main() 登记新用例，全量回归零退化

## 5. 文档

- [x] 5.1 更新 CLAUDE.md 风控红线里影子记录器条目（对比口径改两臂复盘 + baseline 自检闸）
- [x] 5.2 comet-design 阶段产出 Superpowers Design Doc（深度技术设计）
