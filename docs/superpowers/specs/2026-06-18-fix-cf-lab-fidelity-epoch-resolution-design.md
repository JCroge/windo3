---
comet_change: fix-cf-lab-fidelity-epoch-resolution
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-18-fix-cf-lab-fidelity-epoch-resolution
status: final
---

# Design Doc: CF 实验室保真度纪元解析修复

> 需求事实源 = OpenSpec（proposal + `specs/deterministic-replay-harness/spec.md` MODIFIED delta）。本文件只做技术设计。

## 1. 根因（explore 实测）

`utils/decision_replay.py:96` `effective = {**base, **(config or {})}` 把测试传的扰动 override（`{"ladder_rr_enabled": False}`）**无条件压过** `record.config_snapshot`。磁带横跨两纪元（1655 旧 v2 无 snapshot + 1189 新 v3 含 snapshot 且 ladder=True）。全局 pin 对新纪元记录系统性发散。

实测：global_false **0.729** / naked **0.525** / 逐记录纪元解析 **0.890**。
accept/reject 二元保真：v3 **0.991** / full **0.985**（lab 对方向决策本就可信，gate 严格保真低估之）。

## 2. 纪元解析：单层兜底统一处理（核心修复）

```python
# utils/decision_replay.py
_EPOCH_FALLBACK = {                       # 键 → "该键加入 DEFAULTS 之前的纪元默认"
    "ladder_rr_enabled": False,           # ladder 特性（trend-entry-levers-default-on）前 = 关
    "ev_winrate_gate_enabled": True,      # EV 解耦（ev-gate-winrate-decouple）前 = 胜率门恒开
    "ev_neutral_p_win": 0.55,             # 解耦前不参与（门开时用真实胜率），防御性补
}

# replay_decision 有效 config 四层合并：
effective = {
    **production_base_config(),           # 当前生产默认（未翻转的键的基线）
    **_EPOCH_FALLBACK,                     # 翻转键回退到旧纪元默认
    **(record.get("config_snapshot") or {}),  # 录制实际值优先（v3 ladder=True 在此盖回）
    **(config or {}),                      # 真扰动 override（CF 实验旋钮，最顶层）
}
```

一层覆盖三种记录：

| 记录类型 | ladder 结果 | ev_winrate 结果 |
|---|---|---|
| v2（无 snapshot）| _EPOCH_FALLBACK → False ✓ | _EPOCH_FALLBACK → True ✓ |
| v3-full（snapshot 含全键）| snapshot → True ✓ | snapshot → False ✓ |
| v3-partial（缺晚加键）| snapshot → True ✓ | _EPOCH_FALLBACK → True ✓ |

**扰动机制不破**：perturbation override 仍在最顶层，CF 实验"假如 ladder 开/关"照常生效。

## 3. run_arm 对齐（utils/sequential_perturbation.py）

`run_arm(records, config, ...)` 的 `config` 语义收窄为**扰动 override**（最顶层），不再当 baseline 压过 per-record snapshot：
- baseline arm：传 `{}` / `None` → replay_decision 逐记录纪元解析。
- 扰动 arm：传旋钮 delta → 在各记录纪元基线上施扰动。

`build_delta_report` 的 `baseline_config` 调用点相应改传 `{}`。

## 4. 守卫测试（防静默复发）

```python
# 扫描磁带 v3 记录，找"在 DEFAULTS 却缺于某条 v3 snapshot"的键
missing = {k for rec in v3_records
             for k in DEFAULTS
             if k not in (rec.get("config_snapshot") or {})}
# 每个缺键必须被显式分类
assert missing <= set(_EPOCH_FALLBACK) | _GATE_IRRELEVANT
```

`_GATE_IRRELEVANT`：显式 allowlist，列不影响 Judge gate 决策的晚加键（如 `rotation_close_held_enabled`、telegram/retention 类）。未来新增翻转默认键忘登记 → 此测试红 → 强制人工分类（进 _EPOCH_FALLBACK 或 _GATE_IRRELEVANT）。

辅助断言：`set(_EPOCH_FALLBACK) <= set(DEFAULTS)`（无 stale/typo 键）。

## 5. 可信度指标改判

- **accept/reject 二元保真 ≥0.95**（实测 0.985）= 唯一硬门（方向推荐真正依赖的维度）。
- **gate 严格保真降为诊断-only**：打印 / 记入报告，不作断言（对门归因短路顺序过敏，会随门顺序调整反复脆断）。

两个测试改造：
- `tests/test_decision_replay.py::test_production_baseline_restores_fidelity`
- `tests/test_sequential_perturbation.py::test_sequential_baseline_fidelity_restored`

baseline 不再传全局 `{"ladder_rr_enabled": False}`，改纪元解析；加 accept/reject 断言；gate 保真改诊断打印（保留数值可见性）。

## 6. range_position→ev_gate 残余调查（build 任务）

逐记录对比录制 vs 回放的 ev_gate EV 内部输入/输出（p_win、bucketed EV、R:R、cost），钉死 pass→fail 真因。已排除：capture 缺口（`position_in_24h_range=0.1755` 在 tech_analysis.entry_context/short_context 都录上）、ladder/ev_winrate 纪元（补值后 v3 保真不变）。

产出形态：诊断结论写入验证报告；若快修则本 change 内修，否则记 follow-up（不阻塞主修复，因 accept/reject 已证明方向可信）。

## 7. 边界

- observability-only：CF lab 全程离线 write-only，红线守卫禁生产链路 import，无 live 行为变更，不需重启交易进程。
- `_EPOCH_FALLBACK` 是 forward-only 契约：新加翻转默认键时登记其"加入前纪元默认"。

## 8. 测试矩阵

| 测试 | 断言 |
|---|---|
| baseline gate 保真（诊断）| 打印，≥0.85（不硬断） |
| accept/reject 二元保真 | ≥0.95（硬门，实测 0.985） |
| 扰动 override 翻转旋钮 | 回归 perturbation 测试，override 仍生效 |
| 纪元守卫 | 磁带缺键 ⊆ _EPOCH_FALLBACK ∪ _GATE_IRRELEVANT |
| _EPOCH_FALLBACK 完整性 | 键 ⊆ DEFAULTS |

build/verify：`python3 -m pytest tests/test_decision_replay.py tests/test_sequential_perturbation.py -q` + 全量回归无退化。
