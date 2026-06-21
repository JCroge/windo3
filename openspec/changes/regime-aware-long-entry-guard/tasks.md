## 1. 配置接入

- [x] 1.1 `config.yaml` `risk` 段新增 `long_live_max_range_pos_choppy`（默认 0.55）及对应 daily_gain 体制键，附注释说明体制语义
- [x] 1.2 `utils/config_loader.py` 按 four-segment 模式接入新键，缺省回退现有 0.82/0.75
- [x] 1.3 judge `__init__` 读取并保存 `self._long_live_max_range_pos_choppy` 等字段

## 2. 体制感知阈值核心

- [x] 2.1 新增 helper `_resolve_long_range_thresholds(regime) -> (max_range, daily_gain_range_pos)`：bullish→默认，choppy/mixed→收紧，None/未知→回退默认
- [x] 2.2 `_check_entry_position_policy` 多单分支改用该 helper 取阈值（D1 决策：内部 `snapshot()` 取体制，不新增入参）
- [x] 2.3 ~~四处调用点传入 eff_regime~~ — 已被 D1 取代（内部 snapshot 与相邻 `_apply_regime_policy` 同源，无需改调用点）
- [x] 2.4 确认主路径与 deferred 路径共用同一阈值判定，无漂移（共用同一函数 + 同一 snapshot 源）

## 3. 归因

- [x] 3.1 attribution 增补 `entry_regime_used` 与 `entry_range_pos_threshold` 字段
- [x] 3.2 `entry_position_policy` 标记升级为 `long_overheat_v2_regime`

## 4. 测试

- [x] 4.1 单测：choppy/mixed/bearish + range_pos=0.66 → overheated + should_defer（对照 bullish 同值放行）
- [x] 4.2 单测：体制 None/未知 → 回退 0.82 放行（向后兼容）+ 总开关 off 回退
- [x] 4.3 单测：配置覆盖 `long_live_max_range_pos_choppy`（含 YAML override）生效
- [x] 4.4 单测：空单候选不受多单体制阈值影响（既有 short guard 用例 + 全量回归）
- [x] 4.5 回归：本能力 38 passed；全量 `pytest -q` 1358 passed / 8 failed —— 8 个失败为预先存在的 round2 顺序依赖 flakiness（base 同样失败），本 change 零新增回归；新增 decision_replay 纪元键登记修复

## 5. 验证支撑

- [x] 5.1 attribution 新字段 `entry_regime_used` / `entry_range_pos_threshold` + policy `long_overheat_v2_regime` 已落地，供部署后 dissection / 远期收益脚本按体制切分核对 choppy 多单入场位置与 PF
