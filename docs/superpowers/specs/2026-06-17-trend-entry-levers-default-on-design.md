---
comet_change: trend-entry-levers-default-on
role: technical-design
canonical_spec: openspec
---

# Design Doc: trend-entry-levers-default-on（lever2 默认开）

> OpenSpec 为需求真相源（`openspec/changes/trend-entry-levers-default-on/`）。本文档承载技术设计。

## 决策 D0：范围 = lever2-only

只把 lever2（`ladder_rr_enabled`）默认开；lever1（`path_evidence_aligned_enabled`）保持默认关。

- **依据**：lever2 是已定价的口径修正（rejected 流 A/B +0.21R/簇 / tier 定价 R:R 对 TP2 频率不敏感）。干净趋势 ladder R:R 中位 1.79 > 1.50 default 地板 → lever2 单独即可开出高-R:R 干净趋势，无需 lever1 降地板。
- **一次只动一个杠杆 = 干净归因**：先测 lever2 的 live 效果；lever1 验证弱（埋点数据待累积），另起 change。

## 决策 D1：默认开的落地方式

两个 flag 当前**不在** `config_loader.DEFAULTS`，靠 `judge.py:174` 的 `config.get('ladder_rr_enabled', False)` 兜底关。改法：

- `utils/config_loader.py`：`DEFAULTS` 新增 `ladder_rr_enabled: True`；`HARD_LIMITS` 加 bool 项（与现有 bool flag 一致）；env 覆盖映射加 `LADDER_RR_ENABLED`（**逃生阀**）。
- `agents/trading/judge.py:174`：兜底改 `config.get('ladder_rr_enabled', True)`，与 DEFAULTS 一致（无 config 时也默认开）。
- lever1 的 `path_evidence_aligned_enabled` **不进 DEFAULTS**，`judge.py:169` 兜底维持 `False`。
- lever 本体逻辑（`_compute_ladder_rr` / `_effective_rr_for_plan`）零改动。

## 决策 D2：验证栈（红线解读已获用户认可）

**event_backtest 结构性测不了本改动**——`event_backtest._build_plan`（line 579）构造**单档 TP**（`tp = price ± rr_floor×SL`），无 TP1/TP2/TP3 三档结构供阶梯口径作用，且**不读** `ladder_rr_enabled`（独立 plan builder，跑 MA 信号非 LLM-Judge）。翻 flag 对 event_backtest 输出**零影响**，A/B 无意义。

故验证栈（经用户确认满足红线意图——验真实代码于历史数据，非只 mock）：

1. **主同构历史验证 = `cf_lever2_rejected_ab.py`**：用**真实 Judge 的 `_compute_ladder_rr`** 在历史 `rejected_signal_events.jsonl` 上 A/B（lever2 off vs on），保守 TP1 结算 → 含亏单净 **+0.21R/簇**。这比 event_backtest 更同构（同一份线上代码 + 历史数据 + A/B）。
2. **tier 到达频率定价**（本会话已做）：P(达TP2)=68%、频率校准 R:R 1.76~1.80。
3. **部署后 paper 前向**：lever2 默认开后 live+paper 同步生效，paper 双轨可观测 lever2 实际开了哪些、前向结局。
4. **event_backtest**：仅作**非回归 sanity**（确认其行为不变——本就不读 flag，故必然不变），验证报告明确记录其不适用原因。

精神承袭 `trend-entry-rr-fidelity`（commit `da47c38` 已把 lever A/B 验证从 event_backtest 切到 CF 实验室）。

## 决策 D3：风控与回滚

- lever2 **提高** effective_rr → 让被误拒趋势单过**正常 1.50 地板**，作**全尺寸正常单**开仓，**不触发** `low_rr_policies`（缩仓/降杠杆/独立 slot 是 lever1 授 <1.5 地板时的路径）。合理：真实 R:R 1.79 的趋势单应正常对待。
- **全局影响**：lever2 影响**所有**信号的 R:R 评分（不只趋势）。验证须确认它**没把低质信号也放开**——rejected 流 A/B 覆盖的是 `rr_below_floor` 全人群（非仅干净趋势），含亏单净仍为正，部分背书此点；回归补测。
- **回滚** = env `LADDER_RR_ENABLED=false` 即时关，零代码改动。
- **部署**：Judge 为 live+paper 共同决策 → 默认开同时影响两者；首窗口紧盯（无法 paper-only，因 Judge 决策路径单一）。

## 边界条件

- `effective_rr_ladder` / 离场权重 `[0.5,0.25,0.25]` 已在决策记录中观测（`judge.py:3558`），默认开后可回溯每笔实际口径。
- 无 config / 无 env 时：DEFAULTS=True + judge 兜底=True 双保险默认开。

## 测试策略

- **单测**：config 默认变更——`ladder_rr_enabled` 默认 True；`LADDER_RR_ENABLED=false` env 覆盖回退；HARD_LIMITS clamp。判定 `_effective_rr_for_plan` 在默认 config 下走 ladder 分支。
- **回归**：全量 `pytest -q` 绿；既有断言若依赖「默认 TP1 口径」需同步更新（预期少量测试涉及 effective_rr 默认值）。
- **同构历史验证**：重跑 `cf_lever2_rejected_ab.py` 附最新数据，验证报告引其 delta。
- **event_backtest**：跑一次确认非回归 + 记录不适用。

## Implementation Divergence（build 期发现）：翻 lever2 默认对回放保真的副作用

build 时全量回归暴露：翻 lever2 默认开会**打破所有"翻转前"录制磁带的回放保真**。根因——`production_base_config()`（回放基线）`= config_loader.DEFAULTS`，现含 `ladder_rr_enabled=True`；而翻转前的录制磁带（`data/decision_replay_tape.jsonl` 1418 条）的 `config_snapshot` **不含 ladder 键**（录制时它还不在 DEFAULTS/self.config）→ 缺键回落到生产基线（现 on）→ 用阶梯口径回放 TP1-纪元决策 → gate 系统性发散（L2 fidelity 0.31，sequential 0.32，原 ~0.90）。

**这不是 bug，是配置纪元边界**：磁带录于"lever2 off"纪元，用"lever2 on"基线回放本就会发散。`config_snapshot` 是纪元锚——**翻转后新录制的记录会自带 `ladder_rr_enabled=True`**（已进 DEFAULTS→self.config→snapshot），前向回放自洽，无需 pin。

**处理（本 change）**：3 个 config-parity/capture 保真守卫测试（`test_production_baseline_restores_fidelity` / `test_sequential_baseline_fidelity_restored` / `test_capture_record_replays_to_gate_reject` + 其地板-翻转伴随）显式 pin `ladder_rr_enabled=False`，钉翻转前磁带的录制纪元，使其继续守 config-parity（非 ladder 维度）。

**对真实 CF 实验室的影响（须知）**：用**翻转前旧磁带**跑的观察驱动 `cf_direction_recommendation.py` / `cf_rr_fidelity_ab.py`（经 `build_delta_report`/`run_arm` 生产基线）翻转后 baseline_fidelity 会塌、对旧磁带 untrustworthy——除非同样 pin `ladder_rr_enabled=False`。**`cf_lever2_rejected_ab.py` 不受影响**（自算 ladder_rr，不读 flag）。旧磁带随前向新记录累积自然退役（下一个 change 的影子记录器会产自洽新数据）。**不工程化改 production_base_config 排除 ladder**（为一次性迁移加 per-flag 特例不划算）。

## 不做（YAGNI）

- 不开 lever1（另起 change）；不做 lever2 v2 概率校准（R:R 已证对频率不敏感）；不重写 event_backtest 复用 Judge 口径（独立 follow-up）；不改 R:R 地板阈值；不为旧磁带工程化改 production_base_config（pin + 自然退役即可）。
