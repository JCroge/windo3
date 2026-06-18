# 验证报告：ev-gate-winrate-decouple

- **Change**: ev-gate-winrate-decouple
- **Workflow**: full
- **Verify mode**: full（7 tasks / 1 delta spec capability / 真实改动 4 实现文件）
- **日期**: 2026-06-18
- **base-ref**: b6519db6f9137dcf5c980bc9d2da93ace94d7a3b

## 改动摘要

引入 config 开关 `ev_winrate_gate_enabled`（默认 True 保持现状）。关闭后 EV 开仓门用固定中性胜率 `ev_neutral_p_win`（0.55）替代实际滚动胜率，跳过胜率<40%硬阈值与分桶覆盖，保留 EV 阈值经济门。config.yaml 设 `false` 落地诉求。

- `agents/trading/judge.py`：构造新增两字段；`_get_p_win` 关闭时返回 `(0.55,"fixed")`；`_check_expected_value` 分桶块 + 胜率硬阈值前置开关（getattr 容错 `__new__` 构造）
- `utils/config_loader.py`：RISK_DEFAULTS / HARD_LIMITS / env_map / `_load_yaml` / banner 五处接入两键
- `config.yaml`：risk 节点 `ev_winrate_gate_enabled: false` + `ev_neutral_p_win: 0.55`
- `test_ev_gate.py`：3 个新用例 + main() 登记

## 三维度验证

| 维度 | 状态 | 证据 |
|------|------|------|
| Completeness | PASS | tasks 7/7 勾选；capability open-gate-ev 已实现（judge.py:90-91,3630,3660,3706） |
| Correctness | PASS | delta spec 4 场景全有测试；test_ev_gate.py 13 passed；三条胜率路径受开关控制，经济门(judge.py:3714)保留 |
| Coherence | PASS | 实现符合 design.md + Design Doc（2026-06-18-ev-gate-winrate-decouple-design.md）；无 delta spec 漂移 |

## delta spec 场景 → 测试映射

| 场景 | 测试 |
|------|------|
| 开关开启保持现状 | test_ev_gate_block_when_rolling_winrate_collapsed（胜率0.30<0.4 拦） |
| 关闭后低胜率放行 | test_ev_gate_disabled_allows_low_winrate + test_p_win_fixed_when_gate_disabled |
| 关闭仍保留经济门 | test_ev_gate_disabled_still_blocks_bad_economics |
| 配置三级注入与校验 | load_config=False/0.55；HARD_LIMITS 越界抛 ConfigError |

## 回归

- `test_ev_gate.py test_phase2_bucketed_ev.py test_phase2_confidence_split.py`：32 passed
- 更广 sweep（judge/config 相关 13 文件）：118 passed
- 唯一失败 `tests/test_decision_replay.py::test_production_baseline_restores_fidelity` 经 base-ref 复核为 **pre-existing 既有失败**，与本 change 无关

## 安全

- diff 无硬编码密钥 / eval / exec 新增；`ev_neutral_p_win` 有 `(0.0,1.0)` 硬范围校验。

## 结论

CRITICAL/WARNING/SUGGESTION 均无。**All checks passed. Ready for archive.**

## 备注

- 默认值 True，未显式配置环境行为零变化。
- 生效需重启交易进程（Judge 实例化读配置）。
