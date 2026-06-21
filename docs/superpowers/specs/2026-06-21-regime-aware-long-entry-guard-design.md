---
comet_change: regime-aware-long-entry-guard
role: technical-design
canonical_spec: openspec
---

# Design: regime-aware-long-entry-guard

> 需求事实源为 OpenSpec delta spec（`openspec/changes/regime-aware-long-entry-guard/specs/`）。本文档只承载技术设计，不重述需求。

## 1. 背景与定位

多单过热位置门 `_check_entry_position_policy`（`agents/trading/judge.py:2825`）当前用体制无关固定阈值 `_long_live_max_range_pos=0.82` / `_long_live_daily_gain_range_pos=0.75`。诊断证实 choppy/非趋势体制下多单在 0.55–0.66 位置追突破系统性负期望（choppy 多单 PF 0.72、方向胜率 27%、入场后 +1h 远期 25%、信号分与方向相关性 −0.04）。超阈转 `deferred_pullback_overheat` 的"回调入场"机制**已存在**（judge.py:2895-2909），本设计只让阈值体制感知，复用该路径。

## 2. 关键技术决策

### D1. regime 取值：内部 snapshot，不传参
`_check_entry_position_policy` 内部直接 `self._regime_manager.snapshot()['effective_regime']` 取体制，与紧邻调用的 `_apply_regime_policy`（judge.py:2957）**同源同法**。

- 四处调用点（judge.py:802/931/1052/1587）均紧跟 `_apply_regime_policy`，regime 必已可解析，无未解析调用点。
- 文件内已多处 `snapshot()`（1330/2517/2957/3156），再取一次一致且廉价。
- **不改函数签名、不动四处调用点** —— 比"四处传参"改动面更小、无参数漂移风险。

### D2. 阈值解析单一收口
新增私有 helper：
```
_resolve_long_range_thresholds(eff_regime) -> (max_range, daily_gain_range_pos)
  eff_regime == 'bullish'                      -> (0.82, 0.75)   # 默认，不动
  eff_regime in {'choppy','mixed','bearish'}   -> (cfg_choppy_max, cfg_choppy_daily)  # 默认 (0.55, 0.50)
  其它 / None / 非白名单                         -> (0.82, 0.75)   # 回退兼容
  总开关关闭                                     -> (0.82, 0.75)   # 即时回退
```
主路径与 deferred 路径都经此 helper，杜绝漂移。helper 只读不写、纯函数式，单测友好。

### D3. 体制分组语义："只在确认牛市追高"
仅 `bullish` 保留宽松 0.82；`choppy/mixed/bearish` 三个非确认上涨体制一律收紧。`mixed` 是默认/冷启动体制，收紧符合"未确认上涨即不追"的保守意图。`None`/未知/非白名单值回退默认（向后兼容，对应 spec「体制不可得回退」）。

### D4. 安全开关
`risk.long_live_regime_aware_range_enabled`（默认 true）。`false` 时 helper 一律返回默认 (0.82/0.75)，等价变更前行为 —— 实盘可一键回退，不需回滚代码。

### D5. 归因可验证
位置门结果增补 `entry_regime_used`、`entry_range_pos_threshold`；`entry_position_policy` 标记升级 `long_overheat_v2_regime`。使部署后可用现有 dissection / 远期收益脚本按体制切分核对入场位置与 PF。

## 3. 数据流

```
candidate (main / deferred_15m / deferred_pullback / deferred_chase)
   │
   ├─► _apply_regime_policy        (已有: snapshot → eff_regime → R:R/short guard)
   │
   └─► _check_entry_position_policy (本次改)
          eff_regime = snapshot()['effective_regime']
          (max_range, daily_gain_rp) = _resolve_long_range_thresholds(eff_regime)
          long overheat 判定沿用现有 range_pos / pre_move / daily_gain 三分支
          超阈 → should_defer → deferred_pullback_overheat (已有路径)
          attribution += {entry_regime_used, entry_range_pos_threshold, policy=long_overheat_v2_regime}
```

## 4. 配置（four-segment，接 config_loader）

```yaml
risk:
  long_live_regime_aware_range_enabled: true   # 总开关
  long_live_max_range_pos_choppy: 0.55         # 收紧体制 max_range
  long_live_daily_gain_range_pos_choppy: 0.50  # 收紧体制 daily_gain_range_pos
```
缺省全部回退现值（0.82/0.75）。judge `__init__` 读取保存为 `self._long_live_*`。

## 5. 测试策略

| 用例 | 期望 |
|---|---|
| choppy + range_pos=0.66 非probe | overheated + should_defer |
| mixed + 0.66 | overheated + should_defer |
| bearish + 0.66 | overheated + should_defer |
| bullish + 0.66 | normal 放行 |
| eff_regime=None + 0.70 | 回退 0.82，normal 放行 |
| 总开关 false + choppy + 0.66 | 回退 0.82，normal 放行 |
| config 覆盖 long_live_max_range_pos_choppy=0.50 | 0.50 生效 |
| open_short 任意体制 | 不受多单阈值影响，沿用 short guard |
| 回归 | `pytest -q` 全绿（含既有 Long Entry Guard 用例） |

## 6. 边界与风险

- **冷启动默认 mixed → 收紧**：保守，符合意图。
- **probe 单**：现有 `is_probe` 豁免不变。
- **mixed 收紧的代价**：mixed 为默认体制，choppy/mixed 多单可能大量转 defer 难成交 —— 跳过坏入场即目标；总开关可即时回退。
- **regime 误判传导**：本变更不改 regime 分类；regime 判错则阈值随之错，属已知上游依赖，不在本次范围。
- **不碰**：`_compute_score`、regime 分类、出场/SL/紧急清仓、short-side guard。
