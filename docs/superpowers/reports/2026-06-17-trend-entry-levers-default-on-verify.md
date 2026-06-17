# Verification Report: trend-entry-levers-default-on

**Date:** 2026-06-17 · **Workflow:** full · **verify_mode:** full · **范围:** lever2-only

## Summary

| Dimension | Status |
|---|---|
| Completeness | 6/6 tasks；delta `ladder-weighted-rr` 4 场景全覆盖 |
| Correctness | lever2 默认开实现于 config/judge/replay；rejected 流 A/B +0.181R/簇 |
| Coherence | 符合 Design Doc D0–D3 + Implementation Divergence 已记录；1288 绿 |

**Final Assessment: All checks passed. Ready for archive.**

## delta spec 场景核对（`ladder-weighted-rr` ADDED「ladder_rr_enabled 默认启用」）

| 场景 | 实现证据 | 状态 |
|---|---|---|
| 默认启用 | `utils/config_loader.py:135` `"ladder_rr_enabled": True`；`judge.py:176` 兜底 True | ✓ |
| env 逃生阀即时关闭 | `config_loader.py:276` `LADDER_RR_ENABLED→_to_bool`；`tests/test_ladder_rr_default_on.py::test_ladder_rr_env_escape_valve` | ✓ |
| lever1 不随本 change 开 | `judge.py:169` + `decision_replay.py:193` `path_evidence_aligned_enabled` 仍默认 False；`tests/test_rr_fidelity_knob_injection.py` 守 lever1 off/lever2 on | ✓ |
| 过正常地板不走 low_rr_policies | lever2 经 `_effective_rr_for_plan`（`judge.py:3481`）**抬高** effective_rr 过 1.50 default → 全尺寸正常单（`low_rr_policies` 仅 lever1 授 <1.5 地板时触发，本 change lever1 关）；全量回归覆盖 | ✓ |

## 验证证据栈（主证据非 event_backtest）

1. **lever2 rejected 流忠实 A/B**（`cf_lever2_rejected_ab.py`，同一份线上 ladder 口径于历史被拒磁带）：562 趋势簇翻转、77 可结算（klines ~3 天覆盖限制）、成交簇胜率 52.5%、**含亏单保守 TP1 口径净 +13.91R / 77 簇 = +0.181 R/簇**。零 TP2 信用下仍正期望。
2. **tier 到达频率定价**（本会话）：被拒干净趋势 long P(达TP2)=68%、P(TP2|达TP1)=90%；频率校准 R:R 1.76~1.80（对 TP2 假设不敏感）。
3. **event_backtest 非回归**：smoke 跑通（4 trades，exit 0）；结构上不读 `ladder_rr_enabled`（自有 `_build_plan`）→ 翻 flag 零影响。已知失真（MA 信号≠LLM-Judge、单档 TP 无阶梯结构）记入 Design Doc，红线意图由忠实的 rejected 流 A/B 满足（precedent: `trend-entry-rr-fidelity` commit `da47c38`）。
4. **全量回归**：`python3 -m pytest -q` → **1288 passed / 4 deselected**（1285 基线 + 3 新 lever2 测试）。

## Implementation Divergence（build 期发现，已修+已记录）

翻 lever2 默认开打破翻转前磁带（`decision_replay_tape.jsonl`，录于 ladder=off 且 config_snapshot 无 ladder 键）回放保真：L2 fidelity 0.31 / sequential 0.32（原 ~0.90），因 `production_base_config`（= DEFAULTS）现 ladder=on。**配置纪元边界非 bug**。处理：3 个 config-parity/capture 保真守卫 pin `ladder_rr_enabled=False` 钉旧纪元；前向新记录自带 ladder=True 自洽。真实 CF 实验室用旧磁带的驱动（`cf_direction_recommendation.py`/`cf_rr_fidelity_ab.py`）翻转后对旧磁带 untrustworthy 除非 pin ladder=off；`cf_lever2_rejected_ab.py` 不受影响（自算）；旧磁带随影子记录器新数据自然退役。详见 Design Doc。

## 灰度 / 回滚

- 部署影响 live+paper（Judge 共同决策）；首窗口紧盯。
- **回滚** = env `LADDER_RR_ENABLED=false` 即时关，零代码改动。

## Issues

- CRITICAL：无 · WARNING：无 · SUGGESTION：无

## 后续（非本 change）

- ② 前向影子决策记录器（lever1+lever2 影子对比，已与用户约定下一个 change）。
- lever1 默认开（待影子记录器累积 path-evidence 数据验证后另起 change）。
