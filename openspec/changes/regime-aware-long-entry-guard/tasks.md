## 1. 配置接入

- [ ] 1.1 `config.yaml` `risk` 段新增 `long_live_max_range_pos_choppy`（默认 0.55）及对应 daily_gain 体制键，附注释说明体制语义
- [ ] 1.2 `utils/config_loader.py` 按 four-segment 模式接入新键，缺省回退现有 0.82/0.75
- [ ] 1.3 judge `__init__` 读取并保存 `self._long_live_max_range_pos_choppy` 等字段

## 2. 体制感知阈值核心

- [ ] 2.1 新增 helper `_resolve_long_range_thresholds(regime) -> (max_range, daily_gain_range_pos)`：bullish→默认，choppy/mixed→收紧，None/未知→回退默认
- [ ] 2.2 `_check_entry_position_policy` 多单分支改用该 helper 取阈值；新增 `regime` 入参
- [ ] 2.3 在所有调用 `_check_entry_position_policy` 处（judge.py:802/931/1052/1587）传入 `eff_regime`，体制不可得传 None
- [ ] 2.4 确认主路径与 deferred 路径共用同一阈值判定，无漂移

## 3. 归因

- [ ] 3.1 attribution 增补 `entry_regime_used` 与 `entry_range_pos_threshold` 字段
- [ ] 3.2 `entry_position_policy` 标记升级为 `long_overheat_v2_regime`

## 4. 测试

- [ ] 4.1 单测：choppy + range_pos=0.66 → overheated + should_defer（对照 bullish 同值放行）
- [ ] 4.2 单测：体制 None/未知 → 回退 0.82 放行（向后兼容）
- [ ] 4.3 单测：配置覆盖 `long_live_max_range_pos_choppy` 生效
- [ ] 4.4 单测：空单候选不受多单体制阈值影响
- [ ] 4.5 回归：`python3 -m pytest -q` 全绿（含既有 Long Entry Guard / position guard 用例）

## 5. 验证支撑

- [ ] 5.1 确认 attribution 新字段可被现有 dissection / 远期收益脚本读取，供部署后按体制切分核对 choppy 多单入场位置与 PF
