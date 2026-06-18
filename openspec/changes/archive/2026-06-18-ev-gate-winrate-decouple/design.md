# Design (高层): 剔除开仓门的胜率因子

## 架构决策

**单一开关 + 单一注入点**：在 EV 门链路上用 `ev_winrate_gate_enabled` 控制「实际胜率是否参与开仓」。关闭时改用固定中性 `p_win`，保留 EV 阈值这道经济门。开关默认 `true`，向后兼容。

### 为什么用「固定 p_win」而非「停用整个 EV 门」
EV 门同时承担两类拦截：胜率拦截（要剔除）+ R:R/成本经济拦截（要保留）。把 `p_win` 钉死为中性常数，既切断了实际胜率的影响，又让 `EV = p_win·net_profit − (1−p_win)·net_loss` 仍能拦掉赔率太差的单。停用整个门会一并丢掉经济保护，策略衰减期风险更高，故不取。

## 注入点（agents/trading/judge.py）

| 路径 | 位置 | 关闭开关时行为 |
|------|------|----------------|
| ② EV 被胜率压垮 | `_get_p_win()` line 3619 顶部短路 | 返回 `(ev_neutral_p_win, "fixed")`，不读 rolling/bayesian |
| ③ 分桶覆盖 | `_check_expected_value` line 3651 分桶块 | 前置 `ev_winrate_gate_enabled` 条件，整段跳过 |
| ① 胜率硬阈值 | `_check_expected_value` line 3699 | 前置 `ev_winrate_gate_enabled` 条件，跳过 |
| c 经济门 | `_check_expected_value` line 3707 EV 阈值 | **不动**，用固定 p_win 的 EV 继续拦 |

构造函数（line 88 EV 参数块）新增 `_ev_winrate_gate_enabled` / `_ev_neutral_p_win`，沿用 `config.get(..., default) if config else default`。

## 配置流（utils/config_loader.py）

沿用既有四段式（与上个 change `consecutive_loss_limit` 同模式）：
- RISK_DEFAULTS：`ev_winrate_gate_enabled=True`、`ev_neutral_p_win=0.55`
- RANGE_VALIDATORS：`ev_neutral_p_win: (0.0, 1.0)`（布尔不入数值校验）
- env_map：`EV_WINRATE_GATE_ENABLED`(_to_bool)、`EV_NEUTRAL_P_WIN`(float)
- `_load_yaml`：risk 节点映射两键，使 config.yaml 可配
- banner：加「EV 胜率门: 开启/关闭」展示，重启后核对生效

合并优先级不变：RISK_DEFAULTS < config.yaml < 环境变量。

## 数据流（关闭开关后）

```
信号 → _compute_score（不含胜率）→ R:R 门 → EV 门:
   p_win = 0.55 (fixed, 不读实际胜率)
   EV = 0.55·net_profit − 0.45·net_loss
   ├─ 跳过胜率<40%硬阈值
   ├─ 跳过分桶覆盖
   └─ 若 EV < 0.05 → 仍拒（经济门保留）
```

## 不变量

- 开关 `true`（默认）时所有路径与现状逐行一致。
- daily hard stop / regime / short guard / 流动性等其它门不受影响。
- 生效需重启交易进程（Judge 实例化时读配置）。

## delta spec 影响（待 comet-design 确认）

EV 门行为可能由某 capability spec 描述（含 `test_ev_gate.py`、`test_phase2_bucketed_ev.py` 对应验收）。design 阶段需确认是否需要 delta spec 记录「胜率门可关闭」这一新增可配置行为。
